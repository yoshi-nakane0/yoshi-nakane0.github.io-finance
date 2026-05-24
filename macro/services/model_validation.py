"""モデル検証結果を ModelValidationReport に保存する。"""

from __future__ import annotations

import math
from statistics import median
from typing import Iterable, Optional

from django.db.models import Count

from ..models import ForecastSnapshot, ModelValidationReport
from . import crash_probability, forecast_models


def _mean(values):
    values = [value for value in values if value is not None]
    if not values:
        return None
    return sum(values) / len(values)


def _regression_metrics(rows: list[dict]) -> dict:
    errors = [row['error'] for row in rows if row.get('error') is not None]
    abs_errors = [abs(error) for error in errors]
    positive = [row for row in rows if row.get('actual', 0) >= 0]
    negative = [row for row in rows if row.get('actual', 0) < 0]
    direction_hits = [
        row for row in rows
        if (row.get('prediction', 0) >= 0 and row.get('actual', 0) >= 0)
        or (row.get('prediction', 0) < 0 and row.get('actual', 0) < 0)
    ]
    return {
        'mae': _mean(abs_errors),
        'median_abs_error': median(abs_errors) if abs_errors else None,
        'rmse': math.sqrt(_mean([error ** 2 for error in errors])) if errors else None,
        'direction_accuracy': len(direction_hits) / len(rows) if rows else None,
        'hit_rate_positive': (
            len([
                row for row in positive
                if row.get('prediction', 0) >= 0
            ]) / len(positive)
            if positive else None
        ),
        'hit_rate_negative': (
            len([
                row for row in negative
                if row.get('prediction', 0) < 0
            ]) / len(negative)
            if negative else None
        ),
    }


def _probability_metrics(rows: list[dict]) -> dict:
    records = [
        {
            'event': bool(row.get('actual')),
            'probability': row.get('prediction') or 0.0,
        }
        for row in rows
    ]
    return {
        'roc_auc': crash_probability.roc_auc(records),
        'pr_auc': crash_probability.pr_auc(records),
        'brier_score': crash_probability.brier_score(records),
        'calibration_bins': crash_probability.calibration_bins(records),
    }


def _snapshot_rows(model_version: str, target: str, horizon: str) -> list[dict]:
    snapshots = (
        ForecastSnapshot.objects
        .filter(
            model_version=model_version,
            target=target,
            horizon=horizon,
            realized_value__isnull=False,
        )
        .order_by('as_of_date')
    )
    return [
        {
            'as_of_date': row.as_of_date.isoformat(),
            'prediction': row.prediction_value,
            'actual': row.realized_value,
            'error': row.error,
            'realized_at': row.realized_at.isoformat() if row.realized_at else None,
        }
        for row in snapshots
    ]


def validate_model(
    *,
    model_version: str,
    target: str,
    horizon: str,
    validation_method: str = 'walk_forward',
) -> ModelValidationReport:
    rows = _snapshot_rows(model_version, target, horizon)
    warnings = []
    if rows:
        sample_count = len(rows)
        prediction_kind = (
            ForecastSnapshot.objects
            .filter(model_version=model_version, target=target, horizon=horizon)
            .order_by('-as_of_date')
            .values_list('metadata', flat=True)
            .first()
            or {}
        ).get('prediction_kind')
        if prediction_kind == 'drawdown_event_probability':
            metrics = _probability_metrics(rows)
            event_count = sum(1 for row in rows if row.get('actual'))
        else:
            metrics = _regression_metrics(rows)
            event_count = None
    elif model_version.startswith(('return_lightgbm', 'macro_forecast')):
        matrix = forecast_models.build_monthly_feature_matrix(
            'return_forecast' if model_version.startswith('return') else 'macro_forecast',
            target,
            horizon,
        )
        validation = forecast_models.walk_forward_validate(matrix.get('rows', []))
        sample_count = validation['sample_count']
        metrics = validation['metrics']
        rows = validation['rows']
        warnings.extend(validation['warnings'])
        event_count = None
    else:
        sample_count = 0
        metrics = {}
        event_count = None
        warnings.append('検証できる予測実績がまだありません。')

    if sample_count < 10:
        warnings.append('検証サンプルが少ないため、結果は暫定です。')

    return ModelValidationReport.objects.create(
        model_version=model_version,
        target=target,
        horizon=horizon,
        validation_method=validation_method,
        sample_count=sample_count,
        event_count=event_count,
        metrics=metrics,
        rows=rows[-120:],
        warnings=warnings,
    )


def _default_validation_targets() -> set[tuple[str, str, str]]:
    targets = {
        (forecast_models.RETURN_MODEL_VERSION, target, horizon)
        for target in forecast_models.RETURN_TARGETS
        for horizon in forecast_models.HORIZONS
    }
    targets.update({
        (forecast_models.MACRO_MODEL_VERSION, target, horizon)
        for target in forecast_models.MACRO_TARGETS
        for horizon in forecast_models.HORIZONS
    })
    targets.add(('crash_probability_logistic_v1', 'GSPC', '63d'))
    return targets


def run_all_model_validations() -> list[ModelValidationReport]:
    groups = set(_default_validation_targets())
    snapshot_groups = (
        ForecastSnapshot.objects
        .values('model_version', 'target', 'horizon')
        .annotate(total=Count('id'))
    )
    groups.update(
        (row['model_version'], row['target'], row['horizon'])
        for row in snapshot_groups
    )
    reports = []
    for model_version, target, horizon in sorted(groups):
        reports.append(
            validate_model(
                model_version=model_version,
                target=target,
                horizon=horizon,
            )
        )
    return reports
