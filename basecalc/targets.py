def build_targets(features, similar_summary=None):
    price = features.get("price") or 0
    atr = features.get("atr14") or max(price * 0.008, 250)
    indicator_validity = features.get("indicator_validity") or {}
    pivot = features.get("pivots") or {}
    if not indicator_validity.get("pivot", True):
        pivot = {}
    similar_move = abs(similar_summary.get("average_return_pct") or 0) if similar_summary else 0
    similar_step = price * similar_move / 100 if similar_move else atr * 1.2
    upside_candidates = [
        (features.get("previous_high"), "前日高値", "High", "previous_high"),
        (features.get("recent_high"), "直近10本高値", "High", "recent_high"),
        (features.get("high_5d"), "5日高値", "Middle", "high_5d"),
        (pivot.get("r1"), "Pivot R1", "Middle", "pivot_r1"),
        (features.get("high_20d"), "20日高値", "Middle", "high_20d"),
        (pivot.get("r2"), "Pivot R2", "Middle", "pivot_r2"),
        (pivot.get("r3"), "Pivot R3", "Low", "pivot_r3"),
        (features.get("ema20"), "EMA20", "Middle", "ema20"),
        (features.get("ema60"), "EMA60", "Low", "ema60"),
        (features.get("vwap"), "VWAP", "Middle", "vwap"),
        (features.get("bb_upper"), "Bollinger +2σ", "Middle", "bb_upper"),
        (price + atr, "ATR 1.0", "Middle", "atr_1"),
        (price + atr * 1.5, "ATR 1.5", "Low", "atr_1_5"),
        (price + atr * 2.0, "ATR 2.0", "Low", "atr_2"),
        (price + similar_step, "過去類似局面の平均到達幅", "Low", "similar_mfe"),
    ]
    downside_candidates = [
        (features.get("previous_low"), "前日安値", "High", "previous_low"),
        (features.get("recent_low"), "直近10本安値", "High", "recent_low"),
        (features.get("low_5d"), "5日安値", "Middle", "low_5d"),
        (features.get("vwap"), "VWAP", "High", "vwap"),
        (pivot.get("s1"), "Pivot S1", "Middle", "pivot_s1"),
        (features.get("low_20d"), "20日安値", "Middle", "low_20d"),
        (features.get("ema20"), "EMA20", "Middle", "ema20"),
        (features.get("ema60"), "EMA60", "Low", "ema60"),
        (pivot.get("s2"), "Pivot S2", "Middle", "pivot_s2"),
        (pivot.get("s3"), "Pivot S3", "Low", "pivot_s3"),
        (features.get("bb_lower"), "Bollinger -2σ", "Middle", "bb_lower"),
        (price - atr, "ATR 1.0", "Middle", "atr_1"),
        (price - atr * 1.5, "ATR 1.5", "Low", "atr_1_5"),
        (price - atr * 2.0, "ATR 2.0", "Low", "atr_2"),
        (price - similar_step, "過去類似局面の平均到達幅", "Low", "similar_mae"),
    ]
    upside, near_upside = _select_targets(upside_candidates, price, atr, similar_summary, above=True)
    downside, near_downside = _select_targets(downside_candidates, price, atr, similar_summary, above=False)
    if len(upside) < 3:
        upside = _fill_targets(upside, price, atr, similar_summary, above=True)
    if len(downside) < 3:
        downside = _fill_targets(downside, price, atr, similar_summary, above=False)

    bullish_invalidation, bullish_reason, bullish_warning = _invalidation_line(
        price,
        atr,
        [
            (features.get("recent_low"), "直近安値"),
            (features.get("previous_low"), "前日安値"),
            (features.get("ema20"), "EMA20"),
            (price - atr, "ATR補正"),
        ],
        below=True,
    )
    bearish_invalidation, bearish_reason, bearish_warning = _invalidation_line(
        price,
        atr,
        [
            (features.get("recent_high"), "直近高値"),
            (features.get("previous_high"), "前日高値"),
            (features.get("vwap"), "VWAP"),
            (price + atr, "ATR補正"),
        ],
        below=False,
    )
    if bullish_invalidation is None and price:
        bullish_invalidation = _round_price(price - atr)
        bullish_reason = "ATR補正"
    if bearish_invalidation is None and price:
        bearish_invalidation = _round_price(price + atr)
        bearish_reason = "ATR補正"

    probability_cap = _probability_cap(features, similar_summary)
    upside = _cap_probabilities(upside, probability_cap)
    downside = _cap_probabilities(downside, probability_cap)
    return {
        "upside": upside[:4],
        "downside": downside[:4],
        "near_levels": {
            "upside": near_upside[:4],
            "downside": near_downside[:4],
        },
        "target_ranges": _target_ranges(price, atr, similar_summary),
        "invalidation": {
            "bullish": bullish_invalidation,
            "bullish_reason": bullish_reason,
            "bearish": bearish_invalidation,
            "bearish_reason": bearish_reason,
            "warnings": [warning for warning in (bullish_warning, bearish_warning) if warning],
        },
    }


