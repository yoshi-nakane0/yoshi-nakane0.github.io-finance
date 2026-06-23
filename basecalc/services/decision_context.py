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
    output_contract = world_model.get("output_contract") or {}
    contract_stopped = output_contract.get("contract_status") == "error"
    display_price = output_contract.get("display_price") or world_model.get("display_price") or world_model.get("price")

    return {
        "price": display_price if contract_stopped else world_model.get("price"),
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
        "upside_target": None if contract_stopped else _primary_target(world_model.get("upside_targets")),
        "downside_target": None if contract_stopped else _primary_target(world_model.get("downside_targets")),
        "invalidation": world_model.get("invalidation_display"),
        "range_1d": None if contract_stopped else _primary_range(world_model.get("target_ranges")),
        "market_stress": _market_stress_summary(world_model, market_shock),
        "us_index_confirmation": _us_index_confirmation_summary(intermarket),
        "status_summary": _status_summary(status_rows),
        "can_show_prediction": False if contract_stopped else can_show_prediction(world_model, performance),
        "prediction_stop_reasons": prediction_stop_reasons(world_model, performance),
        "contract_status": output_contract.get("contract_status") or "unchecked",
        "stop_reasons": output_contract.get("stop_reasons") or [],
    }


def enrich_basecalc_context(context):
    if not isinstance(context, dict):
        return context
    world_model = context.get("world_model") or {}
    _ensure_intermarket_display_defaults(world_model)
    _ensure_target_display_defaults(world_model)
    ensure_plain_summary_card_display(world_model)
    market_shock = context.get("market_shock") or {}
    status_rows = context.get("basecalc_status_rows") or []
    context["decision"] = build_basecalc_decision_context(
        world_model,
        market_shock,
        status_rows,
        context.get("backtest_performance_by_horizon", {}).get("1d")
        or context.get("performance"),
    )
    context["basecalc_top"] = build_basecalc_top_context(
        world_model,
        context["decision"],
        status_rows,
        context.get("backtest_performance_by_horizon", {}).get("1d")
        or context.get("performance"),
    )
    context.setdefault("detail_mode", False)
    return context


def build_basecalc_top_context(world_model, decision, status_rows=None, performance=None):
    world_model = world_model or {}
    decision = decision or {}
    return {
        "status": _top_status(decision, status_rows),
        "final_judgment": _top_final_judgment(world_model, decision),
        "action": _top_action(world_model),
        "lines": _top_lines(world_model, decision),
        "change_conditions": _top_change_conditions(world_model),
        "reasons": (decision.get("main_reason") or _top_evidence(world_model))[:3],
        "risks": _top_risks(world_model, decision),
        "horizons": _top_horizons(world_model, performance),
        "external": _top_external(world_model, decision),
        "confidence": _top_confidence(world_model, decision, performance),
    }


def _top_status(decision, status_rows):
    attention = decision.get("status_summary") or _status_summary(status_rows or [])
    if decision.get("contract_status") == "error":
        attention = " / ".join((decision.get("stop_reasons") or [])[:2]) or "出力整合性エラー"
    return {
        "readiness": "停止" if decision.get("contract_status") == "error" else decision.get("readiness_label") or "判定不可",
        "data_quality": (
            f"{str(decision.get('data_quality_level') or 'unknown').title()} "
            f"{decision.get('data_quality_score') or 0}/100"
        ),
        "fallback": "あり" if decision.get("fallback_used") else "なし",
        "attention": attention,
        "updated_at": decision.get("last_updated") or "—",
    }


def _top_final_judgment(world_model, decision):
    if (world_model.get("output_contract") or {}).get("contract_status") == "error":
        reasons = world_model.get("stop_reasons") or ["出力の整合性を確認中"]
        return {
            "headline": "方向判断：停止",
            "setup": "再計算待ち",
            "supplement": f"理由: {reasons[0]}",
        }
    if (world_model.get("output_contract") or {}).get("directional_allowed") is False:
        reasons = world_model.get("stop_reasons") or ["検証ゲート停止中"]
        headline = (
            (world_model.get("counter_bias") or {}).get("label")
            or _judgment_with_reversal(
                decision.get("direction_label") or "判定不可",
                world_model.get("reversal_risk_score"),
            )
        )
        return {
            "headline": headline,
            "setup": "方向予測停止・レンジ確認",
            "supplement": f"理由: {reasons[0]}",
        }
    headline = (
        (world_model.get("counter_bias") or {}).get("label")
        or _judgment_with_reversal(
            decision.get("direction_label") or "判定不可",
            world_model.get("reversal_risk_score"),
        )
    )
    return {
        "headline": headline,
        "setup": world_model.get("primary_setup_label") or decision.get("state_label") or "局面確認中",
        "supplement": _action_summary(world_model),
    }


