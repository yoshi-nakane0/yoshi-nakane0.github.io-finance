"""経済状態ベクトルを作る。

画面に大量の元データを並べる代わりに、成長・物価・雇用・政策・
金融環境・信用・世界需要・日本・日経バイアスの9軸へ集約する。
"""

from __future__ import annotations

from typing import Dict, Optional

from ..models import WorldStateSnapshot


MODEL_VERSION = 'economic_state_vector_v1'


def _round_score(value: Optional[float], default: float = 50.0) -> float:
    if value is None:
        return default
    return round(min(max(float(value), 0.0), 100.0), 2)


def _label(value: float, low: str, mid: str, high: str) -> str:
    if value >= 60:
        return high
    if value <= 40:
        return low
    return mid


def _axis(value: Optional[float], label: str, *, inverted: bool = False) -> Dict:
    score = _round_score(value)
    display_score = 100.0 - score if inverted else score
    return {
        'score': round(display_score, 2),
        'label': label,
        'raw_score': score,
    }


def build_economic_state_vector(snapshot: WorldStateSnapshot) -> Dict:
    """WorldStateSnapshot から画面・保存用の状態ベクトルを返す。"""
    growth = _round_score(snapshot.growth_score)
    labor = _round_score(snapshot.labor_score)
    inflation = _round_score(snapshot.inflation_score)
    policy = _round_score(snapshot.policy_pressure_score)
    liquidity = _round_score(snapshot.liquidity_score)
    credit = _round_score(snapshot.credit_score)
    market_trend = _round_score(snapshot.market_trend_score)
    market_stress = _round_score(snapshot.market_stress_score)
    recession = _round_score(snapshot.recession_risk_score)
    inflation_risk = _round_score(snapshot.inflation_reacceleration_score)
    financial_stress = _round_score(snapshot.financial_stress_score)

    financial_conditions = round((liquidity + (100.0 - market_stress)) / 2.0, 2)
    global_demand = round((growth + market_trend) / 2.0, 2)
    japan_cycle = _round_score(
        snapshot.feature_vector.get('PA_N225_3m_return') if snapshot.feature_vector else None,
        default=market_trend,
    )
    nikkei_bias_score = round(
        (
            growth * 0.28
            + financial_conditions * 0.22
            + credit * 0.18
            + market_trend * 0.20
            + (100.0 - inflation_risk) * 0.12
        ),
        2,
    )

    axes = {
        'growth_momentum': _axis(
            growth,
            _label(growth, '悪化', '横ばい', '改善'),
        ),
        'inflation_pressure': _axis(
            inflation,
            _label(inflation, '鈍化', '粘着', '再加速警戒'),
        ),
        'labor_slack': _axis(
            labor,
            _label(labor, '悪化', '減速', '強い'),
        ),
        'policy_stance': _axis(
            policy,
            _label(policy, '緩和方向', '中立', '引締め方向'),
        ),
        'financial_conditions': _axis(
            financial_conditions,
            _label(financial_conditions, '逆風', '中立', '追い風'),
        ),
        'credit_stress': _axis(
            financial_stress,
            _label(financial_stress, '低い', '上昇', '危険'),
        ),
        'global_demand': _axis(
            global_demand,
            _label(global_demand, '悪化', '横ばい', '改善'),
        ),
        'japan_cycle': _axis(
            japan_cycle,
            _label(japan_cycle, '後退', '減速', '回復寄り'),
        ),
        'nikkei_macro_bias': _axis(
            nikkei_bias_score,
            _label(nikkei_bias_score, '下落圧力', '中立', '上昇支援'),
        ),
    }
    return {
        'as_of': snapshot.as_of_date.isoformat(),
        'model_version': MODEL_VERSION,
        'axes': axes,
        'risks': {
            'recession_3m_6m': recession / 100.0,
            'inflation_reacceleration_3m_6m': inflation_risk / 100.0,
            'financial_stress_3m_6m': financial_stress / 100.0,
        },
        'quality': {
            'score': _round_score(snapshot.data_quality, default=0.0),
            'source_freshness': snapshot.source_freshness or {},
        },
    }
