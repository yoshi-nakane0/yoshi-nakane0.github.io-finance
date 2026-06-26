"""モデル出力を固定テンプレートで説明文に変換する。"""

from __future__ import annotations

from typing import Dict, Iterable

from .market_pricing import build_market_pricing_gap
from .policy_path import build_policy_reaction_function


def write_macro_report(
    *,
    state_vector: Dict,
    primary_regime: str,
    previous_regime: str = '',
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
    change_summary = (
        f'前回の{previous_regime}から{primary_regime}へ判断を変更。'
        if previous_regime and previous_regime != primary_regime
        else '前回から主レジームの変更なし。'
    )
    what_changed = []
    if previous_regime and previous_regime != primary_regime:
        what_changed.append(f'主レジームが{previous_regime}から{primary_regime}へ変化')
    if inflation_risk >= 0.7:
        what_changed.append('物価再加速リスク')
    if recession >= 0.2:
        what_changed.append('景気後退リスクを強めに確認')
    if stress >= 0.3:
        what_changed.append('金融ストレスの上昇を確認')
    if not what_changed:
        what_changed.append('主要な判断材料は前回から大きく変わらない')

    market_watch = []
    if inflation_risk >= 0.7:
        market_watch.append('金利上昇リスク')
    if recession >= 0.2:
        market_watch.append('景気減速リスクを株価が十分に織り込んでいるか確認')
    if financial == '引き締まり':
        market_watch.append('金融環境の重さを織り込み不足にしていないか確認')
    if not market_watch:
        market_watch.append('市場の織り込み過不足は金利・雇用・信用で再確認')

    scenario_list = list(scenarios)
    scenario_rows = [
        {
            'name': item['name'],
            'probability': item['probability'],
            'nikkei_bias': item['nikkei_bias'],
            'key_drivers': item['key_drivers'],
            'invalidation_triggers': item['invalidation_triggers'],
        }
        for item in scenario_list
    ]
    market_pricing = build_market_pricing_gap(state_vector=state_vector)
    policy_reaction = build_policy_reaction_function(
        inflation_reacceleration=inflation_risk,
        recession_probability=recession,
        labor_score=((axes.get('labor_slack') or {}).get('score') or 50),
    )
    publish_status = 'reference'
    validation_grade = 'C / 検証不足'
    return {
        'executive_summary': {
            'one_line': f'景気は{growth}基調。物価は{inflation}、金融環境は{financial}。',
            'main_view': f'3〜6か月の主シナリオは {primary_regime}。',
            'confidence': validation_grade,
            'publish_status': publish_status,
        },
        'what_changed_detail': {
            'from_previous': change_summary,
            'positive_changes': [
                item for item in what_changed
                if 'リスク' not in item and 'ストレス' not in item
            ],
            'negative_changes': [
                item for item in what_changed
                if 'リスク' in item or 'ストレス' in item
            ],
            'data_releases': [],
        },
        'growth_view': {
            'current_state': f'成長モメンタムは{growth}。',
            'forecast_3m': f'{primary_regime}を基本に確認。',
            'forecast_6m': '雇用、消費、信用環境の変化で更新。',
            'drivers': [growth],
            'risks': ['失業率の連続上昇', '信用スプレッド拡大'],
        },
        'inflation_view': {
            'current_state': f'物価は{inflation}。',
            'forecast_3m': 'Core CPI/Core PCEの再加速を確認。',
            'forecast_6m': '賃金、期待インフレ、原油で更新。',
            'reacceleration_risk': inflation_risk,
            'drivers': ['Core CPI', 'Core PCE', '賃金', '原油'],
        },
        'labor_view': {
            'current_state': '雇用は失業率、NFP、賃金、求人で確認。',
            'turning_points': ['失業率3か月連続上昇', '求人の急減', '賃金鈍化'],
            'recession_warning': recession,
        },
        'policy_view': {
            'fed_path': policy_reaction['fed_reaction_conditions'][0],
            'boj_path': policy_reaction['boj_reaction_conditions'][0],
            'market_pricing_gap': market_pricing['summary'],
            'reaction_function': policy_reaction,
        },
        'market_implication': {
            'rates': market_pricing['rates'],
            'fx': market_pricing['fx'],
            'equities': market_pricing['equities'],
            'nikkei_futures': f'日経先物へのmacroバイアスは{nikkei}。',
        },
        'scenario_table': scenario_rows,
        'invalidation_triggers': [
            '失業率が3か月連続で上昇',
            'Core PCEが2か月連続で再加速',
            '米10年金利が4.5%以上',
            'HYスプレッドが5%以上',
        ],
        'model_reliability': {
            'data_quality': 'データ品質とは別にモデル検証で上限をかける',
            'validation_grade': validation_grade,
            'live_record': 'Live実績不足なら参考扱い',
            'warnings': ['未検証モデルは本判定に使わない'],
        },
        'headline': f'景気は{growth}基調。物価は{inflation}、金融環境は{financial}。',
        'judgment': (
            f'3〜6か月の主シナリオは {primary_regime}。'
            f' 後退確率は{recession * 100:.0f}%、'
            f'物価再加速確率は{inflation_risk * 100:.0f}%、'
            f'金融ストレス確率は{stress * 100:.0f}%。'
        ),
        'nikkei_implication': f'日経先物へのmacroバイアスは{nikkei}。',
        'change_summary': change_summary,
        'what_changed': what_changed,
        'market_mispricing_watch': market_watch,
        'regime_probabilities': regime_probabilities,
        'risk_probabilities': risk_probabilities,
        'state_vector': state_vector,
        'scenarios': scenario_rows,
    }