def _judgment_with_reversal(direction_label, reversal_score):
    try:
        score = int(reversal_score or 0)
    except (TypeError, ValueError):
        score = 0
    if score >= 70 and "反落" not in direction_label:
        return f"{direction_label}だが、反落警戒"
    return direction_label


def _top_action(world_model):
    if (world_model.get("output_contract") or {}).get("contract_status") == "error":
        return {
            "judgment": "様子見",
            "prohibited": "強いロング・ショート",
            "allowed": "支持抵抗・ATRレンジ確認のみ",
            "caution": "再計算後に判断",
            "note": "価格、ターゲット、レンジの時点がそろうまで判断を止めます。",
        }
    if (world_model.get("output_contract") or {}).get("directional_allowed") is False:
        reasons = (world_model.get("output_contract") or {}).get("stop_reasons") or []
        reason = " / ".join(reasons[:2]) or "方向予測の検証条件が未達"
        return {
            "judgment": "レンジ・節目確認",
            "prohibited": "方向の断定・高値追い・追撃買い",
            "allowed": "支持抵抗・ATRレンジ確認のみ",
            "caution": reason,
            "note": "方向予測は使わず、現在値に近い支持抵抗とATRレンジだけを確認します。",
        }
    note = str(world_model.get("action_note") or "")
    if "押し目" in note or "追撃" in note:
        judgment = "押し目確認待ち"
    elif "禁止" in note:
        judgment = "待ち"
    else:
        judgment = "節目確認を優先"
    return {
        "judgment": judgment,
        "prohibited": "高値追い・追撃買い",
        "allowed": "押し目形成後の再上昇確認",
        "caution": "前日安値・EMA20・VWAP割れで上昇判断を弱める",
        "note": note or "方向だけで追いかけず、節目と外部確認を優先します。",
    }


def _action_summary(world_model):
    note = str(world_model.get("action_note") or "")
    if note:
        return note
    return "追撃ではなく、節目確認を優先します。"


def _top_lines(world_model, decision):
    near_levels = world_model.get("near_levels") or {}
    stopped = (world_model.get("output_contract") or {}).get("contract_status") == "error"
    upside_resistance = None if stopped else _target_price(decision.get("upside_target"))
    downside_support = None if stopped else _target_price(decision.get("downside_target"))
    return {
        "current_price": decision.get("price") or world_model.get("price"),
        "upside_resistance": upside_resistance,
        "downside_support": downside_support,
        "near_upside": None if stopped else _first_level_price(near_levels.get("upside")),
        "near_downside": None if stopped else _first_level_price(near_levels.get("downside")),
        "short_term_weakening": "前日安値・EMA20・VWAP割れ",
        "structural_break": _structural_break_line(
            world_model,
            decision,
            upside_resistance,
            downside_support,
        ),
    }


def _target_price(target):
    return target.get("price") if isinstance(target, dict) else None


def _first_level_price(levels):
    first = (levels or [None])[0]
    return first.get("price") if isinstance(first, dict) else None


def _structural_break_line(world_model, decision, upside_resistance, downside_support):
    invalidation = world_model.get("invalidation") or {}
    far_warning = "無効化ラインが遠くリスク幅が大きいです" in (
        invalidation.get("warnings") or []
    )
    if (
        far_warning
        and (world_model.get("output_contract") or {}).get("directional_allowed") is False
    ):
        direction = world_model.get("direction")
        if direction == "up" and downside_support is not None:
            return _price_text(downside_support)
        if direction == "down" and upside_resistance is not None:
            return _price_text(upside_resistance)
    return decision.get("invalidation") or world_model.get("invalidation_display") or "—"


