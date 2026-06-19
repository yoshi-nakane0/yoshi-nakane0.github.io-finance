"""検証結果に応じたモデル重みを計算する。"""

from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from ..models import ModelValidationReport
from . import forecast_models
from .model_validation import model_display_grade

HOUSE_VIEW_BACKTEST_PATH = Path('static/macro/house_view_backtest.json')


def _bounded(value: float, low: float = 0.0, high: float = 1.25) -> float:
    return round(max(low, min(high, value)), 4)


def validation_weight_for_report(report: ModelValidationReport) -> float:
    display_grade, _ = model_display_grade(report)
    if display_grade == 'blocked':
        return 0.0
    if display_grade == 'hidden':
        base = 0.25
    elif display_grade == 'reference':
        base = 0.45
    else:
        base = 0.85

    metrics = report.metrics or {}
    adjustments = []
    direction_accuracy = metrics.get('direction_accuracy')
    if isinstance(direction_accuracy, (int, float)):
        adjustments.append((direction_accuracy - 0.5) * 1.5)
    skill_score = metrics.get('skill_score')
    if isinstance(skill_score, (int, float)):
        adjustments.append(max(-0.25, min(0.25, skill_score)))
    roc_auc = metrics.get('roc_auc')
    if isinstance(roc_auc, (int, float)):
        adjustments.append((roc_auc - 0.5) * 1.2)
    brier_score = metrics.get('brier_score')
    if isinstance(brier_score, (int, float)):
        adjustments.append(max(-0.25, min(0.2, 0.25 - brier_score)))

    adjustment = sum(adjustments) / len(adjustments) if adjustments else 0.0
    sample_penalty = min(1.0, max(0.2, report.sample_count / 60))
    return _bounded((base + adjustment) * sample_penalty)


def _house_view_backtest_weight(path: str | Path | None = None) -> dict | None:
    payload_path = Path(path) if path else settings.BASE_DIR / HOUSE_VIEW_BACKTEST_PATH
    if not payload_path.exists():
        return None
    try:
        payload = json.loads(payload_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    accuracy = payload.get('backtest_accuracy') or {}
    sample_count = accuracy.get('sample_count') or 0
    hit_rate = accuracy.get('hit_rate')
    if not isinstance(hit_rate, (int, float)) or sample_count <= 0:
        return None
    sample_penalty = min(1.0, max(0.2, sample_count / 20))
    return {
        'model_key': 'macro_hatzius_v1:macro_regime:backtest',
        'model_version': 'macro_hatzius_v1',
        'target': 'macro_regime',
        'horizon': 'backtest',
        'report_id': None,
        'evaluated_at': payload.get('generated_at'),
        'sample_count': sample_count,
        'display_grade': 'show' if sample_count >= 10 else 'reference',
        'display_reason': 'House View Backtest 的中率を反映',
        'validation_weight': _bounded(hit_rate * sample_penalty),
        'metrics_used': {
            'hit_rate': hit_rate,
            'hit_count': accuracy.get('hit_count'),
            'too_bullish_count': accuracy.get('too_bullish_count'),
            'too_defensive_count': accuracy.get('too_defensive_count'),
        },
        'source': 'house_view_backtest',
    }


def build_validation_weight_report(
    *,
    house_view_backtest_path: str | Path | None = None,
) -> dict:
    latest_by_model = {}
    for report in ModelValidationReport.objects.order_by('-evaluated_at', '-id'):
        key = (report.model_version, report.target, report.horizon)
        if forecast_models.is_deprecated_monthly_short_return_model(*key):
            continue
        if key not in latest_by_model:
            latest_by_model[key] = report

    rows = []
    for (model_version, target, horizon), report in sorted(latest_by_model.items()):
        display_grade, display_reason = model_display_grade(report)
        model_key = f'{model_version}:{target}:{horizon}'
        rows.append({
            'model_key': model_key,
            'model_version': model_version,
            'target': target,
            'horizon': horizon,
            'report_id': report.id,
            'evaluated_at': report.evaluated_at.isoformat(),
            'sample_count': report.sample_count,
            'display_grade': display_grade,
            'display_reason': display_reason,
            'validation_weight': validation_weight_for_report(report),
            'metrics_used': report.metrics or {},
            'source': 'model_validation_report',
        })
    house_view_weight = _house_view_backtest_weight(house_view_backtest_path)
    if house_view_weight is not None:
        rows.append(house_view_weight)

    return {
        'generated_at': timezone.now().isoformat(),
        'weighting_policy': 'validation_adjusted',
        'validation_weights': rows,
        'warnings': (
            [] if rows else ['検証済みモデルがないため、重みを計算できません。']
        ),
    }
