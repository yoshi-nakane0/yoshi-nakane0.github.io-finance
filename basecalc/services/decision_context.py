def build_basecalc_decision_context(
    world_model,
    market_shock,
    status_rows,
    performance=None,
):
    world_model = world_model or {}
    market_shock = market_shock or {}
    market_context = world_model.get("market_context") or {}
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
        "external_market": _external_market_summary(market_context),
        "status_summary": _status_summary(status_rows),
        "can_show_prediction": can_show_prediction(world_model, performance),
        "prediction_stop_reasons": prediction_stop_reasons(world_model, performance),
    }


def enrich_basecalc_context(context):
    if not isinstance(context, dict):
        return context
    world_model = context.get("world_model") or {}
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
    market_context = world_model.get("market_context") or {}
    if market_context.get("risk_label") == "risk_off":
        reasons.append("外部市場ストレス")
    return reasons[:3] or ["目立つ警戒点は限定的です"]


def _primary_target(targets):
    for target in targets or []:
        if isinstance(target, dict) and target.get("price") is not None:
            return {
                "label": target.get("label") or "T1",
                "price": target.get("price"),
                "reason": target.get("reason") or "",
            }
    return None


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
    market_context = world_model.get("market_context") or {}
    reasons.extend(market_context.get("evidence") or [])
    return {
        "label": label,
        "reasons": reasons[:2] or ["主要3指数に急変判定は出ていません。"],
        "impact": _market_impact_label(market_context.get("risk_label")),
    }


def _external_market_summary(market_context):
    market_context = market_context or {}
    risk_label = market_context.get("risk_label")
    return {
        "label": {
            "risk_on": "やや追い風",
            "risk_off": "やや逆風",
            "neutral": "中立",
        }.get(risk_label, "データ待ち"),
        "reasons": (market_context.get("evidence") or ["外部市場データ待ち"])[:2],
    }


def _market_impact_label(risk_label):
    return {
        "risk_on": "追い風",
        "risk_off": "逆風",
        "neutral": "中立",
    }.get(risk_label, "中立")


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