def _select_targets(candidates, price, atr, similar_summary, above):
    rows = []
    near_levels = []
    min_distance = _minimum_target_distance(atr)
    for order, candidate in enumerate(candidates):
        value, reason, confidence = candidate[:3]
        if not _valid_price(value):
            continue
        if above and value <= price:
            continue
        if not above and value >= price:
            continue
        rounded = _round_price(value)
        if rounded is None:
            continue
        source = candidate[3] if len(candidate) >= 4 else "custom"
        distance = abs(rounded - price)
        if distance < min_distance:
            near = _near_level_row(rounded, reason, source, price, atr)
            near["line_role"] = _source_role(source)
            near["sources"] = [source]
            near_levels.append(near)
            continue
        rows.append(_candidate_row(rounded, reason, confidence, source, price, atr, order))

    rows = _merge_confluent_levels(rows, atr)
    structural_exists = any(row.get("line_role") == "structural" for row in rows)
    rows = sorted(rows, key=lambda row: _target_sort_key(row, structural_exists))
    near_levels = sorted(
        near_levels,
        key=lambda item: (
            item.get("distance_abs") if item.get("distance_abs") is not None else 999999,
            _role_sort_priority(item.get("line_role"), structural_exists),
        ),
    )
    return _finalize_target_rows(rows, price, atr, similar_summary, above), near_levels


def _fill_targets(targets, price, atr, similar_summary, above):
    direction = 1 if above else -1
    while len(targets) < 3:
        multiplier = (0.8, 1.4, 2.0)[len(targets)]
        next_price = price + direction * atr * multiplier
        rounded = _round_price(next_price)
        targets.append(
            _target_row(
                len(targets) + 1,
                rounded,
                "ATR補完（支持抵抗不足）",
                "Low",
                "atr_fill",
                price,
                atr,
                similar_summary,
                above,
                extra={
                    "line_role": "atr_projection",
                    "sources": ["atr_fill"],
                    "confluence_count": 1,
                    "selection_score": 0,
                },
            )
        )
    return targets


def _target_row(index, target_price, reason, confidence, source, price, atr, similar_summary, above, extra=None):
    distance_pct = ((target_price - price) / price) * 100 if price else 0
    probability_result = _target_probability(index, target_price, price, atr, similar_summary, above)
    probability = probability_result["probability"]
    extra = extra or {}
    confluence_count = int(extra.get("confluence_count") or 1)
    line_role = extra.get("line_role") or _source_role(source)
    row = {
        "label": f"T{index}",
        "price": target_price,
        "reason": reason,
        "confidence": confidence,
        "probability": probability,
        "probability_display": probability_result["display"],
        "probability_source": probability_result["source"],
        "sample_count": probability_result["sample_count"],
        "reliability": probability_result["reliability"],
        "distance_pct": round(distance_pct, 2),
        "distance_abs": round(abs(target_price - price), 0),
        "distance_atr": round(abs(target_price - price) / atr, 2) if atr else None,
        "expected_value_pct": round(abs(distance_pct) * probability, 2) if probability is not None else None,
        "source": source,
        "line_role": line_role,
        "sources": extra.get("sources") or [source],
        "confluence_count": confluence_count,
        "selection_score": extra.get("selection_score"),
    }
    row["rank_score"] = _rank_score(
        confidence,
        probability,
        distance_pct,
        source=source,
        confluence_count=confluence_count,
        line_role=line_role,
    )
    return row


