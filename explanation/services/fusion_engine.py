from .contracts import AuditResult, BasecalcSignal, FusionResult, MacroSignal
from .reversal_engine import evaluate_reversal
from .target_selector import select_trade_targets
from .trade_contract import TradeDecision, no_trade_decision


GRADE_MAX_SCORE = {
    'A': 100,
    'B': 79,
    'B-': 69,
    'C+': 59,
    'C': 49,
    'D': 39,
}


def build_final_decision(
    macro: MacroSignal,
    basecalc: BasecalcSignal,
    audit: AuditResult,
) -> FusionResult:
    if audit.status == 'blocked':
        label = '判定保留'
        stance = 'withhold'
        posture = '主要データがそろうまで判断を保留。'
    else:
        label, stance, posture = _matrix_label(macro.bias, basecalc.bias, audit)

    base_confidence = min(macro.confidence_score, basecalc.confidence_score)
    alignment_bonus = {'aligned': 5, 'partial': 0, 'timeframe_divergence': -3, 'blocked': -30}.get(
        audit.alignment_status,
        0,
    )
    score = _clamp(base_confidence + alignment_bonus - audit.penalty)
    if audit.confidence_cap:
        score = min(score, GRADE_MAX_SCORE.get(audit.confidence_cap, score))
    if stance == 'withhold':
        score = min(score, 39)
    grade = _grade_from_score(score)

    evidence = [
        _basecalc_evidence(basecalc),
        _macro_evidence(macro),
        _audit_evidence(audit),
    ]
    return FusionResult(
        final_label=label,
        final_stance=stance,
        action_posture=posture,
        confidence_score=score,
        confidence_grade=grade,
        evidence=evidence,
        score_breakdown={
            'macro_confidence': macro.confidence_score,
            'basecalc_confidence': basecalc.confidence_score,
            'base_confidence': base_confidence,
            'alignment_bonus': alignment_bonus,
            'audit_penalty': audit.penalty,
            'confidence_cap': audit.confidence_cap,
            'alignment_status': audit.alignment_status,
        },
    )


