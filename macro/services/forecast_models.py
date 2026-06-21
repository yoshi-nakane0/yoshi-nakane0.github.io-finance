"""リターン・マクロ予測モデルの共通処理。"""

from __future__ import annotations

import math
from datetime import date, timedelta
from statistics import mean
from typing import Dict, Iterable, Optional

from dateutil.relativedelta import relativedelta
from django.core.management.base import CommandError
from django.utils import timezone

from ..models import (
    ForecastSnapshot,
    DailyPriceObservation,
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
SHORT_RETURN_MODEL_VERSION = 'short_horizon_return_v1'
SHORT_RETURN_TARGETS = ('N225', 'IXIC')
SHORT_RETURN_DAILY_TICKERS = ('N225', 'IXIC', 'GSPC', 'DJI')
SHORT_RETURN_MACRO_SERIES = ('VIXCLS', 'DGS10')


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


def is_deprecated_monthly_short_return_model(
    model_version: str,
    target: str,
    horizon: str,
) -> bool:
    return (
        model_version == RETURN_MODEL_VERSION
        and target in SHORT_RETURN_TARGETS
        and horizon == '1m'
    )


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


def _month_end(month: date) -> date:
    return month.replace(day=1) + relativedelta(months=1) - timedelta(days=1)


def _daily_prices(ticker: str, as_of: date, lookback_rows: int = 90) -> list[float]:
    rows = (
        DailyPriceObservation.objects
        .filter(ticker=ticker, observation_date__lte=as_of)
        .order_by('-observation_date')
        .values_list('close_price', flat=True)[:lookback_rows + 1]
    )
    return [float(value) for value in reversed(list(rows))]


def _return_pct(prices: list[float], periods: int) -> Optional[float]:
    if len(prices) <= periods or prices[-periods - 1] in (None, 0):
        return None
    return math.log(prices[-1] / prices[-periods - 1]) * 100.0


def _volatility_pct(prices: list[float], periods: int) -> Optional[float]:
    if len(prices) <= periods:
        return None
    returns = []
    recent = prices[-periods - 1:]
    for previous, current in zip(recent, recent[1:]):
        if previous in (None, 0):
            continue
        returns.append(math.log(current / previous))
    if len(returns) < 2:
        return None
    avg = mean(returns)
    variance = mean([(value - avg) ** 2 for value in returns])
    return math.sqrt(variance) * math.sqrt(252) * 100.0


def _drawdown_pct(prices: list[float], periods: int) -> Optional[float]:
    if len(prices) <= 1:
        return None
    recent = prices[-min(len(prices), periods):]
    high = max(recent)
    if high in (None, 0):
        return None
    return (prices[-1] / high - 1.0) * 100.0


def _daily_market_feature_row(target: str, as_of: date) -> dict:
    features = {}
    target_prices = _daily_prices(target, as_of)
    for periods in (5, 20, 60):
        value = _return_pct(target_prices, periods)
        if value is not None:
            features[f'target_return_{periods}d'] = value
    for periods in (20, 60):
        value = _volatility_pct(target_prices, periods)
        if value is not None:
            features[f'target_volatility_{periods}d'] = value
    drawdown = _drawdown_pct(target_prices, 60)
    if drawdown is not None:
        features['target_drawdown_60d'] = drawdown

    for ticker in SHORT_RETURN_DAILY_TICKERS:
        if ticker == target:
            continue
        prices = _daily_prices(ticker, as_of)
        value = _return_pct(prices, 20)
        if value is not None:
            features[f'{ticker}_return_20d'] = value
    return features


def _macro_daily_change_feature_row(as_of: date, window_days: int = 20) -> dict:
    features = {}
    cutoff = as_of - timedelta(days=window_days)
    for series_id in SHORT_RETURN_MACRO_SERIES:
        latest = (
            Observation.objects
            .filter(indicator__fred_series_id=series_id, observation_date__lte=as_of)
            .order_by('-observation_date')
            .values_list('value', flat=True)
            .first()
        )
        previous = (
            Observation.objects
            .filter(indicator__fred_series_id=series_id, observation_date__lte=cutoff)
            .order_by('-observation_date')
            .values_list('value', flat=True)
            .first()
        )
        if latest is None or previous is None:
            continue
        features[f'{series_id}_{window_days}d_change'] = float(latest) - float(previous)
    return features


def _direction_score(direction: str) -> float:
    value = (direction or '').lower()
    if value in {'bullish', 'long', 'up', 'buy'} or '上' in value or '買' in value:
        return 1.0
    if value in {'bearish', 'short', 'down', 'sell'} or '下' in value or '売' in value:
        return -1.0
    return 0.0


def _basecalc_feature_row(as_of: date) -> dict:
    try:
        from basecalc.models import WorldModelPrediction
    except ImportError:
        return {}

    prediction = (
        WorldModelPrediction.objects
        .filter(prediction_timestamp__date__lte=as_of)
        .order_by('-prediction_timestamp', '-created_at')
        .first()
    )
    if prediction is None:
        prediction = (
            WorldModelPrediction.objects
            .filter(prediction_timestamp__isnull=True, created_at__date__lte=as_of)
            .order_by('-created_at')
            .first()
        )
    if prediction is None:
        return {}

    features = {
        'basecalc_direction_score': _direction_score(prediction.direction),
        'basecalc_sentiment_score': prediction.sentiment_score,
        'basecalc_continuation_score': prediction.continuation_score,
        'basecalc_shock_score': prediction.shock_score,
        'basecalc_confidence_score': prediction.confidence_score,
        'basecalc_directional_allowed': 1.0 if prediction.directional_allowed else 0.0,
    }
    for key in (
        'nikkei_technical_score',
        'us_index_confirmation_score',
        'yen_carry_score',
        'futures_trend_score',
    ):
        value = (prediction.features or {}).get(key)
        if value is not None:
            features[f'basecalc_{key}'] = value
    return features


def _short_horizon_feature_row(target: str, as_of: date) -> tuple[dict, list[str]]:
    daily_features = _daily_market_feature_row(target, as_of)
    if not daily_features:
        return {}, []
    macro_features = _macro_daily_change_feature_row(as_of)
    basecalc_features = _basecalc_feature_row(as_of)
    source_modes = ['daily_market']
    if macro_features:
        source_modes.append('daily_macro')
    if basecalc_features:
        source_modes.append('basecalc_optional')
    return {
        **daily_features,
        **macro_features,
        **basecalc_features,
    }, source_modes


def build_short_horizon_feature_matrix(target: str, horizon: str = '1m'):
    horizon_months = parse_horizon_months(horizon)
    if horizon_months != 1 or target not in SHORT_RETURN_TARGETS:
        return {
            'feature_names': [],
            'rows': [],
            'latest': None,
            'warning': 'unsupported short horizon target',
        }

    series = load_monthly_target_series(target)
    if len(series) < horizon_months + 2:
        return {
            'feature_names': [],
            'rows': [],
            'latest': None,
            'warning': 'target series has too few rows',
        }

    rows = []
    source_modes = set()
    latest_feature_map = None
    for month in sorted(series):
        base = series.get(month)
        future = series.get(month + relativedelta(months=horizon_months))
        as_of = _month_end(month)
        features, row_source_modes = _short_horizon_feature_row(target, as_of)
        if features:
            latest_feature_map = features
            source_modes.update(row_source_modes)
        if base in (None, 0) or future is None or not features:
            continue
        rows.append({
            'as_of_date': as_of,
            'feature_map': features,
            'target_value': _target_value(base, future, target),
            'base_value': base,
            'future_value': future,
            'feature_source_modes': row_source_modes,
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
    latest_as_of = _month_end(latest_month)
    latest_feature_map = latest_feature_map or _short_horizon_feature_row(target, latest_as_of)[0]
    latest_values = [
        feature_store.normalize_feature_value(latest_feature_map.get(name)) or 0.0
        for name in feature_names
    ]
    return {
        'feature_names': feature_names,
        'rows': rows,
        'latest': {
            'as_of_date': latest_as_of,
            'x': latest_values,
            'feature_vector': dict(zip(feature_names, latest_values)),
        },
        'metadata': {
            'prediction_kind': 'return_pct',
            'unit': '%',
            'horizon_months': horizon_months,
            'model_version': SHORT_RETURN_MODEL_VERSION,
            'feature_source_modes': sorted(source_modes),
        },
    }


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
            'baseline_models': ['zero', 'last_value', 'moving_average'],
            'baseline': 'best_available_naive_baseline',
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
    confidence = (metadata or {}).get('confidence')
    if confidence is None:
        confidence = round((feature_snapshot.data_quality or 0.0) / 100, 4)
    if prediction_interval is None:
        prediction_interval = {
            'type': 'point_estimate_range',
            'lower': prediction_value,
            'upper': prediction_value,
            'confidence': confidence,
        }
    elif prediction_interval.get('confidence') is None:
        prediction_interval = {
            **prediction_interval,
            'confidence': confidence,
        }
    combined_metadata = {
        **(metadata or {}),
        'confidence': confidence,
        'source_dates': feature_meta['source_dates'],
        'data_vintage': (metadata or {}).get('data_vintage') or 'point_in_time',
        'consensus_status': (metadata or {}).get('consensus_status') or 'missing',
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
