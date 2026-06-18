from datetime import datetime

from django.utils import timezone

from basecalc.services.decision_context import enrich_basecalc_context
from basecalc.snapshot import load_basecalc_snapshot

from .contracts import BasecalcSignal


def load_basecalc_signal() -> BasecalcSignal:
    snapshot = load_basecalc_snapshot() or {}
    context = enrich_basecalc_context(dict(snapshot)) if snapshot else {}
    world_model = context.get('world_model') or {}
    decision = context.get('decision') or {}
    confidence_score = _safe_int(decision.get('confidence_score') or world_model.get('confidence_score'), 0)
    data_quality_score = _safe_int(
        decision.get('data_quality_score') or world_model.get('data_quality_score'),
        confidence_score,
    )
    intermarket = (
        world_model.get('us_index_confirmation')
        or world_model.get('intermarket_technicals')
        or {}
    )
    warnings = []
    warnings.extend(world_model.get('confidence_warnings') or [])
    warnings.extend(world_model.get('warnings') or [])
    warnings.extend((world_model.get('readiness') or {}).get('warnings') or [])
    warnings.extend(decision.get('prediction_stop_reasons') or [])
    warnings.extend(intermarket.get('evidence') or [])

    return BasecalcSignal(
        bias=_technical_bias(world_model, decision),
        summary=_summary(world_model, decision),
        confidence_score=confidence_score,
        confidence_grade=decision.get('confidence') or world_model.get('confidence') or _grade_from_score(confidence_score),
        data_quality_score=data_quality_score,
        readiness_level=decision.get('readiness_level') or world_model.get('readiness_level') or 'blocked',
        can_show_prediction=bool(decision.get('can_show_prediction')),
        support=_target_price(decision.get('downside_target'), world_model.get('downside_targets')),
        resistance=_target_price(decision.get('upside_target'), world_model.get('upside_targets')),
        invalidation=_safe_float(world_model.get('invalidation_price') or decision.get('invalidation')),
        direction_1d=_horizon_bias(world_model, '1d'),
        direction_3d=_horizon_bias(world_model, '3d'),
        direction_5d=_horizon_bias(world_model, '5d'),
        fallback_used=bool(decision.get('fallback_used') or (world_model.get('data_quality') or {}).get('fallback_used')),
        us_index_available=(intermarket.get('readiness') or {}).get('usable') is not False,
        warnings=_dedupe(warnings),
        source=context,
        as_of=_parse_as_of(world_model.get('as_of') or snapshot.get('generated_at')),
    )


def _technical_bias(world_model, decision):
    direction = world_model.get('direction') or decision.get('direction')
    if direction == 'up':
        return 'bullish'
    if direction == 'down':
        return 'bearish'
    return 'neutral'


def _summary(world_model, decision):
    label = decision.get('direction_label') or world_model.get('direction_label') or '判定確認中'
    horizons = world_model.get('horizons') or {}
    directions = [
        _horizon_bias(world_model, horizon)
        for horizon in ('1d', '3d', '5d')
    ]
    if directions and all(direction == 'up' for direction in directions):
        return f'日経先物は{label}。1d/3d/5dは上方向。'
    if directions and all(direction == 'down' for direction in directions):
        return f'日経先物は{label}。1d/3d/5dは下方向。'
    if horizons:
        return f'日経先物は{label}。短期方向はまちまち。'
    return f'日経先物は{label}。'


def _horizon_bias(world_model, horizon):
    item = (world_model.get('horizons') or {}).get(horizon) or {}
    bias = item.get('main_bias') or 'neutral'
    if bias == 'range':
        return 'neutral'
    return bias


def _target_price(primary, targets):
    if isinstance(primary, dict):
        value = _safe_float(primary.get('price'))
        if value is not None:
            return value
    for target in targets or []:
        if isinstance(target, dict):
            value = _safe_float(target.get('price'))
            if value is not None:
                return value
    return None


def _safe_float(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.replace(',', '').strip()
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
