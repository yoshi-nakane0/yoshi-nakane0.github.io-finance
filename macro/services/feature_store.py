"""予測に使う特徴量を再現可能に保存する。"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date
from typing import Optional

from dateutil.relativedelta import relativedelta
from django.db import transaction
from django.utils import timezone

from ..models import (
    FeatureSnapshot,
    Indicator,
    Observation,
    PriceObservation,
    WorldStateSnapshot,
)


def normalize_feature_value(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric) or math.isinf(numeric):
        return None
    return round(numeric, 8)


def _latest_world_state(as_of: Optional[date]) -> Optional[WorldStateSnapshot]:
    qs = WorldStateSnapshot.objects.all()
    if as_of is not None:
        qs = qs.filter(as_of_date__lte=as_of)
    return qs.order_by('-as_of_date').first()


def _latest_observation(indicator: Indicator, as_of: Optional[date]) -> Optional[Observation]:
    qs = Observation.objects.filter(indicator=indicator)
    if as_of is not None:
        qs = qs.filter(observation_date__lte=as_of)
    return qs.order_by('-observation_date').first()


def _price_return(ticker: str, as_of: Optional[date], months: int) -> Optional[float]:
    qs = PriceObservation.objects.filter(ticker=ticker)
    if as_of is not None:
        qs = qs.filter(observation_month__lte=as_of.replace(day=1))
    latest = qs.order_by('-observation_month').first()
    if latest is None or latest.close_price in (None, 0):
        return None
    past_month = latest.observation_month - relativedelta(months=months)
    past = (
        PriceObservation.objects
        .filter(ticker=ticker, observation_month__lte=past_month)
        .order_by('-observation_month')
        .first()
    )
    if past is None or past.close_price in (None, 0):
        return None
    return (latest.close_price - past.close_price) / past.close_price * 100.0


def _source_date_map(
    world_state: Optional[WorldStateSnapshot],
    observations: dict[str, Observation],
) -> dict:
    source_dates = {}
    if world_state is not None:
        source_dates['world_state'] = world_state.as_of_date.isoformat()
        source_dates.update(world_state.explanation.get('source_dates') or {})
    for key, obs in observations.items():
        source_dates[key] = obs.observation_date.isoformat()
    return source_dates


def build_feature_vector(
    *,
    namespace: str,
    target: str,
    horizon: str,
    as_of: Optional[date] = None,
) -> dict:
    """標準化済み特徴量を作る。欠損は 0.0 にする。"""
    del namespace, target, horizon
    as_of = as_of or timezone.localdate()
    features: dict[str, float] = {}
    missing: list[str] = []
    observations: dict[str, Observation] = {}

    world_state = _latest_world_state(as_of)
    if world_state is None:
        missing.append('world_state')
    else:
        for key, value in (world_state.feature_vector or {}).items():
            normalized = normalize_feature_value(value)
            if normalized is not None:
                features[key] = normalized
        for field in (
            'growth_score',
            'labor_score',
            'inflation_score',
            'policy_pressure_score',
            'liquidity_score',
            'credit_score',
            'risk_appetite_score',
            'market_trend_score',
            'market_stress_score',
            'data_quality',
        ):
            normalized = normalize_feature_value(getattr(world_state, field, None))
            feature_name = f'world_{field}'
            if normalized is None:
                missing.append(feature_name)
                normalized = 0.0
            features[feature_name] = normalized

    indicators = (
        Indicator.objects
        .filter(is_active=True, importance__in=[Indicator.Importance.A, Indicator.Importance.B])
        .order_by('display_order', 'fred_series_id')
    )
    for indicator in indicators:
        obs = _latest_observation(indicator, as_of)
        if obs is None:
            missing.append(f'{indicator.fred_series_id}_observation')
            features[f'{indicator.fred_series_id}_expanding_z'] = 0.0
            features[f'{indicator.fred_series_id}_rolling_5y_z'] = 0.0
            continue
        observations[indicator.fred_series_id] = obs
        for source_attr, suffix in (
            ('expanding_z_score', 'expanding_z'),
            ('rolling_5y_z_score', 'rolling_5y_z'),
        ):
            feature_name = f'{indicator.fred_series_id}_{suffix}'
            normalized = normalize_feature_value(getattr(obs, source_attr))
            if normalized is None:
                missing.append(feature_name)
                normalized = 0.0
            features[feature_name] = normalized

    for ticker in PriceObservation.Ticker.values:
        for months in (1, 3, 6):
            feature_name = f'{ticker}_{months}m_return'
            normalized = normalize_feature_value(_price_return(ticker, as_of, months))
            if normalized is None:
                missing.append(feature_name)
                normalized = 0.0
            features[feature_name] = normalized

    features['_missing_feature_count'] = float(len(missing))
    return dict(sorted(features.items()))


def build_feature_metadata(
    *,
    as_of: date,
    feature_vector: dict,
) -> dict:
    world_state = _latest_world_state(as_of)
    source_dates = {}
    if world_state is not None:
        source_dates.update(world_state.explanation.get('source_dates') or {})
        source_dates['world_state'] = world_state.as_of_date.isoformat()
    missing_count = int(feature_vector.get('_missing_feature_count') or 0)
    return {
        'source_dates': source_dates,
        'data_quality': getattr(world_state, 'data_quality', 0.0) if world_state else 0.0,
        'metadata': {
            'missing_feature_count': missing_count,
        },
    }


def hash_feature_vector(feature_vector: dict) -> str:
    normalized = {
        key: normalize_feature_value(value)
        for key, value in feature_vector.items()
    }
    payload = json.dumps(
        normalized,
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def save_feature_snapshot(
    *,
    namespace: str,
    target: str,
    horizon: str,
    model_version: str,
    as_of: date,
    feature_vector: dict,
    source_dates: dict,
    data_quality: float,
    metadata: dict | None = None,
) -> FeatureSnapshot:
    feature_hash = hash_feature_vector(feature_vector)
    with transaction.atomic():
        snapshot, _ = FeatureSnapshot.objects.update_or_create(
            as_of_date=as_of,
            namespace=namespace,
            target=target,
            horizon=horizon,
            model_version=model_version,
            defaults={
                'feature_hash': feature_hash,
                'feature_vector': feature_vector,
                'source_dates': source_dates,
                'data_quality': data_quality or 0.0,
                'metadata': metadata or {},
            },
        )
    return snapshot
