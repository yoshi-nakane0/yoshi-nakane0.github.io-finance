"""トピック別の日次集計値の操作と期間比較ロジック。"""

from datetime import date, timedelta
from typing import Optional

from django.db.models import Avg, Sum

from ..models import SentimentObservation, SentimentTopic


def build_topic_summary(topic: SentimentTopic, days: int = 7) -> dict:
    """トピックの直近 days 日分の集計を返す。前期間（同日数）との比較も含む。"""
    today = date.today()
    current_start = today - timedelta(days=days - 1)
    previous_start = current_start - timedelta(days=days)
    previous_end = current_start - timedelta(days=1)

    current_qs = SentimentObservation.objects.filter(
        topic=topic,
        observation_date__gte=current_start,
        observation_date__lte=today,
    )
    previous_qs = SentimentObservation.objects.filter(
        topic=topic,
        observation_date__gte=previous_start,
        observation_date__lte=previous_end,
    )
    current_agg = current_qs.aggregate(
        total=Sum('articles_count'),
        tone=Avg('tone_avg'),
    )
    previous_agg = previous_qs.aggregate(
        total=Sum('articles_count'),
        tone=Avg('tone_avg'),
    )

    current_tone = current_agg['tone']
    previous_tone = previous_agg['tone']
    tone_change = None
    if current_tone is not None and previous_tone is not None:
        tone_change = current_tone - previous_tone

    daily = list(
        current_qs.order_by('observation_date').values(
            'observation_date', 'articles_count', 'tone_avg',
        )
    )

    return {
        'topic_slug': topic.slug,
        'topic_name': topic.name_ja,
        'category': topic.category,
        'days': days,
        'period_start': current_start.isoformat(),
        'period_end': today.isoformat(),
        'articles_count_total': int(current_agg['total'] or 0),
        'tone_avg': round(current_tone, 3) if current_tone is not None else None,
        'previous_tone_avg': (
            round(previous_tone, 3) if previous_tone is not None else None
        ),
        'tone_change': round(tone_change, 3) if tone_change is not None else None,
        'daily': [
            {
                'date': row['observation_date'].isoformat(),
                'articles_count': row['articles_count'],
                'tone_avg': (
                    round(row['tone_avg'], 3)
                    if row['tone_avg'] is not None else None
                ),
            }
            for row in daily
        ],
    }


def build_summary_for_slug(slug: str, days: int = 7) -> Optional[dict]:
    try:
        topic = SentimentTopic.objects.get(slug=slug, is_active=True)
    except SentimentTopic.DoesNotExist:
        return None
    return build_topic_summary(topic, days=days)


def list_active_topics():
    return SentimentTopic.objects.filter(is_active=True).order_by(
        'display_order', 'slug',
    )
