from copy import deepcopy
from datetime import datetime
from uuid import uuid4

from django.utils import timezone

from .model_version import BASECALC_MODEL_VERSION
from .validation_gate import HORIZONS, build_validation_gate


PRICE_TOLERANCE = 1
LOW_SAMPLE_THRESHOLD = 10


def apply_output_contract(
    world_model,
    *,
    display_price=None,
    latest_price=None,
    validation_report=None,
    performance_by_horizon=None,
):
    if not isinstance(world_model, dict):
        return _empty_contract(display_price)

    model_price = _number(world_model.get("price"))
    display_price = _number(display_price)
    if display_price is None:
        display_price = _number(latest_price)
    if display_price is None:
        display_price = model_price

    errors = []
    warnings = []
    gate_hard_reasons = []
    gate_soft_warnings = []
    confidence_cap_reasons = []
    validation_gate = build_validation_gate(
        world_model,
        validation_report=validation_report,
        performance_by_horizon=performance_by_horizon,
    )
    allowed_horizons = _audit_horizons(
        world_model,
        validation_gate,
        errors,
        gate_hard_reasons,
        gate_soft_warnings,
    )

    if model_price is None or display_price is None:
        errors.append("現在値または計算基準価格がありません")
    elif abs(model_price - display_price) > PRICE_TOLERANCE:
        errors.append("現在値と計算基準価格が不一致")

    _audit_targets(world_model, display_price, errors)
    _audit_ranges(world_model, display_price, errors)
    _audit_probability_samples(world_model, errors)
    confidence_calibrated = _audit_confidence(world_model, validation_report, warnings)
    us_index_status = _audit_us_indices(world_model, warnings)
    _apply_confidence_cap(world_model, confidence_calibrated, validation_gate, warnings, confidence_cap_reasons)

    readiness_level = world_model.get("readiness_level")
    if readiness_level != "ready":
        errors.append("判定可能なデータ状態ではありません")

    hard_block_reasons = _dedupe(errors + gate_hard_reasons)
    soft_warning_reasons = _dedupe(gate_soft_warnings + warnings)
    status = "error" if errors else "limited" if soft_warning_reasons or gate_hard_reasons else "ok"
    direction = world_model.get("direction")
    model_horizon_keys = set((world_model.get("horizons") or {}).keys()) or set(allowed_horizons.keys())
    directional_allowed = status != "error" and direction in {"up", "down"} and any(
        item.get("direction_allowed")
        for horizon, item in allowed_horizons.items()
        if horizon in model_horizon_keys
    )
    target_display_allowed = status != "error"
    probability_display_allowed = target_display_allowed and not _probability_display_restricted(world_model)
    available_display = "停止"
    if status != "error":
        available_display = "方向・目標・レンジ" if directional_allowed else "ATRレンジ・支持抵抗・反転警戒"
    if status == "ok" and _confirmed_contract(confidence_calibrated, validation_gate, us_index_status, directional_allowed):
        status = "confirmed"
    contract = {
        "snapshot_id": world_model.get("snapshot_id") or str(uuid4()),
        "model_version": world_model.get("model_version") or BASECALC_MODEL_VERSION,
        "generated_at": world_model.get("as_of") or timezone.now().isoformat(),
        "source_timestamp": world_model.get("last_updated_display") or "",
        "model_price": model_price,
        "display_price": display_price,
        "latest_price": _number(latest_price),
        "price_source": ((world_model.get("source_status") or {}).get("source") or ""),
        "price_age_minutes": world_model.get("stale_minutes"),
        "ohlcv_bar_count": _ohlcv_bar_count(world_model),
        "data_quality_score": world_model.get("data_quality_score"),
        "readiness_level": readiness_level,
        "directional_allowed": directional_allowed,
        "target_calculated_from_price": model_price,
        "range_calculated_from_price": model_price,
        "confidence_calculated_from_snapshot_id": world_model.get("snapshot_id") or "",
        "confidence_calibrated": confidence_calibrated,
        "confidence_status": "検証済み" if confidence_calibrated else "未較正",
        "validation_report_version": (validation_report or {}).get("schema") or "",
        "validation_gate_status": validation_gate,
        "allowed_horizons": allowed_horizons,
        "allowed_direction": world_model.get("direction") if directional_allowed else "stopped",
        "validated_targets": _validated_targets(world_model, status),
        "invalidated_targets": _invalidated_targets(world_model),
        "us_index_status": us_index_status,
        "contract_status": status,
        "hard_stop_reasons": hard_block_reasons,
        "hard_block_reasons": hard_block_reasons,
        "soft_warning_reasons": soft_warning_reasons,
        "validation_warnings": _dedupe(gate_soft_warnings + confidence_cap_reasons),
        "confidence_cap_reason": " / ".join(_dedupe(confidence_cap_reasons)),
        "display_status": _display_status(status, directional_allowed),
        "stop_reasons": _dedupe(hard_block_reasons + soft_warning_reasons),
        "target_display_allowed": target_display_allowed,
        "probability_display_allowed": probability_display_allowed,
        "explanation_allowed": _explanation_allowed_status(status, directional_allowed),
        "available_display": available_display,
    }
    _apply_contract_to_world_model(world_model, contract)
    return contract


