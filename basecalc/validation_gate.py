HORIZONS = ("1d", "3d", "5d")
MIN_DIRECTION_SIGNAL_ACCURACY = 0.55
MIN_DIRECTION_SIGNAL_SAMPLES = 30


def build_validation_gate(world_model, validation_report=None, performance_by_horizon=None):
    world_model = world_model or {}
    validation_report = validation_report or {}
    performance_by_horizon = performance_by_horizon or {}
    result = {}
    for horizon in HORIZONS:
        report = _horizon_report(validation_report, horizon)
        summary = report.get("summary") or performance_by_horizon.get(horizon) or {}
        baseline = _baseline_gate(summary)
        state_gate = _state_gate(world_model, report)
        state_direction_gate = _state_direction_gate(world_model, report)
        if state_direction_gate.get("has_direction_signal"):
            allowed = state_direction_gate["direction_allowed"]
            reasons = state_direction_gate["reasons"]
        else:
            allowed = baseline["direction_allowed"] and state_gate["direction_allowed"]
            reasons = baseline["reasons"] + state_gate["reasons"]
        result[horizon] = {
            "direction_allowed": allowed,
            "target_probability_allowed": allowed,
            "display_mode": "directional" if allowed else "range_only",
            "reasons": reasons,
            "model_vs_baseline": baseline,
            "state_gate": state_gate,
            "state_direction_gate": state_direction_gate,
        }
    return result


def _horizon_report(validation_report, horizon):
    horizons = validation_report.get("horizons") if isinstance(validation_report, dict) else {}
    payload = (horizons or {}).get(horizon) or {}
    return payload if isinstance(payload, dict) else {}


def _baseline_gate(summary):
    comparison = (summary or {}).get("baseline_comparison") or {}
    rows = comparison.get("rows") or []
    model = _row_by_key(rows, "model")
    atr = _row_by_key(rows, "atr_range")
    if not model or not atr:
        return {
            "direction_allowed": True,
            "reasons": [],
            "sample_count": comparison.get("sample_count"),
        }
    model_score = _score_row(model)
    atr_score = _score_row(atr)
    if atr_score > model_score:
        return {
            "direction_allowed": False,
            "reasons": ["現行モデルがATRベースラインを下回るため"],
            "sample_count": comparison.get("sample_count"),
            "model_score": model_score,
            "atr_score": atr_score,
        }
    return {
        "direction_allowed": True,
        "reasons": [],
        "sample_count": comparison.get("sample_count"),
        "model_score": model_score,
        "atr_score": atr_score,
    }


def _state_gate(world_model, report):
    state_key = (world_model or {}).get("state_key")
    state_label = (world_model or {}).get("state_label") or "現在局面"
    if not state_key:
        return {"direction_allowed": True, "reasons": []}
    for row in report.get("state_summaries") or []:
        if not isinstance(row, dict) or row.get("state_key") != state_key:
            continue
        avg_return = _float(row.get("avg_return_pct"))
        accuracy = _float(row.get("directional_accuracy"))
        if (avg_return is not None and avg_return < 0) or (accuracy is not None and accuracy < 0.5):
            return {
                "direction_allowed": False,
                "reasons": [f"{state_label}の過去成績が弱いため"],
                "avg_return_pct": avg_return,
                "directional_accuracy": accuracy,
            }
    return {"direction_allowed": True, "reasons": []}


def _state_direction_gate(world_model, report):
    direction = (world_model or {}).get("direction")
    state_key = (world_model or {}).get("state_key")
    state_label = (world_model or {}).get("state_label") or "現在局面"
    if "state_direction_summaries" not in report:
        return {"direction_allowed": True, "reasons": [], "has_direction_signal": False}
    if direction not in {"up", "down"} or not state_key:
        return {"direction_allowed": False, "reasons": [], "has_direction_signal": False}
    direction_label = "上方向" if direction == "up" else "下方向"
    for row in report.get("state_direction_summaries") or []:
        if (
            not isinstance(row, dict)
            or row.get("state_key") != state_key
            or row.get("direction") != direction
        ):
            continue
        sample_count = int(row.get("total_predictions") or 0)
        accuracy = _float(row.get("directional_accuracy"))
        avg_return = _float(row.get("avg_return_pct"))
        reasons = []
        if sample_count < MIN_DIRECTION_SIGNAL_SAMPLES:
            reasons.append(f"{state_label}の{direction_label}検証件数が不足しているため")
        if accuracy is not None and accuracy < MIN_DIRECTION_SIGNAL_ACCURACY:
            reasons.append(f"{state_label}の{direction_label}精度が基準未達のため")
        if avg_return is not None and (
            (direction == "up" and avg_return < 0)
            or (direction == "down" and avg_return > 0)
        ):
            reasons.append(f"{state_label}の{direction_label}平均損益が逆方向のため")
        return {
            "direction_allowed": not reasons,
            "reasons": reasons,
            "has_direction_signal": True,
            "sample_count": sample_count,
            "directional_accuracy": accuracy,
            "avg_return_pct": avg_return,
            "required_accuracy": MIN_DIRECTION_SIGNAL_ACCURACY,
        }
    return {
        "direction_allowed": False,
        "reasons": [f"{state_label}の{direction_label}検証が不足しているため"],
        "has_direction_signal": True,
        "sample_count": 0,
        "required_accuracy": MIN_DIRECTION_SIGNAL_ACCURACY,
    }


def _row_by_key(rows, key):
    for row in rows:
        if isinstance(row, dict) and row.get("key") == key:
            return row
    return {}


def _score_row(row):
    values = [
        _float(row.get("risk_adjusted_return_pct")),
        _float(row.get("balanced_accuracy")),
        _float(row.get("directional_accuracy")),
    ]
    return sum(value for value in values if value is not None)


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