def build_trade_decision_v2(
    macro: MacroSignal,
    basecalc: BasecalcSignal,
    audit: AuditResult,
) -> TradeDecision:
    current_price = basecalc.current_price
    price_source = basecalc.price_source or 'market_data'
    long_plan = select_trade_targets('long', current_price, basecalc)
    short_plan = select_trade_targets('short', current_price, basecalc)
    reversal = evaluate_reversal(macro, basecalc)
    validation_level = _validation_level(basecalc)
    hard_block_reasons, soft_warning_reasons = _reason_groups(basecalc)
    confidence_score = _trade_confidence(
        macro,
        basecalc,
        audit,
        validation_level,
        long_plan,
        short_plan,
        hard_block_reasons,
    )
    confidence_grade = _grade_from_score(confidence_score)
    long_score, short_score, no_trade_score = _trade_scores(macro, basecalc, audit, long_plan, short_plan, reversal)
    trend_follow_score = _clamp((basecalc.continuation_score or 0) + _macro_trend_bonus(macro, basecalc))
    reversal_score = _clamp(reversal.get('score') or 0)
    confidence_components = _confidence_components(
        macro,
        basecalc,
        audit,
        confidence_score,
        validation_level,
        long_plan,
        short_plan,
        hard_block_reasons,
    )

    if audit.status == 'blocked' or basecalc.contract_status == 'error':
        reasons = list(hard_block_reasons or basecalc.stop_reasons or audit.items or ['判定に必要なデータが不足'])
        return no_trade_decision(
            decision_type='no_trade_data_blocked',
            current_price=current_price,
            confidence_score=min(confidence_score, 39),
            confidence_grade='D',
            long_score=long_score,
            short_score=short_score,
            no_trade_score=100,
            trend_follow_score=trend_follow_score,
            reversal_score=reversal_score,
            reasons=['データ整合性を確認するまで売買判断を停止。'],
            blocked_reasons=reasons,
            counter_scenario=basecalc.counter_bias,
            reversal_watch=reversal if reversal.get('status') != 'none' else {},
            price_source=price_source,
            decision_status='blocked',
            entry_permission='no_entry',
            validation_level='none',
            hard_block_reasons=reasons,
            soft_warning_reasons=soft_warning_reasons,
            confidence_components=confidence_components,
            position_size_pct=0,
        )

    if _direction_stopped(basecalc, hard_block_reasons):
        stopped_score = min(confidence_score, 49)
        reasons = list(hard_block_reasons or basecalc.stop_reasons or ['方向予測停止'])
        return no_trade_decision(
            decision_type='no_trade_direction_stopped',
            current_price=current_price,
            confidence_score=stopped_score,
            confidence_grade=_grade_from_score(stopped_score),
            long_score=long_score,
            short_score=short_score,
            no_trade_score=100,
            trend_follow_score=trend_follow_score,
            reversal_score=reversal_score,
            reasons=['basecalcの方向予測が停止しているため、売買候補にしない。'],
            blocked_reasons=reasons,
            counter_scenario=basecalc.counter_bias,
            reversal_watch={},
            price_source=price_source,
            decision_status='blocked',
            entry_permission='no_entry',
            validation_level='none',
            hard_block_reasons=reasons,
            soft_warning_reasons=soft_warning_reasons,
            confidence_components=confidence_components,
            position_size_pct=0,
        )

    candidate_status = _candidate_status(basecalc, confidence_score, validation_level)
    confidence_score = min(confidence_score, _candidate_confidence_cap(candidate_status, basecalc, validation_level))
    confidence_grade = _grade_from_score(confidence_score)
    confidence_components = {
        **confidence_components,
        'final_score': confidence_score,
        'decision_status': candidate_status,
    }

    no_chase = _no_chase_decision_type(basecalc, reversal, long_plan, short_plan)
    if no_chase:
        warning = '高値追い禁止' if no_chase == 'no_chase_long' else '突っ込み売り禁止'
        reference_side = 'long' if no_chase == 'no_chase_long' else 'short'
        reference_plan = long_plan if reference_side == 'long' else short_plan
        return _no_trade_decision_with_plan(
            reference_side,
            reference_plan,
            decision_type=no_chase,
            current_price=current_price,
            confidence_score=confidence_score,
            confidence_grade=confidence_grade,
            long_score=long_score,
            short_score=short_score,
            no_trade_score=max(no_trade_score, 75),
            trend_follow_score=trend_follow_score,
            reversal_score=reversal_score,
            reasons=[f'{warning}。逆張りはWATCH止まりで、反転確認までは新規追撃しない。'],
            warnings=[warning],
            blocked_reasons=['targetが現在値に近すぎる'],
            counter_scenario=basecalc.counter_bias,
            reversal_watch=reversal,
            price_source=price_source,
            decision_status='wait',
            entry_permission='no_entry',
            validation_level=validation_level,
            hard_block_reasons=[],
            soft_warning_reasons=soft_warning_reasons,
            confidence_components=confidence_components,
            position_size_pct=0,
        )

    likely_plan = long_plan if long_score >= short_score else short_plan
    likely_side = 'long' if long_score >= short_score else 'short'
    if confidence_score < 50 and candidate_status == 'candidate_confirmed':
        return _no_trade_decision_with_plan(
            likely_side,
            likely_plan,
            decision_type='no_trade_conflict',
            current_price=current_price,
            confidence_score=confidence_score,
            confidence_grade=confidence_grade,
            long_score=long_score,
            short_score=short_score,
            no_trade_score=max(no_trade_score, 80),
            trend_follow_score=trend_follow_score,
            reversal_score=reversal_score,
            reasons=['信頼度が50%未満のため、売買候補にしない。'],
            blocked_reasons=['信頼度不足'],
            counter_scenario=basecalc.counter_bias,
            reversal_watch=reversal if reversal.get('status') != 'none' else {},
            price_source=price_source,
            decision_status='wait',
            entry_permission='no_entry',
            validation_level=validation_level,
            hard_block_reasons=[],
            soft_warning_reasons=soft_warning_reasons,
            confidence_components=confidence_components,
            position_size_pct=0,
        )
    if likely_plan.reward_risk is not None and likely_plan.reward_risk < 1.2:
        return _no_trade_decision_with_plan(
            likely_side,
            likely_plan,
            decision_type='no_trade_conflict',
            current_price=current_price,
            confidence_score=confidence_score,
            confidence_grade=confidence_grade,
            long_score=long_score,
            short_score=short_score,
            no_trade_score=max(no_trade_score, 80),
            trend_follow_score=trend_follow_score,
            reversal_score=reversal_score,
            reasons=['利益幅に対して損切り幅が大きいため見送り。'],
            blocked_reasons=['R/R不足'],
            counter_scenario=basecalc.counter_bias,
            reversal_watch=reversal if reversal.get('status') != 'none' else {},
            price_source=price_source,
            decision_status='wait',
            entry_permission='no_entry',
            validation_level=validation_level,
            hard_block_reasons=[],
            soft_warning_reasons=soft_warning_reasons,
            confidence_components=confidence_components,
            position_size_pct=0,
        )
    if _opposite_reversal_watch(likely_side, reversal):
        return _no_trade_decision_with_plan(
            likely_side,
            likely_plan,
            decision_type='no_trade_conflict',
            current_price=current_price,
            confidence_score=confidence_score,
            confidence_grade=confidence_grade,
            long_score=long_score,
            short_score=short_score,
            no_trade_score=max(no_trade_score, 80),
            trend_follow_score=trend_follow_score,
            reversal_score=reversal_score,
            reasons=['反対方向の反転警戒が強いため、候補にはせず監視に回す。'],
            blocked_reasons=['明確な反転警戒'],
            counter_scenario=basecalc.counter_bias,
            reversal_watch=reversal if reversal.get('status') != 'none' else {},
            price_source=price_source,
            decision_status='wait',
            entry_permission='no_entry',
            validation_level=validation_level,
            hard_block_reasons=[],
            soft_warning_reasons=soft_warning_reasons,
            confidence_components=confidence_components,
            position_size_pct=0,
        )

    selected_side, selected_score, runner_up = _select_side(long_score, short_score, no_trade_score)
    if selected_side == 'no_trade' or selected_score < 60 or selected_score - runner_up < 5:
        return _no_trade_decision_with_plan(
            likely_side,
            likely_plan,
            decision_type='no_trade_conflict',
            current_price=current_price,
            confidence_score=confidence_score,
            confidence_grade=confidence_grade,
            long_score=long_score,
            short_score=short_score,
            no_trade_score=max(no_trade_score, 70),
            trend_follow_score=trend_follow_score,
            reversal_score=reversal_score,
            reasons=['スコア差が小さいため、方向を一つに決めない。'],
            blocked_reasons=['スコア差不足'],
            counter_scenario=basecalc.counter_bias,
            reversal_watch=reversal if reversal.get('status') != 'none' else {},
            price_source=price_source,
            decision_status='wait',
            entry_permission='no_entry',
            validation_level=validation_level,
            hard_block_reasons=[],
            soft_warning_reasons=soft_warning_reasons,
            confidence_components=confidence_components,
            position_size_pct=0,
        )

    plan = long_plan if selected_side == 'long' else short_plan
    if plan.blocked_reasons:
        return _no_trade_decision_with_plan(
            selected_side,
            plan,
            decision_type='no_trade_conflict',
            current_price=current_price,
            confidence_score=confidence_score,
            confidence_grade=confidence_grade,
            long_score=long_score,
            short_score=short_score,
            no_trade_score=max(no_trade_score, 80),
            trend_follow_score=trend_follow_score,
            reversal_score=reversal_score,
            reasons=['target、stop、R/Rの条件がそろわないため見送り。'],
            blocked_reasons=plan.blocked_reasons,
            counter_scenario=basecalc.counter_bias,
            reversal_watch=reversal if reversal.get('status') != 'none' else {},
            price_source=price_source,
            decision_status='wait',
            entry_permission='no_entry',
            validation_level=validation_level,
            hard_block_reasons=[],
            soft_warning_reasons=soft_warning_reasons,
            confidence_components=confidence_components,
            position_size_pct=0,
        )
    if plan.reward_risk is None or plan.reward_risk < 1.2:
        return _no_trade_decision_with_plan(
            selected_side,
            plan,
            decision_type='no_trade_conflict',
            current_price=current_price,
            confidence_score=confidence_score,
            confidence_grade=confidence_grade,
            long_score=long_score,
            short_score=short_score,
            no_trade_score=max(no_trade_score, 80),
            trend_follow_score=trend_follow_score,
            reversal_score=reversal_score,
            reasons=['利益幅に対して損切り幅が大きいため見送り。'],
            blocked_reasons=['R/R不足'],
            counter_scenario=basecalc.counter_bias,
            reversal_watch=reversal if reversal.get('status') != 'none' else {},
            price_source=price_source,
            decision_status='wait',
            entry_permission='no_entry',
            validation_level=validation_level,
            hard_block_reasons=[],
            soft_warning_reasons=soft_warning_reasons,
            confidence_components=confidence_components,
            position_size_pct=0,
        )

    decision_type = _decision_type(selected_side, basecalc, reversal)
    expected_return = _expected_return_for_decision(selected_side, basecalc)
    return TradeDecision(
        selected_side=selected_side,
        decision_type=decision_type,
        horizon='3d',
        current_price=current_price,
        entry_price=current_price,
        entry_zone_low=_entry_zone(current_price, selected_side)[0],
        entry_zone_high=_entry_zone(current_price, selected_side)[1],
        target_1=plan.target_1,
        target_2=plan.target_2,
        stop_price=plan.stop_price,
        invalidation_price=plan.invalidation_price,
        reward_risk=plan.reward_risk,
        expected_return_pct=expected_return,
        probability=plan.probability,
        expected_value=plan.expected_value,
        confidence_score=confidence_score,
        confidence_grade=confidence_grade,
        long_score=long_score,
        short_score=short_score,
        no_trade_score=no_trade_score,
        trend_follow_score=trend_follow_score,
        reversal_score=reversal_score,
        counter_scenario=basecalc.counter_bias,
        reversal_watch=reversal if reversal.get('status') != 'none' else {},
        reasons=_decision_reasons(selected_side, macro, basecalc, plan, decision_type),
        warnings=_decision_warnings(macro, basecalc, reversal),
        blocked_reasons=[],
        price_source=price_source,
        decision_status=candidate_status,
        entry_permission=_entry_permission(candidate_status),
        validation_level=validation_level,
        hard_block_reasons=[],
        soft_warning_reasons=soft_warning_reasons,
        confidence_components=confidence_components,
        position_size_pct=_position_size_pct(candidate_status, confidence_score),
        position_size_cap=_position_size_cap(candidate_status, confidence_score),
    )