def _target_probability(index, target_price, price, atr, similar_summary, above):
    similar_summary = similar_summary or {}
    case_count = int(similar_summary.get("case_count") or 0)
    distance_atr = abs(target_price - price) / atr if atr else None
    if case_count < 10:
        return {
            "probability": None,
            "display": "表示停止",
            "source": "hidden_low_sample",
            "sample_count": case_count,
            "reliability": "low",
        }
    hit_key = "upside_t1_hit_rate" if above else "downside_t1_hit_rate"
    hit_rate = similar_summary.get(hit_key)
    if hit_rate is None:
        hit_rate = similar_summary.get("target_t1_hit_rate")
    prior = _distance_probability_prior(distance_atr, index)
    if hit_rate is None:
        base = prior
        source = "distance_prior"
    else:
        sample_weight = min(max((case_count - 10) / 30, 0), 1)
        base = float(hit_rate) * sample_weight + prior * (1 - sample_weight)
        source = "empirical" if similar_summary.get("is_statistically_valid") and case_count >= 30 else "similarity_low_sample"
    if index == 2:
        base *= 0.86
    elif index >= 3:
        base *= 0.72
    sentiment = (similar_summary or {}).get("direction") or None
    if sentiment == "up" and not above:
        base *= 0.78
    if sentiment == "down" and above:
        base *= 0.78
    reliability = "middle" if source == "empirical" else "low"
    probability = round(max(0, min(1, base)), 2)
    return {
        "probability": probability,
        "display": f"{probability:.2f}" if source == "empirical" else f"参考 {probability:.2f}",
        "source": source,
        "sample_count": case_count,
        "reliability": reliability,
    }


def _probability_cap(features, similar_summary):
    cap = 1.0
    if features.get("readiness_level") != "ready":
        cap = min(cap, 0.3)
    if similar_summary and not similar_summary.get("is_statistically_valid"):
        cap = min(cap, 0.5)
    return cap


def _cap_probabilities(targets, cap):
    if cap >= 1:
        return targets
    capped = []
    for target in targets:
        row = dict(target)
        if row.get("probability") is not None:
            row["probability"] = min(row.get("probability") or 0, cap)
            row["expected_value_pct"] = round(abs(row.get("distance_pct") or 0) * row["probability"], 2)
            row["probability_capped"] = True
            if row.get("probability_source") == "empirical":
                row["probability_display"] = f"{row['probability']:.2f}"
            else:
                row["probability_display"] = f"参考 {row['probability']:.2f}"
        capped.append(row)
    return capped


def _rank_score(confidence, probability, distance_pct, source=None, confluence_count=1, line_role=None):
    confidence_points = {"High": 32, "Middle": 22, "Low": 10}.get(confidence, 8)
    probability_points = (probability if probability is not None else 0.15) * 50
    distance_penalty = min(abs(distance_pct) * 4, 24)
    source_points = min(_source_quality(source) / 2, 22) if source else 0
    confluence_points = min(max(int(confluence_count or 1) - 1, 0) * 6, 18)
    role_penalty = 18 if line_role == "atr_projection" else 0
    return int(round(confidence_points + probability_points + source_points + confluence_points - distance_penalty - role_penalty))