def _price_text(value):
    try:
        return f"{float(value):,.0f}"
    except (TypeError, ValueError):
        return value or "—"


def _top_change_conditions(world_model):
    return [
        {
            "label": "高値終値突破＋米国3指数確認",
            "detail": "上値目標を拡張",
        },
        {
            "label": "EMA20・前日安値割れ",
            "detail": "上昇失敗として扱う",
        },
        {
            "label": "VWAP割れ",
            "detail": "短期需給悪化",
        },
        {
            "label": "米国3指数が同時失速",
            "detail": "追撃買い禁止を強化",
        },
    ]


def _top_risks(world_model, decision):
    risks = list(decision.get("risk_reason") or [])
    counter_reasons = (world_model.get("counter_bias") or {}).get("reasons") or []
    for reason in counter_reasons:
        if reason not in risks:
            risks.append(reason)
    if int(world_model.get("reversal_risk_score") or 0) >= 70:
        label = f"反落警戒{int(world_model.get('reversal_risk_score') or 0)}/100"
        risks = [risk for risk in risks if not str(risk).startswith("反落警戒")]
        if label not in risks:
            risks.insert(0, label)
    intermarket = world_model.get("us_index_confirmation") or {}
    if intermarket.get("confirmation_label") in {"mixed", "divergent", "confirm_down"}:
        label = "米国3指数確認が不十分"
        if label not in risks:
            risks.append(label)
    return risks[:3] or ["目立つ警戒点は限定的です"]


def _top_horizons(world_model, performance):
    output_contract = world_model.get("output_contract") or {}
    if output_contract.get("contract_status") == "error":
        reason = " / ".join((output_contract.get("stop_reasons") or [])[:2])
        return [{"label": "停止", "summary": "方向予測は停止", "note": reason or "出力整合性を確認中"}]
    allowed_horizons = output_contract.get("allowed_horizons") or {}
    direction = world_model.get("direction")
    if direction == "down":
        rows = [
            ("1日", "下落継続だが自律反発に注意"),
            ("3日", "戻り売りと下げ止まりを確認"),
            ("5日", "下落継続と反発の分岐"),
        ]
    else:
        rows = [
            ("1日", "上昇維持だが反落警戒"),
            ("3日", "押し目形成後の再上昇確認"),
            ("5日", "上昇継続と反落の分岐"),
        ]
    note = _direction_precision_note(performance)
    filtered = []
    horizon_keys = ("1d", "3d", "5d")
    for horizon, (label, summary) in zip(horizon_keys, rows):
        gate = allowed_horizons.get(horizon) or {}
        if gate and not gate.get("direction_allowed", True):
            continue
        filtered.append({"label": label, "summary": summary, "note": note})
    if not filtered and allowed_horizons:
        reason = " / ".join((output_contract.get("stop_reasons") or [])[:2])
        return [{"label": "停止", "summary": "方向予測は停止。ATRレンジ・支持抵抗のみ確認", "note": reason}]
    return filtered or [{"label": label, "summary": summary, "note": note} for label, summary in rows]


def _direction_precision_note(performance):
    accuracy = _performance_float(performance, "directional_accuracy")
    if accuracy is not None and accuracy < 0.55:
        return "方向精度は低いため、節目確認を優先"
    return "方向だけでなく、節目とレンジ確認を優先"


def _top_external(world_model, decision):
    us = decision.get("us_index_confirmation") or {}
    market = decision.get("market_stress") or {}
    confirmation_score = (world_model.get("us_index_confirmation") or {}).get("confirmation_score")
    return {
        "us_indices": _us_indices_label(us.get("label"), confirmation_score),
        "chase_risk": _chase_risk_label(world_model.get("chase_risk")),
        "us_reason": _us_component_score_summary(world_model)
        or (us.get("reasons") or ["米国3指数データ待ち"])[0],
        "market_stress": market.get("label") or "通常",
        "market_impact": market.get("impact") or "中立",
    }