def _no_trade_decision_with_plan(_side, _plan, **kwargs):
    return no_trade_decision(**kwargs)


def _matrix_label(macro_bias, basecalc_bias, audit):
    if basecalc_bias == 'bullish':
        if macro_bias == 'positive' and audit.status == 'valid':
            return '強気継続', 'bullish', '上昇継続。ただし節目突破を確認する。'
        if macro_bias == 'negative':
            return '短期上昇・中期警戒', 'conditional_bullish', '短期はbasecalcの上方向を優先し、中期はmacroの逆風を警戒。'
        return '条件付き上昇優勢', 'conditional_bullish', '押し目待ち。高値追いは避ける。'
    if basecalc_bias == 'bearish':
        if macro_bias == 'negative':
            return '下落警戒', 'bearish_alert', '戻りは慎重に扱い、下値確認を優先。'
        if macro_bias == 'positive':
            return '短期下落・中期反転待ち', 'sell_rally_watch', '短期はbasecalcの下方向を優先し、中期はmacroの追い風で反転を監視。'
        return '中立・様子見', 'neutral_wait', '方向がそろうまで様子見。'
    return '中立・様子見', 'neutral_wait', '方向が出るまで待つ。'


def _basecalc_evidence(basecalc):
    if basecalc.bias == 'bullish':
        return 'basecalcは日経先物を上昇優勢と判断し、1d/3d/5dも上方向。'
    if basecalc.bias == 'bearish':
        return 'basecalcは日経先物を下落優勢と判断。'
    return 'basecalcは方向を強く示していない。'


