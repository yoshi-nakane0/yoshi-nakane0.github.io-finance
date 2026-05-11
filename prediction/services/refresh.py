"""GDELT 取得→DB 保存の共通処理（ボタンとコマンドから利用）。"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from django.db import transaction
from django.db.models import Max

from ..models import SentimentArticle, SentimentObservation, SentimentTopic
from .gdelt_client import GdeltApiError, fetch_topic_window

logger = logging.getLogger(__name__)

SKIP_WINDOW_SEC = 300  # 5分
ARTICLE_RETENTION_DAYS = 7
MAX_ARTICLES_PER_TOPIC = 75


def get_last_refresh_at() -> Optional[datetime]:
    """直近の refresh 完了時刻を DB から取得。

    SentimentObservation.updated_at の最大値を「最終取得時刻」として扱う。
    プロセスを跨いでも一貫した値を返す。
    """
    latest = SentimentObservation.objects.aggregate(latest=Max('updated_at'))
    return latest.get('latest')


def should_skip_refresh(now: Optional[datetime] = None) -> tuple:
    """5分以内に実行済みなら (True, 残り秒数) を返す。"""
    last = get_last_refresh_at()
    if last is None:
        return False, 0
    now = now or datetime.now(timezone.utc)
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    elapsed = (now - last).total_seconds()
    if elapsed < SKIP_WINDOW_SEC:
        return True, int(SKIP_WINDOW_SEC - elapsed)
    return False, 0


def _save_observation(topic: SentimentTopic, observation_date: date, aggregate: dict):
    SentimentObservation.objects.update_or_create(
        topic=topic,
        observation_date=observation_date,
        defaults={
            'articles_count': aggregate['articles_count'],
            'tone_avg': aggregate['tone_avg'],
            'tone_min': aggregate['tone_min'],
            'tone_max': aggregate['tone_max'],
        },
    )


def _save_articles(topic: SentimentTopic, articles: list):
    for article in articles:
        if not article.get('url') or article.get('published_at') is None:
            continue
        SentimentArticle.objects.update_or_create(
            topic=topic,
            url=article['url'][:512],
            defaults={
                'title': article.get('title', '')[:2000],
                'published_at': article['published_at'],
                'domain': (article.get('domain') or '')[:128],
                'tone': article.get('tone'),
            },
        )


def _purge_old_articles(topic: SentimentTopic, now: datetime):
    cutoff = now - timedelta(days=ARTICLE_RETENTION_DAYS)
    SentimentArticle.objects.filter(
        topic=topic, published_at__lt=cutoff,
    ).delete()


def refresh_topic(topic: SentimentTopic) -> dict:
    """1トピックを GDELT から取得し DB に保存。"""
    now = datetime.now(timezone.utc)
    window = fetch_topic_window(
        topic.query,
        days=1,
        max_records=MAX_ARTICLES_PER_TOPIC,
        end=now,
    )
    aggregate = {
        'articles_count': window['articles_count'],
        'tone_avg': window['tone_avg'],
        'tone_min': window['tone_min'],
        'tone_max': window['tone_max'],
    }
    today = now.date()
    with transaction.atomic():
        _save_observation(topic, today, aggregate)
        _save_articles(topic, window['articles'])
        _purge_old_articles(topic, now)
    return {
        'topic': topic.slug,
        'observation_date': today.isoformat(),
        'articles_count': aggregate['articles_count'],
        'tone_avg': aggregate['tone_avg'],
    }


def refresh_all_topics(force: bool = False) -> dict:
    """全アクティブトピックを更新。5分以内は force=False ならスキップ。"""
    now = datetime.now(timezone.utc)
    if not force:
        skip, remaining = should_skip_refresh(now)
        if skip:
            return {
                'skipped': True,
                'reason': 'recent_refresh',
                'remaining_sec': remaining,
                'success': [],
                'failed': [],
            }

    success = []
    failed = []
    for topic in SentimentTopic.objects.filter(is_active=True).order_by(
        'display_order', 'slug',
    ):
        try:
            success.append(refresh_topic(topic))
        except GdeltApiError as exc:
            logger.warning("GDELT refresh failed for %s: %s", topic.slug, exc)
            failed.append({'topic': topic.slug, 'error': str(exc)})
        except Exception as exc:
            logger.exception("Unexpected error on topic %s", topic.slug)
            failed.append({'topic': topic.slug, 'error': str(exc)})

    return {
        'skipped': False,
        'success': success,
        'failed': failed,
        'refreshed_at': now.isoformat(),
    }
