from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


BLOCKED_DECISION_TYPES = {
    'no_trade_conflict',
    'no_trade_data_blocked',
    'no_chase_long',
    'no_chase_short',
    'legacy_reference',
}


STATUS_LABELS = {
    'buy_candidate': '買い候補',
    'sell_candidate': '売り候補',
    'wait': '待機',
    'no_trade': '見送り',
    'data_blocked': '判定停止',
}


@dataclass
class BeginnerDecision:
    status: str
    label: str
    plain_action: str
    headline: str
    current_price: Optional[float]
    current_price_display: str
    tradable: bool
    selected_side: str
    entry_display: str
    target_1_display: str
    target_2_display: str
    stop_display: str
    invalidation_display: str
    reward_risk_display: str
    confidence_display: str
    data_state: str
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    next_triggers: Dict[str, List[str]] = field(default_factory=dict)
    macro_summary: str = ''
    basecalc_summary: str = ''
    audit_summary: str = ''

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def build_beginner_decision(snapshot, macro, basecalc, world_model, trade_decision, manual_price):
    macro = macro or {}
    basecalc = basecalc or {}
    world_model = world_model or {}
    trade_decision = trade_decision or {}
    manual_price = manual_price or {}

    selected_side = trade_decision.get('selected_side') or 'no_trade'
    decision_type = trade_decision.get('decision_type') or ''
    current_price = _number(
        trade_decision.get('current_price')
        or basecalc.get('current_price')
        or (world_model.get('output_contract') or {}).get('display_price')
        or world_model.get('display_price')
        or world_model.get('price')
    )
    target_1_price = _target_price(trade_decision.get('target_1'))
    target_2_price = _target_price(trade_decision.get('target_2'))
    stop_price = _number(trade_decision.get('stop_price'))
    invalidation_price = _number(trade_decision.get('invalidation_price'))
    reward_risk = _number(trade_decision.get('reward_risk'))
    confidence_score = _int_value(trade_decision.get('confidence_score'), snapshot.confidence_score)
    confidence_grade = trade_decision.get('confidence_grade') or snapshot.confidence_grade

    output_contract = world_model.get('output_contract') or {}
    contract_status = (
        basecalc.get('contract_status')
        or world_model.get('contract_status')
        or output_contract.get('contract_status')
        or 'unchecked'
    )
    directional_allowed = output_contract.get('directional_allowed')
    if directional_allowed is None:
        directional_allowed = basecalc.get('allowed_direction') not in {'stopped', 'none'} if basecalc.get('allowed_direction') else True

    reasons = _limited(
        list(trade_decision.get('reasons') or [])
        or list(getattr(snapshot, 'evidence', None) or [])
        or ['判断材料を確認中。']
    )
    warnings = _limited(
        list(trade_decision.get('blocked_reasons') or [])
        + list(trade_decision.get('warnings') or [])
    )

    blocking_reasons = []
    if _quality_score(macro, snapshot) < 50:
        blocking_reasons.append('macro データ品質不足')
    if _quality_score(basecalc, snapshot) < 50:
        blocking_reasons.append('basecalc データ品質不足')
    if snapshot.audit_level == 'blocked':
        blocking_reasons.extend(_limited(snapshot.audit_items or ['監査で判定停止']))
    if contract_status == 'error':
        blocking_reasons.extend(_limited(basecalc.get('stop_reasons') or output_contract.get('stop_reasons') or ['basecalc 出力契約エラー']))

    direction_warning = _direction_warning(selected_side, current_price, target_1_price, stop_price)
    if direction_warning:
        blocking_reasons.append(direction_warning)

    tradable = all([
        selected_side in {'long', 'short'},
        decision_type not in BLOCKED_DECISION_TYPES,
        target_1_price is not None,
        stop_price is not None,
        reward_risk is not None and reward_risk >= 1.2,
        confidence_score >= 50,
        snapshot.audit_level != 'blocked',
        contract_status != 'error',
        directional_allowed is True,
        direction_warning is None,
        not blocking_reasons,
    ])

    status = _status(
        tradable=tradable,
        selected_side=selected_side,
        decision_type=decision_type,
        contract_status=contract_status,
        directional_allowed=directional_allowed,
        reward_risk=reward_risk,
        blocking_reasons=blocking_reasons,
        macro_bias=snapshot.macro_bias,
        basecalc_bias=snapshot.basecalc_bias,
    )
    if status in {'wait', 'no_trade', 'data_blocked'}:
        tradable = False

    warnings = _limited(warnings + blocking_reasons + _macro_warnings(macro, selected_side))
    if reward_risk is not None and reward_risk < 1.2 and 'R/R不足' not in warnings:
        warnings.append('R/R不足')
    warnings = _limited(warnings)

    entry_display = _entry_display(trade_decision) if tradable else ('停止' if status == 'data_blocked' else 'なし')
    target_1_display = _price_display(target_1_price) if tradable else '—'
    target_2_display = _price_display(target_2_price) if tradable and target_2_price is not None else '—'
    stop_display = _price_display(stop_price) if tradable else '—'
    invalidation_display = _price_display(invalidation_price) if tradable and invalidation_price is not None else '—'
    reward_risk_display = _reward_risk_display(reward_risk, tradable, status)

    return BeginnerDecision(
        status=status,
        label=STATUS_LABELS[status],
        plain_action=_plain_action(status, reward_risk),
        headline=_headline(status, warnings, selected_side, contract_status, directional_allowed),
        current_price=current_price,
        current_price_display=_price_display(current_price),
        tradable=tradable,
        selected_side=selected_side,
        entry_display=entry_display,
        target_1_display=target_1_display,
        target_2_display=target_2_display,
        stop_display=stop_display,
        invalidation_display=invalidation_display,
        reward_risk_display=reward_risk_display,
        confidence_display=_confidence_display(confidence_grade, confidence_score, manual_price, status),
        data_state=_data_state(snapshot.audit_level, manual_price, confidence_score),
        reasons=reasons,
        warnings=warnings or ['条件がそろうまで待機。'],
        next_triggers=_next_triggers(basecalc, world_model, reward_risk, directional_allowed),
        macro_summary=_macro_summary(snapshot.macro_bias, macro),
        basecalc_summary=_basecalc_summary(snapshot.basecalc_bias, basecalc, contract_status, directional_allowed),
        audit_summary=_audit_summary(snapshot, blocking_reasons),
    ).to_dict()