STRUCTURAL_SOURCES = {
    "previous_high",
    "previous_low",
    "recent_high",
    "recent_low",
    "high_5d",
    "low_5d",
    "high_20d",
    "low_20d",
    "pivot_r1",
    "pivot_s1",
    "pivot_r2",
    "pivot_s2",
    "pivot_r3",
    "pivot_s3",
    "ema20",
    "ema60",
    "vwap",
    "bb_upper",
    "bb_lower",
}

PSYCHOLOGICAL_SOURCES = {
    "round_100",
    "round_500",
    "round_1000",
}

SIMILAR_PROJECTION_SOURCES = {
    "similar_mfe",
    "similar_mae",
}

ATR_PROJECTION_SOURCES = {
    "atr_1",
    "atr_1_5",
    "atr_2",
    "atr_fill",
}

SOURCE_QUALITY = {
    "previous_high": 42,
    "previous_low": 42,
    "recent_high": 44,
    "recent_low": 44,
    "high_5d": 36,
    "low_5d": 36,
    "high_20d": 32,
    "low_20d": 32,
    "pivot_r1": 34,
    "pivot_s1": 34,
    "pivot_r2": 28,
    "pivot_s2": 28,
    "pivot_r3": 20,
    "pivot_s3": 20,
    "vwap": 34,
    "ema20": 30,
    "ema60": 24,
    "bb_upper": 26,
    "bb_lower": 26,
    "round_100": 18,
    "round_500": 24,
    "round_1000": 28,
    "similar_mfe": 22,
    "similar_mae": 22,
    "atr_1": 12,
    "atr_1_5": 8,
    "atr_2": 6,
    "atr_fill": 4,
}


def _source_role(source):
    if source in STRUCTURAL_SOURCES:
        return "structural"
    if source in PSYCHOLOGICAL_SOURCES:
        return "psychological"
    if source in SIMILAR_PROJECTION_SOURCES:
        return "similar_projection"
    if source in ATR_PROJECTION_SOURCES:
        return "atr_projection"
    return "custom"


def _source_quality(source):
    return SOURCE_QUALITY.get(source, 10)


def _confidence_points(confidence):
    return {"High": 28, "Middle": 18, "Low": 8}.get(confidence, 6)


def _distance_fit_points(distance_atr):
    if distance_atr is None:
        return 0
    try:
        value = abs(float(distance_atr))
    except (TypeError, ValueError):
        return 0
    if value < 0.5:
        return 4
    if value <= 0.8:
        return 18
    if value <= 1.3:
        return 22
    if value <= 1.8:
        return 20
    if value <= 2.5:
        return 14
    if value <= 3.5:
        return 8
    return 2


def _candidate_row(value, reason, confidence, source, price, atr, order):
    rounded = _round_price(value)
    distance = abs(rounded - price)
    distance_atr = round(distance / atr, 2) if atr else None
    line_role = _source_role(source)
    return {
        "price": rounded,
        "reason": reason,
        "reason_parts": [reason] if reason else [],
        "confidence": confidence,
        "source": source,
        "sources": [source],
        "line_role": line_role,
        "distance_abs": round(distance, 0),
        "distance_pct": round(((rounded - price) / price) * 100, 2) if price else 0,
        "distance_atr": distance_atr,
        "original_order": order,
    }


def _merge_confluent_levels(rows, atr):
    if not rows:
        return []
    merge_band = max((atr or 0) * 0.18, 30)
    ordered = sorted(rows, key=lambda row: (row.get("price") or 0, _order_value(row)))
    merged = []
    for row in ordered:
        if not merged:
            merged.append(dict(row))
            continue
        previous = merged[-1]
        if abs((row.get("price") or 0) - (previous.get("price") or 0)) <= merge_band:
            merged[-1] = _merge_target_candidates(previous, row)
        else:
            merged.append(dict(row))
    return merged


