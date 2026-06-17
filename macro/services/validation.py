"""保存済みmacro予測の検証。"""

from __future__ import annotations

from datetime import date
from typing import Optional

from ..models import ForecastSnapshot, MacroForecastOutcome


def _predicted_probability(forecast: ForecastSnapshot, target_name: str) -> Optional[float]:
    metadata = forecast.metadata or {}
    probabilities = metadata.get('regime_probabilities') or {}
    if target_name in probabilities:
        return float(probabilities[target_name])
    if forecast.prediction_value is not None:
        return float(forecast.prediction_value)
    return None


def evaluate_forecast_snapshot(
    forecast: ForecastSnapshot,
    *,
    target_date: date,
    target_name: str,
    actual_value: Optional[float],
) -> MacroForecastOutcome:
    predicted_prob = _predicted_probability(forecast, target_name)
    predicted_value = forecast.prediction_value
    brier_score = None
    absolute_error = None
    direction_hit = None
    if predicted_prob is not None and actual_value is not None:
        brier_score = round((predicted_prob - float(actual_value)) ** 2, 6)
        direction_hit = (predicted_prob >= 0.5) == (float(actual_value) >= 0.5)
    if predicted_value is not None and actual_value is not None:
        absolute_error = abs(float(predicted_value) - float(actual_value))

    outcome, _ = MacroForecastOutcome.objects.update_or_create(
        forecast=forecast,
        target_date=target_date,
        target_name=target_name,
        defaults={
            'predicted_value': predicted_value,
            'predicted_prob': predicted_prob,
            'actual_value': actual_value,
            'brier_score': brier_score,
            'absolute_error': absolute_error,
            'direction_hit': direction_hit,
        },
    )
    return outcome
