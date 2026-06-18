from .contracts import AuditResult, BasecalcSignal, MacroSignal


def evaluate_audit(macro: MacroSignal, basecalc: BasecalcSignal) -> AuditResult:
    items = []
    penalty = 0
    confidence_cap = None
    alignment = alignment_status(macro.bias, basecalc.bias)

    if macro.bias == 'data_unavailable' or basecalc.readiness_level == 'blocked':
        items.append('判定に必要な主要データが不足')
        penalty += 30
        confidence_cap = 'D'
        level = 'blocked'
    else:
        level = 'valid'

    if basecalc.readiness_level == 'limited':
        items.append('basecalcの判定状態が限定的')
        penalty += 8
        confidence_cap = _stricter_cap(confidence_cap, 'B')

    if not basecalc.us_index_available:
        items.append('米国3指数確認が不足')
        penalty += 3
        confidence_cap = _stricter_cap(confidence_cap, 'B')
        if level == 'valid':
            level = 'warning'

    if not basecalc.can_show_prediction:
        items.append('予測ゲート停止中')
        penalty += 3
        confidence_cap = _stricter_cap(confidence_cap, 'B-')
        if level == 'valid':
            level = 'warning'

    if basecalc.fallback_used:
        items.append('fallback使用あり')
        penalty += 12
        confidence_cap = _stricter_cap(confidence_cap, 'C+')
        if level == 'valid':
            level = 'warning'

    if macro.warnings:
        items.append(macro.warnings[0])
        confidence_cap = _stricter_cap(confidence_cap, 'B')
        if level == 'valid':
            level = 'warning'

    if alignment == 'conflict':
        items.append('macroとbasecalcの方向が矛盾')
        penalty += 10
        confidence_cap = _stricter_cap(confidence_cap, 'B')
        if level == 'valid':
            level = 'warning'

    data_quality_score = min(macro.data_quality_score, basecalc.data_quality_score)
    if data_quality_score < 60:
        items.append('データ品質が低い')
        penalty += 10
        confidence_cap = _stricter_cap(confidence_cap, 'C+')
        if level == 'valid':
            level = 'warning'

    if not items:
        items.append('データ鮮度、品質、検証条件は許容範囲')

    status = 'blocked' if level == 'blocked' else 'limited' if level == 'warning' else 'valid'
    return AuditResult(
        level=level,
        status=status,
        alignment_status=alignment,
        items=items[:6],
        penalty=penalty,
        confidence_cap=confidence_cap,
        data_quality_score=data_quality_score,
    )


def alignment_status(macro_bias, basecalc_bias):
    macro_direction = _macro_direction(macro_bias)
    technical_direction = _technical_direction(basecalc_bias)
    if macro_direction == 'bad_data' or technical_direction == 'bad_data':
        return 'blocked'
    if macro_direction == technical_direction:
        return 'aligned'
    if 'neutral' in {macro_direction, technical_direction}:
        return 'partial'
    return 'conflict'


def _macro_direction(bias):
    if bias == 'data_unavailable':
        return 'bad_data'
    if bias in {'positive'}:
        return 'positive'
    if bias in {'negative'}:
        return 'negative'
    return 'neutral'


def _technical_direction(bias):
    if bias == 'missing':
        return 'bad_data'
    if bias == 'bullish':
        return 'positive'
    if bias == 'bearish':
        return 'negative'
    return 'neutral'


def _stricter_cap(current, candidate):
    if current is None:
        return candidate
    order = {'A': 5, 'B': 4, 'B-': 3, 'C+': 2, 'C': 1, 'D': 0}
    return current if order.get(current, 0) <= order.get(candidate, 0) else candidate