def _macro_evidence(macro):
    if macro.bias == 'neutral_inflation_risk':
        return 'macroは景気判断が中立だが、物価再加速リスクと金利上昇リスクが残る。'
    if macro.bias == 'positive':
        return 'macroは経済環境を支援的に見ている。'
    if macro.bias == 'negative':
        return 'macroは経済環境または市場ストレスを警戒している。'
    return macro.summary


def _audit_evidence(audit):
    if audit.status == 'valid':
        return '監査では判断を止める問題は確認されていない。'
    return '、'.join(audit.items[:3]) + 'のため、強い判断にはしない。'


def _grade_from_score(score):
    if score >= 85:
        return 'A'
    if score >= 70:
        return 'B'
    if score >= 60:
        return 'B-'
    if score >= 50:
        return 'C+'
    if score >= 40:
        return 'C'
    return 'D'


def _clamp(value):
    return max(0, min(100, int(round(value))))


def _trade_scores(macro, basecalc, audit, long_plan, short_plan, reversal):
    long_score = 35
    short_score = 35
    no_trade_score = 25 + audit.penalty

    long_score += _direction_points(basecalc, 'long')
    short_score += _direction_points(basecalc, 'short')
    long_score += _expected_points(basecalc, 'long')
    short_score += _expected_points(basecalc, 'short')
    long_score += _macro_points(macro, 'long')
    short_score += _macro_points(macro, 'short')
    long_score += _rr_points(long_plan)
    short_score += _rr_points(short_plan)
    long_score += _ev_points(long_plan)
    short_score += _ev_points(short_plan)

    long_score += min(12, int((basecalc.continuation_score or 0) / 10)) if basecalc.bias == 'bullish' else 0
    short_score += min(12, int((basecalc.continuation_score or 0) / 10)) if basecalc.bias == 'bearish' else 0
    long_score -= min(24, int((basecalc.reversal_risk_score or 0) / 3))
    short_score -= min(24, int((basecalc.rebound_improvement_score or 0) / 3))
    long_score -= audit.penalty
    short_score -= audit.penalty

    if reversal.get('status') == 'entry':
        if reversal.get('side') == 'long':
            long_score += 14
        if reversal.get('side') == 'short':
            short_score += 14
    elif reversal.get('status') == 'watch':
        no_trade_score += 12

    if long_plan.blocked_reasons:
        long_score -= 20
        no_trade_score += 8
    if short_plan.blocked_reasons:
        short_score -= 20
        no_trade_score += 8
    if basecalc.data_quality_score < 60:
        no_trade_score += 35
    no_trade_score += min(20, int((_number((macro.factor_vector or {}).get('event_risk_score')) or 0) / 5))
    return _clamp(long_score), _clamp(short_score), _clamp(no_trade_score)


