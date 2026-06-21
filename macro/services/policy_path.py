"""政策スタンスの要約。"""

from __future__ import annotations

from .policy_expectation import build_policy_expectation_context


def summarize_policy_path() -> dict:
    context = build_policy_expectation_context()
    return {
        'label': context.get('label') or context.get('policy_bias') or 'neutral',
        'summary': context.get('summary') or '',
        'data_quality_display': context.get('data_quality_display') or '—',
    }


def build_policy_reaction_function(
    *,
    inflation_reacceleration: float,
    recession_probability: float,
    labor_score: float,
    usd_jpy_pressure: str = 'unknown',
) -> dict:
    fed_conditions = []
    if inflation_reacceleration >= 0.7 and labor_score >= 55:
        fed_bias = 'hold_or_hawkish'
        fed_conditions.append('インフレ再加速と雇用の底堅さで利下げしにくい。')
    elif recession_probability >= 0.35 or labor_score <= 40:
        fed_bias = 'cut_watch'
        fed_conditions.append('景気後退または雇用悪化で利下げを検討しやすい。')
    else:
        fed_bias = 'hold'
        fed_conditions.append('インフレと雇用の追加データ待ち。')

    boj_conditions = []
    if usd_jpy_pressure == 'yen_weakness' and inflation_reacceleration >= 0.55:
        boj_bias = 'hike_watch'
        boj_conditions.append('円安と物価圧力で日銀の利上げ警戒が残る。')
    elif recession_probability >= 0.35:
        boj_bias = 'hold'
        boj_conditions.append('外需悪化時は日銀が慎重になりやすい。')
    else:
        boj_bias = 'gradual_normalization'
        boj_conditions.append('賃金と物価を確認しながら正常化を探る。')

    return {
        'fed_next_move_bias': fed_bias,
        'fed_reaction_conditions': fed_conditions,
        'boj_next_move_bias': boj_bias,
        'boj_reaction_conditions': boj_conditions,
        'market_pricing_gap': '市場織り込みとの差は金利、為替、FedWatchで確認。',
    }
