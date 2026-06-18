"""House View 予測の Backtest 精度と Live 精度を分けて返す。"""

from __future__ import annotations

import json
from pathlib import Path

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.utils import timezone

from ..models import ForecastSnapshot, RegimeSnapshot
from .house_view_backtest import _empty_summary, _summary


HOUSE_VIEW_MODEL_VERSION = 'macro_hatzius_v1'
HOUSE_VIEW_TARGET = 'macro_regime'
HOUSE_VIEW_BACKTEST_PATH = Path('static/macro/house_view_backtest.json')


def _target_date(as_of_date, horizon: str):
    if horizon.startswith('3m'):
        return as_of_date + relativedelta(months=3)
    if horizon.startswith('6m'):
        return as_of_date + relativedelta(months=6)
    return as_of_date + relativedelta(months=3)


def _live_rows() -> list[dict]:
    rows = []
    snapshots = (
        ForecastSnapshot.objects
        .filter(model_version=HOUSE_VIEW_MODEL_VERSION, target=HOUSE_VIEW_TARGET)
        .order_by('as_of_date')
    )
    for forecast in snapshots:
        predicted_regime = (forecast.metadata or {}).get('primary_regime')
        if not predicted_regime:
            continue
        target_date = _target_date(forecast.as_of_date, forecast.horizon)
        actual = (
            RegimeSnapshot.objects
            .filter(snapshot_date__gt=forecast.as_of_date, snapshot_date__lte=target_date)
            .order_by('-snapshot_date')
            .first()
        )
        if actual is None or actual.regime_label == RegimeSnapshot.Label.UNKNOWN:
            continue
        hit = predicted_regime == actual.regime_label
        rows.append({
            'as_of_date': forecast.as_of_date.isoformat(),
            'target_date': target_date.isoformat(),
            'actual_snapshot_date': actual.snapshot_date.isoformat(),
            'horizon': forecast.horizon,
            'predicted_regime': predicted_regime,
            'actual_regime': actual.regime_label,
            'hit': hit,
            'miss_type': 'hit' if hit else 'wrong_regime',
            'confidence': (forecast.metadata or {}).get('confidence'),
        })
    return rows


def _load_backtest_accuracy(backtest_path: str | Path | None = None) -> dict:
    path = Path(backtest_path) if backtest_path else settings.BASE_DIR / HOUSE_VIEW_BACKTEST_PATH
    if not path.exists():
        return {
            **_empty_summary(),
            'sample_kind': 'backtest_replay',
            'status': 'not_generated',
            'warning': 'ローカルBacktest結果JSONがまだありません。',
        }
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {
            **_empty_summary(),
            'sample_kind': 'backtest_replay',
            'status': 'invalid_json',
            'warning': 'ローカルBacktest結果JSONを読めません。',
        }
    accuracy = payload.get('backtest_accuracy') or {}
    return {
        **accuracy,
        'sample_kind': 'backtest_replay',
        'status': 'available',
        'generated_at': payload.get('generated_at'),
        'period': payload.get('period'),
        'horizons': accuracy.get('horizons') or {},
        'data_modes': accuracy.get('data_modes') or {},
    }


def build_house_view_validation_report(backtest_path: str | Path | None = None) -> dict:
    rows = _live_rows()

    hit_count = sum(1 for row in rows if row['hit'])
    sample_count = len(rows)
    hit_rate = round(hit_count / sample_count, 4) if sample_count else None
    live_accuracy = {
        **_summary(rows),
        'sample_kind': 'live_saved_forecasts',
        'status': 'available' if rows else 'waiting_for_realizations',
    }
    backtest_accuracy = _load_backtest_accuracy(backtest_path)
    return {
        'generated_at': timezone.now().isoformat(),
        'model_version': HOUSE_VIEW_MODEL_VERSION,
        'target': HOUSE_VIEW_TARGET,
        'accuracy_sections': {
            'backtest': backtest_accuracy,
            'live': live_accuracy,
        },
        'sample_count': sample_count,
        'hit_count': hit_count,
        'hit_rate': hit_rate,
        'rows': rows[-120:],
        'warnings': (
            [] if sample_count >= 10 else ['検証件数が少ないため、的中率は暫定です。']
        ),
    }