def _direction_points(basecalc, side):
    if side == 'long':
        return 22 if basecalc.bias == 'bullish' else 10 if basecalc.primary_direction == 'up' else -8 if basecalc.bias == 'bearish' else 0
    return 22 if basecalc.bias == 'bearish' else 10 if basecalc.primary_direction == 'down' else -8 if basecalc.bias == 'bullish' else 0


def _expected_points(basecalc, side):
    values = [
        _number(basecalc.expected_return_1d),
        _number(basecalc.expected_return_3d),
        _number(basecalc.expected_return_5d),
    ]
    values = [value for value in values if value is not None]
    if not values:
        return 0
    avg = sum(values) / len(values)
    signed = avg if side == 'long' else -avg
    return max(-18, min(18, int(round(signed * 12))))


def _macro_points(macro, side):
    factors = macro.factor_vector or {}
    long_filter = _number(factors.get('macro_long_filter'))
    short_filter = _number(factors.get('macro_short_filter'))
    growth = _number(factors.get('growth_score')) or 50
    inflation = _number(factors.get('inflation_risk_score')) or 0
    rates = _number(factors.get('rates_pressure_score')) or 0
    fx = _number(factors.get('fx_support_score')) or 50
    stress = _number(factors.get('credit_stress_score')) or 0
    event = _number(factors.get('event_risk_score')) or 0
    risk_appetite = _number(factors.get('risk_appetite_score')) or 50
    if side == 'long' and long_filter is not None:
        return int(round(
            (long_filter - 1) * 20
            + (growth - 50) / 5
            + (fx - 50) / 12
            + (risk_appetite - 50) / 10
            - inflation / 12
            - rates / 14
            - event / 10
        ))
    if side == 'short' and short_filter is not None:
        return int(round(
            (short_filter - 1) * 20
            + (50 - growth) / 6
            + stress / 10
            + inflation / 14
            + rates / 16
            + event / 14
            - (risk_appetite - 50) / 12
        ))
    if macro.bias == 'positive':
        return 10 if side == 'long' else -8
    if macro.bias == 'negative':
        return 10 if side == 'short' else -8
    if macro.bias == 'neutral_inflation_risk':
        return 6 if side == 'short' else -5
    return 0


def _rr_points(plan):
    if plan.reward_risk is None:
        return -10
    if plan.reward_risk >= 2:
        return 12
    if plan.reward_risk >= 1.2:
        return 8
    return -18


def _ev_points(plan):
    value = getattr(plan, 'expected_value', None)
    if value is None:
        return 0
    if value <= 0:
        return -18
    if value >= 300:
        return 8
    return 4


def _select_side(long_score, short_score, no_trade_score):
    ordered = sorted(
        [('long', long_score), ('short', short_score), ('no_trade', no_trade_score)],
        key=lambda item: item[1],
        reverse=True,
    )
    return ordered[0][0], ordered[0][1], ordered[1][1]


def _no_chase_decision_type(basecalc, reversal, long_plan, short_plan):
    if (basecalc.bias == 'bullish' or basecalc.primary_direction == 'up') and (
        basecalc.reversal_risk_score >= 75 or reversal.get('side') == 'short'
    ):
        if long_plan.blocked_reasons or (long_plan.reward_risk is not None and long_plan.reward_risk < 1.2):
            return 'no_chase_long'
    if (basecalc.bias == 'bearish' or basecalc.primary_direction == 'down') and (
        basecalc.rebound_improvement_score >= 75 or reversal.get('side') == 'long'
    ):
        if short_plan.blocked_reasons or (short_plan.reward_risk is not None and short_plan.reward_risk < 1.2):
            return 'no_chase_short'
    return ''


