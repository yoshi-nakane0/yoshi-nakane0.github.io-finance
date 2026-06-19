def build_basecalc_decision_context(
    world_model,
    market_shock,
    status_rows,
    performance=None,
):
    world_model = world_model or {}
    market_shock = market_shock or {}
    intermarket = world_model.get("us_index_confirmation") or world_model.get("intermarket_technicals") or {}
    data_quality = world_model.get("data_quality") or {}
    readiness_display = world_model.get("readiness_display") or {}

    return {
        "price": world_model.get("price"),
        "last_updated": world_model.get("last_updated_display"),
        "readiness_level": world_model.get("readiness_level"),
        "readiness_label": _readiness_label(world_model.get("readiness_level")),
        "direction": world_model.get("direction"),
        "direction_label": world_model.get("direction_label") or "判定不可",
        "state_label": world_model.get("state_label") or "状態不明",
        "confidence": world_model.get("confidence") or "Low",
        "confidence_score": int(world_model.get("confidence_score") or 0),
        "data_quality_level": data_quality.get("level") or "unknown",
        "data_quality_score": data_quality.get("score")
        or world_model.get("data_quality_score")
        or 0,
        "fallback_used": data_quality.get("fallback_used") is True,
        "daily_bars": readiness_display.get("daily_bars"),
        "main_reason": _top_evidence(world_model),
        "risk_reason": _top_risk(world_model, market_shock),
        "upside_target": _primary_target(world_model.get("upside_targets")),
        "downside_target": _primary_target(world_model.get("downside_targets")),
        "invalidation": world_model.get("invalidation_display"),
        "range_1d": _primary_range(world_model.get("target_ranges")),
        "market_stress": _market_stress_summary(world_model, market_shock),
        "us_index_confirmation": _us_index_confirmation_summary(intermarket),
        "status_summary": _status_summary(status_rows),
        "can_show_prediction": can_show_prediction(world_model, performance),
        "prediction_stop_reasons": prediction_stop_reasons(world_model, performance),
    }


def enrich_basecalc_context(context):
    if not isinstance(context, dict):
        return context
    world_model = context.get("world_model") or {}
    _ensure_intermarket_display_defaults(world_model)
    _ensure_target_display_defaults(world_model)
    market_shock = context.get("market_shock") or {}
    status_rows = context.get("basecalc_status_rows") or []
    context["decision"] = build_basecalc_decision_context(
        world_model,
        market_shock,
        status_rows,
        context.get("backtest_performance_by_horizon", {}).get("1d")
        or context.get("performance"),
    )
    context.setdefault("detail_mode", False)
    return context


def _ensure_intermarket_display_defaults(world_model):
    if not isinstance(world_model, dict):
        return
    intermarket = world_model.get("us_index_confirmation") or world_model.get("intermarket_technicals") or {}
    if not intermarket:
        intermarket = {
            "confirmation_score": 0,
            "confirmation_label": "mixed",
            "risk_label": "technical_confirm",
            "components": {},
            "evidence": ["米国3指数データ待ち"],
            "readiness": {
                "level": "blocked",
                "usable": False,
                "reason": "米国3指数データなし",
            },
        }
    world_model.setdefault("us_index_confirmation", intermarket)
    world_model.setdefault("intermarket_technicals", intermarket)
    world_model.setdefault("primary_setup_label", world_model.get("state_label") or "状態確認中")
    world_model.setdefault("primary_setup", "range_wait")
    world_model.setdefault("technical_regime", world_model.get("state_key") or "range")
    world_model.setdefault("chase_risk", "unknown")
    if not world_model.get("scenarios"):
        world_model["scenarios"] = {
            "baseline": {
                "text": world_model.get("main_scenario") or "日経先物テクニカルを確認中です。",
            },
            "upside": {
                "text": "日経先物の上値抵抗ゾーン突破を確認します。",
            },
            "downside": {
                "text": "日経先物の下値支持ゾーン割れを確認します。",
            },
        }
    if not world_model.get("horizons"):
        direction = world_model.get("direction") or "neutral"
        main_bias = "up" if direction == "up" else "down" if direction == "down" else "range"
        world_model["horizons"] = {
            horizon: {
                "main_bias": main_bias,
                "setup_label": world_model.get("primary_setup_label") or "",
            }
            for horizon in ("1d", "3d", "5d")
        }


def can_show_prediction(world_model, performance=None):
    world_model = world_model or {}
    similar = world_model.get("similar_summary") or {}
    data_quality = world_model.get("data_quality") or {}

    base_gate = (
        world_model.get("readiness_level") == "ready"
        and int(world_model.get("confidence_score") or 0) >= 45
        and int(similar.get("case_count") or 0) >= 30
        and similar.get("is_statistically_valid") is True
        and data_quality.get("fallback_used") is not True
        and data_quality.get("level") not in {"bad"}
    )
    if not base_gate:
        return False
    if performance is None:
        return True
    return _has_baseline_validation(performance)


def prediction_stop_reasons(world_model, performance=None):
    world_model = world_model or {}
    if can_show_prediction(world_model, performance):
        return []
    similar = world_model.get("similar_summary") or {}
    data_quality = world_model.get("data_quality") or {}
    reasons = []
    if world_model.get("readiness_level") != "ready":
        reasons.append("判定状態が未達")
    if int(world_model.get("confidence_score") or 0) < 45:
        reasons.append("信頼度不足")
    if int(similar.get("case_count") or 0) < 30:
        reasons.append("類似局面不足")
    if similar.get("is_statistically_valid") is not True:
        reasons.append("検証件数不足")
    if data_quality.get("fallback_used") is True:
        reasons.append("fallback使用")
    if data_quality.get("level") in {"bad"}:
        reasons.append("データ品質不足")
    if performance is not None and not _has_baseline_validation(performance):
        if _has_baseline_metrics(performance):
            reasons.append("ベースライン比較未達")
        else:
            reasons.append("ベースライン比較未整備")
    return reasons or ["予測表示条件を満たしていません"]


def _top_evidence(world_model):
    evidence = world_model.get("evidence") or []
    if not evidence:
        return ["主要根拠はデータ待ちです"]
    return [str(item) for item in evidence[:3]]


def _top_risk(world_model, market_shock):
    reasons = []
    if int(world_model.get("reversal_risk_score") or 0) >= 60:
        reasons.append("反落警戒")
    if int(world_model.get("rebound_improvement_score") or 0) >= 60:
        reasons.append("反発警戒")
    if int(world_model.get("shock_score") or 0) >= 55:
        reasons.append("突発性")
    if market_shock.get("has_data") and market_shock.get("tone") == "negative":
        reasons.append(market_shock.get("summary") or "外部市場ストレス")
    intermarket = world_model.get("us_index_confirmation") or {}
    if intermarket.get("confirmation_label") in {"confirm_down", "divergent"}:
        reasons.append("米国3指数確認が弱い")
    return reasons[:3] or ["目立つ警戒点は限定的です"]


def _primary_target(targets):
    for index, target in enumerate(targets or []):
        if isinstance(target, dict) and target.get("price") is not None:
            return {
                "label": target.get("display_label") or _target_display_label(target.get("label"), index),
                "price": target.get("price"),
                "reason": target.get("reason") or "",
                "probability_label": target.get("probability_label") or _target_probability_label(target),
            }
    return None


def _ensure_target_display_defaults(world_model):
    if not isinstance(world_model, dict):
        return
    for key in ("upside_targets", "downside_targets"):
        for index, target in enumerate(world_model.get(key) or []):
            if not isinstance(target, dict):
                continue
            target.setdefault("display_label", _target_display_label(target.get("label"), index))
            target.setdefault("probability_label", _target_probability_label(target))
            target.setdefault("distance_label", _target_distance_label(target.get("distance_pct")))
            target.setdefault("sample_label", _target_sample_label(target.get("sample_count")))
            target.setdefault("reliability_label", _target_reliability_label(target.get("reliability")))
    near_levels = world_model.get("near_levels") or {}
    for key in ("upside", "downside"):
        for level in near_levels.get(key) or []:
            if not isinstance(level, dict):
                continue
            level.setdefault("distance_label", _target_distance_label(level.get("distance_pct")))


def _target_display_label(label, index):
    label_text = str(label or "").strip().upper()
    if label_text.startswith("T") and label_text[1:].isdigit():
        return f"第{label_text[1:]}候補"
    if not label_text:
        return f"第{index + 1}候補"
    return str(label)


def _target_probability_label(target):
    display = target.get("probability_display")
    if display:
        return str(display)
    probability = target.get("probability")
    if probability is None:
        return "参考"
    try:
        return f"{float(probability) * 100:.0f}%"
    except (TypeError, ValueError):
        return "参考"


def _target_distance_label(value):
    if value is None:
        return ""
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return ""


def _target_sample_label(value):
    if value is None:
        return ""
    try:
        return f"検証{int(value)}件"
    except (TypeError, ValueError):
        return ""


