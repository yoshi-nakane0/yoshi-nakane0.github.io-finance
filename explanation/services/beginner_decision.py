from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


BLOCKED_DECISION_TYPES = {
    'no_trade_conflict',
    'no_trade_data_blocked',
    'no_trade_direction_stopped',
    'no_chase_long',
    'no_chase_short',
    'legacy_reference',
}


STATUS_LABELS = {
    'buy_candidate': 'ロング候補',
    'sell_candidate': 'ショート候補',
    'wait': '待機',
    'no_trade': '見送り',
    'data_blocked': '判定停止',
}


@dataclass
class BeginnerDecision:
    status: str
    label: str
    plain_action: str
    top_reasons: List[str]
    top_reason_summary: str
    headline: str
    current_price: Optional[float]
    current_price_display: str
    tradable: bool
    candidate_visible: bool
    execution_allowed: bool
    position_allowed: bool
    candidate_status: str
    entry_permission: str
    position_size_pct: Optional[int]
    selected_side: str
    entry_display: str
    target_1_display: str
    target_2_display: str
    stop_display: str
    invalidation_display: str
    reward_risk_display: str
    trade_availability_display: str
    no_candidate_reason_display: str
    confidence_display: str
    entry_permission_label: str
    position_size_display: str
    top_next_condition_summary: str
    confidence_component_rows: List[Dict[str, str]]
    data_state: str
    wait_reasons: List[str] = field(default_factory=list)
    wait_reason_cards: List[Dict[str, str]] = field(default_factory=list)
    wait_reason_summary: str = ''
    reference_candidate: Dict[str, Any] = field(default_factory=dict)
    watch_levels: Dict[str, Any] = field(default_factory=dict)
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
    decision_status = trade_decision.get('decision_status') or 'wait'
    entry_permission = trade_decision.get('entry_permission') or 'no_entry'
    position_size_pct = trade_decision.get('position_size_pct')

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
        + list(trade_decision.get('hard_block_reasons') or [])
        + list(trade_decision.get('soft_warning_reasons') or [])
        + list(trade_decision.get('warnings') or [])
    )

    blocking_reasons = []
    if _quality_score(macro, snapshot) < 50:
        blocking_reasons.append('macro データ品質不足')
    if _quality_score(basecalc, snapshot) < 50:
        blocking_reasons.append('basecalc データ品質不足')
    if snapshot.audit_level == 'blocked':
        blocking_reasons.extend(_limited(snapshot.audit_items or ['監査で判定停止']))
    if contract_status == 'error' or decision_status == 'blocked':
        blocking_reasons.extend(_limited(basecalc.get('stop_reasons') or output_contract.get('stop_reasons') or ['basecalc 出力契約エラー']))

    direction_warning = _direction_warning(selected_side, current_price, target_1_price, stop_price)
    if direction_warning:
        blocking_reasons.append(direction_warning)
    blocking_reasons.extend(_score_gate_reasons(selected_side, trade_decision, decision_status))

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
        contract_status='error' if decision_status == 'blocked' else contract_status,
        directional_allowed=directional_allowed,
        reward_risk=reward_risk,
        blocking_reasons=blocking_reasons,
        macro_bias=snapshot.macro_bias,
        basecalc_bias=snapshot.basecalc_bias,
    )
    if status in {'wait', 'no_trade', 'data_blocked'}:
        tradable = False
    candidate_visible = all([
        selected_side in {'long', 'short'},
        tradable or decision_status in {'watch_only', 'candidate_limited', 'candidate_confirmed'},
        target_1_price is not None,
        stop_price is not None,
        reward_risk is not None and (reward_risk >= 1.2 or decision_status == 'watch_only'),
        contract_status != 'error',
        direction_warning is None,
    ])
    if status == 'data_blocked':
        candidate_visible = False
    if candidate_visible and selected_side == 'long':
        status = 'buy_candidate'
    elif candidate_visible and selected_side == 'short':
        status = 'sell_candidate'
    execution_allowed = candidate_visible and entry_permission in {'limited_entry', 'full_entry'}
    position_allowed = entry_permission == 'full_entry' and tradable

    wait_reasons = _wait_reasons(
        directional_allowed=directional_allowed,
        reward_risk=reward_risk,
        confidence_score=confidence_score,
        blocking_reasons=blocking_reasons,
        status=status,
    )
    reference_warnings = _reference_warnings(basecalc, output_contract, directional_allowed)
    warnings = _limited(warnings + blocking_reasons + reference_warnings + _macro_warnings(macro, selected_side))
    if reward_risk is not None and reward_risk < 1.2 and 'R/R不足' not in warnings:
        warnings.append('R/R不足')
    warnings = _limited(warnings)
    plain_action = _plain_action(status, reward_risk, entry_permission, selected_side, position_size_pct)
    top_reasons = _top_reasons(reasons, warnings)

    entry_display = _entry_display_for_permission(trade_decision, candidate_visible, entry_permission, status)
    target_1_display = _price_display(target_1_price) if candidate_visible else '—'
    target_2_display = _price_display(target_2_price) if candidate_visible and target_2_price is not None else '—'
    stop_display = _price_display(stop_price) if candidate_visible else '—'
    invalidation_display = _price_display(invalidation_price) if candidate_visible and invalidation_price is not None else '—'
    reward_risk_display = _reward_risk_display(reward_risk, execution_allowed, status)
    reference_candidate = _reference_candidate(
        status=status,
        tradable=tradable,
        decision=trade_decision,
        current_price=current_price,
        target_1_price=target_1_price,
        stop_price=stop_price,
        reward_risk=reward_risk,
        confidence_grade=confidence_grade,
        confidence_score=confidence_score,
        wait_reasons=wait_reasons,
    )
    watch_levels = _watch_levels(basecalc, world_model, snapshot, status, tradable)
    next_triggers = _next_triggers(basecalc, world_model, reward_risk, directional_allowed)
    confidence_display = _confidence_display(confidence_grade, confidence_score, manual_price, status)

    return BeginnerDecision(
        status=status,
        label=_status_label(status, selected_side, decision_status),
        plain_action=plain_action,
        top_reasons=top_reasons,
        top_reason_summary=' / '.join(top_reasons),
        headline=_headline(status, warnings, selected_side, contract_status, directional_allowed),
        current_price=current_price,
        current_price_display=_price_display(current_price),
        tradable=tradable,
        candidate_visible=candidate_visible,
        execution_allowed=execution_allowed,
        position_allowed=position_allowed,
        candidate_status=decision_status,
        entry_permission=entry_permission,
        position_size_pct=position_size_pct,
        selected_side=selected_side,
        entry_display=entry_display,
        target_1_display=target_1_display,
        target_2_display=target_2_display,
        stop_display=stop_display,
        invalidation_display=invalidation_display,
        reward_risk_display=reward_risk_display,
        trade_availability_display=_trade_availability_display(status, decision_status),
        no_candidate_reason_display=_no_candidate_reason_display(candidate_visible, wait_reasons, warnings, top_reasons),
        confidence_display=confidence_display,
        entry_permission_label=_entry_permission_label(entry_permission, status),
        position_size_display=_position_size_display(position_size_pct, entry_permission, status),
        top_next_condition_summary=' / '.join(_top_next_conditions(next_triggers, selected_side)),
        confidence_component_rows=_confidence_component_rows(
            trade_decision.get('confidence_components') or {},
            confidence_display,
        ),
        data_state=_data_state(snapshot.audit_level, manual_price, confidence_score),
        wait_reasons=wait_reasons,
        wait_reason_cards=_wait_reason_cards(wait_reasons, warnings),
        wait_reason_summary=_wait_reason_summary(status, wait_reasons),
        reference_candidate=reference_candidate,
        watch_levels=watch_levels,
        reasons=reasons,
        warnings=warnings or ['条件がそろうまで待機。'],
        next_triggers=next_triggers,
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


def _plain_action(status, reward_risk, entry_permission='no_entry', selected_side='no_trade', position_size_pct=None):
    if status in {'buy_candidate', 'sell_candidate'}:
        if entry_permission == 'limited_entry':
            wait_label = '押し目' if selected_side == 'long' else '戻り' if selected_side == 'short' else '条件'
            try:
                pct = int(float(position_size_pct))
            except (TypeError, ValueError):
                pct = 0
            if pct > 0:
                return f'{wait_label}まで待つ。成行追撃は不可。建玉は通常の{pct}%。'
            return f'{wait_label}まで待つ。成行追撃は不可。'
        if entry_permission == 'watch_only':
            return '監視のみ'
        if reward_risk is not None and reward_risk < 1.5:
            return '条件付きで入る'
        return '入る候補'
    if status == 'data_blocked':
        return '停止'
    return '入らない'


def _status_label(status, selected_side, decision_status):
    if decision_status == 'candidate_limited' and selected_side == 'long':
        return '限定ロング候補'
    if decision_status == 'candidate_limited' and selected_side == 'short':
        return '限定ショート候補'
    if decision_status == 'watch_only':
        return '監視のみ'
    if decision_status == 'candidate_confirmed' and selected_side == 'long':
        return '買い候補'
    if decision_status == 'candidate_confirmed' and selected_side == 'short':
        return '売り候補'
    return STATUS_LABELS[status]


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


def _top_reasons(reasons, warnings):
    combined = []
    for item in list(reasons or []) + list(warnings or []):
        if item and item not in combined:
            combined.append(item)
    if not combined:
        combined.append('条件がそろうまで待機。')
    return combined[:3]


def _top_next_conditions(next_triggers, selected_side):
    ordered_keys = ['wait', 'long', 'short']
    if selected_side == 'long':
        ordered_keys = ['long', 'short', 'wait']
    elif selected_side == 'short':
        ordered_keys = ['short', 'long', 'wait']
    result = []
    for key in ordered_keys:
        for item in (next_triggers or {}).get(key) or []:
            if item and item not in result:
                result.append(item)
            if len(result) >= 3:
                return result
    return result or ['条件変化を待つ']


def _confidence_component_rows(components, confidence_display=''):
    if not isinstance(components, dict) or not components:
        return []
    rows = [{'label': '総合信頼度', 'value': confidence_display}] if confidence_display else []
    mapping = [
        ('basecalc_direction', 'basecalc方向', 1),
        ('macro_alignment', 'macro整合', 1),
        ('validation_quality', '検証品質', 1),
        ('target_quality', 'target品質', 1),
        ('data_quality', 'データ品質', 1),
        ('intermarket_confirmation', '米国指数', 1),
        ('event_penalty', 'イベント', -1),
        ('audit_penalty', '監査', -1),
    ]
    for key, label, sign in mapping:
        value = components.get(key)
        if value is None or value == '':
            continue
        numeric = _number(value)
        if numeric is None:
            rows.append({'label': label, 'value': str(value)})
            continue
        display_value = int(numeric * sign) if float(numeric * sign).is_integer() else round(numeric * sign, 1)
        rows.append({'label': label, 'value': f'{display_value}点'})
    validation_level = _validation_level_display(components.get('validation_level'))
    if validation_level:
        rows.append({'label': '検証状態', 'value': validation_level})
    risk_reward = _number(components.get('risk_reward'))
    if risk_reward is not None:
        rows.append({'label': 'R/R', 'value': f'{risk_reward:.2f}'})
    hard_block_penalty = _number(components.get('hard_block_penalty'))
    if hard_block_penalty:
        rows.append({'label': '停止減点', 'value': f'-{int(hard_block_penalty)}点'})
    target_hit_rate = _rate_component_display(components.get('target_hit_rate'))
    if target_hit_rate:
        rows.append({'label': 'T1到達率', 'value': target_hit_rate})
    stop_hit_rate = _rate_component_display(components.get('stop_hit_rate'))
    if stop_hit_rate:
        rows.append({'label': 'stop到達率', 'value': stop_hit_rate})
    realized_rr = _number(components.get('avg_realized_rr'))
    if realized_rr is not None:
        rows.append({'label': '実績R/R', 'value': f'{realized_rr:.2f}'})
    cap_reason = components.get('confidence_cap_reason')
    if cap_reason:
        rows.append({'label': '上限理由', 'value': str(cap_reason)})
    return rows


def _validation_level_display(value):
    return {
        'none': '未検証',
        'low': '検証少',
        'medium': '一部検証',
        'high': '検証済み',
    }.get(value or '', '')


def _rate_component_display(value):
    numeric = _number(value)
    if numeric is None:
        return ''
    if numeric > 1:
        numeric = numeric / 100
    return f'{numeric * 100:.0f}%'


def _reward_risk_display(value, tradable, status):
    if status == 'data_blocked':
        return '—'
    if tradable:
        return f'{float(value):.2f}'
    if value is not None and value < 1.2:
        return '不採用（1.2未満）'
    return '不採用'


def _trade_availability_display(status, decision_status):
    if status == 'data_blocked' or decision_status == 'blocked':
        return '判定停止'
    if status == 'no_trade':
        return '見送り / 条件未達'
    if decision_status == 'watch_only':
        return '監視のみ'
    if status == 'wait':
        return '待機 / 条件待ち'
    if decision_status == 'candidate_limited':
        return '限定候補'
    if decision_status == 'candidate_confirmed':
        return '通常候補'
    return STATUS_LABELS.get(status, '条件確認中')


def _no_candidate_reason_display(candidate_visible, wait_reasons, warnings, top_reasons):
    if candidate_visible:
        return ''
    for source in (wait_reasons, warnings, top_reasons):
        for item in source or []:
            if item:
                return str(item)
    return '条件未達'


def _entry_permission_label(entry_permission, status):
    if status == 'data_blocked':
        return '停止'
    if entry_permission == 'full_entry':
        return '入る'
    if entry_permission == 'limited_entry':
        return '条件付きで入る'
    if entry_permission == 'watch_only':
        return '監視のみ'
    if status in {'buy_candidate', 'sell_candidate'}:
        return '入る候補'
    return '入らない'


def _position_size_display(value, entry_permission, status):
    if status == 'data_blocked':
        return 'なし'
    try:
        pct = int(float(value))
    except (TypeError, ValueError):
        pct = 0
    if entry_permission == 'full_entry' and pct >= 100:
        return '通常サイズ'
    if pct > 0:
        return f'通常の{pct}%まで'
    return 'なし'


def _entry_display(decision):
    low = _number(decision.get('entry_zone_low'))
    high = _number(decision.get('entry_zone_high'))
    if low is not None and high is not None:
        return f'{_format_price(low)}〜{_format_price(high)}円'
    return _price_display(_number(decision.get('entry_price')))


def _entry_display_for_permission(decision, candidate_visible, entry_permission, status):
    if not candidate_visible:
        return '停止' if status == 'data_blocked' else 'なし'
    if entry_permission == 'watch_only':
        return '監視のみ'
    return _entry_display(decision)


def _direction_warning(side, current_price, target_price, stop_price):
    if current_price is None or target_price is None or stop_price is None:
        return None
    if side == 'long' and (target_price <= current_price or stop_price >= current_price):
        return 'target/stop が現在値と整合していません'
    if side == 'short' and (target_price >= current_price or stop_price <= current_price):
        return 'target/stop が現在値と整合していません'
    return None


def _score_gate_reasons(selected_side, trade_decision, decision_status='wait'):
    if decision_status in {'watch_only', 'candidate_limited', 'candidate_confirmed'}:
        return []
    if selected_side not in {'long', 'short'}:
        return []
    side_score = _int_or_none(trade_decision.get(f'{selected_side}_score'))
    no_trade_score = _int_or_none(trade_decision.get('no_trade_score'))
    if side_score is None or no_trade_score is None:
        return []
    reasons = []
    if side_score < 65:
        reasons.append('スコア不足')
    if side_score - no_trade_score < 8:
        reasons.append('no_tradeより弱い')
    return reasons


def _wait_reasons(*, directional_allowed, reward_risk, confidence_score, blocking_reasons, status):
    reasons = []
    if directional_allowed is False:
        reasons.append('方向予測停止')
    if reward_risk is not None and reward_risk < 1.2:
        reasons.append('R/R不足')
    if confidence_score < 50:
        reasons.append('信頼度不足')
    for reason in blocking_reasons:
        if reason in {'スコア不足', 'no_tradeより弱い'}:
            reasons.append(reason)
        elif 'target/stop' in reason:
            reasons.append('target/stop不整合')
        elif 'データ品質不足' in reason and status == 'data_blocked':
            reasons.append(reason)
    if status in {'wait', 'no_trade'} and not reasons:
        reasons.append('条件未達')
    return _dedupe(reasons)


def _wait_reason_summary(status, wait_reasons):
    if status not in {'wait', 'no_trade', 'data_blocked'} or not wait_reasons:
        return ''
    return f'{STATUS_LABELS[status]}：{" / ".join(wait_reasons)}'


def _wait_reason_cards(wait_reasons, warnings):
    cards = []
    for reason in list(wait_reasons or []) + list(warnings or []):
        card = _wait_reason_card(reason)
        if not card:
            continue
        if not any(row['label'] == card['label'] for row in cards):
            cards.append(card)
    return cards or [_wait_reason_card('条件未達')]


def _wait_reason_card(reason):
    text = str(reason or '')
    if 'データ品質不足' in text or 'target/stop' in text or '不整合' in text:
        return {
            'label': 'データ異常',
            'detail': '価格やデータに確認が必要です',
            'unlock_condition': 'データ更新後に再判定',
        }
    if text in {'方向予測停止', 'スコア不足', 'no_tradeより弱い', '条件未達'}:
        return {
            'label': '方向優位なし',
            'detail': 'ロング・ショートの優位性がまだ弱い状態です',
            'unlock_condition': '上方向または下方向の条件突破を待つ',
        }
    if 'R/R不足' in text:
        return {
            'label': 'R/R不足',
            'detail': '期待値が基準を下回っています',
            'unlock_condition': '押し目・戻りを待つ',
        }
    if '信頼度不足' in text:
        return {
            'label': '信頼度不足',
            'detail': '信頼度が基準に届いていません',
            'unlock_condition': '信頼度50以上まで待つ',
        }
    if '検証不足' in text or '検証件数' in text:
        return {
            'label': '検証不足',
            'detail': '過去の確認件数がまだ足りません',
            'unlock_condition': '方向は参考、限定候補扱い',
        }
    if '重要イベント' in text or 'イベント' in text:
        return {
            'label': 'イベント警戒',
            'detail': '重要イベント前後で値動きが荒れやすい状態です',
            'unlock_condition': '発表通過後に再判定',
        }
    if '高値追い禁止' in text:
        return {
            'label': '高値追い禁止',
            'detail': '上方向でも現在値から追う条件ではありません',
            'unlock_condition': '押し目形成後に再判定',
        }
    if '突っ込み売り禁止' in text:
        return {
            'label': '突っ込み売り禁止',
            'detail': '下方向でも現在値から追う条件ではありません',
            'unlock_condition': '戻り形成後に再判定',
        }
    return {
        'label': text or '条件未達',
        'detail': '条件がそろうまで待機します',
        'unlock_condition': '条件変化後に再判定',
    }


def _reference_warnings(basecalc, output_contract, directional_allowed):
    if directional_allowed is not False:
        return []
    reasons = list(basecalc.get('stop_reasons') or []) + list(output_contract.get('stop_reasons') or [])
    if any('ATR' in str(reason) for reason in reasons):
        return ['ATR基準に届かないため、方向予測は参考表示にしています。']
    return []


def _reference_candidate(
    *,
    status,
    tradable,
    decision,
    current_price,
    target_1_price,
    stop_price,
    reward_risk,
    confidence_grade,
    confidence_score,
    wait_reasons,
):
    side = _reference_side(current_price, target_1_price, stop_price)
    decision_type = decision.get('decision_type') or ''
    if status in {'wait', 'no_trade', 'data_blocked'}:
        return {'available': False}
    if not tradable:
        return {'available': False}
    if '方向予測停止' in wait_reasons:
        return {'available': False}
    if decision_type in {'no_trade_direction_stopped', 'no_trade_data_blocked'}:
        return {'available': False}
    if decision.get('selected_side') == 'no_trade' and decision.get('blocked_reasons'):
        return {'available': False}
    if tradable or status not in {'wait', 'no_trade'} or side is None:
        return {'available': False}
    return {
        'available': True,
        'side': side,
        'label': 'ロング監視ライン' if side == 'long' else 'ショート監視ライン',
        'note': '監視ライン。売買候補ではありません。',
        'entry_display': _entry_display(decision),
        'target_1_display': _price_display(target_1_price),
        'stop_display': _price_display(stop_price),
        'reward_risk_display': 'N/A' if reward_risk is None else f'{float(reward_risk):.2f}',
        'confidence_display': f'{confidence_grade} / {confidence_score}%（参考）',
    }


def _watch_levels(basecalc, world_model, snapshot, status, tradable):
    if tradable or status not in {'wait', 'no_trade', 'data_blocked'}:
        return {'available': False}
    lines = world_model.get('practical_lines') or {}
    scenario = snapshot.scenario or {}
    levels = scenario.get('levels') or {}
    resistance = basecalc.get('resistance') or levels.get('resistance') or lines.get('upside_resistance')
    support = basecalc.get('support') or levels.get('support') or lines.get('downside_support')
    rows = []
    if resistance is not None:
        rows.append({'label': '上値抵抗', 'value': _price_display(resistance)})
    if support is not None:
        rows.append({'label': '下値支持', 'value': _price_display(support)})
    if scenario.get('change_condition'):
        rows.append({'label': '判断変更条件', 'value': scenario.get('change_condition')})
    rows.append({'label': '米国3指数確認', 'value': '改善または失速の確認待ち'})
    rows.append({'label': 'データ品質回復条件', 'value': '方向予測ゲートと鮮度の回復'})
    return {
        'available': bool(rows),
        'note': 'この水準は売買候補ではありません。条件変化を確認するための目安です。',
        'rows': _limited_rows(rows, 5),
    }


def _reference_side(current_price, target_price, stop_price):
    if current_price is None or target_price is None or stop_price is None:
        return None
    if target_price > current_price and stop_price < current_price:
        return 'long'
    if target_price < current_price and stop_price > current_price:
        return 'short'
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
    long_items.append('米国3指数が改善')
    short_items.append('米国3指数が失速')
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
    suffix = '（参考）' if manual_price.get('active') or status in {'wait', 'no_trade'} else ''
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


def _int_or_none(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


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
    return [_normalize_reason_text(item) for item in items if item][:limit]


def _limited_rows(rows, limit=5):
    return rows[:limit]


def _normalize_reason_text(text):
    text = str(text or '').strip()
    text = text.replace('重要指標の発表前後のため一段階下げます。のため、強い判断にはしない。', '重要指標の発表前後のため、強い判断にはしない。')
    text = text.replace('ます。のため', 'ます。そのため')
    text = text.replace('。のため', '。そのため')
    text = text.replace('ため一段階下げます。そのため、強い判断にはしない。', '重要指標の発表前後のため、強い判断にはしない。')
    return text


def _dedupe(items):
    result = []
    for item in items:
        if item and item not in result:
            result.append(item)
    return result