def _top_confidence(world_model, decision, performance):
    if decision.get("contract_status") == "error":
        return {
            "data_quality": "確認中",
            "direction": "停止",
            "range": "参考",
            "validation_note": "出力契約エラーのため、信頼度を強調しません。",
        }
    world_model = world_model or {}
    output_contract = world_model.get("output_contract") or {}
    data_quality_score = _optional_int(decision.get("data_quality_score"))
    if data_quality_score is None:
        data_quality_score = _optional_int((world_model.get("data_quality") or {}).get("score")) or 0
    confidence_score = _optional_int(decision.get("confidence_score"))
    if confidence_score is None:
        confidence_score = _optional_int(world_model.get("confidence_score"))
    t1_rate = _performance_float(performance, "target_t1_hit_rate")
    direction_allowed = output_contract.get("directional_allowed")
    target_probability_allowed = _target_probability_allowed(output_contract)
    stop_reason = _first_stop_reason(output_contract, decision)
    return {
        "data_quality": _data_quality_confidence_label(data_quality_score, confidence_score),
        "direction": _direction_confidence_label(
            output_contract,
            performance,
            confidence_score,
            direction_allowed,
        ),
        "range": _range_confidence_label(
            world_model,
            t1_rate,
            target_probability_allowed,
        ),
        "validation_note": _validation_confidence_note(
            output_contract,
            decision,
            direction_allowed,
            stop_reason,
        ),
    }


def _us_indices_label(label, confirmation_score):
    label = label or "データ待ち"
    if confirmation_score is None:
        return label
    try:
        return f"{label}（確認 {int(round(float(confirmation_score)))}/100）"
    except (TypeError, ValueError):
        return label


def _us_component_score_summary(world_model):
    components = ((world_model.get("us_index_confirmation") or {}).get("components") or {})
    if not isinstance(components, dict) or not components:
        return ""
    parts = []
    for key in ("nasdaq100_futures", "sp500_futures", "dow_futures"):
        component = components.get(key)
        if not isinstance(component, dict):
            continue
        score = component.get("score")
        try:
            score_text = f"{int(round(float(score))):+d}"
        except (TypeError, ValueError):
            continue
        parts.append(f"{component.get('symbol') or key} {score_text}")
    return " / ".join(parts)


def _data_quality_confidence_label(score, confidence_score=None):
    level = "高" if score >= 80 else "中" if score >= 60 else "低"
    if confidence_score is None:
        return f"データ{level} {score}/100"
    confidence_level = "高" if confidence_score >= 70 else "中" if confidence_score >= 50 else "低"
    return f"データ{level} {score}/100 / 信頼度{confidence_level} {confidence_score}/100"


def _direction_confidence_label(output_contract, performance, confidence_score, direction_allowed):
    if direction_allowed is False:
        return "停止（検証未達）"
    if confidence_score is not None and confidence_score < 45:
        return f"参考（信頼度 {confidence_score}/100）"
    if _confidence_is_uncalibrated(output_contract):
        if confidence_score is not None:
            return f"{_confidence_direction_level(confidence_score)} {confidence_score}/100（検証中）"
        return "検証中"
    return _direction_confidence(performance)


def _range_confidence_label(world_model, t1_rate, target_probability_allowed):
    if target_probability_allowed is False:
        return "参考（到達確率停止）"
    similar_count = _optional_int((world_model.get("similar_summary") or {}).get("case_count"))
    if _targets_hide_probability(world_model):
        if similar_count is not None:
            return f"参考（到達確率なし・類似{similar_count}件）"
        return "参考（到達確率なし）"
    if similar_count is not None and similar_count < 10:
        return f"参考（類似{similar_count}件）"
    if t1_rate is None:
        return "中"
    return "中〜高" if t1_rate >= 0.75 else "中"


def _validation_confidence_note(output_contract, decision, direction_allowed, stop_reason):
    if direction_allowed is False:
        return f"方向予測停止: {stop_reason}。検証ページで詳細確認。"
    reasons = _confidence_stop_reasons(output_contract, decision)
    if reasons:
        return f"参考扱い: {' / '.join(reasons[:2])}。検証ページで詳細確認。"
    if _confidence_is_uncalibrated(output_contract):
        return "検証中: 信頼度別の過去実績を確認中。詳細は検証ページ。"
    return "方向精度は限定的。レンジ・節目確認を優先。詳細は検証ページ。"


