"""World Model の状態ベクトルを作成・保存する。"""

from __future__ import annotations

from calendar import monthrange
from datetime import date
from typing import Dict, Optional

from dateutil.relativedelta import relativedelta
from django.db import transaction
from django.utils import timezone

from ..models import (
    Observation,
    PolicyExpectationSnapshot,
    PriceObservation,
    WorldStateSnapshot,
)
from . import regime
from .crash_alert import compute_crash_alert


MODEL_VERSION = 'world_state_v1'

STATE_SCORE_FIELDS = (
    'growth_score',
    'labor_score',
    'inflation_score',
    'policy_pressure_score',
    'liquidity_score',
    'credit_score',
    'risk_appetite_score',
    'market_trend_score',
    'external_shock_score',
    'market_stress_score',
    'recession_risk_score',
    'inflation_reacceleration_score',
    'financial_stress_score',
)


def _clamp(value: Optional[float], low: float = 0.0, high: float = 100.0) -> Optional[float]:
    if value is None:
        return None
    return round(min(max(float(value), low), high), 2)


def _from_signed_score(value: Optional[float]) -> Optional[float]:
    """既存レジームの -100〜100 に近いスコアを 0〜100 へ変換する。"""
    if value is None:
        return None
    return _clamp((float(value) + 100.0) / 2.0)


def _risk_pct(probabilities: Dict, key: str) -> float:
    value = (probabilities or {}).get(key)
    if value is None:
        return 50.0
    if 0 <= value <= 1:
        return round(value * 100.0, 2)
    return _clamp(value) or 0.0


def _metric_score(
    value: Optional[float],
    *,
    low: float,
    high: float,
    reverse: bool = False,
) -> Optional[float]:
    if value is None:
        return None
    if high == low:
        return 50.0
    pct = (float(value) - low) / (high - low) * 100.0
    if reverse:
        pct = 100.0 - pct
    return _clamp(pct)


def _latest_observation(series_id: str, as_of: Optional[date]) -> Optional[Observation]:
    qs = Observation.objects.filter(indicator__fred_series_id=series_id)
    if as_of is not None:
        qs = qs.filter(observation_date__lte=as_of)
    return qs.select_related('indicator').order_by('-observation_date').first()


def _latest_price(ticker: str, as_of: Optional[date]) -> Optional[PriceObservation]:
    qs = PriceObservation.objects.filter(ticker=ticker)
    if as_of is not None:
        qs = qs.filter(observation_month__lte=as_of.replace(day=1))
    return qs.order_by('-observation_month').first()


def _source_dates(as_of: Optional[date]) -> Dict[str, str]:
    sources = {}
    for series_id in (
        'INDPRO',
        'UNRATE',
        'PCEPILFE',
        'T10Y2Y',
        'BAMLH0A0HYM2',
        'VIXCLS',
        'DEXJPUS',
    ):
        obs = _latest_observation(series_id, as_of)
        if obs is not None:
            sources[series_id] = obs.observation_date.isoformat()
    for ticker in PriceObservation.Ticker.values:
        price = _latest_price(ticker, as_of)
        if price is not None:
            sources[f'PA_{ticker}'] = price.observation_month.isoformat()
    return sources


def _price_drawdown_pct(ticker: str, as_of: Optional[date], months: int = 7) -> Optional[float]:
    qs = PriceObservation.objects.filter(ticker=ticker)
    if as_of is not None:
        qs = qs.filter(observation_month__lte=as_of.replace(day=1))
    rows = list(qs.order_by('-observation_month')[:months])
    if len(rows) < 2:
        return None
    rows.reverse()
    peak = rows[0].close_price
    max_drawdown = 0.0
    for row in rows[1:]:
        if row.close_price > peak:
            peak = row.close_price
        if peak:
            drawdown = (row.close_price - peak) / peak * 100.0
            max_drawdown = min(max_drawdown, drawdown)
    return round(abs(max_drawdown), 2)


def _monthly_return_pct(ticker: str, as_of: Optional[date], months: int = 1) -> Optional[float]:
    latest = _latest_price(ticker, as_of)
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


def _average(values) -> Optional[float]:
    valid = [float(value) for value in values if value is not None]
    if not valid:
        return None
    return round(sum(valid) / len(valid), 2)