def _status(
    *,
    tradable,
    selected_side,
    decision_type,
    contract_status,
    directional_allowed,
    reward_risk,
    blocking_reasons,
    macro_bias,
    basecalc_bias,
):
    if contract_status == 'error' or any('データ品質不足' in reason for reason in blocking_reasons):
        return 'data_blocked'
    if directional_allowed is False:
        return 'wait'
    if blocking_reasons:
        return 'wait'
    if not tradable:
        if reward_risk is not None and reward_risk < 1.2:
            return 'no_trade'
        if decision_type in BLOCKED_DECISION_TYPES or selected_side == 'no_trade':
            return 'no_trade'
        return 'wait'
    if selected_side == 'long':
        return 'wait' if macro_bias == 'negative' or basecalc_bias == 'bearish' else 'buy_candidate'
    if selected_side == 'short':
        return 'wait' if macro_bias == 'positive' or basecalc_bias == 'bullish' else 'sell_candidate'
    return 'no_trade'


def _plain_action(status, reward_risk):
    if status in {'buy_candidate', 'sell_candidate'}:
        if reward_risk is not None and reward_risk < 1.5:
            return '条件付きで入る'
        return '入る候補'
    if status == 'data_blocked':
        return '停止'
    return '入らない'


def _headline(status, warnings, selected_side, contract_status, directional_allowed):
    if status == 'data_blocked':
        return 'データの不足または矛盾があるため、売買判断を止めます。'
    if directional_allowed is False:
        return '今は待機。basecalc の方向予測が検証条件を満たしていないため、ロング/ショートは採用しません。'
    if status == 'buy_candidate':
        return '買い候補。条件を満たす押し目を待ち、高値追いは避けます。'
    if status == 'sell_candidate':
        return '売り候補。戻り売りを優先し、突っ込み売りは避けます。'
    reason = '、'.join(warnings[:2]) if warnings else '条件未達'
    if selected_side == 'no_trade' or status == 'no_trade':
        return f'今は入らない。理由は {reason} です。'
    return f'今は待機。理由は {reason} です。'


def _reward_risk_display(value, tradable, status):
    if status == 'data_blocked':
        return '—'
    if tradable:
        return f'{float(value):.2f}'
    if value is not None and value < 1.2:
        return '不採用（1.2未満）'
    return '不採用'


def _entry_display(decision):
    low = _number(decision.get('entry_zone_low'))
    high = _number(decision.get('entry_zone_high'))
    if low is not None and high is not None:
        return f'{_format_price(low)}〜{_format_price(high)}円'
    return _price_display(_number(decision.get('entry_price')))


