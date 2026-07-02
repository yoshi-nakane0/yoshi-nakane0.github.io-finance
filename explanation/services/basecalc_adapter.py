from copy import deepcopy
from datetime import datetime, timedelta

from django.utils import timezone

from basecalc.market_bars import attach_saved_daily_bars
from basecalc.market_shock import build_market_shock_context
from basecalc.outcomes import performance_summary
from basecalc.output_contract import apply_output_contract
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
from basecalc.validation_report import load_validation_report
from basecalc.views import (
    _manual_price_status_row,
    _manual_price_override_context,
    _parse_saved_snapshot_timestamp,
    _saved_world_model_timestamp,
    _snapshot_with_manual_price_override,
    get_stale_futures_snapshot,
)
from basecalc.world_model import build_world_model
from basecalc.signal_contract import build_basecalc_signal_contract

from .contracts import BasecalcSignal


def load_basecalc_signal(price_override=None) -> BasecalcSignal:
    snapshot = load_basecalc_snapshot() or {}
    if price_override is not None:
        context = _manual_price_context(snapshot, price_override)
    else:
        context = enrich_basecalc_context(dict(snapshot)) if snapshot else {}
    world_model = context.get('world_model') or {}
    if isinstance(world_model, dict):
        existing_basecalc_signal = dict(world_model.get('basecalc_signal') or {})
        validation_report = _validation_report_for_basecalc_context(context, world_model)
        apply_output_contract(
            world_model,
            display_price=(world_model.get('output_contract') or {}).get('display_price') or world_model.get('price'),
            validation_report=validation_report,
            performance_by_horizon=context.get('backtest_performance_by_horizon') or {},
        )
        generated_basecalc_signal = build_basecalc_signal_contract(world_model)
        for key in (
            'hard_stop_reasons',
            'hard_block_reasons',
            'soft_warning_reasons',
            'validation_warnings',
            'confidence_cap_reason',
            'display_status',
        ):
            if not generated_basecalc_signal.get(key) and existing_basecalc_signal.get(key):
                generated_basecalc_signal[key] = existing_basecalc_signal.get(key)
        world_model['basecalc_signal'] = generated_basecalc_signal
        context = enrich_basecalc_context(context)
        world_model = context.get('world_model') or {}
    output_contract = world_model.get('output_contract') or {}
    decision = context.get('decision') or {}
    basecalc_signal = world_model.get('basecalc_signal') or {}
    confidence_score = _safe_int(
        _first_present(
            output_contract.get('confidence_score'),
            basecalc_signal.get('confidence_score'),
            decision.get('confidence_score'),
            world_model.get('confidence_score'),
        ),
        0,
    )
    data_quality_score = _safe_int(
        decision.get('data_quality_score') or world_model.get('data_quality_score'),
        confidence_score,
    )
    intermarket = (
        world_model.get('us_index_confirmation')
        or world_model.get('intermarket_technicals')
        or {}
    )
    hard_block_reasons = output_contract.get('hard_block_reasons') or basecalc_signal.get('hard_block_reasons') or []
    soft_warning_reasons = output_contract.get('soft_warning_reasons') or basecalc_signal.get('soft_warning_reasons') or []
    validation_warnings = output_contract.get('validation_warnings') or basecalc_signal.get('validation_warnings') or []
    confidence_cap_reason = output_contract.get('confidence_cap_reason') or basecalc_signal.get('confidence_cap_reason') or ''
    display_status = _normalize_display_status(output_contract.get('display_status') or basecalc_signal.get('display_status') or '')
    warnings = []
    warnings.extend(soft_warning_reasons or output_contract.get('stop_reasons') or [])
    warnings.extend(validation_warnings)
    warnings.extend(world_model.get('confidence_warnings') or [])
    warnings.extend(world_model.get('warnings') or [])
    warnings.extend((world_model.get('readiness') or {}).get('warnings') or [])
    warnings.extend(decision.get('prediction_stop_reasons') or [])
    warnings.extend(intermarket.get('evidence') or [])

    current_price = _safe_float(
        output_contract.get('display_price')
        or basecalc_signal.get('display_price')
        or world_model.get('display_price')
        or world_model.get('price')
    )
    invalidation = world_model.get('invalidation') or {}
    if not isinstance(invalidation, dict):
        invalidation = {}

    return BasecalcSignal(
        bias=_technical_bias(world_model, decision, output_contract),
        summary=_summary(world_model, decision, output_contract),
        confidence_score=confidence_score,
        confidence_grade=(
            output_contract.get('confidence_label')
            or basecalc_signal.get('confidence_label')
            or decision.get('confidence')
            or world_model.get('confidence')
            or _grade_from_score(confidence_score)
        ),
        data_quality_score=data_quality_score,
        readiness_level=decision.get('readiness_level') or world_model.get('readiness_level') or 'blocked',
        can_show_prediction=bool(decision.get('can_show_prediction')) and output_contract.get('contract_status') != 'error',
        support=None if output_contract.get('contract_status') == 'error' else (
            _target_price(decision.get('downside_target'), world_model.get('downside_targets'))
            or _near_level_price(world_model, 'downside')
        ),
        resistance=None if output_contract.get('contract_status') == 'error' else (
            _target_price(decision.get('upside_target'), world_model.get('upside_targets'))
            or _near_level_price(world_model, 'upside')
        ),
        invalidation=_safe_float(world_model.get('invalidation_price') or decision.get('invalidation')),
        current_price=current_price,
        price_source='manual' if (context.get('manual_price_override') or {}).get('active') else 'market_data',
        direction_1d=_horizon_bias(world_model, '1d'),
        direction_3d=_horizon_bias(world_model, '3d'),
        direction_5d=_horizon_bias(world_model, '5d'),
        primary_direction=world_model.get('primary_direction') or basecalc_signal.get('primary_direction') or world_model.get('direction') or 'range',
        primary_setup=world_model.get('primary_setup') or basecalc_signal.get('primary_setup') or 'range_wait',
        counter_bias=world_model.get('counter_bias') or basecalc_signal.get('counter_bias') or {},
        scenario_probabilities=world_model.get('scenario_probabilities') or basecalc_signal.get('scenario_probabilities') or {},
        horizons=world_model.get('horizons') or basecalc_signal.get('horizons') or {},
        expected_return_1d=_expected_return(world_model, '1d'),
        expected_return_3d=_expected_return(world_model, '3d'),
        expected_return_5d=_expected_return(world_model, '5d'),
        bullish_invalidation=_safe_float(invalidation.get('bullish') or world_model.get('bullish_invalidation')),
        bearish_invalidation=_safe_float(invalidation.get('bearish') or world_model.get('bearish_invalidation')),
        reversal_risk_score=_safe_int(world_model.get('reversal_risk_score'), 0),
        rebound_improvement_score=_safe_int(world_model.get('rebound_improvement_score'), 0),
        continuation_score=_safe_int(world_model.get('continuation_score'), 0),
        shock_score=_safe_int(world_model.get('shock_score'), 0),
        fallback_used=bool(decision.get('fallback_used') or (world_model.get('data_quality') or {}).get('fallback_used')),
        us_index_available=(intermarket.get('readiness') or {}).get('usable') is not False,
        contract_status=output_contract.get('contract_status') or 'unchecked',
        allowed_direction=output_contract.get('allowed_direction') or 'stopped',
        allowed_horizons=output_contract.get('allowed_horizons') or {},
        validated_targets=output_contract.get('validated_targets') or {},
        invalidated_targets=output_contract.get('invalidated_targets') or {},
        stop_reasons=output_contract.get('stop_reasons') or [],
        hard_block_reasons=hard_block_reasons,
        soft_warning_reasons=soft_warning_reasons,
        validation_warnings=validation_warnings,
        confidence_cap_reason=confidence_cap_reason,
        display_status=display_status,
        confidence_calibrated=bool(output_contract.get('confidence_calibrated')),
        validation_gate_status=output_contract.get('validation_gate_status') or {},
        warnings=_dedupe(warnings),
        source=context,
        as_of=_parse_as_of(world_model.get('as_of') or snapshot.get('generated_at')),
    )


