"""リターン・マクロ予測モデルの共通処理。"""

from __future__ import annotations

import math
from datetime import date
from statistics import mean
from typing import Dict, Iterable, Optional

from dateutil.relativedelta import relativedelta
from django.core.management.base import CommandError
from django.utils import timezone

from ..models import (
    ForecastSnapshot,
    Indicator,
    Observation,
    PriceObservation,
    VintageObservation,
    WorldStateSnapshot,
)
from . import feature_store


RETURN_TARGETS = ('GSPC', 'IXIC', 'DJI', 'N225')
MACRO_TARGETS = ('DGS10', 'T10Y2Y', 'PCEPILFE', 'UNRATE', 'INDPRO', 'DEXJPUS')
HORIZONS = ('1m', '3m', '6m')
RETURN_MODEL_VERSION = 'return_lightgbm_v2'
MACRO_MODEL_VERSION = 'macro_forecast_lightgbm_v1'


def parse_horizon_months(horizon: str) -> int:
    if not horizon.endswith('m'):
        raise ValueError(f'unsupported horizon: {horizon}')
    return int(horizon[:-1])


def load_monthly_target_series(target: str) -> dict[date, float]:
    if target in RETURN_TARGETS:
        rows = (
            PriceObservation.objects
            .filter(ticker=target)
            .order_by('observation_month')
            .values_list('observation_month', 'close_price')
        )
        return {month.replace(day=1): float(value) for month, value in rows}

    rows = (
        Observation.objects
        .filter(indicator__fred_series_id=target)
        .order_by('observation_date')
        .values_list('observation_date', 'value')
    )
    monthly = {}
    for obs_date, value in rows:
        monthly[obs_date.replace(day=1)] = float(value)
    return monthly


def _prediction_kind(target: str) -> str:
    if target in RETURN_TARGETS:
        return 'return_pct'
    if target in ('PCEPILFE', 'INDPRO'):
        return 'level_change'
    return 'level_change'


def _unit(target: str) -> str:
    if target in RETURN_TARGETS:
        return '%'
    if target in ('DGS10', 'T10Y2Y', 'UNRATE'):
        return '%pt'
    if target == 'DEXJPUS':
        return 'JPY'
    return 'index'


def _target_value(base: float, future: float, target: str) -> float:
    if base in (None, 0) or future is None:
        raise ValueError('base and future values are required')
    if target in RETURN_TARGETS:
        return math.log(future / base) * 100.0
    return future - base


def _world_feature_row(as_of: date) -> dict:
    snapshot = (
        WorldStateSnapshot.objects
        .filter(as_of_date__lte=as_of)
        .order_by('-as_of_date')
        .first()
    )
    if snapshot is not None and snapshot.feature_vector:
        features = {
            key: feature_store.normalize_feature_value(value) or 0.0
            for key, value in snapshot.feature_vector.items()
        }
        for field in (
            'growth_score',
            'labor_score',
            'inflation_score',
            'policy_pressure_score',
            'credit_score',
            'liquidity_score',
            'risk_appetite_score',
            'market_trend_score',
            'market_stress_score',
            'data_quality',
        ):
            features[f'world_{field}'] = feature_store.normalize_feature_value(
                getattr(snapshot, field, None),
            ) or 0.0
        return features

    indicators = Indicator.objects.filter(
        is_active=True,
        importance__in=[Indicator.Importance.A, Indicator.Importance.B],
    )
    features = {}
    for indicator in indicators:
        obs = (
            Observation.objects
            .filter(indicator=indicator, observation_date__lte=as_of)
            .order_by('-observation_date')
            .first()
        )
        if obs is None:
            continue
        features[f'{indicator.fred_series_id}_expanding_z'] = (
            feature_store.normalize_feature_value(obs.expanding_z_score) or 0.0
        )
        features[f'{indicator.fred_series_id}_rolling_5y_z'] = (
            feature_store.normalize_feature_value(obs.rolling_5y_z_score) or 0.0
        )
    return features


def _vintage_feature_row(as_of: date) -> dict:
    """保存済みビンテージから、その時点で利用可能だった特徴量を作る。"""
    rows = (
        VintageObservation.objects
        .filter(
            indicator__is_active=True,
            indicator__importance__in=[Indicator.Importance.A, Indicator.Importance.B],
            observation_date__lte=as_of,
            realtime_start__lte=as_of,
        )
        .select_related('indicator')
        .order_by(
            'indicator__fred_series_id',
            '-observation_date',
            '-realtime_start',
        )
    )
    latest_by_series = {}
    for row in rows:
        series_id = row.indicator.fred_series_id
        if series_id in latest_by_series:
            continue
        latest_by_series[series_id] = row
    if not latest_by_series:
        return {}
    return {
        f'{series_id}_vintage_value': feature_store.normalize_feature_value(row.value) or 0.0
        for series_id, row in latest_by_series.items()
    }


