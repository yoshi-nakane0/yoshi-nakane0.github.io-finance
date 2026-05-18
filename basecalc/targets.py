def build_targets(features, similar_summary=None):
    price = features.get("price") or 0
    atr = features.get("atr14") or max(price * 0.008, 250)
    pivot = features.get("pivots") or {}
    similar_move = abs(similar_summary.get("average_return_pct") or 0) if similar_summary else 0
    similar_step = price * similar_move / 100 if similar_move else atr * 1.2

    upside_candidates = [
        (features.get("previous_high"), "前日高値", "High"),
        (features.get("recent_high"), "直近高値", "High"),
        (pivot.get("r1"), "Pivot R1", "Middle"),
        (features.get("high_20d"), "20日高値", "Middle"),
        (pivot.get("r2"), "Pivot R2", "Middle"),
        (features.get("bb_upper"), "Bollinger +2σ", "Middle"),
        (price + atr, "ATR上限", "Middle"),
        (price + similar_step, "過去類似局面の平均到達幅", "Low"),
    ]
    downside_candidates = [
        (features.get("previous_low"), "前日安値", "High"),
        (features.get("recent_low"), "直近安値", "High"),
        (features.get("vwap"), "VWAP", "High"),
        (pivot.get("s1"), "Pivot S1", "Middle"),
        (features.get("low_20d"), "20日安値", "Middle"),
        (features.get("ema20"), "EMA20", "Middle"),
        (pivot.get("s2"), "Pivot S2", "Middle"),
        (features.get("bb_lower"), "Bollinger -2σ", "Middle"),
        (price - atr, "ATR下限", "Middle"),
        (price - similar_step, "過去類似局面の平均到達幅", "Low"),
    ]

    upside = _select_targets(upside_candidates, price, above=True)
    downside = _select_targets(downside_candidates, price, above=False)
    if len(upside) < 2:
        upside = _fill_targets(upside, price, atr, above=True)
    if len(downside) < 2:
        downside = _fill_targets(downside, price, atr, above=False)

    bullish_invalidation = _round_price(
        min(
            value
            for value in [
                features.get("recent_low"),
                features.get("previous_low"),
                features.get("ema20"),
                price - atr,
            ]
            if _valid_price(value) and value < price
        )
        if price
        else None
    )
    bearish_invalidation = _round_price(
        max(
            value
            for value in [
                features.get("recent_high"),
                features.get("previous_high"),
                features.get("vwap"),
                price + atr,
            ]
            if _valid_price(value) and value > price
        )
        if price
        else None
    )
    if bullish_invalidation is None and price:
        bullish_invalidation = _round_price(price - atr)
    if bearish_invalidation is None and price:
        bearish_invalidation = _round_price(price + atr)

    return {
        "upside": upside[:4],
        "downside": downside[:4],
        "invalidation": {
            "bullish": bullish_invalidation,
            "bearish": bearish_invalidation,
        },
    }


def _select_targets(candidates, price, above):
    seen = set()
    selected = []
    for value, reason, confidence in sorted(
        candidates,
        key=lambda candidate: abs((candidate[0] or price) - price),
    ):
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
        selected.append(
            {
                "label": f"T{len(selected) + 1}",
                "price": rounded,
                "reason": reason,
                "confidence": confidence,
            }
        )
    return selected


def _fill_targets(targets, price, atr, above):
    direction = 1 if above else -1
    while len(targets) < 2:
        next_price = price + direction * atr * (len(targets) + 1)
        targets.append(
            {
                "label": f"T{len(targets) + 1}",
                "price": _round_price(next_price),
                "reason": "ATR基準",
                "confidence": "Low",
            }
        )
    return targets


def _round_price(value):
    if not _valid_price(value):
        return None
    return int(round(float(value) / 10) * 10)


def _valid_price(value):
    try:
        return value is not None and float(value) > 0
    except (TypeError, ValueError):
        return False
