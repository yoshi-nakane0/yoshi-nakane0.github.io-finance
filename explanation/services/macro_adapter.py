from datetime import datetime

from django.utils import timezone

from macro.services.dashboard_cache import load_static_macro_payload
from macro.services.house_view import build_house_view_context

from .contracts import MacroSignal


def load_macro_signal() -> MacroSignal:
    context = _load_house_view_context()
    confidence_score = _safe_int(context.get('confidence_score'), 0)
    data_quality = context.get('data_quality_report') or {}
    warnings = []
    warnings.extend(context.get('blocking_issues') or [])
    warnings.extend(context.get('main_risks') or [])
    warnings.extend(data_quality.get('warnings') or [])

    return MacroSignal(
        bias=_macro_bias(context),
        summary=context.get('house_view') or 'macro判断はデータ確認中です。',
        confidence_score=confidence_score,
        confidence_grade=context.get('confidence_grade') or _grade_from_score(confidence_score),
        data_quality_score=_safe_int(data_quality.get('freshness_score'), confidence_score),
        display_status=context.get('display_status') or 'reference',
        publish_status=context.get('publish_status') or context.get('display_status') or 'reference',
        warnings=_dedupe(warnings),
        source=context,
        as_of=_parse_as_of(context.get('generated_at') or context.get('as_of')),
    )


def _load_house_view_context():
    static_payload = load_static_macro_payload() or {}
    static_generated_at = static_payload.get('generated_at')
    context = build_house_view_context()
    if context.get('display_allowed') and _safe_int(context.get('confidence_score'), 0) > 0:
        return _with_generated_at(context, static_generated_at)
    static_house_view = static_payload.get('house_view') or {}
    if static_house_view:
        return _with_generated_at(static_house_view, static_generated_at)
    return _with_generated_at(context, static_generated_at)


def _with_generated_at(context, generated_at):
    if not generated_at or context.get('generated_at'):
        return context
    enriched = dict(context)
    enriched['generated_at'] = generated_at
    return enriched


def _macro_bias(context):
    if not context.get('display_allowed', True):
        return 'data_unavailable'
    label = context.get('regime_label') or ''
    probabilities = context.get('probabilities') or {}
    inflation_risk = float(probabilities.get('inflation_reacceleration') or 0)
    stress = float(probabilities.get('financial_stress') or 0)
    if inflation_risk >= 0.7:
        return 'neutral_inflation_risk'
    if stress >= 0.65 or label in {'contraction', 'financial_stress'}:
        return 'negative'
    if label in {'expansion', 'expansion_with_inflation_risk', 'recovery'}:
        return 'positive'
    if label in {'slowdown'}:
        return 'neutral_cautious'
    return 'neutral'


def _safe_int(value, default):
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


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


def _dedupe(items):
    result = []
    for item in items:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result[:8]


def _parse_as_of(value):
    if not value:
        return timezone.now()
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return timezone.now()
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed
