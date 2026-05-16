"""指標詳細ページ・類似局面詳細ページ用のコンテキスト構築。"""

import csv
import logging
import math
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.utils import timezone

from ..models import Indicator, Observation, PriceObservation
from .dashboard import format_pct, format_signed, format_value
from .detail_analysis import (
    correlation_label,
    correlation_with_sp500,
    get_values_at_crash_months,
    interpret_state,
)
from .linkage import LAG_CANDIDATES, _pearson, _shifted_series, _aggregate_to_monthly
from .sparkline import generate_sparkline_svg
from .yfinance_client import get_monthly_close

logger = logging.getLogger(__name__)

# 詳細ページの表示期間オプション。'all' は全期間（=絞り込み無し）。
RANGE_TO_YEARS = {
    '1y': 1,
    '3y': 3,
    '5y': 5,
    '10y': 10,
    'all': None,
}
RANGE_OPTIONS = [
    {'key': '1y', 'label': '1年'},
    {'key': '3y', 'label': '3年'},
    {'key': '5y', 'label': '5年'},
    {'key': '10y', 'label': '10年'},
    {'key': 'all', 'label': '全期間'},
]
DEFAULT_RANGE_PARAM = '10y'


def normalize_range_param(value: Optional[str]) -> str:
    if value in RANGE_TO_YEARS:
        return value
    return DEFAULT_RANGE_PARAM


def _resolve_cutoff(range_param: str) -> Optional[date]:
    years = RANGE_TO_YEARS.get(range_param)
    if years is None:
        return None
    today = timezone.localdate()
    try:
        return today.replace(year=today.year - years)
    except ValueError:
        return today.replace(year=today.year - years, day=28)


def _filtered_observations(
    indicator: Indicator, range_param: str,
) -> List[Observation]:
    qs = Observation.objects.filter(indicator=indicator).order_by('observation_date')
    cutoff = _resolve_cutoff(range_param)
    if cutoff is not None:
        qs = qs.filter(observation_date__gte=cutoff)
    return list(qs)


def _summary_stats(observations: List[Observation]) -> Dict:
    if not observations:
        return {}
    values = [o.value for o in observations]
    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    std = math.sqrt(var)
    return {
        'count': n,
        'mean': mean,
        'std': std,
        'min_value': min(values),
        'max_value': max(values),
        'first_date': observations[0].observation_date,
        'last_date': observations[-1].observation_date,
    }


def _extreme_months(observations: List[Observation], top: int = 5):
    """最も高かった月と最も低かった月を返す。"""
    valid = [o for o in observations if o.deviation_from_long_term is not None]
    by_dev_desc = sorted(valid, key=lambda o: o.deviation_from_long_term, reverse=True)
    by_dev_asc = sorted(valid, key=lambda o: o.deviation_from_long_term)
    return by_dev_desc[:top], by_dev_asc[:top]


def _top_correlations(target_indicator: Indicator, top: int = 5) -> List[Dict]:
    """target と他の重要度A/B指標との相関上位を返す。"""
    target_obs = list(
        Observation.objects
        .filter(indicator=target_indicator, deviation_from_long_term__isnull=False)
        .order_by('observation_date')
        .values_list('observation_date', 'deviation_from_long_term')
    )
    if len(target_obs) < 24:
        return []
    target_monthly = _aggregate_to_monthly(target_obs)

    others = list(
        Indicator.objects
        .filter(is_active=True, importance__in=['A', 'B'])
        .exclude(pk=target_indicator.pk)
    )
    results = []
    for other in others:
        other_obs = list(
            Observation.objects
            .filter(indicator=other, deviation_from_long_term__isnull=False)
            .order_by('observation_date')
            .values_list('observation_date', 'deviation_from_long_term')
        )
        if len(other_obs) < 24:
            continue
        other_monthly = _aggregate_to_monthly(other_obs)

        best_corr = None
        best_lag = 0
        for lag in LAG_CANDIDATES:
            shifted = _shifted_series(target_monthly, other_monthly, lag)
            if len(shifted) < 24:
                continue
            xs = [p[1] for p in shifted]
            ys = [p[2] for p in shifted]
            corr = _pearson(xs, ys)
            if corr is None:
                continue
            if best_corr is None or abs(corr) > abs(best_corr):
                best_corr = corr
                best_lag = lag

        if best_corr is None:
            continue
        results.append({
            'other_id': other.fred_series_id,
            'other_name': other.name_ja,
            'correlation': best_corr,
            'lag_months': best_lag,
        })

    results.sort(key=lambda x: abs(x['correlation']), reverse=True)
    return results[:top]


