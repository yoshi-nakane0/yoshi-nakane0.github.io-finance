def build_readiness_score(snapshot, validation_summary):
    validation_summary = validation_summary or {}
    total_count = _safe_int(validation_summary.get('total_count'))
    actionable_count = _safe_int(validation_summary.get('actionable_count'))
    score = 0
    score += 25 if snapshot.audit_level != 'blocked' else 0
    score += _gate_sync_score(snapshot)
    score += _validation_score(validation_summary)
    score += 15 if snapshot.as_of else 0
    score += 10
    score += 10 if validation_summary.get('available') else 5
    score = min(score, _validation_cap(total_count))
    return {
        'score': score,
        'label': _label(score),
        'score_type': 'validation_readiness_score',
        'title': '検証 readiness',
        'minimum_required_results': 50,
        'remaining_results_to_90': max(0, 50 - total_count),
        'validation_state_display': _validation_state_display(total_count),
        'actionable_result_display': _actionable_result_display(total_count, actionable_count),
        'validation_attention_display': _validation_attention_display(total_count, actionable_count),
        'note': '検証件数と表示整合性から見た補助指標です。現在判断を止める条件ではありません。',
    }


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