def _historical_feature_row(as_of: date) -> tuple[dict, str]:
    vintage_features = _vintage_feature_row(as_of)
    if vintage_features:
        return vintage_features, 'vintage_point_in_time'
    return _world_feature_row(as_of), 'revised_observation_fallback'


def _matrix_from_feature_maps(feature_maps: Iterable[dict]) -> tuple[list[str], list[list[float]]]:
    maps = list(feature_maps)
    names = sorted({key for item in maps for key in item.keys()})
    matrix = [
        [feature_store.normalize_feature_value(item.get(name)) or 0.0 for name in names]
        for item in maps
    ]
    return names, matrix


def build_monthly_feature_matrix(namespace: str, target: str, horizon: str):
    del namespace
    horizon_months = parse_horizon_months(horizon)
    series = load_monthly_target_series(target)
    if len(series) < horizon_months + 12:
        return {
            'feature_names': [],
            'rows': [],
            'latest': None,
            'warning': 'target series has too few rows',
        }

    rows = []
    latest_feature_map = None
    for month in sorted(series):
        base = series.get(month)
        future = series.get(month + relativedelta(months=horizon_months))
        features, source_mode = _historical_feature_row(month)
        if features:
            latest_feature_map = features
        if base in (None, 0) or future is None or not features:
            continue
        rows.append({
            'as_of_date': month,
            'feature_map': features,
            'target_value': _target_value(base, future, target),
            'base_value': base,
            'future_value': future,
            'feature_source_mode': source_mode,
        })

    if not rows:
        return {
            'feature_names': [],
            'rows': [],
            'latest': None,
            'warning': 'feature matrix is empty',
        }

    feature_names, matrix = _matrix_from_feature_maps(row['feature_map'] for row in rows)
    for row, values in zip(rows, matrix):
        row['x'] = values
        row.pop('feature_map', None)
    latest_month = max(series)
    latest_feature_map = latest_feature_map or _world_feature_row(latest_month)
    latest_values = [
        feature_store.normalize_feature_value(latest_feature_map.get(name)) or 0.0
        for name in feature_names
    ]
    return {
        'feature_names': feature_names,
        'rows': rows,
        'latest': {
            'as_of_date': latest_month,
            'x': latest_values,
            'feature_vector': dict(zip(feature_names, latest_values)),
        },
        'metadata': {
            'prediction_kind': _prediction_kind(target),
            'unit': _unit(target),
            'horizon_months': horizon_months,
            'feature_source_modes': sorted({
                row.get('feature_source_mode', 'unknown')
                for row in rows
            }),
        },
    }


def train_lightgbm_regressor(x_train, y_train, x_valid, y_valid) -> dict:
    try:
        import lightgbm as lgb
        import numpy as np
    except ImportError as exc:
        raise CommandError(
            f'学習用依存が見つかりません: {exc}. '
            '`pip install -r requirements-train.txt` を実行してください。'
        )

    if len(y_train) < 20:
        raise CommandError('学習サンプルが不足しています。')
    train_set = lgb.Dataset(x_train, label=y_train)
    params = {
        'objective': 'regression',
        'metric': 'mae',
        'learning_rate': 0.05,
        'num_leaves': 16,
        'max_depth': 4,
        'min_data_in_leaf': 8,
        'feature_fraction': 0.85,
        'bagging_fraction': 0.85,
        'bagging_freq': 3,
        'lambda_l2': 1.0,
        'verbose': -1,
    }
    if len(y_valid):
        valid_set = lgb.Dataset(x_valid, label=y_valid, reference=train_set)
        booster = lgb.train(
            params,
            train_set,
            num_boost_round=200,
            valid_sets=[valid_set],
            callbacks=[
                lgb.early_stopping(stopping_rounds=20),
                lgb.log_evaluation(0),
            ],
        )
        valid_pred = booster.predict(x_valid)
        mae = float(np.mean(np.abs(valid_pred - y_valid)))
        rmse = float(np.sqrt(np.mean((valid_pred - y_valid) ** 2)))
    else:
        booster = lgb.train(params, train_set, num_boost_round=200)
        mae = None
        rmse = None
    return {
        'booster': booster,
        'validation_mae': mae,
        'validation_rmse': rmse,
    }


