import logging
from collections import defaultdict
from datetime import date

from django.core.cache import cache
from django.db.models import Prefetch, Q
from django.shortcuts import render
from django.views.decorators.cache import cache_control
from django.views.decorators.gzip import gzip_page
from django.views.decorators.http import require_GET

from earning.services.expectation import compute_expectation_score, expectation_level
from earning.services.risk import compute_risk_score as compute_event_risk_score
from earning.services.theme_strength import fallback_theme_score, normalize_theme

logger = logging.getLogger(__name__)

FUNDAMENTAL_ICONS = {
    'up': 'bi-arrow-up-right',
    'flat': 'bi-dash',
    'down': 'bi-arrow-down-right',
}

DIRECTION_ICONS = FUNDAMENTAL_ICONS

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


def to_5_scale(value):
    if value is None:
        return None
    try:
        v = float(value)
    except (ValueError, TypeError):
        return None
    if v < 20:
        return 1
    if v < 40:
        return 2
    if v < 60:
        return 3
    if v < 80:
        return 4
    return 5


def _linear_to_100(value, low, high):
    if value is None:
        return None
    try:
        v = float(value)
    except (ValueError, TypeError):
        return None
    if high == low:
        return 50.0
    score = (v - low) / (high - low) * 100.0
    return max(0.0, min(100.0, score))


def compute_risk_score(event):
    if event is None:
        return None
    market_parts = []
    vix_risk = _linear_to_100(event.vix_at_event, 10.0, 30.0)
    if vix_risk is not None:
        market_parts.append(vix_risk)
    hy_risk = _linear_to_100(event.hy_spread_at_event, 2.5, 6.0)
    if hy_risk is not None:
        market_parts.append(hy_risk)
    skew_risk = _linear_to_100(event.skew_at_event, 120.0, 150.0)
    if skew_risk is not None:
        market_parts.append(skew_risk)
    market_risk = sum(market_parts) / len(market_parts) if market_parts else None

    valid_reactions = [r for r in (event.past_reactions or []) if r is not None]
    individual_risk = None
    if valid_reactions:
        abs_avg = sum(abs(r) for r in valid_reactions) / len(valid_reactions)
        individual_risk = _linear_to_100(abs_avg, 0.0, 10.0)

    if market_risk is None and individual_risk is None:
        return None
    if market_risk is None:
        return individual_risk
    if individual_risk is None:
        return market_risk
    return market_risk * 0.6 + individual_risk * 0.4


def build_theme_strength_pool():
    from earning.models import EarningsEvent
    theme_buckets = defaultdict(list)
    industry_buckets = defaultdict(list)
    queryset = (
        EarningsEvent.objects
        .select_related('stock')
        .filter(reaction_close__isnull=False)
        .only('reaction_close', 'stock__theme', 'stock__industry')
    )
    for ev in queryset:
        try:
            value = float(ev.reaction_close)
        except (ValueError, TypeError):
            continue
        theme = (ev.stock.theme or '').strip()
        industry = (ev.stock.industry or '').strip()
        if theme:
            theme_buckets[theme].append(value)
        if industry:
            industry_buckets[industry].append(value)
    pool = {'theme': {}, 'industry': {}}
    for theme, values in theme_buckets.items():
        if values:
            normalized = normalize_theme(theme)
            pool['theme'][normalized] = sum(values) / len(values)
    for industry, values in industry_buckets.items():
        if values:
            pool['industry'][industry] = sum(values) / len(values)
    return pool


def compute_theme_strength(theme, industry, theme_pool, explicit_score=None):
    explicit_score = parse_float(explicit_score)
    fallback_score = fallback_theme_score(theme)
    if explicit_score is not None:
        if fallback_score is not None:
            explicit_score = max(explicit_score, fallback_score)
        return max(0.0, min(100.0, explicit_score))
    if fallback_score is not None:
        return fallback_score
    if not theme_pool:
        return None
    avg = None
    if theme:
        avg = theme_pool.get('theme', {}).get(normalize_theme(theme))
    if avg is None and industry:
        avg = theme_pool.get('industry', {}).get(industry.strip())
    if avg is None:
        return None
    return _linear_to_100(avg, -5.0, 5.0)


def normalize_choice(value, choices, default):
    if not value:
        return default
    value = str(value).strip().lower()
    return value if value in choices else default