def _build_feature_vector(
    metrics: Dict,
    world_scores: Dict,
    *,
    as_of: Optional[date],
) -> Dict[str, float]:
    feature_vector = {
        'INDPRO_yoy': metrics.get('indpro_yoy'),
        'UNRATE_value': metrics.get('unrate_value'),
        'UNRATE_6m_change': metrics.get('unrate_6m_change'),
        'PCEPILFE_yoy': metrics.get('core_pce_yoy'),
        'T10Y2Y_value': metrics.get('yield_curve_2y10y'),
        'BAMLH0A0HYM2_value': metrics.get('hy_spread'),
        'VIXCLS_value': metrics.get('vix'),
        'PA_GSPC_DD200': _price_drawdown_pct(PriceObservation.Ticker.SP500, as_of),
    }
    for ticker in PriceObservation.Ticker.values:
        feature_vector[f'PA_{ticker}_1m_return'] = _monthly_return_pct(ticker, as_of, 1)
        feature_vector[f'PA_{ticker}_3m_return'] = _monthly_return_pct(ticker, as_of, 3)
    for field in STATE_SCORE_FIELDS:
        key = f'world_{field}'
        feature_vector[key] = world_scores.get(field)
    return {
        key: round(float(value), 6)
        for key, value in feature_vector.items()
        if value is not None
    }


def _driver_labels(metrics: Dict, scores: Dict) -> tuple[list[str], list[str]]:
    positive = []
    negative = []
    if metrics.get('unrate_value') is not None and metrics['unrate_value'] <= 4.5:
        positive.append('失業率が低い')
    if metrics.get('hy_spread') is not None and metrics['hy_spread'] <= 4.5:
        positive.append('HYスプレッドが低い')
    if metrics.get('indpro_yoy') is not None and metrics['indpro_yoy'] > 1.0:
        positive.append('鉱工業生産が前年比で増加')
    if metrics.get('core_pce_yoy') is not None and metrics['core_pce_yoy'] >= 3.0:
        negative.append('Core PCEが高い')
    if metrics.get('yield_curve_2y10y') is not None and metrics['yield_curve_2y10y'] < 0:
        negative.append('イールドカーブが逆転')
    if scores.get('market_stress_score') is not None and scores['market_stress_score'] >= 60:
        negative.append('市場ストレスが高い')
    return positive[:4], negative[:4]


def _summary(scores: Dict) -> str:
    growth = scores.get('growth_score')
    labor = scores.get('labor_score')
    inflation = scores.get('inflation_score')
    parts = []
    if growth is None:
        parts.append('成長は判定保留')
    elif growth >= 60:
        parts.append('成長は強め')
    elif growth <= 40:
        parts.append('成長は弱め')
    else:
        parts.append('成長は中立')
    if labor is not None:
        parts.append('雇用は強い' if labor >= 60 else '雇用は注意' if labor <= 40 else '雇用は中立')
    if inflation is not None and inflation >= 60:
        parts.append('物価再加速に注意')
    elif inflation is not None:
        parts.append('物価は比較的安定')
    return '、'.join(parts) + '。'


def build_world_state_assessment_from_metrics(
    metrics: Dict,
    *,
    as_of: Optional[date] = None,
    crash_alert_payload: Optional[Dict] = None,
) -> Dict:
    """任意のメトリクスから World State 評価を作る。シナリオ分析でも使う。"""
    assessment = regime.build_regime_assessment_from_metrics(metrics, as_of=as_of)
    scores = assessment.get('scores') or {}
    risks = assessment.get('risk_probabilities') or {}
    crash = crash_alert_payload or compute_crash_alert(as_of=as_of)
    category_summary = {
        item.get('category'): item.get('avg_score')
        for item in crash.get('category_summary', [])
    }

    market_trend = _metric_score(
        _average([
            _monthly_return_pct(PriceObservation.Ticker.SP500, as_of, 3),
            _monthly_return_pct(PriceObservation.Ticker.NASDAQ, as_of, 3),
            _monthly_return_pct(PriceObservation.Ticker.NYDOW, as_of, 3),
            _monthly_return_pct(PriceObservation.Ticker.NIKKEI, as_of, 3),
        ]),
        low=-12.0,
        high=12.0,
    )
    policy_expectation_score = _policy_expectation_score()
    policy_score_inputs = [
        _metric_score(metrics.get('core_pce_yoy'), low=1.5, high=4.5),
        _metric_score(metrics.get('breakeven_5y'), low=1.5, high=3.5),
        _metric_score(metrics.get('yield_curve_2y10y'), low=-1.5, high=1.5),
    ]
    if policy_expectation_score is not None:
        policy_score_inputs.append(policy_expectation_score)

    world_scores = {
        'growth_score': _from_signed_score(scores.get('growth')),
        'labor_score': _from_signed_score(scores.get('labor')),
        'inflation_score': _metric_score(metrics.get('core_pce_yoy'), low=1.5, high=4.5),
        'policy_pressure_score': _average(policy_score_inputs),
        'liquidity_score': _clamp(100.0 - (category_summary.get('credit_liquidity') or 50.0)),
        'credit_score': _metric_score(metrics.get('hy_spread'), low=8.0, high=2.5),
        'risk_appetite_score': _clamp(100.0 - (category_summary.get('volatility_sentiment') or 50.0)),
        'market_trend_score': market_trend,
        'external_shock_score': _metric_score(metrics.get('vix'), low=12.0, high=40.0),
        'market_stress_score': crash.get('market_stress_score'),
        'recession_risk_score': _risk_pct(risks, 'recession'),
        'inflation_reacceleration_score': _risk_pct(risks, 'inflation_reacceleration'),
        'financial_stress_score': _risk_pct(risks, 'financial_stress'),
    }
    world_scores = {key: _clamp(value) for key, value in world_scores.items()}
    source_dates = _source_dates(as_of)
    positive, negative = _driver_labels(metrics, world_scores)
    warnings = list(assessment.get('warnings') or [])
    if not source_dates:
        warnings.append('World State に使える観測値がまだありません。')
    feature_vector = _build_feature_vector(metrics, world_scores, as_of=as_of)
    data_quality = _average([
        assessment.get('data_quality'),
        crash.get('data_quality_pct'),
        min(len(source_dates) / 8 * 100, 100),
    ]) or 0.0

    return {
        **world_scores,
        'as_of_date': as_of or timezone.localdate(),
        'data_quality': _clamp(data_quality) or 0.0,
        'source_freshness': source_dates,
        'feature_vector': feature_vector,
        'explanation': {
            'summary': _summary(world_scores),
            'positive_drivers': positive,
            'negative_drivers': negative,
            'source_dates': source_dates,
            'regime_label': assessment.get('regime_label'),
            'inflation_flag': assessment.get('inflation_flag'),
            'policy_expectation': _latest_policy_expectation_payload(),
        },
        'warnings': warnings,
        'model_version': MODEL_VERSION,
    }


