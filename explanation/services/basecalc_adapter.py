from copy import deepcopy
from datetime import datetime, timedelta

from django.utils import timezone

from basecalc.market_bars import attach_saved_daily_bars
from basecalc.market_shock import build_market_shock_context
from basecalc.outcomes import performance_summary
from basecalc.services.decision_context import (
    build_basecalc_decision_context,
    enrich_basecalc_context,
    ensure_plain_summary_card_display,
)
from basecalc.status import (
    intermarket_status_entry,
    price_status_entry,
    status_display_rows,
)
from basecalc.snapshot import load_basecalc_snapshot
from basecalc.views import (
    _manual_price_status_row,
    _manual_price_override_context,
    _snapshot_with_manual_price_override,
    get_stale_futures_snapshot,
)
from basecalc.world_model import build_world_model

from .contracts import BasecalcSignal


def load_basecalc_signal(price_override=None) -> BasecalcSignal:
    snapshot = load_basecalc_snapshot() or {}
    if price_override is not None:
        context = _manual_price_context(snapshot, price_override)
    else:
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


def _manual_price_context(saved_snapshot, price):
    saved_context = dict(saved_snapshot or {})
    enriched_saved_context = enrich_basecalc_context(deepcopy(saved_context)) if saved_context else {}
    base_snapshot = get_stale_futures_snapshot()
    if isinstance(base_snapshot, dict):
        base_snapshot = attach_saved_daily_bars(base_snapshot)
    else:
        base_snapshot = _fallback_snapshot_from_saved_context(saved_context, price)
    manual_snapshot = _snapshot_with_manual_price_override(base_snapshot, price)
    intermarket_context = _intermarket_context(saved_context)
    world_model = build_world_model(price, manual_snapshot, intermarket_context)
    ensure_plain_summary_card_display(world_model)
    if not _manual_recalc_is_usable(world_model) and enriched_saved_context:
        return _saved_context_with_manual_price(
            enriched_saved_context,
            price,
            basis='saved_basecalc_with_manual_price',
        )
    market_shock = _safe_market_shock_context(manual_snapshot, intermarket_context)
    basecalc_status = {
        'price_data': price_status_entry(
            manual_snapshot,
            world_model.get('readiness_level'),
        ),
        'intermarket': intermarket_status_entry(intermarket_context),
    }
    status_rows = status_display_rows(basecalc_status, world_model)
    manual_price = _manual_price_override_context(price)
    if manual_price['active']:
        status_rows.append(_manual_price_status_row(manual_price))
    decision = build_basecalc_decision_context(
        world_model,
        market_shock,
        status_rows,
        performance_summary('1d', is_backtest=True),
    )
    return {
        **saved_context,
        'data': {
            **(saved_context.get('data') or {}),
            'price_display': _price_display(price),
            'world_model': world_model,
        },
        'decision': decision,
        'world_model': world_model,
        'market_shock': market_shock,
        'intermarket_technicals': world_model.get('intermarket_technicals') or {},
        'basecalc_status': basecalc_status,
        'basecalc_status_rows': status_rows,
        'manual_price_override': manual_price,
        'manual_price_mode': _manual_price_mode('recalculated_basecalc_with_manual_price'),
        'price_param': str(int(price)),
    }


def _manual_recalc_is_usable(world_model):
    if not isinstance(world_model, dict):
        return False
    if world_model.get('readiness_level') != 'ready':
        return False
    return _safe_int(world_model.get('confidence_score'), 0) >= 40


def _saved_context_with_manual_price(context, price, basis):
    result = deepcopy(context)
    world_model = deepcopy(result.get('world_model') or {})
    features = deepcopy(world_model.get('features') or {})
    world_model['price'] = price
    world_model['manual_price_override'] = True
    world_model['manual_price'] = int(price)
    features['price'] = price
    features['close'] = price
    world_model['features'] = features
    data = deepcopy(result.get('data') or {})
    data['price_display'] = _price_display(price)
    data['world_model'] = world_model
    result['data'] = data
    result['world_model'] = world_model

    manual_price = _manual_price_override_context(price)
    result['manual_price_override'] = manual_price
    result['manual_price_mode'] = _manual_price_mode(basis)
    result['price_param'] = str(int(price))

    decision = deepcopy(result.get('decision') or {})
    if decision:
        decision['price'] = price
    result['decision'] = decision

    status_rows = list(result.get('basecalc_status_rows') or [])
    if manual_price['active'] and not any(row.get('key') == 'manual_price' for row in status_rows):
        status_rows.append(_manual_price_status_row(manual_price))
    result['basecalc_status_rows'] = status_rows
    return result


def _manual_price_mode(basis):
    return {
        'basis': basis,
        'macro_source': 'Macroページの保存済み最新判断',
        'basecalc_source': 'Basecalcページの保存済みチャート判断に手入力価格を反映',
    }


def _intermarket_context(context):
    world_model = context.get('world_model') or (context.get('data') or {}).get('world_model') or {}
    return (
        context.get('intermarket_technicals')
        or world_model.get('us_index_confirmation')
        or world_model.get('intermarket_technicals')
        or {}
    )


def _safe_market_shock_context(base_snapshot, intermarket_context):
    try:
        return build_market_shock_context(
            base_snapshot=base_snapshot,
            intermarket_context=intermarket_context,
        )
    except Exception:
        return {
            'has_data': False,
            'summary': '市場ショック判定データなし',
            'tone': 'unknown',
            'rows': [],
        }


def _fallback_snapshot_from_saved_context(context, price):
    world_model = context.get('world_model') or (context.get('data') or {}).get('world_model') or {}
    features = world_model.get('features') or {}
    previous = _safe_float(features.get('close') or world_model.get('price')) or price
    return {
        'symbol': features.get('source_symbol') or 'NIY=F',
        'source': features.get('source_name') or 'saved_snapshot',
        'instrument_key': features.get('instrument_key') or 'cme_nikkei_futures',
        'instrument_type': features.get('instrument_type') or 'futures',
        'price': price,
        'previous_close': previous,
        'change_pct': None,
        'opens': [previous, price],
        'highs': [previous, price],
        'lows': [previous, price],
        'closes': [previous, price],
        'volumes': [0, 0],
        'timestamps': [
            int((timezone.now() - timedelta(days=1)).timestamp()),
            int(timezone.now().timestamp()),
        ],
        'fetched_at': timezone.now(),
        'fallback_used': True,
    }


def _price_display(value):
    try:
        return f'{float(value):,.0f}'
    except (TypeError, ValueError):
        return ''


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