def _opposite_reversal_watch(side, reversal):
    if not isinstance(reversal, dict):
        return False
    if reversal.get('status') != 'watch':
        return False
    if (reversal.get('score') or 0) < 75:
        return False
    return (
        (side == 'long' and reversal.get('side') == 'short')
        or (side == 'short' and reversal.get('side') == 'long')
    )


def _direction_stopped(basecalc, hard_block_reasons=None):
    return basecalc.allowed_direction in {'stopped', 'none'} and bool(hard_block_reasons)


def _candidate_status(basecalc, confidence_score, validation_level):
    if basecalc.allowed_direction in {'stopped', 'none'}:
        return 'watch_only'
    if confidence_score < 50:
        return 'watch_only'
    warnings = (
        list(basecalc.soft_warning_reasons or [])
        + list(basecalc.validation_warnings or [])
        + list(basecalc.warnings or [])
    )
    gate_rows = (basecalc.validation_gate_status or {}).values()
    if any((row.get('validation_level') if isinstance(row, dict) else '') == 'low' for row in gate_rows):
        return 'candidate_limited'
    if not basecalc.confidence_calibrated:
        return 'candidate_limited'
    if any('検証不足' in reason or '検証件数が不足' in reason for reason in warnings):
        return 'candidate_limited'
    if confidence_score < 70:
        return 'candidate_limited'
    if basecalc.contract_status == 'limited' or validation_level != 'high' or warnings:
        return 'candidate_limited'
    return 'candidate_confirmed'


def _candidate_confidence_cap(decision_status, basecalc=None, validation_level='none'):
    if decision_status == 'watch_only':
        return 59
    if decision_status == 'candidate_limited':
        warnings = (
            list(getattr(basecalc, 'soft_warning_reasons', None) or [])
            + list(getattr(basecalc, 'validation_warnings', None) or [])
            + list(getattr(basecalc, 'warnings', None) or [])
        )
        weak_validation = validation_level in {'none', 'low'}
        low_basecalc = getattr(basecalc, 'confidence_score', 0) < 50
        uncalibrated = not getattr(basecalc, 'confidence_calibrated', False)
        validation_warning = any('検証不足' in reason or '検証件数が不足' in reason for reason in warnings)
        return 59 if weak_validation or low_basecalc or uncalibrated or validation_warning else 69
    return 100


def _position_size_cap(decision_status, confidence_score=0):
    if decision_status == 'watch_only':
        return 'max_25_percent'
    if decision_status == 'candidate_limited':
        if confidence_score < 60:
            return 'max_25_percent'
        return 'max_50_percent'
    if decision_status == 'candidate_confirmed':
        return 'normal'
    return 'none'


def _entry_permission(decision_status):
    if decision_status == 'candidate_confirmed':
        return 'full_entry'
    if decision_status == 'candidate_limited':
        return 'limited_entry'
    if decision_status == 'watch_only':
        return 'watch_only'
    return 'no_entry'


def _position_size_pct(decision_status, confidence_score):
    if decision_status == 'candidate_confirmed':
        return 100
    if decision_status == 'candidate_limited':
        return 50 if confidence_score >= 60 else 25
    return 0


def _validation_level(basecalc):
    rows = [
        row for row in (basecalc.validation_gate_status or {}).values()
        if isinstance(row, dict)
    ]
    levels = {row.get('validation_level') for row in rows}
    if not rows:
        return 'none'
    if 'blocked' in levels:
        return 'none'
    if 'low' in levels:
        return 'low'
    if 'limited' in levels:
        return 'medium'
    if all(level == 'confirmed' for level in levels):
        return 'high'
    return 'medium'


def _reason_groups(basecalc):
    hard_reasons = list(getattr(basecalc, 'hard_stop_reasons', None) or [])
    for reason in basecalc.hard_block_reasons or []:
        if reason not in hard_reasons:
            hard_reasons.append(reason)
    soft_reasons = list(basecalc.soft_warning_reasons or [])
    if basecalc.contract_status == 'error' and not hard_reasons:
        hard_reasons = list(basecalc.stop_reasons or [])
    if basecalc.contract_status != 'error':
        for reason in basecalc.stop_reasons or []:
            if _looks_like_hard_stop(reason):
                if reason not in hard_reasons:
                    hard_reasons.append(reason)
            elif reason not in soft_reasons:
                soft_reasons.append(reason)
    for reason in basecalc.warnings or []:
        if reason and reason not in soft_reasons:
            soft_reasons.append(reason)
    for reason in basecalc.validation_warnings or []:
        if reason and reason not in soft_reasons:
            soft_reasons.append(reason)
    return hard_reasons, soft_reasons