def risk_class(value):
    if value is None:
        return 'metric-muted'
    if value <= 45:
        return 'metric-positive'
    if value >= 75:
        return 'metric-negative'
    return 'metric-neutral'


def risk_score_label(value):
    if value is None:
        return '—'
    if value <= 45:
        return '低リスク'
    if value >= 75:
        return '高リスク'
    return '中リスク'


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


def _deviation_class(value):
    if value is None:
        return 'deviation-muted'
    sign = 'pos' if value >= 0 else 'neg'
    abs_val = abs(value)
    if abs_val <= 2:
        magnitude = 'mild'
    elif abs_val <= 5:
        magnitude = 'moderate'
    else:
        magnitude = 'large'
    return f'deviation-{magnitude}-{sign}'


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


def expectation_score_class(value):
    return expectation_level(value)['class']


def expectation_score_label(value):
    return expectation_level(value)['label']


def fetch_earnings_from_db(today=None, period='all'):
    from earning.models import EarningsEvent, EarningsPriceWindow

    queryset = EarningsEvent.objects.select_related('stock').prefetch_related('predictions').all()
    if period == 'upcoming' and today is not None:
        queryset = queryset.filter(Q(event_date__gte=today) | Q(event_date__isnull=True))
        queryset = queryset.prefetch_related(
            Prefetch(
                'price_window',
                queryset=EarningsPriceWindow.objects
                .filter(offset_days__gte=-21, offset_days__lte=-1)
                .only('event_id', 'offset_days', 'close'),
                to_attr='_feature_price_window',
            )
        )
    elif period == 'completed' and today is not None:
        queryset = queryset.filter(event_date__lt=today)
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
            'expectation_score_value': ev.expectation_score,
            'direction': ev.direction,
            'sales_forecast': ev.sales_forecast,
            'surp_current': ev.surp_current,
            'eps_forecast': ev.eps_forecast,
            'surp_eps_current': ev.surp_eps_current,
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