def _confidence_is_uncalibrated(output_contract):
    return (
        output_contract.get("confidence_calibrated") is False
        or output_contract.get("confidence_status") == "未較正"
    )


def _confidence_direction_level(score):
    if score >= 75:
        return "高"
    if score >= 45:
        return "中"
    return "低"


def _confidence_stop_reasons(output_contract, decision):
    reasons = []
    for reason in (output_contract.get("stop_reasons") or []) + (decision.get("stop_reasons") or []):
        if reason and reason not in reasons:
            reasons.append(reason)
    return reasons


def _targets_hide_probability(world_model):
    for key in ("upside_targets", "downside_targets"):
        for target in world_model.get(key) or []:
            if not isinstance(target, dict):
                continue
            if target.get("probability_display") == "表示停止":
                return True
            if target.get("probability_source") == "hidden_low_sample":
                return True
    return False


def _optional_int(value):
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _target_probability_allowed(output_contract):
    allowed_horizons = output_contract.get("allowed_horizons") or {}
    if not allowed_horizons:
        return output_contract.get("probability_display_allowed")
    allowed_values = [
        item.get("target_probability_allowed")
        for item in allowed_horizons.values()
        if isinstance(item, dict) and "target_probability_allowed" in item
    ]
    if not allowed_values:
        return output_contract.get("probability_display_allowed")
    return any(allowed_values)


def _first_stop_reason(output_contract, decision):
    reasons = output_contract.get("stop_reasons") or decision.get("stop_reasons") or []
    return reasons[0] if reasons else "検証条件が未達"


def _direction_confidence(performance):
    accuracy = _performance_float(performance, "directional_accuracy")
    if accuracy is None:
        return "低〜中"
    if accuracy >= 0.6:
        return "中"
    if accuracy >= 0.45:
        return "低〜中"
    return "低〜中"


def _performance_float(performance, key):
    if not isinstance(performance, dict) or performance.get(key) is None:
        return None
    try:
        return float(performance.get(key))
    except (TypeError, ValueError):
        return None


def ensure_plain_summary_card_display(world_model):
    if not isinstance(world_model, dict):
        return
    world_model["chase_risk_label"] = _chase_risk_label(world_model.get("chase_risk"))
    world_model["chase_risk_sentence"] = _chase_risk_sentence(world_model.get("chase_risk"))
    horizons = world_model.get("horizons")
    if not isinstance(horizons, dict):
        return
    for horizon, item in horizons.items():
        if not isinstance(item, dict):
            continue
        item["horizon_label"] = _horizon_label(horizon)
        item["main_bias_label"] = _main_bias_label(item.get("main_bias"))


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
    ensure_plain_summary_card_display(world_model)


def _horizon_label(horizon):
    return {
        "1d": "1営業日後の方向",
        "3d": "3営業日後の方向",
        "5d": "5営業日後の方向",
    }.get(str(horizon or ""), f"{horizon}後の方向")


def _main_bias_label(value):
    return {
        "up": "上昇方向",
        "down": "下落方向",
        "range": "方向感なし",
        "neutral": "方向感なし",
    }.get(str(value or ""), "方向感なし")


def _chase_risk_label(value):
    return {
        "low": "低い",
        "medium": "中程度",
        "high": "高い",
        "unknown": "判定不可",
    }.get(str(value or ""), "判定不可")


def _chase_risk_sentence(value):
    return {
        "low": "追いかけリスクは低い（米国3指数が同じ方向を確認）",
        "medium": "追いかけリスクは中程度（米国3指数の確認が不十分）",
        "high": "追いかけリスクは高い（米国3指数が逆方向または分裂）",
        "unknown": "追いかけリスクは判定不可（米国3指数データ不足）",
    }.get(str(value or ""), "追いかけリスクは判定不可（米国3指数データ不足）")


def can_show_prediction(world_model, performance=None):
    world_model = world_model or {}
    output_contract = world_model.get("output_contract") or {}
    if output_contract.get("contract_status") == "error":
        return False
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
    output_contract = world_model.get("output_contract") or {}
    reasons = list(output_contract.get("stop_reasons") or [])
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
    if market_shock.get("has_data") and tone == "neutral":
        label = "通常"
    elif score >= 70 or tone == "negative":
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