def _merge_target_candidates(base, row):
    base_quality = _source_quality(base.get("source"))
    row_quality = _source_quality(row.get("source"))
    primary = dict(row if row_quality > base_quality else base)
    secondary = base if row_quality > base_quality else row

    sources = []
    for source in (primary.get("sources") or [primary.get("source")]) + (secondary.get("sources") or [secondary.get("source")]):
        if source and source not in sources:
            sources.append(source)

    reason_parts = []
    for reason in (primary.get("reason_parts") or [primary.get("reason")]) + (secondary.get("reason_parts") or [secondary.get("reason")]):
        if reason and reason not in reason_parts:
            reason_parts.append(reason)

    confidences = [primary.get("confidence"), secondary.get("confidence")]
    if "High" in confidences or len(sources) >= 3:
        confidence = "High"
    elif "Middle" in confidences or len(sources) >= 2:
        confidence = "Middle"
    else:
        confidence = primary.get("confidence") or secondary.get("confidence") or "Low"

    roles = [primary.get("line_role"), secondary.get("line_role")]
    if "structural" in roles:
        line_role = "structural"
    elif "psychological" in roles:
        line_role = "psychological"
    elif "similar_projection" in roles:
        line_role = "similar_projection"
    elif "atr_projection" in roles:
        line_role = "atr_projection"
    else:
        line_role = primary.get("line_role") or secondary.get("line_role") or "custom"

    primary.update(
        {
            "sources": sources,
            "reason_parts": reason_parts,
            "reason": " / ".join(reason_parts[:4]),
            "confidence": confidence,
            "line_role": line_role,
            "confluence_count": len(sources),
            "original_order": min(_order_value(primary), _order_value(secondary)),
        }
    )
    return primary


def _target_selection_score(row, structural_exists):
    source = row.get("source")
    line_role = row.get("line_role")
    confluence_count = len(row.get("sources") or [source])
    score = (
        _source_quality(source)
        + _confidence_points(row.get("confidence"))
        + _distance_fit_points(row.get("distance_atr"))
        + min(max(confluence_count - 1, 0) * 8, 24)
    )
    if line_role == "atr_projection":
        score -= 36 if structural_exists else 10
    elif line_role == "similar_projection":
        score -= 8
    return score


def _role_sort_priority(line_role, structural_exists):
    if line_role == "structural":
        return 0
    if line_role == "psychological":
        return 1
    if line_role == "similar_projection":
        return 2
    if line_role == "atr_projection":
        return 5 if structural_exists else 2
    return 4


def _target_sort_key(row, structural_exists):
    selection_score = _target_selection_score(row, structural_exists)
    return (
        _role_sort_priority(row.get("line_role"), structural_exists),
        -selection_score,
        row.get("distance_atr") if row.get("distance_atr") is not None else 999,
        _order_value(row),
    )


def _finalize_target_rows(rows, price, atr, similar_summary, above):
    structural_exists = any(row.get("line_role") == "structural" for row in rows)
    finalized = []
    for index, candidate in enumerate(rows, start=1):
        sources = candidate.get("sources") or [candidate.get("source")]
        extra = {
            "line_role": candidate.get("line_role"),
            "sources": sources,
            "confluence_count": len(sources),
            "selection_score": _target_selection_score(candidate, structural_exists),
        }
        finalized.append(
            _target_row(
                index,
                candidate.get("price"),
                candidate.get("reason"),
                candidate.get("confidence"),
                candidate.get("source"),
                price,
                atr,
                similar_summary,
                above,
                extra=extra,
            )
        )
    return finalized


def _distance_probability_prior(distance_atr, index):
    try:
        value = abs(float(distance_atr))
    except (TypeError, ValueError):
        value = 1.5
    if value <= 0.8:
        prior = 0.58
    elif value <= 1.3:
        prior = 0.50
    elif value <= 1.8:
        prior = 0.42
    elif value <= 2.5:
        prior = 0.32
    elif value <= 3.5:
        prior = 0.24
    else:
        prior = 0.16
    if index >= 3:
        prior *= 0.82
    elif index == 2:
        prior *= 0.92
    return prior


