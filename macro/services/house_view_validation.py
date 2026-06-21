"""House View 予測の Backtest 精度と Live 精度を分けて返す。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.utils import timezone

from ..models import ForecastSnapshot, RegimeSnapshot
from .house_view_backtest import _empty_summary, _summary


HOUSE_VIEW_MODEL_VERSION = 'macro_hatzius_v1'
HOUSE_VIEW_TARGET = 'macro_regime'
HOUSE_VIEW_BACKTEST_PATH = Path('static/macro/house_view_backtest.json')
PSEUDO_LIVE_SAMPLE_LIMIT = 20


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
        prediction = forecast.prediction_value
        actual_event = 1.0 if hit else 0.0
        absolute_error = abs(actual_event - prediction)
        rows.append({
            'as_of_date': forecast.as_of_date.isoformat(),
            'target_date': target_date.isoformat(),
            'actual_snapshot_date': actual.snapshot_date.isoformat(),
            'horizon': forecast.horizon,
            'predicted_regime': predicted_regime,
            'actual_regime': actual.regime_label,
            'hit': hit,
            'miss_type': 'hit' if hit else 'wrong_regime',
            'prediction': prediction,
            'actual_event': actual_event,
            'brier_score': round((prediction - actual_event) ** 2, 4),
            'absolute_error': round(absolute_error, 4),
            'confidence': (forecast.metadata or {}).get('confidence'),
        })
    return rows


def _load_backtest_payload(
    backtest_path: str | Path | None = None,
) -> tuple[dict | None, str, str | None]:
    path = Path(backtest_path) if backtest_path else settings.BASE_DIR / HOUSE_VIEW_BACKTEST_PATH
    if not path.exists():
        return None, 'not_generated', 'ローカルBacktest結果JSONがまだありません。'
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None, 'invalid_json', 'ローカルBacktest結果JSONを読めません。'
    if not isinstance(payload, dict):
        return None, 'invalid_json', 'ローカルBacktest結果JSONの形式が不正です。'
    return payload, 'available', None


def _load_backtest_accuracy(backtest_path: str | Path | None = None) -> dict:
    payload, status, warning = _load_backtest_payload(backtest_path)
    if payload is None:
        return {
            **_empty_summary(),
            'sample_kind': 'backtest_replay',
            'status': status,
            'warning': warning,
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


def _parse_date(value) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _pseudo_live_summary(backtest_path: str | Path | None = None) -> dict:
    payload, status, warning = _load_backtest_payload(backtest_path)
    base = {
        'sample_kind': 'recent_backtest_replay',
        'sample_limit': PSEUDO_LIVE_SAMPLE_LIMIT,
    }
    if payload is None:
        return {
            **_empty_summary(),
            **base,
            'status': status,
            'warning': warning,
            'period': None,
            'rows': [],
        }

    today = timezone.localdate()
    eligible_rows = []
    for row in payload.get('rows') or []:
        as_of = _parse_date(row.get('as_of_date'))
        target_date = _parse_date(row.get('target_date'))
        if as_of is None or target_date is None or target_date > today:
            continue
        eligible_rows.append({
            **row,
            '_as_of': as_of,
            '_target_date': target_date,
            'miss_type': row.get('miss_type') or (
                'hit' if row.get('hit') else 'wrong_regime'
            ),
        })

    eligible_rows.sort(key=lambda row: (row['_as_of'], row['_target_date']))
    selected = eligible_rows[-PSEUDO_LIVE_SAMPLE_LIMIT:]
    rows = [
        {key: value for key, value in row.items() if not key.startswith('_')}
        for row in selected
    ]
    if not rows:
        return {
            **_empty_summary(),
            **base,
            'status': 'waiting_for_backtest_rows',
            'period': None,
            'rows': [],
        }

    return {
        **_summary(rows),
        **base,
        'status': 'available',
        'period': {
            'start': rows[0].get('as_of_date'),
            'end': rows[-1].get('as_of_date'),
        },
        'rows': rows,
    }


def _operation_health() -> dict:
    snapshots = list(
        ForecastSnapshot.objects
        .filter(model_version=HOUSE_VIEW_MODEL_VERSION, target=HOUSE_VIEW_TARGET)
        .order_by('as_of_date', 'created_at')
    )
    if not snapshots:
        return {
            'status_label': '未保存',
            'saved_forecast_count': 0,
            'latest_as_of': None,
            'pending_count': 0,
            'settled_count': 0,
            'overdue_count': 0,
            'missing_features_hash_count': 0,
            'missing_prediction_interval_count': 0,
            'notes': ['保存済みのLive予測がまだありません。'],
        }

    today = timezone.localdate()
    pending_count = 0
    settled_count = 0
    overdue_count = 0
    for forecast in snapshots:
        target_date = _target_date(forecast.as_of_date, forecast.horizon)
        if target_date > today:
            pending_count += 1
            continue
        actual_exists = (
            RegimeSnapshot.objects
            .filter(snapshot_date__gt=forecast.as_of_date, snapshot_date__lte=target_date)
            .exclude(regime_label=RegimeSnapshot.Label.UNKNOWN)
            .exists()
        )
        if actual_exists:
            settled_count += 1
        else:
            overdue_count += 1

    missing_features_hash_count = sum(
        1 for snapshot in snapshots if not snapshot.features_hash
    )
    missing_prediction_interval_count = sum(
        1 for snapshot in snapshots if not snapshot.prediction_interval
    )
    notes = []
    if pending_count:
        notes.append(f'結果待ちの予測が{pending_count}件あります。')
    if overdue_count:
        notes.append(f'結果日を過ぎた未確定予測が{overdue_count}件あります。')
    if missing_features_hash_count:
        notes.append(f'特徴量ハッシュ未保存が{missing_features_hash_count}件あります。')
    if missing_prediction_interval_count:
        notes.append(f'予測幅未保存が{missing_prediction_interval_count}件あります。')
    if not notes:
        notes.append('保存済みLive予測は検証に必要な情報を持っています。')

    status_label = '正常'
    if overdue_count or missing_features_hash_count or missing_prediction_interval_count:
        status_label = '注意'

    latest = snapshots[-1]
    return {
        'status_label': status_label,
        'saved_forecast_count': len(snapshots),
        'latest_as_of': latest.as_of_date.isoformat(),
        'pending_count': pending_count,
        'settled_count': settled_count,
        'overdue_count': overdue_count,
        'missing_features_hash_count': missing_features_hash_count,
        'missing_prediction_interval_count': missing_prediction_interval_count,
        'notes': notes,
    }


def _reliability(sample_count: int, hit_count: int, hit_rate) -> dict:
    if sample_count <= 0:
        return {
            'model_validation': 'C / 検証不足',
            'live_record': 'Live実績 未評価',
            'display_status': '参考',
            'note': '実際に保存した予測の結果がまだ確定していません。',
        }
    if sample_count < 10:
        return {
            'model_validation': 'C / 暫定',
            'live_record': f'Live実績 {sample_count}件 / 的中 {hit_count}件',
            'display_status': '参考',
            'note': '検証件数が少ないため、予測精度としては参考扱いです。',
        }

    if hit_rate is None:
        grade = 'C'
        rate_display = '—'
    else:
        grade = 'A' if hit_rate >= 0.65 else 'B' if hit_rate >= 0.55 else 'C'
        rate_display = f'{hit_rate:.0%}'
    return {
        'model_validation': f'{grade} / {rate_display}',
        'live_record': f'Live実績 {sample_count}件 / 的中 {hit_count}件',
        'display_status': '表示可',
        'note': 'Live検証の件数が最低基準を満たしています。',
    }


def _live_summary(rows: list[dict]) -> dict:
    summary = _summary(rows)
    brier_values = [row['brier_score'] for row in rows if row.get('brier_score') is not None]
    absolute_errors = [
        row['absolute_error'] for row in rows if row.get('absolute_error') is not None
    ]
    summary['avg_brier_score'] = (
        round(sum(brier_values) / len(brier_values), 4) if brier_values else None
    )
    summary['mae'] = (
        round(sum(absolute_errors) / len(absolute_errors), 4) if absolute_errors else None
    )
    return summary


def build_house_view_validation_report(backtest_path: str | Path | None = None) -> dict:
    rows = _live_rows()

    hit_count = sum(1 for row in rows if row['hit'])
    sample_count = len(rows)
    hit_rate = round(hit_count / sample_count, 4) if sample_count else None
    live_accuracy = {
        **_live_summary(rows),
        'sample_kind': 'live_saved_forecasts',
        'status': 'available' if rows else 'waiting_for_realizations',
    }
    backtest_accuracy = _load_backtest_accuracy(backtest_path)
    pseudo_live_accuracy = _pseudo_live_summary(backtest_path)
    return {
        'generated_at': timezone.now().isoformat(),
        'model_version': HOUSE_VIEW_MODEL_VERSION,
        'target': HOUSE_VIEW_TARGET,
        'accuracy_sections': {
            'backtest': backtest_accuracy,
            'live': live_accuracy,
            'pseudo_live': pseudo_live_accuracy,
        },
        'operation_health': _operation_health(),
        'sample_count': sample_count,
        'hit_count': hit_count,
        'hit_rate': hit_rate,
        'reliability': _reliability(sample_count, hit_count, hit_rate),
        'rows': rows[-120:],
        'warnings': (
            [] if sample_count >= 10 else ['検証件数が少ないため、的中率は暫定です。']
        ),
    }
