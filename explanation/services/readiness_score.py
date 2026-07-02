def build_readiness_score(snapshot, validation_summary):
    validation_summary = validation_summary or {}
    total_count = _safe_int(validation_summary.get('total_count'))
    actionable_count = _safe_int(validation_summary.get('actionable_count'))
    validation_readiness_score = _validation_readiness_score(snapshot, validation_summary, total_count)
    system_quality_components = _system_quality_components(snapshot)
    system_quality_score = _system_quality_score(system_quality_components)
    decision_confidence_score = _decision_confidence_score(snapshot)
    return {
        'score': validation_readiness_score,
        'label': _label(validation_readiness_score),
        'score_type': 'score_bundle',
        'title': '判定スコア',
        'system_quality_score': system_quality_score,
        'system_quality_label': _system_quality_label(system_quality_score),
        'system_quality_components': system_quality_components,
        'decision_confidence_score': decision_confidence_score,
        'decision_confidence_label': _decision_confidence_label(snapshot),
        'validation_readiness_score': validation_readiness_score,
        'validation_readiness_label': _label(validation_readiness_score),
        'minimum_required_results': 50,
        'remaining_results_to_90': max(0, 50 - total_count),
        'validation_state_display': _validation_state_display(total_count),
        'actionable_result_display': _actionable_result_display(total_count, actionable_count),
        'validation_attention_display': _validation_attention_display(total_count, actionable_count),
        'note': 'ページ完成度、今回判断の信頼度、ライブ検証の蓄積度を分けた補助指標です。現在判断を止める条件ではありません。',
    }


def _validation_readiness_score(snapshot, validation_summary, total_count):
    score = 0
    score += 25 if snapshot.audit_level != 'blocked' else 0
    score += _gate_sync_score(snapshot)
    score += _validation_score(validation_summary)
    score += 15 if snapshot.as_of else 0
    score += 10
    score += 10 if validation_summary.get('available') else 5
    return min(score, _validation_cap(total_count))


def _system_quality_score(components):
    return min(100, sum(row['score'] for row in components))


def _system_quality_components(snapshot):
    decision = snapshot.trade_decision or {}
    stopped_score = 20 if snapshot.audit_level != 'blocked' and not decision.get('hard_block_reasons') else 8
    return [
        _quality_component(
            '判断材料',
            20 if snapshot.evidence else 12,
            20,
            '根拠あり' if snapshot.evidence else '判断材料が不足',
        ),
        _quality_component(
            '理由分離',
            20 if _has_reason_separation(decision) else 10,
            20,
            'hard/soft分離済み' if _has_reason_separation(decision) else '理由分離が不足',
        ),
        _quality_component(
            '停止状態',
            stopped_score,
            20,
            '判定継続可' if stopped_score == 20 else '判定停止理由あり',
        ),
        _quality_component(
            '判定契約',
            20 if _has_decision_contract(decision) else 10,
            20,
            '状態契約あり' if _has_decision_contract(decision) else '状態契約が不足',
        ),
        _quality_component(
            '表示文言',
            20 if snapshot.action_posture and snapshot.final_label else 12,
            20,
            '表示文言あり' if snapshot.action_posture and snapshot.final_label else '表示文言が不足',
        ),
    ]


def _quality_component(label, score, max_score, message):
    return {
        'label': label,
        'score': score,
        'max_score': max_score,
        'value': f'{score}/{max_score}',
        'status': 'OK' if score >= max_score else '要確認',
        'message': message,
    }


def _has_reason_separation(decision):
    return 'hard_block_reasons' in decision and 'soft_warning_reasons' in decision


def _has_decision_contract(decision):
    return bool(
        decision.get('decision_status')
        and decision.get('entry_permission')
        and decision.get('selected_side')
    )


def _decision_confidence_score(snapshot):
    decision = snapshot.trade_decision or {}
    score = decision.get('confidence_score')
    if score is None:
        score = snapshot.confidence_score
    score = _safe_int(score)
    if _is_blocked_decision(snapshot):
        return min(score, 39)
    return score


def _system_quality_label(score):
    if score >= 90:
        return '実用表示可'
    if score >= 70:
        return '改善中'
    if score >= 50:
        return '参考表示'
    return '要修正'


def _decision_confidence_label(snapshot):
    decision = snapshot.trade_decision or {}
    if _is_blocked_decision(snapshot):
        return '判定停止'
    status = decision.get('decision_status')
    if status == 'candidate_confirmed':
        return '通常候補'
    if status == 'candidate_limited':
        return '限定候補'
    if status == 'watch_only':
        return '監視候補'
    if status == 'blocked':
        return '判定停止'
    return '待機'


def _is_blocked_decision(snapshot):
    decision = snapshot.trade_decision or {}
    return (
        snapshot.audit_level == 'blocked'
        or bool(decision.get('hard_block_reasons'))
        or decision.get('decision_type') == 'no_trade_data_blocked'
        or decision.get('decision_status') == 'blocked'
    )


def _gate_sync_score(snapshot):
    decision = snapshot.trade_decision or {}
    if decision.get('decision_type') == 'no_trade_direction_stopped':
        has_candidate = any(
            decision.get(key) is not None
            for key in ('target_1', 'target_2', 'stop_price', 'reward_risk')
        )
        return 0 if has_candidate else 20
    return 20


def _validation_score(summary):
    total = _safe_int(summary.get('total_count'))
    if total >= 50:
        return 20
    if total >= 30:
        return 16
    if total >= 10:
        return 12
    if total > 0:
        return 8
    return 0


def _validation_cap(total):
    if total < 10:
        return 69
    if total < 30:
        return 79
    if total < 50:
        return 89
    return 100


def _validation_state_display(total):
    if total >= 50:
        return '検証済み'
    if total >= 10:
        return '一部検証済み'
    return '検証中'


def _actionable_result_display(total, actionable):
    if total < 10 or actionable <= 0:
        return '不足'
    if total >= 50 and actionable >= 10:
        return '十分'
    return '蓄積中'


def _validation_attention_display(total, actionable):
    if total < 50:
        return '検証不足のため建玉サイズを制限'
    if actionable < 10:
        return '売買候補の実績が少ないため建玉サイズを確認'
    return '検証済み。通常のリスク管理を継続'


def _label(score):
    if score >= 90:
        return '実績確認済み'
    if score >= 70:
        return '検証運用中'
    if score >= 50:
        return '検証参考'
    return '検証不足'


def _safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