def _validation_report_for_basecalc_context(context, world_model):
    report = load_validation_report()
    if not report:
        return None
    report_timestamp = _parse_saved_snapshot_timestamp(report.get('generated_at'))
    saved_timestamp = (
        _parse_saved_snapshot_timestamp((context or {}).get('generated_at'))
        or _saved_world_model_timestamp(context, world_model)
    )
    if report_timestamp is not None and saved_timestamp is not None and report_timestamp < saved_timestamp:
        return None
    return report


def _manual_price_context(saved_snapshot, price):
    saved_context = dict(saved_snapshot or {})
    enriched_saved_context = enrich_basecalc_context(deepcopy(saved_context)) if saved_context else {}
    base_snapshot = get_stale_futures_snapshot()
    if isinstance(base_snapshot, dict):
        base_snapshot = attach_saved_daily_bars(base_snapshot)
    else:
        base_snapshot = _fallback_snapshot_from_saved_context(saved_context, price)
        base_snapshot = attach_saved_daily_bars(base_snapshot)
    manual_snapshot = _snapshot_with_manual_price_override(base_snapshot, price)
    intermarket_context = _intermarket_context(saved_context)
    world_model = build_world_model(price, manual_snapshot, intermarket_context)
    ensure_plain_summary_card_display(world_model)
    if not _manual_recalc_is_usable(world_model) and enriched_saved_context:
        return _saved_context_with_manual_price(
            enriched_saved_context,
            price,
            basis='saved_basecalc_with_manual_price_recalc_unavailable',
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
    performance = performance_summary('1d', is_backtest=True)
    performance_by_horizon = {
        horizon: performance_summary(horizon, is_backtest=True)
        for horizon in ('1d', '3d', '5d')
    }
    apply_output_contract(
        world_model,
        display_price=price,
        validation_report=load_validation_report(),
        performance_by_horizon=performance_by_horizon,
    )
    world_model['basecalc_signal'] = build_basecalc_signal_contract(world_model)
    decision = build_basecalc_decision_context(
        world_model,
        market_shock,
        status_rows,
        performance,
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
        'backtest_performance_by_horizon': performance_by_horizon,
        'manual_price_override': manual_price,
        'manual_price_mode': _manual_price_mode('recalculated_basecalc_with_manual_price'),
        'price_param': str(int(price)),
    }


def _manual_recalc_is_usable(world_model):
    if not isinstance(world_model, dict):
        return False
    return world_model.get('readiness_level') == 'ready'


def _saved_context_with_manual_price(context, price, basis):
    result = deepcopy(context)
    world_model = deepcopy(result.get('world_model') or {})
    world_model['manual_price_override'] = True
    world_model['manual_price'] = int(price)
    apply_output_contract(
        world_model,
        display_price=price,
        latest_price=price,
        validation_report=load_validation_report(),
        performance_by_horizon=result.get('backtest_performance_by_horizon') or {},
    )
    world_model['basecalc_signal'] = build_basecalc_signal_contract(world_model)
    data = deepcopy(result.get('data') or {})
    data['price_display'] = _price_display(world_model.get('price'))
    data['world_model'] = world_model
    result['data'] = data
    result['world_model'] = world_model

    manual_price = _manual_price_override_context(price)
    result['manual_price_override'] = manual_price
    result['manual_price_mode'] = _manual_price_mode(basis)
    result['price_param'] = str(int(world_model.get('price') or price))

    result = enrich_basecalc_context(result)

    status_rows = list(result.get('basecalc_status_rows') or [])
    if manual_price['active'] and not any(row.get('key') == 'manual_price' for row in status_rows):
        status_rows.append(_manual_price_status_row(manual_price))
    result['basecalc_status_rows'] = status_rows
    return result


def _manual_price_mode(basis):
    basecalc_source = (
        'Basecalcページの保存済み判断を固定し、手入力価格との差は契約エラーとして停止'
        if basis == 'saved_basecalc_with_manual_price_recalc_unavailable'
        else 'Basecalcページを手入力価格で再計算'
    )
    return {
        'basis': basis,
        'macro_source': 'Macroページの保存済み最新判断',
        'basecalc_source': basecalc_source,
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
        'fallback_used': False,
    }


def _price_display(value):
    try:
        return f'{float(value):,.0f}'
    except (TypeError, ValueError):
        return ''


def _technical_bias(world_model, decision, output_contract=None):
    if (output_contract or {}).get('contract_status') == 'error':
        return 'neutral'
    direction = world_model.get('direction') or decision.get('direction')
    if direction == 'up':
        return 'bullish'
    if direction == 'down':
        return 'bearish'
    return 'neutral'


def _summary(world_model, decision, output_contract=None):
    if (output_contract or {}).get('contract_status') == 'error':
        reasons = (output_contract or {}).get('stop_reasons') or ['出力整合性を確認中']
        return f"basecalcの方向判断は停止。理由：{reasons[0]}"
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


def _near_level_price(world_model, side):
    near_levels = (world_model.get('near_levels') or {}).get(side) or []
    for level in near_levels:
        if not isinstance(level, dict):
            continue
        value = _safe_float(level.get('price'))
        if value is not None:
            return value
    return None


def _expected_return(world_model, horizon):
    value = world_model.get(f'expected_return_{horizon}')
    if value is not None:
        return _safe_float(value)
    item = (world_model.get('horizons') or {}).get(horizon) or {}
    if item.get('expected_return_pct') is not None:
        return _safe_float(item.get('expected_return_pct'))
    item = (world_model.get('expected_returns') or {}).get(horizon) or {}
    if isinstance(item, dict):
        return _safe_float(item.get('value') or item.get('expected_return_pct'))
    return _safe_float(item)


def _safe_float(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.replace(',', '').strip()
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_present(*values):
    for value in values:
        if value is not None and value != '':
            return value
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


def _normalize_display_status(value):
    return {
        'limited_candidate': 'candidate_limited',
        'confirmed_candidate': 'candidate_confirmed',
        'watch_candidate': 'watch_only',
    }.get(value or '', value or '')


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