def _apply_contract_to_world_model(world_model, contract):
    world_model["output_contract"] = contract
    world_model["contract_status"] = contract["contract_status"]
    world_model["stop_reasons"] = contract["stop_reasons"]
    world_model["display_price"] = contract["display_price"]
    world_model["model_price"] = contract["model_price"]
    if contract["contract_status"] != "error":
        return
    world_model["directional_allowed"] = False
    world_model["direction"] = "neutral"
    world_model["direction_label"] = "方向判断停止"
    world_model["primary_direction"] = "range"
    world_model["primary_scenario"] = "方向判断停止"
    world_model["scenario_label"] = "判定停止"
    world_model["action_note"] = "矛盾があるため、強いロング・ショートは出しません。"
    world_model["upside_targets"] = []
    world_model["downside_targets"] = []
    world_model["target_ranges"] = []
    for item in (world_model.get("horizons") or {}).values():
        if isinstance(item, dict):
            item["main_bias"] = "range"
            item["main_bias_label"] = "方向感なし"
            item["display_allowed"] = False
            item["stop_reason"] = " / ".join(contract["stop_reasons"][:2])


def _audit_targets(world_model, display_price, errors):
    if display_price is None:
        return
    for target in world_model.get("upside_targets") or []:
        price = _number((target or {}).get("price"))
        if price is not None and price <= display_price:
            errors.append("上値目標が現在値より下にあります")
    for target in world_model.get("downside_targets") or []:
        price = _number((target or {}).get("price"))
        if price is not None and price >= display_price:
            errors.append("下値目標が現在値より上にあります")


def _audit_ranges(world_model, display_price, errors):
    if display_price is None:
        return
    for row in world_model.get("target_ranges") or []:
        high = _number((row or {}).get("high"))
        low = _number((row or {}).get("low"))
        if high is not None and high < display_price:
            errors.append("レンジ上限が現在値より下にあります")
        if low is not None and low > display_price:
            errors.append("レンジ下限が現在値より上にあります")


def _audit_horizons(world_model, validation_gate, errors, gate_hard_reasons, gate_soft_warnings):
    horizons = world_model.get("horizons") or {}
    allowed = {}
    for horizon in HORIZONS:
        item = horizons.get(horizon) or {}
        bias = item.get("main_bias")
        expected = _number(item.get("expected_return_pct"))
        direction_allowed = True
        reasons = []
        if (bias == "up" and expected is not None and expected < 0) or (
            bias == "down" and expected is not None and expected > 0
        ):
            direction_allowed = False
            reasons.append("方向と期待リターンが矛盾")
            errors.append("方向と期待リターンが矛盾")
        gate = validation_gate.get(horizon) or {}
        if gate and not gate.get("direction_allowed", True):
            direction_allowed = False
            hard_reasons = gate.get("hard_reasons") or gate.get("reasons") or []
            reasons.extend(hard_reasons)
            gate_hard_reasons.extend(hard_reasons)
        if gate:
            gate_soft_warnings.extend(gate.get("soft_reasons") or gate.get("warnings") or [])
        allowed[horizon] = {
            "direction_allowed": direction_allowed,
            "target_probability_allowed": True,
            "display_mode": "directional" if direction_allowed else "range_only",
            "reasons": _dedupe(reasons),
            "warnings": _dedupe((gate or {}).get("warnings") or []),
            "hard_reasons": _dedupe(reasons),
            "soft_reasons": _dedupe((gate or {}).get("soft_reasons") or (gate or {}).get("warnings") or []),
            "validation_level": (gate or {}).get("validation_level") or ("confirmed" if direction_allowed else "blocked"),
            "confidence_penalty": (gate or {}).get("confidence_penalty") or 0,
        }
    return allowed


def _audit_probability_samples(world_model, errors):
    similar = world_model.get("similar_summary") or {}
    case_count = int(similar.get("case_count") or 0)
    if case_count and case_count < LOW_SAMPLE_THRESHOLD:
        for target in (world_model.get("upside_targets") or []) + (world_model.get("downside_targets") or []):
            if isinstance(target, dict) and target.get("probability") is not None:
                errors.append("類似事例不足なのに到達確率を表示しています")
                return


def _audit_confidence(world_model, validation_report, warnings):
    rows = []
    for payload in ((validation_report or {}).get("horizons") or {}).values():
        rows.extend(payload.get("confidence_calibration_rows") or [])
    if not rows:
        return False
    ordered = sorted(
        [row for row in rows if isinstance(row, dict)],
        key=lambda row: str(row.get("bucket") or row.get("confidence_bucket") or ""),
    )
    last = None
    for row in ordered:
        value = _number(row.get("avg_return_pct") or row.get("directional_accuracy"))
        if value is None:
            continue
        if last is not None and value < last:
            warnings.append("信頼度が未較正です")
            return False
        last = value
    return True


