import logging
from collections import defaultdict
from datetime import date

from django.core.cache import cache
from django.shortcuts import render
from django.views.decorators.cache import cache_control
from django.views.decorators.gzip import gzip_page
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)

FUNDAMENTAL_ICONS = {
    'up': 'bi-arrow-up-right',
    'flat': 'bi-dash',
    'down': 'bi-arrow-down-right',
}

DIRECTION_ICONS = FUNDAMENTAL_ICONS

SENTIMENT_ICONS = {
    'up': 'bi-arrow-up',
    'flat': 'bi-dash',
    'down': 'bi-arrow-down',
}

STATUS_CLASS_MAP = {
    'up': 'status-up',
    'flat': 'status-flat',
    'down': 'status-down',
}

GUIDANCE_LABELS = {
    'up': '上方修正',
    'flat': '維持',
    'down': '下方修正',
}

INTERPRETATION_LABELS = {
    'bullish': '強気',
    'neutral': '中立',
    'bearish': '弱気',
}

INTERPRETATION_CLASS_MAP = {
    'bullish': 'interpretation-bullish',
    'neutral': 'interpretation-neutral',
    'bearish': 'interpretation-bearish',
}

WATCH_TIER_CLASS_MAP = {
    '最重要': 'tier-top',
    '重要': 'tier-important',
    '補助': 'tier-auxiliary',
}

CACHE_TTL = 86400


def parse_float(value):
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (ValueError, TypeError):
        return None


def normalize_choice(value, choices, default):
    if not value:
        return default
    value = str(value).strip().lower()
    return value if value in choices else default


def risk_class(value):
    if value is None:
        return 'metric-muted'
    if value >= 75:
        return 'metric-positive'
    if value <= 45:
        return 'metric-negative'
    return 'metric-neutral'


def theme_score_class(value):
    if value is None:
        return 'theme-muted'
    if value >= 80:
        return 'theme-strong'
    if value >= 60:
        return 'theme-somewhat-strong'
    if value >= 40:
        return 'theme-neutral'
    if value >= 20:
        return 'theme-weak'
    return 'theme-very-weak'


def theme_score_label(value):
    if value is None:
        return '—'
    if value >= 80:
        return '強い'
    if value >= 60:
        return 'やや強い'
    if value >= 40:
        return '中立'
    if value >= 20:
        return '弱い'
    return 'かなり弱い'


def reaction_class(value):
    if value is None:
        return 'reaction-muted'
    if value > 1.0:
        return 'reaction-positive'
    if value < -1.0:
        return 'reaction-negative'
    return 'reaction-neutral'


def relative_strength_class(value):
    if value is None:
        return 'rs-muted'
    if value >= 70:
        return 'rs-strong'
    if value <= 40:
        return 'rs-weak'
    return 'rs-neutral'


def gross_margin_class(value):
    if value is None:
        return 'metric-muted'
    if value >= 50:
        return 'metric-good'
    if value >= 30:
        return 'metric-mid'
    return 'metric-bad'


def operating_margin_class(value):
    if value is None:
        return 'metric-muted'
    if value >= 20:
        return 'metric-good'
    if value >= 10:
        return 'metric-mid'
    return 'metric-bad'


def format_percent(value, with_sign=True):
    if value is None:
        return '—'
    sign = '+' if with_sign and value > 0 else ''
    return f'{sign}{value:.1f}%'


def fetch_earnings_from_db():
    from earning.models import EarningsEvent

    queryset = EarningsEvent.objects.select_related('stock').prefetch_related('predictions').all()
    items = []
    for ev in queryset:
        stock = ev.stock
        date_obj = ev.event_date
        date_display = date_obj.isoformat() if date_obj else '決算日未定'
        prediction = next((p for p in ev.predictions.all() if p.model_version == 'baseline-v1'), None)
        items.append({
            'date': date_display,
            'date_obj': date_obj,
            'company': stock.company,
            'industry': stock.industry,
            'market': stock.market,
            'symbol': stock.symbol,
            'fundamental': ev.fundamental,
            'risk_value': ev.risk_value,
            'direction': ev.direction,
            'sentiment': ev.sentiment,
            'sales_current': ev.sales_current,
            'sales_forecast': ev.sales_forecast,
            'sales_4q_ago': ev.sales_4q_ago,
            'sales_4q_prior_period': ev.sales_4q_prior_period,
            'surp_forecast': '-',
            'surp_4q_ago': ev.surp_4q_ago,
            'surp_current': ev.surp_current,
            'surp_4q_prior_period': ev.surp_4q_prior_period,
            'eps_current': ev.eps_current,
            'eps_forecast': ev.eps_forecast,
            'eps_4q_ago': ev.eps_4q_ago,
            'eps_4q_prior_period': ev.eps_4q_prior_period,
            'surp_eps_forecast': '-',
            'surp_eps_4q_ago': ev.surp_eps_4q_ago,
            'surp_eps_current': ev.surp_eps_current,
            'surp_eps_4q_prior_period': ev.surp_eps_4q_prior_period,
            'fiscal_period': ev.fiscal_period,
            'summary': ev.summary,
            'theme': stock.theme,
            'theme_score_value': ev.theme_score,
            'watch_tier': stock.watch_tier,
            'watch_role': stock.watch_role,
            'nikkei_weight_value': stock.nikkei_weight,
            'gross_margin_value': ev.gross_margin,
            'operating_margin_value': ev.operating_margin,
            'guidance_revision': ev.guidance_revision,
            'relative_strength_value': ev.relative_strength,
            'reaction_close_value': ev.reaction_close,
            'reaction_next_day_value': ev.reaction_next_day,
            'market_interpretation': ev.market_interpretation,
            'past_reactions_raw': list(ev.past_reactions or []),
            'predicted_reaction_raw': prediction.predicted_reaction if prediction else None,
            '_event_obj': ev,
        })
    return items