def build_indicator_detail_context(
    indicator: Indicator,
    range_param: str = DEFAULT_RANGE_PARAM,
) -> Dict:
    range_param = normalize_range_param(range_param)
    observations = _filtered_observations(indicator, range_param)
    if not observations:
        return {
            'indicator': indicator,
            'has_data': False,
        }

    # 月次集約値で大きめのスパークラインを生成
    monthly_map: Dict[date, float] = {}
    for o in observations:
        monthly_map[o.observation_date.replace(day=1)] = o.value
    sorted_months = sorted(monthly_map.keys())
    monthly_values = [monthly_map[m] for m in sorted_months]
    chart_svg = generate_sparkline_svg(
        monthly_values, width=320, height=120, stroke_width=2.0
    )

    stats = _summary_stats(observations)
    high_months, low_months = _extreme_months(observations, top=5)

    high_rows = [{
        'date': o.observation_date.isoformat(),
        'value_display': format_value(o.value, indicator.unit),
        'deviation_display': format_signed(o.deviation_from_long_term, 2),
    } for o in high_months]
    low_rows = [{
        'date': o.observation_date.isoformat(),
        'value_display': format_value(o.value, indicator.unit),
        'deviation_display': format_signed(o.deviation_from_long_term, 2),
    } for o in low_months]

    correlations = _top_correlations(indicator, top=5)
    corr_rows = [{
        'other_id': c['other_id'],
        'other_name': c['other_name'],
        'correlation_display': f"{c['correlation']:+.2f}",
        'is_negative': c['correlation'] < 0,
        'lag_label': '同時' if c['lag_months'] == 0 else (
            f"{abs(c['lag_months'])}ヶ月先行" if c['lag_months'] > 0 else f"{abs(c['lag_months'])}ヶ月遅行"
        ),
    } for c in correlations]

    latest = observations[-1]
    state = interpret_state(indicator, latest)

    crash_value_rows = []
    for row in get_values_at_crash_months(indicator):
        crash_value_rows.append({
            'month_label': row['month_label'],
            'crash_label': row['crash_label'],
            'value_display': format_value(row['value'], indicator.unit),
            'yoy_display': format_pct(row['yoy_change']),
        })

    sp500_corr = correlation_with_sp500(indicator)
    sp500_corr_block = {
        'value': sp500_corr,
        'value_display': f'{sp500_corr:+.2f}' if sp500_corr is not None else '—',
        'label': correlation_label(sp500_corr),
        'is_positive': sp500_corr is not None and sp500_corr >= 0,
    }

    return {
        'indicator': indicator,
        'has_data': True,
        'chart_svg': chart_svg,
        'latest_date': latest.observation_date,
        'latest_value_display': format_value(latest.value, indicator.unit),
        'yoy_display': format_pct(latest.yoy_change),
        'deviation_display': format_signed(latest.deviation_from_long_term, 2),
        'stats': {
            'count': stats['count'],
            'mean_display': format_value(stats['mean'], indicator.unit),
            'std_display': format_value(stats['std'], indicator.unit),
            'min_display': format_value(stats['min_value'], indicator.unit),
            'max_display': format_value(stats['max_value'], indicator.unit),
            'first_date': stats['first_date'],
            'last_date': stats['last_date'],
        },
        'high_months': high_rows,
        'low_months': low_rows,
        'correlations': corr_rows,
        'state_interpretation': state,
        'crash_values': crash_value_rows,
        'sp500_correlation': sp500_corr_block,
    }