def _order_value(row):
    value = row.get("original_order") if isinstance(row, dict) else None
    return value if value is not None else 999


def _target_priority(source, confidence, distance, atr, order):
    source_priority = {
        "atr_1": 0,
        "atr_1_5": 1,
        "atr_2": 2,
        "similar_mfe": 3,
        "similar_mae": 3,
        "recent_high": 4,
        "recent_low": 4,
        "high_5d": 5,
        "low_5d": 5,
        "pivot_r1": 6,
        "pivot_s1": 6,
        "pivot_r2": 7,
        "pivot_s2": 7,
        "high_20d": 8,
        "low_20d": 8,
    }.get(source, 12)
    confidence_penalty = {"High": 0, "Middle": 1, "Low": 2}.get(confidence, 2)
    ideal_distance = atr * (1.0 if order < 8 else 1.5)
    distance_penalty = abs(distance - ideal_distance) / max(atr, 1)
    return (source_priority, confidence_penalty, round(distance_penalty, 2), order)


def _near_level_row(price_value, reason, source, price, atr):
    return {
        "price": price_value,
        "reason": reason,
        "source": source,
        "distance_abs": round(abs(price_value - price), 0),
        "distance_pct": round(((price_value - price) / price) * 100, 2) if price else 0,
        "distance_atr": round(abs(price_value - price) / atr, 2) if atr else None,
    }


def _target_ranges(price, atr, similar_summary):
    distribution = (similar_summary or {}).get("return_distribution") or {}
    p75_step = abs(float(distribution.get("p75") or 0)) * price / 100 if price else 0
    p25_step = abs(float(distribution.get("p25") or 0)) * price / 100 if price else 0
    horizons = [
        ("1d", "1日想定レンジ", 0.9),
        ("3d", "3日メインレンジ", 1.6),
        ("5d", "5日拡張レンジ", 2.3),
    ]
    rows = []
    for key, label, multiplier in horizons:
        upside_step = max(atr * multiplier, p75_step if key != "1d" else 0)
        downside_step = max(atr * multiplier, p25_step if key != "1d" else 0)
        rows.append(
            {
                "horizon": key,
                "label": label,
                "low": _round_price(price - downside_step),
                "high": _round_price(price + upside_step),
                "basis": "ATR・過去分布" if (p75_step or p25_step) and key != "1d" else "ATR",
            }
        )
    return rows


def _minimum_target_distance(atr):
    return max((atr or 0) * 0.5, 1)


def _round_number_candidates(price, above):
    if not price:
        return []
    candidates = []
    for step in (100, 500, 1000):
        rounded = _round_price(((int(price / step) + (1 if above else 0)) * step) if above else (int(price / step) * step))
        if rounded and ((above and rounded > price) or (not above and rounded < price)):
            candidates.append((rounded, f"{step}円刻み", "Middle", f"round_{step}"))
    return candidates


def _invalidation_line(price, atr, candidates, below):
    usable = [
        (value, reason)
        for value, reason in candidates
        if _valid_price(value) and ((below and value < price) or (not below and value > price))
    ]
    if not usable:
        return None, "", ""
    value, reason = (max(usable, key=lambda item: item[0]) if below else min(usable, key=lambda item: item[0]))
    distance = abs(price - value)
    warning = ""
    if distance < atr * 0.35:
        value = price - atr * 0.7 if below else price + atr * 0.7
        reason = f"{reason} + ATR補正"
    elif distance > atr * 2.5:
        warning = "無効化ラインが遠くリスク幅が大きいです"
    return _round_price(value), reason, warning


def _round_price(value):
    if not _valid_price(value):
        return None
    return int(round(float(value) / 10) * 10)


def _valid_price(value):
    try:
        return value is not None and float(value) > 0
    except (TypeError, ValueError):
        return False