def _looks_like_hard_stop(reason):
    return any(
        term in (reason or '')
        for term in (
            '不一致',
            '逆方向',
            '矛盾',
            '判定可能なデータ状態ではありません',
            '方向予測停止',
            '現在値より下',
            '現在値より上',
        )
    )


def _confidence_components(macro, basecalc, audit, score, validation_level, long_plan, short_plan, hard_block_reasons=None):
    components = _confidence_component_values(macro, basecalc, audit, validation_level, long_plan, short_plan, hard_block_reasons)
    return {
        **components,
        'confidence_cap_reason': basecalc.confidence_cap_reason or audit.confidence_cap or '',
        'validation_level': validation_level,
        'raw_score': score,
        'confidence_formula': 'weighted_components_v1',
        'basecalc_weight': 0.35,
        'macro_weight': 0.2,
        'validation_weight': 0.15,
        'target_weight': 0.15,
        'data_quality_weight': 0.1,
        'intermarket_weight': 0.05,
    }


def _confidence_component_values(macro, basecalc, audit, validation_level, long_plan, short_plan, hard_block_reasons=None):
    best_rr = max(
        [value for value in (long_plan.reward_risk, short_plan.reward_risk) if value is not None],
        default=0,
    )
    validation_quality = {
        'none': 0,
        'low': 35,
        'medium': 65,
        'high': 90,
    }.get(validation_level, 50)
    target_metrics = _target_validation_metrics(basecalc)
    target_quality = _target_quality(target_metrics, best_rr)
    components = {
        'basecalc_direction': basecalc.confidence_score,
        'macro_alignment': macro.confidence_score,
        'validation_quality': validation_quality,
        'target_quality': target_quality,
        'risk_reward': round(best_rr, 2) if best_rr else 0,
        'data_quality': min(macro.data_quality_score, basecalc.data_quality_score),
        'intermarket_confirmation': 10 if basecalc.us_index_available else -5,
        'event_penalty': int((_number((macro.factor_vector or {}).get('event_risk_score')) or 0) / 10),
        'audit_penalty': audit.penalty,
        'hard_block_penalty': 30 if hard_block_reasons else 0,
    }
    if target_metrics:
        if target_metrics.get('target_hit_rate') is not None:
            components['target_hit_rate'] = round(target_metrics['target_hit_rate'], 2)
        if target_metrics.get('stop_hit_rate') is not None:
            components['stop_hit_rate'] = round(target_metrics['stop_hit_rate'], 2)
        if target_metrics.get('avg_realized_rr') is not None:
            components['avg_realized_rr'] = round(target_metrics['avg_realized_rr'], 2)
    return components


def _target_validation_metrics(basecalc):
    rows = []
    validation_gate_status = getattr(basecalc, 'validation_gate_status', None) or {}
    if isinstance(validation_gate_status, dict):
        for horizon in ('1d', '3d', '5d'):
            row = validation_gate_status.get(horizon)
            if isinstance(row, dict):
                rows.extend(_target_metric_row_candidates(row))
    source = getattr(basecalc, 'source', None) or {}
    if isinstance(source, dict):
        performance_by_horizon = source.get('backtest_performance_by_horizon') or {}
        if isinstance(performance_by_horizon, dict):
            for horizon in ('1d', '3d', '5d'):
                row = performance_by_horizon.get(horizon)
                if isinstance(row, dict):
                    rows.extend(_target_metric_row_candidates(row))
    metrics = {
        'target_hit_rate': _average_metric(rows, ('target_t1_hit_rate', 'target_1_hit_rate', 'target_hit_rate')),
        'stop_hit_rate': _average_metric(rows, ('stop_hit_rate', 'invalidation_rate', 'stop_rate')),
        'avg_realized_rr': _average_metric(rows, ('avg_realized_rr', 'realized_rr', 'avg_reward_risk')),
    }
    return {key: value for key, value in metrics.items() if value is not None}


def _target_metric_row_candidates(row):
    rows = [row]
    for key in ('model_vs_baseline', 'state_gate', 'state_direction_gate', 'summary'):
        nested = row.get(key)
        if isinstance(nested, dict):
            rows.append(nested)
    return rows