def _audit_us_indices(world_model, warnings):
    intermarket = world_model.get("us_index_confirmation") or world_model.get("intermarket_technicals") or {}
    readiness = intermarket.get("readiness") if isinstance(intermarket, dict) else {}
    components = intermarket.get("components") if isinstance(intermarket, dict) else {}
    usable = not isinstance(readiness, dict) or readiness.get("usable") is not False
    has_components = bool(components)
    if not usable or not has_components:
        warnings.append("米国3指数確認が不足")
        return "missing"
    return "confirmed"


def _apply_confidence_cap(world_model, confidence_calibrated, validation_gate, warnings, cap_reasons):
    cap = 100
    if not confidence_calibrated and _number(world_model.get("confidence_score")) is not None:
        cap = min(cap, 64)
        cap_reasons.append("信頼度が未較正です")
    similar = world_model.get("similar_summary") or {}
    if int(similar.get("case_count") or 0) < LOW_SAMPLE_THRESHOLD:
        cap = min(cap, 59)
        reason = "類似事例不足のため信頼度を限定"
        warnings.append(reason)
        cap_reasons.append(reason)
    if _current_state_is_blocked(validation_gate):
        cap = min(cap, 49)
        reason = "局面別成績が弱いため信頼度を50未満に制限"
        warnings.append(reason)
        cap_reasons.append(reason)
    elif _current_state_is_limited(validation_gate):
        cap = min(cap, 59)
        reason = "局面別検証不足のため信頼度を限定"
        warnings.append(reason)
        cap_reasons.append(reason)
    score = _number(world_model.get("confidence_score"))
    if score is None or score <= cap:
        return
    world_model["confidence_score"] = int(cap)
    world_model["confidence"] = _confidence_label(cap)


def _current_state_is_blocked(validation_gate):
    for row in (validation_gate or {}).values():
        state_gate = row.get("state_gate") if isinstance(row, dict) else {}
        if isinstance(state_gate, dict) and not state_gate.get("direction_allowed", True):
            return True
        state_direction_gate = row.get("state_direction_gate") if isinstance(row, dict) else {}
        if (
            isinstance(state_direction_gate, dict)
            and state_direction_gate.get("has_direction_signal")
            and not state_direction_gate.get("direction_allowed", True)
        ):
            return True
    return False


def _current_state_is_limited(validation_gate):
    for row in (validation_gate or {}).values():
        if not isinstance(row, dict):
            continue
        if (row.get("validation_level") or "") in {"low", "limited"}:
            return True
    return False


def _confirmed_contract(confidence_calibrated, validation_gate, us_index_status, directional_allowed):
    return all([
        confidence_calibrated,
        us_index_status == "confirmed",
        directional_allowed,
        _validation_is_confirmed(validation_gate),
    ])


def _validation_is_confirmed(validation_gate):
    rows = [row for row in (validation_gate or {}).values() if isinstance(row, dict)]
    if not rows:
        return False
    return all((row.get("validation_level") or "") == "confirmed" for row in rows)


def _display_status(status, directional_allowed):
    if status == "error":
        return "blocked"
    if not directional_allowed:
        return "watch_only"
    if status == "confirmed":
        return "candidate_confirmed"
    if status == "limited":
        return "limited_candidate"
    return "candidate_ok"


def _explanation_allowed_status(status, directional_allowed):
    if status == "error":
        return "blocked"
    if status == "confirmed":
        return "confirmed"
    if status == "limited":
        return "limited"
    return "allowed"


def _confidence_label(score):
    if score >= 70:
        return "High"
    if score >= 50:
        return "Middle"
    return "Low"


def _validated_targets(world_model, status):
    if status == "error":
        return {"upside": [], "downside": []}
    return {
        "upside": deepcopy(world_model.get("upside_targets") or []),
        "downside": deepcopy(world_model.get("downside_targets") or []),
    }


def _invalidated_targets(world_model):
    return {
        "upside": deepcopy(world_model.get("upside_targets") or []),
        "downside": deepcopy(world_model.get("downside_targets") or []),
    }


def _probability_display_restricted(world_model):
    similar = world_model.get("similar_summary") or {}
    return (
        int(similar.get("case_count") or 0) < LOW_SAMPLE_THRESHOLD
        or similar.get("is_statistically_valid") is not True
    )


def _ohlcv_bar_count(world_model):
    bar_counts = ((world_model.get("readiness") or {}).get("bar_counts") or {})
    if isinstance(bar_counts, dict):
        return bar_counts.get("closes") or bar_counts.get("daily")
    return None


def _empty_contract(display_price):
    return {
        "contract_status": "error",
        "display_price": display_price,
        "model_price": None,
        "directional_allowed": False,
        "target_display_allowed": False,
        "probability_display_allowed": False,
        "hard_block_reasons": ["world model がありません"],
        "hard_stop_reasons": ["world model がありません"],
        "soft_warning_reasons": [],
        "validation_warnings": [],
        "confidence_cap_reason": "",
        "display_status": "blocked",
        "stop_reasons": ["world model がありません"],
        "explanation_allowed": "blocked",
    }


def _number(value):
    try:
        if isinstance(value, datetime):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _dedupe(items):
    result = []
    for item in items:
        if item and item not in result:
            result.append(item)
    return result