def _direction_warning(side, current_price, target_price, stop_price):
    if current_price is None or target_price is None or stop_price is None:
        return None
    if side == 'long' and (target_price <= current_price or stop_price >= current_price):
        return 'target/stop が現在値と整合していません'
    if side == 'short' and (target_price >= current_price or stop_price <= current_price):
        return 'target/stop が現在値と整合していません'
    return None


def _next_triggers(basecalc, world_model, reward_risk, directional_allowed):
    lines = world_model.get('practical_lines') or {}
    resistance = basecalc.get('resistance') or lines.get('upside_resistance') or _near_level(world_model, 'upside')
    support = basecalc.get('support') or lines.get('downside_support') or _near_level(world_model, 'downside')
    long_items = ['上値抵抗を終値で突破']
    short_items = ['下値支持を終値で割り込み']
    wait_items = []
    if resistance is not None:
        long_items[0] = f'上値抵抗 {_price_display(resistance)} を終値で突破'
    if support is not None:
        short_items[0] = f'下値支持 {_price_display(support)} を終値で割り込み'
    long_items.extend(['米国3指数が改善', 'R/R 1.2以上'])
    short_items.extend(['米国3指数が失速', 'R/R 1.2以上'])
    if directional_allowed is False:
        wait_items.append('方向予測停止')
    if reward_risk is not None and reward_risk < 1.2:
        wait_items.append('R/R不足')
    wait_items.append('レンジ内')
    return {
        'long': _limited(long_items),
        'short': _limited(short_items),
        'wait': _limited(wait_items),
    }


def _near_level(world_model, side):
    for item in (world_model.get('near_levels') or {}).get(side) or []:
        if isinstance(item, dict) and item.get('price') is not None:
            return item.get('price')
    return None


def _macro_summary(macro_bias, macro):
    label = {'positive': '追い風', 'negative': '逆風'}.get(macro_bias, '中立')
    summary = macro.get('summary') or ''
    return f'{label}。{summary}'.strip()


def _basecalc_summary(basecalc_bias, basecalc, contract_status, directional_allowed):
    label = {'bullish': '上方向', 'bearish': '下方向', 'range': 'レンジ', 'neutral': '中立'}.get(basecalc_bias, '中立')
    if contract_status == 'error':
        return '方向判断停止。出力条件に矛盾があります。'
    if directional_allowed is False:
        return 'レンジまたは検証未達。方向予測は採用しません。'
    summary = basecalc.get('summary') or ''
    return f'{label}。{summary}'.strip()


def _audit_summary(snapshot, blocking_reasons):
    if blocking_reasons:
        return ' / '.join(_limited(blocking_reasons))
    if snapshot.audit_level == 'warning':
        return '注意あり。詳細は監査を確認。'
    return '停止条件なし。'


def _macro_warnings(macro, selected_side):
    factor = macro.get('factor_vector') or {}
    warnings = []
    if _number(factor.get('event_risk_score')) and _number(factor.get('event_risk_score')) >= 70:
        warnings.append('重要イベント前後')
    if selected_side == 'long' and _number(factor.get('rates_pressure_score')) and _number(factor.get('rates_pressure_score')) >= 75:
        warnings.append('金利上昇が上値を抑制')
    if selected_side == 'long' and _number(factor.get('credit_stress_score')) and _number(factor.get('credit_stress_score')) >= 70:
        warnings.append('信用ストレス上昇')
    if _quality_score(macro, None) < 60:
        warnings.append('macro データ品質不足')
    return warnings


def _data_state(audit_level, manual_price, confidence_score):
    if audit_level == 'blocked':
        return '停止'
    if manual_price.get('active') or confidence_score < 60 or audit_level == 'warning':
        return '参考'
    return '通常'


def _confidence_display(grade, score, manual_price, status):
    suffix = '（参考）' if manual_price.get('active') or status in {'wait', 'no_trade'} or score < 60 else ''
    return f'{grade} / {score}%{suffix}'


def _target_price(target):
    if isinstance(target, dict):
        return _number(target.get('price'))
    return _number(target)


def _quality_score(source, snapshot):
    if source and source.get('data_quality_score') is not None:
        return _int_value(source.get('data_quality_score'), 0)
    if snapshot is not None:
        return _int_value(getattr(snapshot, 'data_quality_score', None), 100)
    return 100


def _int_value(value, default):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _price_display(value):
    if value is None:
        return 'N/A'
    return f'{_format_price(value)}円'


def _format_price(value):
    return f'{float(value):,.0f}'


def _limited(items, limit=3):
    return [str(item) for item in items if item][:limit]
