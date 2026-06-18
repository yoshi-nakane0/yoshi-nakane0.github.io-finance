from .contracts import AuditResult, BasecalcSignal, FusionResult, MacroSignal


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
    alignment_bonus = {'aligned': 5, 'partial': 0, 'conflict': -10, 'blocked': -30}.get(
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


def _matrix_label(macro_bias, basecalc_bias, audit):
    if basecalc_bias == 'bullish':
        if macro_bias == 'positive' and audit.status == 'valid':
            return '強気継続', 'bullish', '上昇継続。ただし節目突破を確認する。'
        if macro_bias == 'negative':
            return '戻り売り警戒', 'sell_rally_watch', '上昇局面でも追いかけず、戻り売りリスクを警戒。'
        return '条件付き上昇優勢', 'conditional_bullish', '押し目待ち。高値追いは避ける。'
    if basecalc_bias == 'bearish':
        if macro_bias == 'negative':
            return '下落警戒', 'bearish_alert', '戻りは慎重に扱い、下値確認を優先。'
        if macro_bias == 'positive':
            return '中立・様子見', 'neutral_wait', '下落が止まるまで新規判断を急がない。'
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
