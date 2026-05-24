"""予測スナップショットの後追い検証。"""

from __future__ import annotations

import math
from datetime import date
from typing import Dict, Optional

from dateutil.relativedelta import relativedelta
from django.db.models import Avg, Count
from django.utils import timezone

from ..models import ForecastSnapshot, PriceObservation
from .crash_probability import TARGET_TICKERS, future_drawdown, load_price_series


def _parse_days(value: str) -> Optional[int]:
    if not value or not value.endswith('d'):
        return None
    try:
        return int(value[:-1])
    except ValueError:
        return None


def _parse_months(value: str) -> Optional[int]:
    if not value or not value.endswith('m'):
        return None
    try:
        return int(value[:-1])
    except ValueError:
        return None


def _target_ticker(target: str) -> Optional[str]:
    if target in TARGET_TICKERS:
        return TARGET_TICKERS[target]
    try:
        return PriceObservation.Ticker(target)
    except ValueError:
        return None


def _latest_price_month(ticker: str) -> Optional[date]:
    return (
        PriceObservation.objects
        .filter(ticker=ticker)
        .order_by('-observation_month')
        .values_list('observation_month', flat=True)
        .first()
    )


def _settle_crash_probability(snapshot: ForecastSnapshot) -> Optional[Dict]:
    ticker = _target_ticker(snapshot.target)
    if ticker is None:
        return None
    horizon_days = (
        snapshot.metadata.get('horizon_days')
        or _parse_days(snapshot.horizon)
    )
    if horizon_days is None:
        return None
    horizon_months = max(1, math.ceil(horizon_days / 30.4375))
    month_start = snapshot.as_of_date.replace(day=1)
    needed_month = month_start + relativedelta(months=horizon_months)
    latest_month = _latest_price_month(ticker)
    if latest_month is None or latest_month < needed_month:
        return None

    threshold = snapshot.metadata.get('drawdown_threshold_pct', -10.0)
    max_drawdown, lead_time_days = future_drawdown(
        load_price_series(ticker),
        month_start,
        horizon_months,
        threshold,
    )
    if max_drawdown is None:
        return None
    realized = 1.0 if max_drawdown <= threshold else 0.0
    return {
        'realized_value': realized,
        'realized_at': needed_month,
        'error': realized - snapshot.prediction_value,
        'metadata': {
            **(snapshot.metadata or {}),
            'max_drawdown_pct': max_drawdown,
            'lead_time_days': lead_time_days,
            'settled_kind': 'drawdown_event',
        },
    }


def _settle_return_forecast(snapshot: ForecastSnapshot) -> Optional[Dict]:
    ticker = _target_ticker(snapshot.target)
    if ticker is None:
        return None
    horizon_months = (
        snapshot.metadata.get('horizon_months')
        or _parse_months(snapshot.horizon)
    )
    if horizon_months is None:
        return None
    prices = load_price_series(ticker)
    month_start = snapshot.as_of_date.replace(day=1)
    future_month = month_start + relativedelta(months=horizon_months)
    base = prices.get(month_start)
    future = prices.get(future_month)
    if base in (None, 0) or future is None:
        return None
    realized = (future - base) / base * 100.0
    return {
        'realized_value': realized,
        'realized_at': future_month,
        'error': realized - snapshot.prediction_value,
        'metadata': {
            **(snapshot.metadata or {}),
            'settled_kind': 'return_pct',
        },
    }


def settle_snapshot(snapshot: ForecastSnapshot) -> bool:
    if snapshot.realized_value is not None:
        return False
    if snapshot.model_version.startswith('crash_probability_logistic'):
        result = _settle_crash_probability(snapshot)
    elif snapshot.model_version.startswith('lightgbm_return'):
        result = _settle_return_forecast(snapshot)
    else:
        result = None
    if result is None:
        return False
    snapshot.realized_value = result['realized_value']
    snapshot.realized_at = result['realized_at']
    snapshot.error = result['error']
    snapshot.metadata = result['metadata']
    snapshot.save(
        update_fields=['realized_value', 'realized_at', 'error', 'metadata'],
    )
    return True


def settle_due_forecasts(limit: Optional[int] = None) -> Dict:
    qs = ForecastSnapshot.objects.filter(realized_value__isnull=True).order_by(
        'as_of_date',
        'created_at',
    )
    if limit:
        qs = qs[:limit]
    checked = 0
    settled = 0
    for snapshot in qs:
        checked += 1
        if settle_snapshot(snapshot):
            settled += 1
    return {
        'checked_count': checked,
        'settled_count': settled,
        'finished_at': timezone.now().isoformat(),
    }


def _fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return '—'
    sign = '+' if value > 0 else ''
    return f'{sign}{value:.2f}'


def build_forecast_monitor_context() -> Dict:
    total = ForecastSnapshot.objects.count()
    settled = ForecastSnapshot.objects.filter(realized_value__isnull=False)
    settled_count = settled.count()
    pending_count = max(total - settled_count, 0)
    avg_abs_error = None
    if settled_count:
        avg_abs_error = sum(
            abs(row.error or 0.0)
            for row in settled.only('error')
        ) / settled_count
    by_model = (
        ForecastSnapshot.objects
        .values('model_version')
        .annotate(total=Count('id'))
        .order_by('model_version')
    )
    latest_rows = list(
        ForecastSnapshot.objects
        .filter(realized_value__isnull=False)
        .order_by('-realized_at', '-as_of_date')[:4]
    )
    return {
        'total_count': total,
        'settled_count': settled_count,
        'pending_count': pending_count,
        'avg_abs_error_display': _fmt_pct(avg_abs_error),
        'by_model': list(by_model),
        'latest_rows': [
            {
                'as_of_date': row.as_of_date,
                'model_version': row.model_version,
                'target': row.target,
                'horizon': row.horizon,
                'prediction_display': _fmt_pct(row.prediction_value),
                'realized_display': _fmt_pct(row.realized_value),
                'error_display': _fmt_pct(row.error),
                'realized_at': row.realized_at,
            }
            for row in latest_rows
        ],
        'tone': 'good' if total and settled_count else 'warning',
        'status_label': '検証中' if total else '予測記録なし',
    }