def _target_reliability_label(value):
    labels = {
        "high": "信頼度高め",
        "medium": "通常",
        "low": "参考",
    }
    return labels.get(str(value or "").lower(), "")


def _primary_range(ranges):
    for item in ranges or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "")
        if "1" in label or not label:
            return item
    return (ranges or [None])[0]


def _market_stress_summary(world_model, market_shock):
    score = int(world_model.get("shock_score") or 0)
    tone = market_shock.get("tone")
    if score >= 70 or tone == "negative":
        label = "強警戒"
    elif score >= 45:
        label = "警戒"
    else:
        label = "通常"
    reasons = []
    if market_shock.get("has_data") and market_shock.get("summary"):
        reasons.append(market_shock["summary"])
    intermarket = world_model.get("us_index_confirmation") or {}
    reasons.extend(intermarket.get("evidence") or [])
    return {
        "label": label,
        "reasons": reasons[:2] or ["主要3指数に急変判定は出ていません。"],
        "impact": _market_impact_label(intermarket.get("confirmation_label")),
    }


def _us_index_confirmation_summary(intermarket):
    intermarket = intermarket or {}
    label = intermarket.get("confirmation_label")
    return {
        "label": {
            "confirm_up": "上昇確認",
            "confirm_down": "下落確認",
            "divergent": "方向分裂",
            "mixed": "まちまち",
        }.get(label, "データ待ち"),
        "reasons": (intermarket.get("evidence") or ["米国3指数データ待ち"])[:2],
    }


def _market_impact_label(confirmation_label):
    return {
        "confirm_up": "確認",
        "confirm_down": "警戒",
        "divergent": "警戒",
        "mixed": "中立",
    }.get(confirmation_label, "中立")


def _readiness_label(level):
    return {
        "ready": "判定可能",
        "limited": "参考表示",
        "blocked": "判定不可",
    }.get(level, "判定不可")


def _status_summary(status_rows):
    rows = status_rows or []
    blocked = [row for row in rows if row.get("decision_level") == "blocked"]
    limited = [row for row in rows if row.get("decision_level") == "limited"]
    if blocked:
        return "要確認: " + "、".join(row.get("label", "") for row in blocked[:2])
    if limited:
        return "一部参考: " + "、".join(row.get("label", "") for row in limited[:2])
    return "主要データは判定可能"


def _has_baseline_validation(performance):
    if not isinstance(performance, dict):
        return False
    comparison = performance.get("baseline_comparison")
    if isinstance(comparison, dict):
        if int(comparison.get("sample_count") or 0) >= 30:
            if _model_beats_atr_baseline(comparison):
                return True
    required = {
        "model_directional_accuracy",
        "continuation_directional_accuracy",
        "zero_prediction_mae",
        "model_mae",
        "mae_improvement_rate",
    }
    if not required.issubset(performance.keys()):
        return False
    if int(performance.get("total_predictions") or 0) < 30:
        return False
    model_accuracy = float(performance.get("model_directional_accuracy") or 0)
    continuation_accuracy = float(
        performance.get("continuation_directional_accuracy") or 0
    )
    model_mae = float(performance.get("model_mae") or 0)
    zero_mae = float(performance.get("zero_prediction_mae") or 0)
    if model_accuracy < continuation_accuracy:
        return False
    return zero_mae > 0 and model_mae <= zero_mae


def _model_beats_atr_baseline(comparison):
    rows = comparison.get("rows") or []
    model = _baseline_row(rows, "model")
    atr_range = _baseline_row(rows, "atr_range")
    if not model or not atr_range:
        return False
    return _baseline_rank_tuple(model) >= _baseline_rank_tuple(atr_range)


def _baseline_row(rows, key):
    for row in rows:
        if isinstance(row, dict) and row.get("key") == key:
            return row
    return None


def _baseline_rank_tuple(row):
    return (
        float(row.get("risk_adjusted_return_pct") or 0),
        float(row.get("balanced_accuracy") or 0),
        float(row.get("directional_accuracy") or 0),
    )


def _has_baseline_metrics(performance):
    if not isinstance(performance, dict):
        return False
    comparison = performance.get("baseline_comparison")
    if isinstance(comparison, dict) and int(comparison.get("sample_count") or 0) > 0:
        return True
    required = {
        "model_directional_accuracy",
        "continuation_directional_accuracy",
        "zero_prediction_mae",
        "model_mae",
        "mae_improvement_rate",
    }
    return required.issubset(performance.keys()) and int(
        performance.get("total_predictions") or 0
    ) > 0
