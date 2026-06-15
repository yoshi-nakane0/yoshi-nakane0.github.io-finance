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
    round_upside = _round_number_candidates(price, above=True)
    round_downside = _round_number_candidates(price, above=False)
    upside_candidates.extend(round_upside)
    downside_candidates.extend(round_downside)

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
    seen = set()
    selected = []
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
        if rounded in seen:
            continue
        seen.add(rounded)
        source = candidate[3] if len(candidate) >= 4 else "custom"
        distance = abs(rounded - price)
        if distance < min_distance:
            near_levels.append(_near_level_row(rounded, reason, source, price, atr))
            continue
        selected.append(
            (
                _target_priority(source, confidence, distance, atr, order),
                _target_row(
                    len(selected) + 1,
                    rounded,
                    reason,
                    confidence,
                    source,
                    price,
                    atr,
                    similar_summary,
                    above,
                ),
            )
        )
    rows = [row for _, row in sorted(selected, key=lambda item: item[0])]
    for index, row in enumerate(rows, start=1):
        row["label"] = f"T{index}"
    return rows, sorted(near_levels, key=lambda item: item["distance_abs"])


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
                "ATR基準",
                "Low",
                "atr_fill",
                price,
                atr,
                similar_summary,
                above,
            )
        )
    return targets


def _target_row(index, target_price, reason, confidence, source, price, atr, similar_summary, above):
    distance_pct = ((target_price - price) / price) * 100 if price else 0
    probability_result = _target_probability(index, target_price, price, atr, similar_summary, above)
    probability = probability_result["probability"]
    return {
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
        "rank_score": _rank_score(confidence, probability, distance_pct),
    }


def _target_probability(index, target_price, price, atr, similar_summary, above):
    similar_summary = similar_summary or {}
    case_count = int(similar_summary.get("case_count") or 0)
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
    if hit_rate is None:
        hit_rate = 0.5
    sample_weight = min(case_count / 30, 1)
    base = float(hit_rate) * sample_weight + 0.5 * (1 - sample_weight)
    if index >= 2:
        base *= 0.78
    sentiment = (similar_summary or {}).get("direction") or None
    if sentiment == "up" and not above:
        base *= 0.75
    if sentiment == "down" and above:
        base *= 0.75
    source = "empirical" if similar_summary.get("is_statistically_valid") and case_count >= 30 else "similarity_low_sample"
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
        capped.append(row)
    return capped


def _rank_score(confidence, probability, distance_pct):
    confidence_points = {"High": 35, "Middle": 24, "Low": 12}.get(confidence, 10)
    distance_penalty = min(abs(distance_pct) * 4, 24)
    probability_points = (probability if probability is not None else 0.15) * 60
    return int(round(confidence_points + probability_points - distance_penalty))


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
    value, reason = (min(usable, key=lambda item: item[0]) if below else max(usable, key=lambda item: item[0]))
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
