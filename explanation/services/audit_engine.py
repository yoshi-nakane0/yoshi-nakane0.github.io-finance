from datetime import timedelta

from django.utils import timezone

from .contracts import AuditResult, BasecalcSignal, MacroSignal


BASECALC_STALE_AFTER = timedelta(days=1)


def evaluate_audit(macro: MacroSignal, basecalc: BasecalcSignal) -> AuditResult:
    items = []
    penalty = 0
    confidence_cap = None
    alignment = alignment_status(macro.bias, basecalc.bias)

    if basecalc.contract_status == 'error':
        items.append('basecalcの方向判断は停止')
        items.extend(basecalc.stop_reasons[:2])
        penalty += 30
        confidence_cap = 'D'
        level = 'blocked'
    elif _basecalc_is_stale(basecalc):
        items.append('Basecalcデータが古いため判定停止')
        penalty += 30
        confidence_cap = 'D'
        level = 'blocked'
    elif macro.bias == 'data_unavailable' or basecalc.readiness_level == 'blocked':
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

    if level != 'blocked' and alignment == 'timeframe_divergence':
        items.append('macroとbasecalcは時間軸分岐')

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
    result_alignment = 'blocked' if level == 'blocked' else alignment
    return AuditResult(
        level=level,
        status=status,
        alignment_status=result_alignment,
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
    return 'timeframe_divergence'


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


def _basecalc_is_stale(basecalc):
    if basecalc.as_of is None:
        return False
    as_of = basecalc.as_of
    if timezone.is_naive(as_of):
        as_of = timezone.make_aware(as_of, timezone.get_current_timezone())
    return timezone.now() - as_of > BASECALC_STALE_AFTER