def enrich_item(item, pool=None, event_obj=None, theme_pool=None):
    risk_value = item.get('risk_value')

    fundamental = item.get('fundamental')
    direction = item.get('direction')
    summary_text = item.get('summary') or '要約未取得'

    explicit_expectation_score = item.get('expectation_score_value')
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

    risk_100 = compute_event_risk_score(event_obj)
    if risk_100 is None:
        risk_100 = risk_value
    theme_100 = compute_theme_strength(
        item.get('theme'),
        item.get('industry'),
        theme_pool,
        explicit_score=theme_score_value,
    )
    expectation_100 = explicit_expectation_score
    if expectation_100 is None:
        expectation_100 = compute_expectation_score(
            theme_score=theme_100,
            risk_score=risk_100,
            eps_surprise=item.get('surp_eps_current'),
            sales_surprise=item.get('surp_current'),
            guidance_revision=guidance_revision,
            past_reactions=valid_past,
        )

    search_parts = [
        str(item.get('symbol') or ''),
        str(item.get('company') or ''),
        str(item.get('industry') or ''),
        str(item.get('theme') or ''),
    ]
    item['search_text'] = ' '.join(p for p in search_parts if p).lower()

    risk_5 = to_5_scale(risk_100)
    theme_5 = to_5_scale(theme_100)
    expectation_meta = expectation_level(expectation_100)

    item.update({
        'risk_display': '—' if risk_100 is None else f'{risk_100:.0f}%',
        'risk_score_display': '—' if risk_5 is None else str(risk_5),
        'risk_score_label': risk_score_label(risk_100),
        'risk_class': risk_class(risk_100),
        'risk_gauge_value': risk_5 * 20 if risk_5 is not None else 0,
        'fundamental_class': STATUS_CLASS_MAP.get(fundamental, 'status-flat'),
        'fundamental_icon': FUNDAMENTAL_ICONS.get(fundamental, 'bi-dash'),
        'direction_class': STATUS_CLASS_MAP.get(direction, 'status-flat'),
        'direction_icon': DIRECTION_ICONS.get(direction, 'bi-dash'),
        'expectation_score_display': '—' if expectation_100 is None else f'{expectation_100:.0f}',
        'expectation_score_value': 0 if expectation_100 is None else expectation_100,
        'expectation_label': expectation_meta['label'],
        'expectation_class': expectation_meta['class'],
        'expectation_scale_display': '—' if expectation_meta['scale'] is None else str(expectation_meta['scale']),
        'summary': summary_text,
        'fiscal_period': item.get('fiscal_period') or '—',
        'theme_label': item.get('theme') or '—',
        'theme_score_display': '—' if theme_5 is None else str(theme_5),
        'theme_score_class': theme_score_class(theme_100),
        'theme_score_label': theme_score_label(theme_100),
        'theme_short_lock': theme_100 is not None and theme_100 >= 80,
        'theme_gauge_value': theme_5 * 20 if theme_5 is not None else 0,
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
    predicted = item.get('predicted_reaction_raw')
    actual_close = item.get('reaction_close_value')
    deviation = None
    if predicted is not None and actual_close is not None:
        deviation = actual_close - predicted

    similar = []
    if event_obj is not None and pool is not None:
        from earning.services.similarity import find_similar_events
        similar = find_similar_events(event_obj, pool, top_n=3)

    item.update({
        'predicted_reaction_value': predicted,
        'predicted_reaction_display': format_percent(predicted) if predicted is not None else '—',
        'predicted_reaction_class': reaction_class(predicted),
        'has_prediction': predicted is not None,
        'reaction_deviation_value': deviation,
        'reaction_deviation_display': format_percent(deviation) if deviation is not None else '—',
        'reaction_deviation_class': _deviation_class(deviation),
        'has_deviation': deviation is not None,
        'similar_events': similar,
        'has_similar_events': bool(similar),
    })

    import json as _json
    from earning.services.features import FEATURE_COLUMNS, build_feature_row
    from earning.services.scenarios import MACRO_KEYS, compute_feature_ranges

    baseline_features = None
    feature_ranges = None
    has_whatif = False
    if predicted is not None and event_obj is not None:
        row = build_feature_row(event_obj)
        if row is not None:
            baseline_features = {c: row[c] for c in FEATURE_COLUMNS}
            feature_ranges = compute_feature_ranges(baseline_features)
            has_whatif = all(baseline_features.get(k) is not None for k in MACRO_KEYS)

    item.update({
        'baseline_features_json': _json.dumps(baseline_features) if baseline_features else None,
        'feature_ranges_json': _json.dumps(feature_ranges) if feature_ranges else None,
        'baseline_prediction_value': predicted,
        'has_whatif': has_whatif,
    })
    item.pop('_event_obj', None)


def compute_theme_aggregations(items, theme_pool=None):
    """
    1日分の決算企業をテーマ単位で集計する。テーマ強度はテーマプールから取得。
    """
    buckets = defaultdict(int)
    for item in items:
        theme = (item.get('theme') or '').strip()
        if not theme:
            continue
        buckets[theme] += 1

    aggregations = []
    for theme, count in buckets.items():
        explicit_scores = [
            item.get('theme_score_value')
            for item in items
            if (item.get('theme') or '').strip() == theme and item.get('theme_score_value') is not None
        ]
        explicit_avg = sum(explicit_scores) / len(explicit_scores) if explicit_scores else None
        avg = compute_theme_strength(theme, None, theme_pool, explicit_score=explicit_avg)
        if avg is None:
            continue
        aggregations.append({
            'theme': theme,
            'count': count,
            'avg_score': avg,
            'avg_display': f'{avg:.0f}',
            'label': theme_score_label(avg),
            'class': theme_score_class(avg),
        })
    aggregations.sort(key=lambda x: (-x['avg_score'], x['theme']))
    return aggregations


def group_by_date(items, is_past=False, theme_pool=None):
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
                'theme_aggregations': compute_theme_aggregations(current_companies, theme_pool=theme_pool),
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
                'theme_aggregations': compute_theme_aggregations([item], theme_pool=theme_pool),
            })
    return grouped


