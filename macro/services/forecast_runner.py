"""経済状態推定から予測保存までをまとめる。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from typing import List, Optional

from django.db import transaction
from django.utils import timezone

from ..models import (
    ForecastSnapshot,
    MacroForecastRun,
    MacroScenario,
    RegimeSnapshot,
    WorldStateSnapshot,
)
from .report_writer import write_macro_report
from .scenario_engine import build_macro_scenarios, persist_macro_scenarios
from .state_vector import build_economic_state_vector
from .world_state import compute_current_world_state


MODEL_VERSION = 'macro_hatzius_v1'


@dataclass
class MacroForecastResult:
    run: MacroForecastRun
    snapshot: ForecastSnapshot
    scenarios: List[MacroScenario]


def _latest_regime(as_of: date) -> Optional[RegimeSnapshot]:
    return (
        RegimeSnapshot.objects
        .filter(snapshot_date__lte=as_of)
        .order_by('-snapshot_date')
        .first()
    )


def _previous_regime(as_of: date) -> str:
    previous = (
        RegimeSnapshot.objects
        .filter(snapshot_date__lt=as_of)
        .order_by('-snapshot_date')
        .first()
    )
    return previous.regime_label if previous else ''


def _primary_regime(probabilities: dict, fallback: str) -> str:
    if probabilities:
        return max(probabilities.items(), key=lambda item: item[1])[0]
    return fallback or RegimeSnapshot.Label.UNKNOWN


def _stable_features_hash(payload: dict) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _prediction_interval(probability: float, confidence: float) -> dict:
    width = 0.10
    return {
        'type': 'regime_probability_range',
        'lower': round(max(probability - width, 0.0), 4),
        'upper': round(min(probability + width, 1.0), 4),
        'confidence': round(confidence / 100, 4) if confidence else 0.0,
    }


def run_macro_forecast(*, as_of: Optional[date] = None) -> MacroForecastResult:
    target_date = as_of or timezone.localdate()
    world_snapshot = (
        WorldStateSnapshot.objects
        .filter(as_of_date=target_date)
        .first()
        or compute_current_world_state(as_of=target_date)
    )
    regime_snapshot = _latest_regime(target_date)
    regime_probabilities = (
        regime_snapshot.regime_probabilities
        if regime_snapshot and regime_snapshot.regime_probabilities
        else {}
    )
    risk_probabilities = (
        regime_snapshot.risk_probabilities
        if regime_snapshot and regime_snapshot.risk_probabilities
        else {}
    )
    primary = _primary_regime(
        regime_probabilities,
        regime_snapshot.regime_label if regime_snapshot else '',
    )
    previous_regime = _previous_regime(target_date)
    state_vector = build_economic_state_vector(world_snapshot)
    scenario_payloads = build_macro_scenarios(
        state_vector=state_vector,
        regime_probabilities=regime_probabilities,
        risk_probabilities=risk_probabilities,
    )
    report = write_macro_report(
        state_vector=state_vector,
        primary_regime=primary,
        previous_regime=previous_regime,
        regime_probabilities=regime_probabilities,
        risk_probabilities=risk_probabilities,
        scenarios=scenario_payloads,
    )
    prediction_value = regime_probabilities.get(primary, 0.0)
    regime_confidence = regime_snapshot.confidence if regime_snapshot else 0.0
    feature_payload = {
        'as_of': target_date.isoformat(),
        'model_version': MODEL_VERSION,
        'target': 'macro_regime',
        'horizon': '3m_6m',
        'primary_regime': primary,
        'state_vector': state_vector,
        'regime_probabilities': regime_probabilities,
        'risk_probabilities': risk_probabilities,
        'world_feature_vector': world_snapshot.feature_vector or {},
        'source_freshness': world_snapshot.source_freshness or {},
    }
    features_hash = _stable_features_hash(feature_payload)
    prediction_interval = _prediction_interval(prediction_value, regime_confidence)

    with transaction.atomic():
        snapshot, _ = ForecastSnapshot.objects.update_or_create(
            as_of_date=target_date,
            model_version=MODEL_VERSION,
            target='macro_regime',
            horizon='3m_6m',
            defaults={
                'prediction_value': prediction_value,
                'prediction_interval': prediction_interval,
                'features_hash': features_hash,
                'metadata': {
                    'primary_regime': primary,
                    'previous_regime': previous_regime,
                    'features_hash': features_hash,
                    'feature_payload': feature_payload,
                    'state_vector': state_vector,
                    'regime_probabilities': regime_probabilities,
                    'risk_probabilities': risk_probabilities,
                    'data_quality': world_snapshot.data_quality,
                    'report': report,
                },
            },
        )
        run, _ = MacroForecastRun.objects.update_or_create(
            as_of=target_date,
            defaults={
                'forecast': snapshot,
                'primary_regime': primary,
                'previous_regime': previous_regime,
                'confidence': (
                    regime_snapshot.confidence if regime_snapshot else 0.0
                ),
                'data_quality_score': world_snapshot.data_quality,
                'state_vector': state_vector,
                'regime_probabilities': regime_probabilities,
                'risk_probabilities': risk_probabilities,
                'report': report,
                'warnings': list(world_snapshot.warnings or [])
                + list(regime_snapshot.warnings if regime_snapshot else []),
                'model_version': MODEL_VERSION,
            },
        )
        scenarios = persist_macro_scenarios(run, scenario_payloads)
    return MacroForecastResult(run=run, snapshot=snapshot, scenarios=scenarios)