def build_world_state_assessment(as_of: Optional[date] = None) -> dict:
    metrics = regime.collect_key_metrics(as_of=as_of)
    return build_world_state_assessment_from_metrics(metrics, as_of=as_of)


def _policy_expectation_score() -> Optional[float]:
    snapshot = PolicyExpectationSnapshot.objects.order_by('-as_of').first()
    if snapshot is None:
        return None
    if snapshot.policy_bias in ('hawkish_headwind', 'rate_up_headwind'):
        return 85.0
    if snapshot.policy_bias in ('inflation_headwind', 'rates_volatility_headwind'):
        return 75.0
    if snapshot.policy_bias == 'dovish_tailwind':
        return 25.0
    return 50.0


def _latest_policy_expectation_payload() -> dict:
    snapshot = PolicyExpectationSnapshot.objects.order_by('-as_of').first()
    if snapshot is None:
        return {}
    return {
        'policy_bias': snapshot.policy_bias,
        'data_quality': snapshot.data_quality,
        'summary': (snapshot.payload or {}).get('summary', ''),
    }


def compute_current_world_state(
    cadence: str = WorldStateSnapshot.Cadence.DAILY,
    *,
    as_of: Optional[date] = None,
) -> WorldStateSnapshot:
    target_date = as_of or timezone.localdate()
    assessment = build_world_state_assessment(as_of=target_date)
    defaults = {
        field: assessment.get(field)
        for field in STATE_SCORE_FIELDS
    }
    defaults.update({
        'cadence': cadence,
        'data_quality': assessment.get('data_quality') or 0.0,
        'source_freshness': assessment.get('source_freshness') or {},
        'feature_vector': assessment.get('feature_vector') or {},
        'explanation': assessment.get('explanation') or {},
        'warnings': assessment.get('warnings') or [],
        'model_version': assessment.get('model_version') or MODEL_VERSION,
    })
    with transaction.atomic():
        snapshot, _ = WorldStateSnapshot.objects.update_or_create(
            as_of_date=target_date,
            defaults=defaults,
        )
    return snapshot


def _month_end(value: date) -> date:
    return value.replace(day=monthrange(value.year, value.month)[1])


def backfill_world_states(
    years: int = 20,
    cadence: str = WorldStateSnapshot.Cadence.MONTHLY,
    *,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> dict:
    end_date = end or timezone.localdate()
    start_date = start or (end_date - relativedelta(years=years)).replace(day=1)
    current = start_date.replace(day=1)
    processed = 0
    success = 0
    failed = 0
    failures = []
    while current <= end_date:
        as_of = _month_end(current)
        if as_of > end_date:
            as_of = end_date
        processed += 1
        try:
            snapshot = compute_current_world_state(cadence=cadence, as_of=as_of)
            if not snapshot.feature_vector:
                snapshot.warnings = [
                    *(snapshot.warnings or []),
                    'この月は特徴量が不足しています。',
                ]
                snapshot.save(update_fields=['warnings'])
            success += 1
        except Exception as exc:
            failed += 1
            failures.append({'as_of_date': as_of.isoformat(), 'error': str(exc)})
        current = current + relativedelta(months=1)
    return {
        'processed_count': processed,
        'success_count': success,
        'failed_count': failed,
        'failures': failures,
    }