def _events_at_month(year: int, month: int) -> List[Dict]:
    """イベントCSVから指定年月の重要(★★/★★★)イベントを抽出。"""
    csv_path = Path(settings.BASE_DIR) / 'static' / 'events' / 'data.csv'
    if not csv_path.exists():
        return []
    items = []
    try:
        with csv_path.open('r', encoding='utf-8', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                date_str = (row.get('date') or '').strip()
                if not date_str:
                    continue
                try:
                    d = datetime.strptime(date_str, '%Y-%m-%d').date()
                except ValueError:
                    continue
                if d.year != year or d.month != month:
                    continue
                impact = (row.get('impact') or '').strip()
                if '★' not in impact:
                    continue
                items.append({
                    'date': d.isoformat(),
                    'time': (row.get('time') or '').strip(),
                    'currency': (row.get('currency') or '').strip(),
                    'event': (row.get('event') or '').strip(),
                    'impact': impact,
                })
    except Exception:
        logger.exception("Failed to read events CSV")
        return []
    items.sort(key=lambda x: (x['date'], x['time']))
    return items


def build_similar_detail_context(month_start: date) -> Dict:
    """指定月の詳細を作る。"""
    month_end = (month_start.replace(day=1) + relativedelta(months=1)) - relativedelta(days=1)

    indicators = list(
        Indicator.objects.filter(is_active=True).order_by('display_order')
    )
    rows = []
    for ind in indicators:
        obs = (
            Observation.objects
            .filter(indicator=ind, observation_date__lte=month_end)
            .order_by('-observation_date')
            .first()
        )
        if obs is None:
            continue
        rows.append({
            'series_id': ind.fred_series_id,
            'name_ja': ind.name_ja,
            'category': ind.get_category_display(),
            'importance': ind.importance,
            'date': obs.observation_date.isoformat(),
            'value_display': format_value(obs.value, ind.unit),
            'unit': ind.unit,
            'yoy_display': format_pct(obs.yoy_change),
            'deviation_display': format_signed(obs.deviation_from_long_term, 2),
        })

    # 月次価格と、+1m/+3m/+6m リターン
    nikkei_returns = _multi_month_returns(PriceObservation.Ticker.NIKKEI, month_start)
    spx_returns = _multi_month_returns(PriceObservation.Ticker.SP500, month_start)
    nydow_returns = _multi_month_returns(PriceObservation.Ticker.NYDOW, month_start)
    nasdaq_returns = _multi_month_returns(PriceObservation.Ticker.NASDAQ, month_start)

    events = _events_at_month(month_start.year, month_start.month)

    return {
        'month_start': month_start,
        'month_label': month_start.strftime('%Y年%m月'),
        'indicator_rows': rows,
        'nikkei_returns': nikkei_returns,
        'spx_returns': spx_returns,
        'nydow_returns': nydow_returns,
        'nasdaq_returns': nasdaq_returns,
        'events': events,
    }


def _multi_month_returns(ticker: str, month_start: date) -> Dict[str, str]:
    base_close = get_monthly_close(ticker, month_start)
    if base_close is None or base_close == 0:
        return {'r1m': '—', 'r3m': '—', 'r6m': '—'}
    out = {}
    for label, months in [('r1m', 1), ('r3m', 3), ('r6m', 6)]:
        target = month_start + relativedelta(months=months)
        target_close = get_monthly_close(ticker, target)
        if target_close is None:
            out[label] = '—'
            continue
        r = (target_close - base_close) / base_close * 100.0
        out[label] = format_pct(r)
    return out
