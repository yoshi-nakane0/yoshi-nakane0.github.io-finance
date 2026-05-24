def build_targets(features, similar_summary=None):
    price = features.get("price") or 0
    atr = features.get("atr14") or max(price * 0.008, 250)
    pivot = features.get("pivots") or {}
    similar_move = abs(similar_summary.get("average_return_pct") or 0) if similar_summary else 0
    similar_step = price * similar_move / 100 if similar_move else atr * 1.2
    highs = [features.get("intraday_high"), features.get("high_5d"), features.get("high_20d")]
    lows = [features.get("intraday_low"), features.get("low_5d"), features.get("low_20d")]

    upside_candidates = [
        (features.get("previous_high"), "前日高値", "High", "previous_high"),
        (features.get("recent_high"), "直近10本高値", "High", "recent_high"),
        (features.get("high_5d"), "5日高値", "Middle", "high_5d"),
        (features.get("intraday_high"), "短時間足高値", "Middle", "intraday_high"),
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
        (features.get("intraday_low"), "短時間足安値", "Middle", "intraday_low"),
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
    upside_candidates.extend(_round_number_candidates(price, above=True))
    downside_candidates.extend(_round_number_candidates(price, above=False))

    upside = _select_targets(upside_candidates, price, atr, similar_summary, above=True)
    downside = _select_targets(downside_candidates, price, atr, similar_summary, above=False)
    if len(upside) < 2:
        upside = _fill_targets(upside, price, atr, similar_summary, above=True)
    if len(downside) < 2:
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

    return {
        "upside": upside[:4],
        "downside": downside[:4],
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
    for candidate in sorted(
        candidates,
        key=lambda candidate: abs((candidate[0] or price) - price),
    ):
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
        selected.append(_target_row(len(selected) + 1, rounded, reason, confidence, source, price, atr, similar_summary, above))
    return selected


def _fill_targets(targets, price, atr, similar_summary, above):
    direction = 1 if above else -1
    while len(targets) < 2:
        next_price = price + direction * atr * (len(targets) + 1)
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
    probability = _target_probability(index, target_price, price, atr, similar_summary, above)
    return {
        "label": f"T{index}",
        "price": target_price,
        "reason": reason,
        "confidence": confidence,
        "probability": probability,
        "distance_pct": round(distance_pct, 2),
        "expected_value_pct": round(abs(distance_pct) * probability, 2),
        "source": source,
        "rank_score": _rank_score(confidence, probability, distance_pct),
    }


def _target_probability(index, target_price, price, atr, similar_summary, above):
    base = None
    if similar_summary and similar_summary.get("case_count"):
        hit_rate = similar_summary.get("target_t1_hit_rate") or 0.5
        sample_weight = min((similar_summary.get("case_count") or 0) / 12, 1)
        base = hit_rate * sample_weight + 0.5 * (1 - sample_weight)
        if index >= 2:
            base *= 0.78
    if base is None:
        distance = abs(target_price - price)
        if distance < atr * 0.5:
            base = 0.65
        elif distance < atr:
            base = 0.5
        elif distance < atr * 1.5:
            base = 0.35
        else:
            base = 0.2
    sentiment = (similar_summary or {}).get("direction") or None
    if sentiment == "up" and not above:
        base *= 0.75
    if sentiment == "down" and above:
        base *= 0.75
    return round(max(0, min(1, base)), 2)


def _rank_score(confidence, probability, distance_pct):
    confidence_points = {"High": 35, "Middle": 24, "Low": 12}.get(confidence, 10)
    distance_penalty = min(abs(distance_pct) * 4, 24)
    return int(round(confidence_points + probability * 60 - distance_penalty))


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