def _average_metric(rows, keys):
    values = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in keys:
            value = _number(row.get(key))
            if value is None:
                continue
            if 'rate' in key and value > 1:
                value = value / 100
            values.append(value)
            break
    if not values:
        return None
    return sum(values) / len(values)


def _target_quality(metrics, planned_rr):
    if not metrics:
        return 80 if planned_rr >= 1.2 else 35
    target_hit = metrics.get('target_hit_rate')
    stop_hit = metrics.get('stop_hit_rate')
    realized_rr = metrics.get('avg_realized_rr')
    score = 10
    if target_hit is not None:
        score += _clamp(target_hit * 100) * 0.40
    else:
        score += 20
    if stop_hit is not None:
        score += _clamp((1 - stop_hit) * 100) * 0.30
    else:
        score += 15
    rr = realized_rr if realized_rr is not None else planned_rr
    if rr is not None:
        score += _clamp((rr / 1.5) * 100) * 0.25
    else:
        score += 10
    return _clamp(score)


def _decision_type(selected_side, basecalc, reversal):
    if reversal.get('entry_allowed') and reversal.get('side') == selected_side:
        return 'reversal_entry'
    if selected_side == 'long':
        return 'pullback' if basecalc.primary_setup in {'pullback_long', 'breakout_long'} else 'trend_follow'
    return 'rally_sell' if basecalc.primary_setup in {'pullback_short', 'failed_breakout_short'} else 'trend_follow'


def _decision_reasons(selected_side, macro, basecalc, plan, decision_type):
    side_label = 'ロング' if selected_side == 'long' else 'ショート'
    reasons = [
        f'{side_label}スコアが最も高く、{decision_type}として採用。',
        f'target、stop、R/R {plan.reward_risk:.2f} が成立。',
    ]
    if macro.bias in {'positive', 'negative', 'neutral_inflation_risk'}:
        reasons.append(f'Macro補正: {macro.bias}')
    if basecalc.scenario_probabilities:
        reasons.append('Basecalcのシナリオ確率を加味。')
    return reasons[:4]


def _decision_warnings(macro, basecalc, reversal):
    warnings = []
    if macro.bias == 'neutral_inflation_risk':
        warnings.append('インフレ再加速リスクで上値確信度を抑制')
    if reversal.get('status') == 'watch':
        warnings.append(reversal.get('label') or '逆張りWATCH')
    if basecalc.shock_score >= 55:
        warnings.append('ショックリスク上昇')
    for reason in (
        list(basecalc.stop_reasons or [])
        + list(basecalc.soft_warning_reasons or [])
        + list(basecalc.validation_warnings or [])
        + list(basecalc.warnings or [])
    ):
        if reason and reason not in warnings:
            warnings.append(reason)
    return warnings[:4]


def _expected_return_for_decision(side, basecalc):
    value = _number(basecalc.expected_return_3d)
    if value is None:
        value = _number(basecalc.expected_return_1d)
    if value is None:
        return None
    return value if side == 'long' else -value


def _entry_zone(current_price, side):
    if current_price is None:
        return None, None
    width = max(20, current_price * 0.0015)
    if side == 'long':
        return round(current_price - width), round(current_price + width / 2)
    return round(current_price - width / 2), round(current_price + width)


def _trade_confidence(macro, basecalc, audit, validation_level, long_plan, short_plan, hard_block_reasons=None):
    components = _confidence_component_values(
        macro,
        basecalc,
        audit,
        validation_level,
        long_plan,
        short_plan,
        hard_block_reasons,
    )
    intermarket_quality = 100 if basecalc.us_index_available else 0
    score = (
        components['basecalc_direction'] * 0.35
        + components['macro_alignment'] * 0.20
        + components['validation_quality'] * 0.15
        + components['target_quality'] * 0.15
        + components['data_quality'] * 0.10
        + intermarket_quality * 0.05
        - components['event_penalty']
        - components['audit_penalty']
        - components['hard_block_penalty']
    )
    if audit.confidence_cap:
        score = min(score, GRADE_MAX_SCORE.get(audit.confidence_cap, score))
    if macro.confidence_score < 50 and basecalc.confidence_score < 50:
        score = min(score, 49)
    if macro.factor_vector.get('macro_stale'):
        score = min(score, 59)
    return _clamp(score)


def _macro_trend_bonus(macro, basecalc):
    if macro.bias == 'positive' and basecalc.bias == 'bullish':
        return 8
    if macro.bias == 'negative' and basecalc.bias == 'bearish':
        return 8
    if macro.bias in {'positive', 'negative'}:
        return -6
    return 0


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
