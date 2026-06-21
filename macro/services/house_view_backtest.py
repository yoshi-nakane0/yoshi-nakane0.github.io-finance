"""House View の過去再現 Backtest をローカルで実行する。"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Iterable

from dateutil.relativedelta import relativedelta
from django.utils import timezone

from ..models import Indicator, RegimeSnapshot, VintageObservation
from . import regime


REGIME_ORDER = {
    RegimeSnapshot.Label.CONTRACTION: 0,
    RegimeSnapshot.Label.SLOWDOWN: 1,
    RegimeSnapshot.Label.RECOVERY: 2,
    RegimeSnapshot.Label.EXPANSION: 3,
}

VINTAGE_VALUE_SERIES = {
    'INDPRO': 'indpro_value',
    'UNRATE': 'unrate_value',
    'T5YIE': 'breakeven_5y',
    'BAMLH0A0HYM2': 'hy_spread',
    'T10Y2Y': 'yield_curve_2y10y',
    'T10Y3M': 'yield_curve_3m10y',
    'VIXCLS': 'vix',
}

VINTAGE_YOY_SERIES = {
    'INDPRO': 'indpro_yoy',
    'GDPC1': 'gdp_yoy',
    'PCEPILFE': 'core_pce_yoy',
    'CPIAUCSL': 'cpi_yoy',
    'CPILFESL': 'core_cpi_yoy',
    'PCEPI': 'pce_yoy',
    'RSAFS': 'rsa_sales_yoy',
    'JTSJOL': 'jolts_yoy',
    'CES0500000003': 'wage_yoy',
}


def _month_starts(start: date, end: date):
    current = start.replace(day=1)
    final = end.replace(day=1)
    while current <= final:
        yield current
        current = current + relativedelta(months=1)


def _actual_regime(as_of: date, target_date: date):
    return (
        RegimeSnapshot.objects
        .filter(snapshot_date__gt=as_of, snapshot_date__lte=target_date)
        .order_by('-snapshot_date')
        .first()
    )


def _actual_regime_row(as_of: date, target_date: date) -> dict | None:
    actual = _actual_regime(as_of, target_date)
    if actual is not None and actual.regime_label != RegimeSnapshot.Label.UNKNOWN:
        return {
            'regime_label': actual.regime_label,
            'snapshot_date': actual.snapshot_date,
            'source': 'saved_snapshot',
        }

    assessment, actual_source = _build_assessment(target_date, 'revised_reference')
    if assessment is None:
        return None
    regime_label = assessment.get('regime_label')
    if not regime_label or regime_label == RegimeSnapshot.Label.UNKNOWN:
        return None
    return {
        'regime_label': regime_label,
        'snapshot_date': target_date,
        'source': actual_source,
    }


def _visible_vintage(series_id: str, as_of: date, observation_date: date | None = None):
    indicator = Indicator.objects.filter(fred_series_id=series_id).first()
    if indicator is None:
        return None
    qs = VintageObservation.objects.filter(
        indicator=indicator,
        observation_date__lte=observation_date or as_of,
        realtime_start__lte=as_of,
    ).filter(realtime_end__gte=as_of)
    return qs.order_by('-observation_date', '-realtime_start').first()


def _vintage_abs_change(series_id: str, as_of: date, months: int):
    latest = _visible_vintage(series_id, as_of)
    if latest is None:
        return None
    past_target = latest.observation_date - relativedelta(months=months)
    past = _visible_vintage(series_id, as_of, observation_date=past_target)
    if past is None:
        return None
    return latest.value - past.value


def _vintage_pct_change(series_id: str, as_of: date, months: int):
    latest = _visible_vintage(series_id, as_of)
    if latest is None or latest.value in (None, 0):
        return None
    past_target = latest.observation_date - relativedelta(months=months)
    past = _visible_vintage(series_id, as_of, observation_date=past_target)
    if past is None or past.value in (None, 0):
        return None
    return (latest.value - past.value) / abs(past.value) * 100.0


def _collect_vintage_metrics(as_of: date) -> dict:
    metrics = {}
    for series_id, key in VINTAGE_VALUE_SERIES.items():
        latest = _visible_vintage(series_id, as_of)
        if latest is not None:
            metrics[key] = latest.value

    for series_id, key in VINTAGE_YOY_SERIES.items():
        yoy = _vintage_pct_change(series_id, as_of, 12)
        if yoy is not None:
            metrics[key] = yoy

    indpro_3m = _vintage_pct_change('INDPRO', as_of, 3)
    if indpro_3m is not None:
        metrics['indpro_3m_change_pct'] = indpro_3m
    unrate_6m = _vintage_abs_change('UNRATE', as_of, 6)
    if unrate_6m is not None:
        metrics['unrate_6m_change'] = unrate_6m
    payems_mom = _vintage_abs_change('PAYEMS', as_of, 1)
    if payems_mom is not None:
        metrics['payems_mom'] = payems_mom
    core_pce_3m_ago = _vintage_pct_change('PCEPILFE', as_of - relativedelta(months=3), 12)
    if core_pce_3m_ago is not None:
        metrics['core_pce_yoy_3m_ago'] = core_pce_3m_ago
    tcu_3m = _vintage_abs_change('TCU', as_of, 3)
    if tcu_3m is not None:
        metrics['tcu_3m_change'] = tcu_3m
    umcsent_3m = _vintage_abs_change('UMCSENT', as_of, 3)
    if umcsent_3m is not None:
        metrics['umcsent_3m_change'] = umcsent_3m
    return metrics


def _miss_type(predicted: str, actual: str) -> str:
    if predicted == actual:
        return 'hit'
    predicted_order = REGIME_ORDER.get(predicted)
    actual_order = REGIME_ORDER.get(actual)
    if predicted_order is None or actual_order is None:
        return 'wrong_regime'
    if predicted_order > actual_order:
        return 'too_bullish'
    if predicted_order < actual_order:
        return 'too_defensive'
    return 'wrong_regime'


def _empty_summary() -> dict:
    return {
        'sample_count': 0,
        'hit_count': 0,
        'hit_rate': None,
        'too_bullish_count': 0,
        'too_defensive_count': 0,
    }


def _summary(rows: Iterable[dict]) -> dict:
    rows = list(rows)
    if not rows:
        return _empty_summary()
    hit_count = sum(1 for row in rows if row.get('hit'))
    too_bullish = sum(1 for row in rows if row.get('miss_type') == 'too_bullish')
    too_defensive = sum(1 for row in rows if row.get('miss_type') == 'too_defensive')
    return {
        'sample_count': len(rows),
        'hit_count': hit_count,
        'hit_rate': round(hit_count / len(rows), 4),
        'too_bullish_count': too_bullish,
        'too_defensive_count': too_defensive,
    }


def _group_summaries(rows: list[dict], key: str) -> dict:
    groups = defaultdict(list)
    for row in rows:
        groups[row[key]].append(row)
    return {
        group_key: _summary(group_rows)
        for group_key, group_rows in sorted(groups.items())
    }


def _build_assessment(as_of: date, data_mode: str) -> tuple[dict | None, str]:
    if data_mode == 'auto':
        point_in_time_assessment, _ = _build_assessment(as_of, 'point_in_time')
        if point_in_time_assessment is not None:
            return point_in_time_assessment, 'point_in_time'
        assessment = regime.build_current_regime_assessment(as_of=as_of)
        return assessment, 'revised_reference'
    if data_mode == 'point_in_time':
        metrics = _collect_vintage_metrics(as_of)
        if not metrics:
            return None, data_mode
        assessment = regime.build_regime_assessment_from_metrics(metrics, as_of=as_of)
        return assessment, data_mode
    assessment = regime.build_current_regime_assessment(as_of=as_of)
    return assessment, 'revised_reference'


def run_house_view_backtest(
    *,
    start: date,
    end: date,
    horizons: Iterable[int] = (3, 6),
    data_mode: str = 'auto',
    max_rows: int = 240,
) -> dict:
    """過去の各月に戻ったつもりで House View を再計算する。"""
    rows = []
    warnings = []
    horizon_values = tuple(int(horizon) for horizon in horizons)

    for as_of in _month_starts(start, end):
        assessment, row_data_mode = _build_assessment(as_of, data_mode)
        if assessment is None:
            warnings.append(
                f'{as_of.isoformat()} は改定前データが不足しているため検証をスキップしました。'
            )
            continue
        predicted = assessment.get('regime_label')
        if not predicted or predicted == RegimeSnapshot.Label.UNKNOWN:
            continue
        for horizon in horizon_values:
            target_date = as_of + relativedelta(months=horizon)
            if target_date > timezone.localdate():
                warnings.append(
                    f'{as_of.isoformat()} の{horizon}m先はまだ実績日が来ていないためスキップしました。'
                )
                continue
            actual = _actual_regime_row(as_of, target_date)
            if actual is None:
                continue
            miss_type = _miss_type(predicted, actual['regime_label'])
            rows.append({
                'as_of_date': as_of.isoformat(),
                'target_date': target_date.isoformat(),
                'actual_snapshot_date': actual['snapshot_date'].isoformat(),
                'actual_source': actual['source'],
                'horizon': f'{horizon}m',
                'validation_target': f'macro_regime_{horizon}m',
                'predicted_regime': predicted,
                'actual_regime': actual['regime_label'],
                'hit': miss_type == 'hit',
                'miss_type': miss_type,
                'data_mode': row_data_mode,
                'confidence': assessment.get('rule_strength'),
                'data_quality': assessment.get('data_quality'),
            })

    backtest_accuracy = {
        **_summary(rows),
        'horizons': _group_summaries(rows, 'horizon'),
        'data_modes': _group_summaries(rows, 'data_mode'),
    }
    if not rows:
        warnings.append('Backtestで検証できるサンプルがありません。')

    return {
        'generated_at': timezone.now().isoformat(),
        'execution_scope': 'local_heavy_backtest',
        'validation_target': 'macro_regime',
        'period': {
            'start': start.isoformat(),
            'end': end.isoformat(),
        },
        'horizons': [f'{horizon}m' for horizon in horizon_values],
        'backtest_accuracy': backtest_accuracy,
        'row_count_total': len(rows),
        'rows': rows[-max_rows:],
        'warnings': warnings,
    }
