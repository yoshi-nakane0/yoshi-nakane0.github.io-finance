"""Prediction ページのビュー。

- index: 4 トピックの集計＋直近24時間のネガティブ記事を表示
- refresh: ボタンから GDELT 取得を実行（ハイブリッドセッション認証付き）
- authenticate: パスフレーズ入力を受け、セッションフラグを立てる
"""

import logging
from datetime import datetime, timedelta, timezone

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .api.auth import (
    SESSION_AUTH_KEY,
    SESSION_DURATION_SEC,
    get_passphrase,
    grant_session,
    has_valid_session,
    session_remaining_seconds,
    verify_passphrase,
)
from .models import SentimentArticle
from .services.refresh import (
    SKIP_WINDOW_SEC,
    get_last_refresh_at,
    refresh_all_topics,
    should_skip_refresh,
)
from .services.sentiment_aggregator import build_topic_summary, list_active_topics

logger = logging.getLogger(__name__)

ARTICLES_PER_TOPIC = 3
RECENT_HOURS = 24


def _build_cards(days: int = 7) -> list:
    return [build_topic_summary(topic, days=days) for topic in list_active_topics()]


def _build_articles_by_topic() -> list:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=RECENT_HOURS)
    sections = []
    for topic in list_active_topics():
        rows = list(
            SentimentArticle.objects
            .filter(topic=topic, published_at__gte=cutoff)
            .order_by('-published_at')
            .values('title', 'url', 'domain', 'published_at')[:ARTICLES_PER_TOPIC]
        )
        sections.append({
            'topic_slug': topic.slug,
            'topic_name': topic.name_ja,
            'category': topic.category,
            'articles': [
                {
                    'title': r['title'],
                    'url': r['url'],
                    'domain': r['domain'],
                    'published_at': r['published_at'],
                }
                for r in rows
            ],
        })
    return sections


def index(request):
    cards = _build_cards(days=7)
    articles_by_topic = _build_articles_by_topic()
    last_refresh = get_last_refresh_at()
    skip, remaining = should_skip_refresh()
    context = {
        'cards': cards,
        'articles_by_topic': articles_by_topic,
        'last_refresh': last_refresh,
        'is_authenticated': has_valid_session(request),
        'session_remaining_sec': session_remaining_seconds(request),
        'session_duration_hours': SESSION_DURATION_SEC // 3600,
        'refresh_skip': skip,
        'refresh_skip_remaining': remaining,
        'refresh_window_sec': SKIP_WINDOW_SEC,
        'passphrase_configured': bool(get_passphrase()),
    }
    return render(request, 'prediction/list.html', context)


@require_POST
def authenticate(request):
    """パスフレーズを検証し、合えばセッションフラグを立てる。"""
    if not get_passphrase():
        messages.error(
            request,
            'パスフレーズが未設定です（.env の PREDICTION_AUTH_PASSPHRASE）',
        )
        return redirect(reverse('prediction:index'))
    passphrase = request.POST.get('passphrase', '')
    if verify_passphrase(passphrase):
        grant_session(request)
        messages.success(request, '認証しました。これで更新ボタンが使えます。')
    else:
        messages.error(request, 'パスフレーズが正しくありません')
    return redirect(reverse('prediction:index'))


@require_POST
def refresh(request):
    """GDELT 取得を実行。ハイブリッドセッション認証必須。"""
    if not has_valid_session(request):
        messages.error(request, '認証が必要です。パスフレーズを入力してください。')
        return redirect(reverse('prediction:index'))

    try:
        result = refresh_all_topics(force=False)
    except Exception as exc:
        logger.exception('GDELT refresh failed')
        messages.error(request, f'更新中にエラー: {exc}')
        return redirect(reverse('prediction:index'))

    if result.get('skipped'):
        messages.info(
            request,
            f"直近 {SKIP_WINDOW_SEC // 60} 分以内に更新済みのためスキップ "
            f"（あと {result['remaining_sec']} 秒）",
        )
        return redirect(reverse('prediction:index'))

    ok = len(result['success'])
    ng = len(result['failed'])
    if ng == 0:
        messages.success(request, f'{ok} トピックを更新しました')
    elif ok == 0:
        messages.error(request, f'全 {ng} トピックの取得に失敗しました')
    else:
        messages.warning(request, f'{ok} 件成功 / {ng} 件失敗')
    return redirect(reverse('prediction:index'))


@require_POST
def logout(request):
    request.session.pop(SESSION_AUTH_KEY, None)
    messages.success(request, '認証を解除しました')
    return redirect(reverse('prediction:index'))
