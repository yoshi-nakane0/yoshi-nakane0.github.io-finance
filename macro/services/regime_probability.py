"""景気確率モデルの履歴検証。"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.utils import timezone

from ..models import Observation
from .crash_probability import brier_score, calibration_bins, pr_auc, roc_auc, wilson_interval
from .regime import (
    PROBABILITY_MODEL_VERSION,
    _latest_observation,
    build_regime_assessment_from_metrics,
    collect_key_metrics,
)


OUTPUT_RELATIVE_PATH = Path('static') / 'macro' / 'regime_probability_model.json'


def _month_end(month_start: date) -> date:
    return month_start.replace(day=1) + relativedelta(months=1) - timedelta(days=1)


def _add_month(month_start: date) -> date:
    return month_start + relativedelta(months=1)


def _month_starts(start: date, end: date) -> List[date]:
    current = start.replace(day=1)
    last = end.replace(day=1)
    rows = []
    while current <= last:
        rows.append(current)
        current = _add_month(current)
    return rows


def _actual_recession(month_start: date, horizon_months: int) -> Optional[bool]:
    target = _month_end(month_start + relativedelta(months=horizon_months))
    obs = _latest_observation('USREC', as_of=target)
    if obs is None or obs.value is None:
        return None
    return obs.value >= 0.5


def build_validation_dataset(*, years: int = 20, horizon_months: int = 3) -> List[Dict]:
    latest = (
        Observation.objects
        .order_by('-observation_date')
        .values_list('observation_date', flat=True)
        .first()
    )
    if latest is None:
        return []
    start = date(max(latest.year - years, 1900), latest.month, 1)
    rows = []
    for month in _month_starts(start, latest):
        metrics = collect_key_metrics(as_of=_month_end(month))
        assessment = build_regime_assessment_from_metrics(
            metrics,
            as_of=_month_end(month),
        )
        predicted = assessment.get('risk_probabilities', {}).get('recession')
        actual = _actual_recession(month, horizon_months)
        if predicted is None or actual is None:
            continue
        rows.append({
            'month': month.isoformat(),
            'probability': predicted,
            'event': actual,
            'regime_label': assessment.get('regime_label'),
            'rule_strength': assessment.get('rule_strength'),
            'data_quality': assessment.get('data_quality'),
        })
    return rows


def validate_regime_probability_model(
    *,
    years: int = 20,
    horizon_months: int = 3,
) -> Dict:
    rows = build_validation_dataset(years=years, horizon_months=horizon_months)
    event_count = sum(1 for row in rows if row['event'])
    event_interval = wilson_interval(event_count, len(rows)) if rows else None
    return {
        'model_version': PROBABILITY_MODEL_VERSION,
        'evaluated_at': timezone.localdate().isoformat(),
        'truth_source': 'USREC',
        'target': 'US recession probability',
        'horizon_months': horizon_months,
        'sample_count': len(rows),
        'event_count': event_count,
        'event_rate_interval': event_interval,
        'metrics': {
            'roc_auc': roc_auc(rows),
            'pr_auc': pr_auc(rows),
            'brier_score': brier_score(rows),
            'calibration_bins': calibration_bins(rows),
        },
        'rows': rows,
        'limitations': [
            'USRECの後追い判定を正解として使うため、直近月は確定が遅れます。',
            '確率は売買判断ではなく、景気リスクの比較用です。',
        ],
    }


def save_validation_payload(payload: Dict, output: Optional[str] = None) -> Path:
    path = Path(settings.BASE_DIR) / (output or OUTPUT_RELATIVE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    return path
