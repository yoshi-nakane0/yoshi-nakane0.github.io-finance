"""world model の運用状態を記録・表示する補助処理。"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Dict, Optional

from django.conf import settings
from django.utils import timezone

from ..models import WorldModelRun


EXPECTED_INTERVAL_DAYS = {
    WorldModelRun.Cadence.DAILY: 2,
    WorldModelRun.Cadence.WEEKLY: 10,
    WorldModelRun.Cadence.MONTHLY: 40,
    WorldModelRun.Cadence.ARCHIVE: 40,
}

MONTHLY_OUTPUT_FILES = (
    Path('static') / 'macro' / 'crash_alert_backtest.json',
    Path('static') / 'macro' / 'crash_probability_model.json',
    Path('static') / 'macro' / 'regime_probability_model.json',
    Path('static') / 'macro' / 'return_forecast_model.json',
    Path('static') / 'macro' / 'macro_forecast_model.json',
)


def start_run(*, cadence: str, name: str, steps=None) -> WorldModelRun:
    return WorldModelRun.objects.create(
        cadence=cadence,
        name=name,
        status=WorldModelRun.Status.RUNNING,
        started_at=timezone.now(),
        steps=steps or [],
    )


def finish_run(
    run: WorldModelRun,
    *,
    status: str = WorldModelRun.Status.SUCCESS,
    summary: Optional[Dict] = None,
    error: str = '',
) -> WorldModelRun:
    run.status = status
    run.finished_at = timezone.now()
    run.summary = summary or {}
    run.error = error
    run.save(update_fields=['status', 'finished_at', 'summary', 'error'])
    return run


def latest_run(cadence: str) -> Optional[WorldModelRun]:
    return WorldModelRun.objects.filter(cadence=cadence).order_by('-started_at').first()


def _format_dt(value) -> str:
    if value is None:
        return '—'
    return timezone.localtime(value).strftime('%Y-%m-%d %H:%M')


def _latest_monthly_output_time():
    mtimes = []
    for relative_path in MONTHLY_OUTPUT_FILES:
        path = Path(settings.BASE_DIR) / relative_path
        if path.exists():
            mtimes.append(path.stat().st_mtime)
    if not mtimes:
        return None
    return timezone.datetime.fromtimestamp(max(mtimes), tz=timezone.get_current_timezone())


def _fallback_row(cadence: str, label: str) -> Optional[Dict]:
    daily = latest_run(WorldModelRun.Cadence.DAILY)
    if cadence == WorldModelRun.Cadence.WEEKLY and daily is not None:
        finished = daily.finished_at or daily.started_at
        return {
            'cadence': cadence,
            'label': label,
            'status': 'success',
            'status_label': '日次内で確認',
            'last_finished_at': _format_dt(finished),
            'age_days': (timezone.now() - finished).days,
            'is_stale': False,
            'summary_label': '共有DB未設定のため、日次更新の実行履歴を表示しています。',
        }

    monthly_output_time = _latest_monthly_output_time()
    if cadence == WorldModelRun.Cadence.MONTHLY and monthly_output_time is not None:
        return {
            'cadence': cadence,
            'label': label,
            'status': 'success',
            'status_label': '出力更新済み',
            'last_finished_at': _format_dt(monthly_output_time),
            'age_days': (timezone.now() - monthly_output_time).days,
            'is_stale': False,
            'summary_label': '月次モデル出力ファイルを確認しました。',
        }

    if cadence == WorldModelRun.Cadence.ARCHIVE and monthly_output_time is not None:
        return {
            'cadence': cadence,
            'label': label,
            'status': 'success',
            'status_label': '月次内で退避',
            'last_finished_at': _format_dt(monthly_output_time),
            'age_days': (timezone.now() - monthly_output_time).days,
            'is_stale': False,
            'summary_label': '月次メンテナンス内の履歴退避を前提に表示しています。',
        }
    return None


def _row(cadence: str, label: str) -> Dict:
    run = latest_run(cadence)
    expected = EXPECTED_INTERVAL_DAYS.get(cadence)
    if run is None:
        fallback = _fallback_row(cadence, label)
        if fallback is not None:
            return fallback
        return {
            'cadence': cadence,
            'label': label,
            'status': 'missing',
            'status_label': '記録なし',
            'last_finished_at': '—',
            'age_days': None,
            'is_stale': True,
            'summary_label': 'まだ実行履歴がありません。',
        }
    finished = run.finished_at or run.started_at
    age_days = (timezone.now() - finished).days
    is_stale = expected is not None and age_days > expected
    status_label = WorldModelRun.Status(run.status).label
    return {
        'cadence': cadence,
        'label': label,
        'status': run.status,
        'status_label': status_label,
        'last_finished_at': _format_dt(finished),
        'age_days': age_days,
        'is_stale': is_stale,
        'summary_label': run.summary.get('message') or run.name,
    }


def build_operations_context() -> Dict:
    rows = [
        _row(WorldModelRun.Cadence.DAILY, '日次更新'),
        _row(WorldModelRun.Cadence.WEEKLY, '週次検証'),
        _row(WorldModelRun.Cadence.MONTHLY, '月次学習'),
        _row(WorldModelRun.Cadence.ARCHIVE, '履歴退避'),
    ]
    has_problem = any(
        row['status'] in ('failed', 'missing') or row['is_stale']
        for row in rows
    )
    last_monthly = latest_run(WorldModelRun.Cadence.MONTHLY)
    next_monthly_due = None
    if last_monthly and last_monthly.finished_at:
        next_monthly_due = last_monthly.finished_at + timedelta(days=30)
    return {
        'tone': 'warning' if has_problem else 'good',
        'status_label': '要確認' if has_problem else '運用中',
        'rows': rows,
        'next_monthly_due': _format_dt(next_monthly_due),
    }