def build_grouped_payload(today, period='all'):
    from earning.models import EarningsEvent, EarningsPriceWindow
    from earning.services.similarity import build_similarity_pool

    include_upcoming = period in {'all', 'upcoming'}
    include_completed = period in {'all', 'completed'}
    earnings_data = fetch_earnings_from_db(today=today, period=period)

    future_earnings = []
    completed_earnings = []
    for item in earnings_data:
        item_date = item.get('date_obj')
        if item_date is None:
            if include_upcoming:
                future_earnings.append(item)
        elif item_date >= today:
            if include_upcoming:
                future_earnings.append(item)
        else:
            if include_completed:
                completed_earnings.append(item)

    try:
        future_earnings.sort(key=lambda x: (x.get('date_obj') is None, x.get('date_obj') or date.max))
        completed_earnings.sort(key=lambda x: x.get('date_obj') or date.min, reverse=True)
    except Exception as e:
        logger.warning("Error sorting data: %s", e)

    if include_completed and completed_earnings:
        seen_symbols = set()
        latest_completed = []
        for item in completed_earnings:
            sym = item.get('symbol')
            if sym in seen_symbols:
                continue
            seen_symbols.add(sym)
            latest_completed.append(item)
        completed_earnings = latest_completed

    pool = None
    needs_similarity = any(item.get('predicted_reaction_raw') is not None for item in future_earnings)
    if needs_similarity:
        pool_events = list(
            EarningsEvent.objects
            .filter(reaction_close__isnull=False)
            .select_related('stock')
            .prefetch_related(
                Prefetch(
                    'price_window',
                    queryset=EarningsPriceWindow.objects
                    .filter(offset_days__gte=-21, offset_days__lte=-1)
                    .only('event_id', 'offset_days', 'close'),
                    to_attr='_feature_price_window',
                )
            )
        )
        pool = build_similarity_pool(pool_events)

    theme_pool = build_theme_strength_pool()

    for item in future_earnings:
        enrich_item(item, pool=pool, event_obj=item.get('_event_obj'), theme_pool=theme_pool)
    for item in completed_earnings:
        enrich_item(item, pool=None, event_obj=item.get('_event_obj'), theme_pool=theme_pool)

    return {
        'upcoming': group_by_date(future_earnings, is_past=False, theme_pool=theme_pool),
        'completed': group_by_date(completed_earnings, is_past=True, theme_pool=theme_pool),
    }


def build_theme_index():
    """
    全銘柄をテーマ別にまとめた一覧を返す。テーマパネルでの表示用。
    """
    from earning.models import Stock
    buckets = defaultdict(list)
    for s in Stock.objects.all().order_by('company'):
        theme = (s.theme or '').strip() or '（未設定）'
        buckets[theme].append({
            'symbol': s.symbol,
            'company': s.company,
            'market': s.market,
        })
    result = []
    for theme in sorted(buckets.keys()):
        stocks = buckets[theme]
        result.append({
            'name': theme,
            'count': len(stocks),
            'stocks': stocks,
        })
    return result


def completed_date_group_count(today):
    from earning.models import EarningsEvent
    from django.db.models import Max
    latest_dates = (
        EarningsEvent.objects
        .filter(event_date__lt=today)
        .values('stock_id')
        .annotate(max_date=Max('event_date'))
        .values_list('max_date', flat=True)
    )
    return len(set(latest_dates))


def build_cache_key(today, period='all'):
    """
    Cache key invalidated by date and the latest EarningsEvent.updated_at,
    so any DB write (importer or scraper) busts the cache.
    """
    from earning.models import EarningsEvent
    last = EarningsEvent.objects.order_by('-updated_at').values_list('updated_at', flat=True).first()
    stamp = int(last.timestamp() * 1000) if last else 0
    return f'earnings_data_grouped_v11:{period}:{today.isoformat()}:{stamp}'


def load_grouped_earnings(today=None, period='all'):
    target_date = today or date.today()
    cache_key = build_cache_key(target_date, period)
    cached_payload = cache.get(cache_key)
    if cached_payload is None:
        cached_payload = build_grouped_payload(target_date, period=period)
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
    grouped_payload = load_grouped_earnings(today, period='upcoming')
    grouped_earnings = grouped_payload.get('upcoming', [])
    past_group_count = completed_date_group_count(today)

    theme_index = build_theme_index()
    total_stocks = sum(t['count'] for t in theme_index)

    context = {
        'earnings_data': grouped_earnings,
        'has_past_earnings': past_group_count > 0,
        'past_group_count': past_group_count,
        'updated_date': today.strftime('%Y.%m.%d'),
        'theme_index': theme_index,
        'theme_total_count': len(theme_index),
        'stock_total_count': total_stocks,
    }

    return render(request, 'earning/index.html', context)


@require_GET
@cache_control(public=True, max_age=0, s_maxage=300, stale_while_revalidate=86400)
@gzip_page
def completed(request):
    grouped_payload = load_grouped_earnings(period='completed')
    context = {
        'earnings_data': grouped_payload.get('completed', []),
    }
    return render(request, 'earning/_date_groups.html', context)
