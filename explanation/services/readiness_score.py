def build_readiness_score(snapshot, validation_summary):
    validation_summary = validation_summary or {}
    total_count = validation_summary.get('total_count') or 0
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
        'minimum_required_results': 50,
        'remaining_results_to_90': max(0, 50 - total_count),
        'note': '検証件数と表示整合性から見たページ状態です。売買結果を保証するものではありません。',
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
    total = summary.get('total_count') or 0
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


def _label(score):
    if score >= 90:
        return '実用運用可'
    if score >= 70:
        return '検証運用中'
    if score >= 50:
        return '参考表示'
    return '判定停止'
