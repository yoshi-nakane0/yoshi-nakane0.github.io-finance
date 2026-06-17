"""基本・上振れ・下振れシナリオを作る。"""

from __future__ import annotations

from typing import Dict, Iterable, List

from ..models import MacroScenario


BASE_INVALIDATION_TRIGGERS = [
    '米失業率が3か月連続で上昇',
    'クレジットスプレッドが急拡大',
    'コアインフレが2か月連続で再加速',
    '米10年金利が急上昇し株式バリュエーションを圧迫',
    'ドル円が急変し日経先物の外需株に逆風',
]


def _normalize(probabilities: Dict[str, float]) -> Dict[str, float]:
    total = sum(max(value, 0.0) for value in probabilities.values())
    if total <= 0:
        return {'baseline': 0.60, 'upside': 0.25, 'downside': 0.15}
    normalized = {
        key: round(max(value, 0.0) / total, 4)
        for key, value in probabilities.items()
    }
    drift = round(1.0 - sum(normalized.values()), 4)
    normalized['baseline'] = round(normalized['baseline'] + drift, 4)
    return normalized


def build_macro_scenarios(
    *,
    state_vector: Dict,
    regime_probabilities: Dict[str, float],
    risk_probabilities: Dict[str, float],
) -> List[Dict]:
    """モデル出力だけを入力にして3シナリオを返す。"""
    expansion = regime_probabilities.get('expansion', 0.0)
    slowdown = regime_probabilities.get('slowdown', 0.0)
    contraction = regime_probabilities.get('contraction', 0.0)
    recovery = regime_probabilities.get('recovery', 0.0)
    inflation_risk = risk_probabilities.get('inflation_reacceleration', 0.0)
    stress_risk = risk_probabilities.get('financial_stress', 0.0)

    scenario_probs = _normalize({
        'baseline': 0.45 + expansion * 0.25 + slowdown * 0.15,
        'upside': 0.12 + recovery * 0.35 + expansion * 0.18,
        'downside': 0.10 + contraction * 0.45 + stress_risk * 0.20,
    })
    axes = state_vector.get('axes') or {}
    growth_label = (axes.get('growth_momentum') or {}).get('label', '横ばい')
    inflation_label = (axes.get('inflation_pressure') or {}).get('label', '粘着')
    policy_label = (axes.get('policy_stance') or {}).get('label', '中立')
    nikkei_label = (axes.get('nikkei_macro_bias') or {}).get('label', '中立')
    baseline_bias = (
        MacroScenario.NikkeiBias.LONG
        if nikkei_label == '上昇支援'
        else MacroScenario.NikkeiBias.SHORT
        if nikkei_label == '下落圧力'
        else MacroScenario.NikkeiBias.NEUTRAL
    )

    return [
        {
            'name': MacroScenario.Name.BASELINE,
            'probability': scenario_probs['baseline'],
            'growth_view': f'景気は{growth_label}を基本に、後退確率を確認しながら判断する。',
            'inflation_view': f'物価は{inflation_label}。再加速確率は{inflation_risk * 100:.0f}%として扱う。',
            'policy_view': f'政策スタンスは{policy_label}。金利上昇時は株式の上値を抑えやすい。',
            'market_view': '短期判断はbasecalcを優先し、macroは3〜6か月の環境認識として使う。',
            'nikkei_bias': baseline_bias,
            'key_drivers': ['成長モメンタム', '物価圧力', '金融環境', '信用ストレス'],
            'invalidation_triggers': BASE_INVALIDATION_TRIGGERS,
        },
        {
            'name': MacroScenario.Name.UPSIDE,
            'probability': scenario_probs['upside'],
            'growth_view': '雇用悪化が限定的で、成長が持ち直す。',
            'inflation_view': 'インフレ鈍化が確認され、実質金利の重しが弱まる。',
            'policy_view': '利下げ期待または金融環境の緩みが株式を支える。',
            'market_view': '信用スプレッドが落ち着き、リスク資産に追い風となる。',
            'nikkei_bias': MacroScenario.NikkeiBias.LONG,
            'key_drivers': ['雇用安定', 'インフレ鈍化', '信用環境改善'],
            'invalidation_triggers': ['雇用統計の急悪化', 'コア物価の再加速', 'VIXの急上昇'],
        },
        {
            'name': MacroScenario.Name.DOWNSIDE,
            'probability': scenario_probs['downside'],
            'growth_view': '雇用悪化と需要鈍化が同時に進む。',
            'inflation_view': '物価再加速や原油高が実質所得と政策期待を悪化させる。',
            'policy_view': '利下げ期待が後退し、長期金利上昇がバリュエーションを圧迫する。',
            'market_view': '信用スプレッド拡大とボラティリティ上昇が同時に出る。',
            'nikkei_bias': MacroScenario.NikkeiBias.SHORT,
            'key_drivers': ['雇用悪化', '信用スプレッド拡大', '原油高', '円急変'],
            'invalidation_triggers': ['信用環境の改善', '雇用の再加速', '米金利の安定化'],
        },
    ]


def persist_macro_scenarios(run, scenario_payloads: Iterable[Dict]) -> List[MacroScenario]:
    scenarios = []
    for payload in scenario_payloads:
        scenario, _ = MacroScenario.objects.update_or_create(
            run=run,
            name=payload['name'],
            defaults={
                'probability': payload['probability'],
                'growth_view': payload['growth_view'],
                'inflation_view': payload['inflation_view'],
                'policy_view': payload['policy_view'],
                'market_view': payload['market_view'],
                'nikkei_bias': payload['nikkei_bias'],
                'key_drivers': payload['key_drivers'],
                'invalidation_triggers': payload['invalidation_triggers'],
            },
        )
        scenarios.append(scenario)
    return scenarios