def enrich_item(item):
    risk_value = item.get('risk_value')

    fundamental = item.get('fundamental')
    direction = item.get('direction')
    sentiment = item.get('sentiment')

    summary_text = item.get('summary') or '要約未取得'

    theme_score_value = item.get('theme_score_value')
    gross_margin_value = item.get('gross_margin_value')
    operating_margin_value = item.get('operating_margin_value')
    relative_strength_value = item.get('relative_strength_value')
    guidance_revision = item.get('guidance_revision') or ''
    reaction_close_value = item.get('reaction_close_value')
    reaction_next_day_value = item.get('reaction_next_day_value')
    market_interpretation = item.get('market_interpretation') or ''

    past_reactions_raw = item.get('past_reactions_raw') or []
    past_reactions = []
    valid_past = []
    for value in past_reactions_raw:
        if value is None:
            past_reactions.append({
                'value': None,
                'display': '—',
                'class': 'reaction-muted',
            })
        else:
            past_reactions.append({
                'value': value,
                'display': format_percent(value),
                'class': reaction_class(value),
            })
            valid_past.append(value)

    past_avg = sum(valid_past) / len(valid_past) if valid_past else None
    has_past_reactions = bool(valid_past)

    item.update({
        'risk_display': '—' if risk_value is None else f'{risk_value:.0f}%',
        'risk_class': risk_class(risk_value),
        'fundamental_class': STATUS_CLASS_MAP.get(fundamental, 'status-flat'),
        'fundamental_icon': FUNDAMENTAL_ICONS.get(fundamental, 'bi-dash'),
        'direction_class': STATUS_CLASS_MAP.get(direction, 'status-flat'),
        'direction_icon': DIRECTION_ICONS.get(direction, 'bi-dash'),
        'sentiment_class': STATUS_CLASS_MAP.get(sentiment, 'status-flat'),
        'sentiment_icon': SENTIMENT_ICONS.get(sentiment, 'bi-dash'),
        'summary': summary_text,
        'fiscal_period': item.get('fiscal_period') or '—',
        'theme_label': item.get('theme') or '—',
        'theme_score_display': '—' if theme_score_value is None else f'{theme_score_value:.0f}',
        'theme_score_class': theme_score_class(theme_score_value),
        'theme_score_label': theme_score_label(theme_score_value),
        'theme_short_lock': theme_score_value is not None and theme_score_value >= 80,
        'watch_tier': item.get('watch_tier') or '',
        'watch_tier_class': WATCH_TIER_CLASS_MAP.get(item.get('watch_tier') or '', ''),
        'watch_role': item.get('watch_role') or '',
        'has_watch_info': bool(item.get('watch_tier') or item.get('watch_role')),
        'gross_margin_display': '—' if gross_margin_value is None else f'{gross_margin_value:.0f}%',
        'gross_margin_class': gross_margin_class(gross_margin_value),
        'operating_margin_display': '—' if operating_margin_value is None else f'{operating_margin_value:.0f}%',
        'operating_margin_class': operating_margin_class(operating_margin_value),
        'guidance_revision': guidance_revision,
        'guidance_revision_label': GUIDANCE_LABELS.get(guidance_revision, '—'),
        'guidance_revision_class': STATUS_CLASS_MAP.get(guidance_revision, 'status-flat'),
        'guidance_revision_icon': FUNDAMENTAL_ICONS.get(guidance_revision, 'bi-dash'),
        'relative_strength_display': '—' if relative_strength_value is None else f'{relative_strength_value:.0f}',
        'relative_strength_class': relative_strength_class(relative_strength_value),
        'has_fundamentals': any(
            v is not None for v in [gross_margin_value, operating_margin_value, relative_strength_value]
        ) or bool(guidance_revision),
        'reaction_close_display': format_percent(reaction_close_value),
        'reaction_close_class': reaction_class(reaction_close_value),
        'reaction_next_day_display': format_percent(reaction_next_day_value),
        'reaction_next_day_class': reaction_class(reaction_next_day_value),
        'market_interpretation': market_interpretation,
        'market_interpretation_label': INTERPRETATION_LABELS.get(market_interpretation, '—'),
        'market_interpretation_class': INTERPRETATION_CLASS_MAP.get(market_interpretation, 'interpretation-muted'),
        'has_reaction': reaction_close_value is not None or reaction_next_day_value is not None or bool(market_interpretation),
        'past_reactions': past_reactions,
        'past_reactions_avg_display': format_percent(past_avg),
        'past_reactions_avg_class': reaction_class(past_avg),
        'has_past_reactions': has_past_reactions,
    })


