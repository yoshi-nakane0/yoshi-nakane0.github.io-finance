"""House Viewと市場価格の織り込み差を要約する。"""

from __future__ import annotations


def _axis_score(state_vector: dict, key: str) -> float:
    axis = ((state_vector.get('axes') or {}).get(key) or {})
    value = axis.get('score')
    if value is None:
        return 50.0
    return float(value)


def build_market_pricing_gap(*, state_vector: dict, market_inputs: dict | None = None) -> dict:
    market_inputs = market_inputs or {}
    growth_score = _axis_score(state_vector, 'growth_momentum')
    inflation_score = _axis_score(state_vector, 'inflation_pressure')
    financial_score = _axis_score(state_vector, 'financial_conditions')
    nikkei_score = _axis_score(state_vector, 'nikkei_macro_bias')
    dgs10 = market_inputs.get('dgs10')
    hy_spread = market_inputs.get('hy_spread')
    usd_jpy_trend = market_inputs.get('usd_jpy_trend') or 'unknown'

    if inflation_score >= 70 and dgs10 is not None and dgs10 >= 4.5:
        rates = 'インフレ再加速を十分警戒'
    elif inflation_score >= 70:
        rates = 'インフレ再加速を織り込み不足'
    else:
        rates = '金利市場は中立'

    if hy_spread is not None and hy_spread >= 5.0:
        credit = '信用市場は景気悪化を警戒'
    elif growth_score >= 60 and financial_score <= 45:
        credit = '景気悪化警戒は限定的'
    else:
        credit = '信用市場は中立'

    if usd_jpy_trend == 'yen_weakness':
        fx = '円安が日経を支えやすい'
    elif usd_jpy_trend == 'yen_strength':
        fx = '円高が日経の重荷'
    else:
        fx = '為替の織り込みは中立'

    if nikkei_score >= 60:
        equities = '株式はマクロ追い風を一部織り込み'
    elif nikkei_score <= 45:
        equities = '株式はマクロ逆風を警戒'
    else:
        equities = '株式は方向感を確認中'

    return {
        'rates': rates,
        'fx': fx,
        'equities': equities,
        'credit': credit,
        'macro_view_gap': {
            'growth_score': growth_score,
            'inflation_score': inflation_score,
            'financial_conditions_score': financial_score,
            'nikkei_macro_bias_score': nikkei_score,
        },
        'summary': f'macro viewと市場価格のズレ: 金利={rates}、信用={credit}、為替={fx}、株式={equities}。',
    }
