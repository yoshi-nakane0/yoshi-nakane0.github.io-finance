"""Prediction の軽量 JSON API。

外部の AI（Claude / ChatGPT 等）から叩く想定。
全エンドポイントは X-API-KEY ヘッダ必須。
"""

from datetime import datetime, timedelta, timezone

from django.http import JsonResponse
from django.views.decorators.http import require_GET

from ..models import SentimentArticle, SentimentTopic
from ..services.sentiment_aggregator import (
    build_summary_for_slug,
    list_active_topics,
)
from .auth import require_api_key


def _parse_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _resolve_topic(request) -> tuple:
    """topic スラッグから SentimentTopic を引く。見つからなければ (None, error)。"""
    slug = (request.GET.get('topic') or '').strip()
    if not slug:
        return None, JsonResponse({'error': 'topic is required'}, status=400)
    try:
        topic = SentimentTopic.objects.get(slug=slug, is_active=True)
    except SentimentTopic.DoesNotExist:
        return None, JsonResponse(
            {'error': f'topic not found: {slug}'}, status=404,
        )
    return topic, None


@require_GET
@require_api_key
def summary(request):
    """トピックの日次集計を取得する。

    引数:
      - topic: スラッグ（必須）。例 inflation / geopolitical / fed / recession
      - days: 集計期間（既定 7、1〜30）
    """
    days = _parse_int(request.GET.get('days'), default=7, minimum=1, maximum=30)
    slug = (request.GET.get('topic') or '').strip()
    if not slug:
        return JsonResponse(
            {
                'topics': [
                    {'slug': t.slug, 'name_ja': t.name_ja, 'category': t.category}
                    for t in list_active_topics()
                ],
            }
        )
    payload = build_summary_for_slug(slug, days=days)
    if payload is None:
        return JsonResponse({'error': f'topic not found: {slug}'}, status=404)
    return JsonResponse(payload)


@require_GET
@require_api_key
def articles(request):
    """トピックの直近記事一覧を取得する。

    引数:
      - topic: スラッグ（必須）
      - hours: 何時間前までを対象とするか（既定 24、1〜168）
      - limit: 最大件数（既定 20、1〜100）

    注: GDELT Doc API の ArtList モードは個別記事のトーンを返さないため、
    記事ごとのトーンは含まれない。トーンは summary エンドポイントの
    日次平均値（topic 全体）でのみ取得可能。
    """
    topic, error = _resolve_topic(request)
    if error:
        return error

    hours = _parse_int(request.GET.get('hours'), default=24, minimum=1, maximum=168)
    limit = _parse_int(request.GET.get('limit'), default=20, minimum=1, maximum=100)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    qs = (
        SentimentArticle.objects
        .filter(topic=topic, published_at__gte=cutoff)
        .order_by('-published_at')
    )

    items = list(qs.values('title', 'url', 'domain', 'published_at')[:limit])

    return JsonResponse({
        'topic_slug': topic.slug,
        'topic_name': topic.name_ja,
        'hours': hours,
        'count': len(items),
        'articles': [
            {
                'title': row['title'],
                'url': row['url'],
                'domain': row['domain'],
                'published_at': (
                    row['published_at'].isoformat()
                    if row['published_at'] else None
                ),
            }
            for row in items
        ],
    })