def walk_forward_validate(rows, *, min_train: int = 36) -> dict:
    if len(rows) <= min_train:
        return {
            'sample_count': 0,
            'metrics': {},
            'rows': [],
            'warnings': ['walk-forward 検証に必要なサンプルが不足しています。'],
        }
    rows = sorted(rows, key=lambda row: row['as_of_date'])
    validation_rows = []
    warnings = []
    try:
        import lightgbm as lgb
        import numpy as np
    except ImportError as exc:
        warnings.append(
            f'LightGBM がないため、直近平均の参考検証に切り替えました: {exc}'
        )
        for idx in range(min_train, len(rows)):
            train_values = [row['target_value'] for row in rows[:idx]]
            actual = rows[idx]['target_value']
            prediction = mean(train_values[-min_train:])
            validation_rows.append({
                'as_of_date': rows[idx]['as_of_date'].isoformat(),
                'prediction': prediction,
                'actual': actual,
                'error': actual - prediction,
                'training_end': rows[idx - 1]['as_of_date'].isoformat(),
                'training_samples': idx,
                'model_type': 'rolling_mean_fallback',
            })
        method = 'rolling_mean_fallback'
    else:
        params = {
            'objective': 'regression',
            'metric': 'mae',
            'learning_rate': 0.05,
            'num_leaves': 12,
            'max_depth': 4,
            'min_data_in_leaf': 6,
            'feature_fraction': 0.9,
            'bagging_fraction': 0.9,
            'bagging_freq': 3,
            'lambda_l2': 1.0,
            'verbose': -1,
            'seed': 42,
        }
        for idx in range(min_train, len(rows)):
            train_rows = rows[:idx]
            x_train = np.array([row['x'] for row in train_rows], dtype='float64')
            y_train = np.array(
                [row['target_value'] for row in train_rows],
                dtype='float64',
            )
            train_set = lgb.Dataset(x_train, label=y_train, free_raw_data=True)
            booster = lgb.train(
                params,
                train_set,
                num_boost_round=80,
                callbacks=[lgb.log_evaluation(0)],
            )
            actual = rows[idx]['target_value']
            prediction = float(
                booster.predict(
                    np.array([rows[idx]['x']], dtype='float64'),
                    num_iteration=booster.best_iteration,
                )[0]
            )
            validation_rows.append({
                'as_of_date': rows[idx]['as_of_date'].isoformat(),
                'prediction': prediction,
                'actual': actual,
                'error': actual - prediction,
                'training_end': rows[idx - 1]['as_of_date'].isoformat(),
                'training_samples': idx,
                'model_type': 'lightgbm_refit',
            })
        method = 'lightgbm_refit_walk_forward'
    abs_errors = [abs(row['error']) for row in validation_rows]
    squared_errors = [row['error'] ** 2 for row in validation_rows]
    baseline_abs_errors = [abs(row['actual']) for row in validation_rows]
    direction_hits = [
        1
        for row in validation_rows
        if (row['prediction'] >= 0 and row['actual'] >= 0)
        or (row['prediction'] < 0 and row['actual'] < 0)
    ]
    sample_count = len(validation_rows)
    model_mae = mean(abs_errors) if abs_errors else None
    baseline_mae = mean(baseline_abs_errors) if baseline_abs_errors else None
    skill_score = (
        1 - model_mae / baseline_mae
        if model_mae is not None and baseline_mae not in (None, 0)
        else None
    )
    return {
        'sample_count': sample_count,
        'metrics': {
            'mae': model_mae,
            'rmse': math.sqrt(mean(squared_errors)) if squared_errors else None,
            'baseline_mae': baseline_mae,
            'skill_score': skill_score,
            'direction_accuracy': (
                len(direction_hits) / sample_count if sample_count else None
            ),
            'model_refit_count': sample_count if method == 'lightgbm_refit_walk_forward' else 0,
            'validation_method': method,
        },
        'rows': validation_rows,
        'warnings': warnings,
    }


def save_forecast_snapshot(
    *,
    namespace: str,
    model_version: str,
    target: str,
    horizon: str,
    prediction_value: float,
    prediction_interval: Optional[dict] = None,
    feature_vector: Optional[dict] = None,
    as_of: Optional[date] = None,
    metadata: Optional[dict] = None,
) -> ForecastSnapshot:
    as_of = as_of or timezone.localdate()
    if feature_vector is None:
        feature_vector = feature_store.build_feature_vector(
            namespace=namespace,
            target=target,
            horizon=horizon,
            as_of=as_of,
        )
    feature_meta = feature_store.build_feature_metadata(
        as_of=as_of,
        feature_vector=feature_vector,
    )
    feature_snapshot = feature_store.save_feature_snapshot(
        namespace=namespace,
        target=target,
        horizon=horizon,
        model_version=model_version,
        as_of=as_of,
        feature_vector=feature_vector,
        source_dates=feature_meta['source_dates'],
        data_quality=feature_meta['data_quality'],
        metadata=feature_meta['metadata'],
    )
    combined_metadata = {
        **(metadata or {}),
        'feature_snapshot_id': feature_snapshot.id,
        'feature_snapshot_hash': feature_snapshot.feature_hash,
        'feature_namespace': namespace,
        'data_quality': feature_snapshot.data_quality,
        'missing_features': feature_snapshot.metadata.get('missing_features', []),
        'missing_feature_count': feature_snapshot.metadata.get('missing_feature_count', 0),
    }
    snapshot, _ = ForecastSnapshot.objects.update_or_create(
        as_of_date=as_of,
        model_version=model_version,
        target=target,
        horizon=horizon,
        defaults={
            'prediction_value': prediction_value,
            'prediction_interval': prediction_interval,
            'features_hash': feature_snapshot.feature_hash,
            'metadata': combined_metadata,
        },
    )
    return snapshot
