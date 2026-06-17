"""モデル出力を固定テンプレートで説明文に変換する。"""

from __future__ import annotations

from typing import Dict, Iterable


def write_macro_report(
    *,
    state_vector: Dict,
    primary_regime: str,
    regime_probabilities: Dict[str, float],
    risk_probabilities: Dict[str, float],
    scenarios: Iterable[Dict],
) -> Dict:
    axes = state_vector.get('axes') or {}
    growth = (axes.get('growth_momentum') or {}).get('label', '横ばい')
    inflation = (axes.get('inflation_pressure') or {}).get('label', '粘着')
    financial = (axes.get('financial_conditions') or {}).get('label', '中立')
    nikkei = (axes.get('nikkei_macro_bias') or {}).get('label', '中立')
    recession = risk_probabilities.get('recession', 0.0)
    inflation_risk = risk_probabilities.get('inflation_reacceleration', 0.0)
    stress = risk_probabilities.get('financial_stress', 0.0)
    scenario_rows = [
        {
            'name': item['name'],
            'probability': item['probability'],
            'nikkei_bias': item['nikkei_bias'],
            'key_drivers': item['key_drivers'],
            'invalidation_triggers': item['invalidation_triggers'],
        }
        for item in scenarios
    ]
    return {
        'headline': f'景気は{growth}基調。物価は{inflation}、金融環境は{financial}。',
        'judgment': (
            f'3〜6か月の主シナリオは {primary_regime}。'
            f' 後退確率は{recession * 100:.0f}%、'
            f'物価再加速確率は{inflation_risk * 100:.0f}%、'
            f'金融ストレス確率は{stress * 100:.0f}%。'
        ),
        'nikkei_implication': (
            f'日経先物へのmacroバイアスは{nikkei}。'
            '今日〜数週間の売買判断はbasecalcを優先する。'
        ),
        'regime_probabilities': regime_probabilities,
        'risk_probabilities': risk_probabilities,
        'state_vector': state_vector,
        'scenarios': scenario_rows,
    }