def compute_theme_aggregations(items):
    """
    1日分の決算企業をテーマ単位で集計する。
    """
    buckets = defaultdict(list)
    for item in items:
        theme = (item.get('theme') or '').strip()
        score = item.get('theme_score_value')
        if not theme or score is None:
            continue
        buckets[theme].append(score)

    aggregations = []
    for theme, scores in buckets.items():
        avg = sum(scores) / len(scores)
        aggregations.append({
            'theme': theme,
            'count': len(scores),
            'avg_score': avg,
            'avg_display': f'{avg:.0f}',
            'label': theme_score_label(avg),
            'class': theme_score_class(avg),
        })
    aggregations.sort(key=lambda x: (-x['avg_score'], x['theme']))
    return aggregations


def group_by_date(items, is_past=False):
    grouped = []
    if not items:
        return grouped

    try:
        current_date = None
        current_companies = []

        def flush():
            grouped.append({
                'date': current_date,
                'companies': current_companies,
                'is_past': is_past,
                'theme_aggregations': compute_theme_aggregations(current_companies),
            })

        for item in items:
            if current_date != item['date']:
                if current_date is not None:
                    flush()
                current_date = item['date']
                current_companies = []
            current_companies.append(item)

        if current_date is not None:
            flush()
    except Exception as e:
        logger.warning("Error grouping data: %s", e)
        for item in items:
            grouped.append({
                'date': item['date'],
                'companies': [item],
                'is_past': is_past,
                'theme_aggregations': compute_theme_aggregations([item]),
            })
    return grouped


def build_grouped_payload(today):
    earnings_data = fetch_earnings_from_db()

    future_earnings = []
    completed_earnings = []
    for item in earnings_data:
        item_date = item.get('date_obj')
        if item_date is None:
            future_earnings.append(item)
        elif item_date >= today:
            future_earnings.append(item)
        else:
            completed_earnings.append(item)

    try:
        future_earnings.sort(key=lambda x: (x.get('date_obj') is None, x.get('date_obj') or date.max))
        completed_earnings.sort(key=lambda x: x.get('date_obj') or date.min, reverse=True)
    except Exception as e:
        logger.warning("Error sorting data: %s", e)

    for item in future_earnings:
        enrich_item(item)
    for item in completed_earnings:
        enrich_item(item)

    return {
        'upcoming': group_by_date(future_earnings, is_past=False),
        'completed': group_by_date(completed_earnings, is_past=True),
    }


def build_cache_key(today):
    """
    Cache key invalidated by date and the latest EarningsEvent.updated_at,
    so any DB write (importer or scraper) busts the cache.
    """
    from earning.models import EarningsEvent
    last = EarningsEvent.objects.order_by('-updated_at').values_list('updated_at', flat=True).first()
    stamp = int(last.timestamp() * 1000) if last else 0
    return f'earnings_data_grouped_v7:{today.isoformat()}:{stamp}'


def load_grouped_earnings(today=None):
    target_date = today or date.today()
    cache_key = build_cache_key(target_date)
    cached_payload = cache.get(cache_key)
    if cached_payload is None:
        cached_payload = build_grouped_payload(target_date)
        cache.set(cache_key, cached_payload, CACHE_TTL)
    return cached_payload


@require_GET
@cache_control(public=True, max_age=0, s_maxage=300, stale_while_revalidate=86400)
@gzip_page
def index(request):
    """
    決算カレンダーページの表示
    """
    today = date.today()
    grouped_payload = load_grouped_earnings(today)
    grouped_earnings = grouped_payload.get('upcoming', [])
    past_earnings_data = grouped_payload.get('completed', [])

    context = {
        'earnings_data': grouped_earnings,
        'has_past_earnings': bool(past_earnings_data),
        'past_group_count': len(past_earnings_data),
        'updated_date': today.strftime('%Y.%m.%d'),
    }

    return render(request, 'earning/index.html', context)


@require_GET
@cache_control(public=True, max_age=0, s_maxage=300, stale_while_revalidate=86400)
@gzip_page
def completed(request):
    grouped_payload = load_grouped_earnings()
    context = {
        'earnings_data': grouped_payload.get('completed', []),
    }
    return render(request, 'earning/_date_groups.html', context)
