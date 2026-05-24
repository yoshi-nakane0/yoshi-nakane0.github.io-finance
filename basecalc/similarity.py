from math import sqrt

from .indicators import (
    calculate_atr,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_macd,
    calculate_rsi,
    calculate_vwap,
    detect_price_structure,
)


def find_similar_cases(features, ohlcv, limit=12, min_similarity=0.35):
    closes = _clean(ohlcv.get("closes"))
    highs = _clean(ohlcv.get("highs"))
    lows = _clean(ohlcv.get("lows"))
    timestamps = ohlcv.get("timestamps") or []
    if len(closes) < 35:
        return _empty_summary()

    ema20 = calculate_ema(closes, 20)
    rsi14 = calculate_rsi(closes, 14)
    macd = calculate_macd(closes)
    atr14 = calculate_atr(highs, lows, closes, 14)
    bands = calculate_bollinger_bands(closes)
    ema5 = calculate_ema(closes, 5)
    ema60 = calculate_ema(closes, 60)
    vwap = calculate_vwap({"highs": highs, "lows": lows, "closes": closes})

    current_vector = _vector_from_features(features)
    direction = _direction_from_score(features.get("sentiment_score"))
    cases = []
    searched_case_count = 0
    last_index = len(closes) - 1
    for index in range(60, last_index - 5):
        if closes[index] in (None, 0):
            continue
        searched_case_count += 1
        local_structure = detect_price_structure(
            {
                "highs": highs[max(0, index - 12) : index + 1],
                "lows": lows[max(0, index - 12) : index + 1],
            }
        )
        local_features = {
            "ema5_gap_pct": _pct(closes[index], ema5[index]),
            "ema20_gap_pct": _pct(closes[index], ema20[index]),
            "ema60_gap_pct": _pct(closes[index], ema60[index]),
            "vwap_gap_pct": _pct(closes[index], vwap[index]),
            "rsi14": rsi14[index],
            "macd_histogram": (macd["histogram"][index] or 0) / max(atr14[index] or 1, 1),
            "atr_ratio": _atr_ratio(atr14, index),
            "bb_width_pct": bands["width"][index],
            "change_3d_pct": _pct(closes[index], closes[index - 3]),
            "change_5d_pct": _pct(closes[index], closes[index - 5]),
            "distance_recent_high_pct": _pct(closes[index], max(highs[index - 10 : index + 1])),
            "distance_recent_low_pct": _pct(closes[index], min(lows[index - 10 : index + 1])),
            "structure_bias": local_structure.get("bias") or 0,
        }
        vector = _vector_from_features(local_features)
        if len(vector) != len(current_vector):
            continue
        distance = sqrt(sum((left - right) ** 2 for left, right in zip(current_vector, vector)))
        similarity = max(0.0, 1 - distance / 8)
        future_1d = closes[index + 1]
        future_3d = closes[index + 3]
        future_5d = closes[index + 5]
        return_1d = _pct(closes[index + 1], closes[index])
        return_3d = _pct(future_3d, closes[index])
        return_5d = _pct(future_5d, closes[index])
        future_high = max(highs[index + 1 : index + 6])
        future_low = min(lows[index + 1 : index + 6])
        mfe_pct = _pct(future_high, closes[index])
        mae_pct = _pct(future_low, closes[index])
        atr_pct = ((atr14[index] or 0) / closes[index]) * 100 if closes[index] else 0.8
        upside_threshold = max(0.8, atr_pct)
        downside_threshold = -max(0.8, atr_pct)
        if similarity < min_similarity:
            continue
        cases.append(
            {
                "date": _label_from_timestamp(timestamps[index] if index < len(timestamps) else None),
                "similarity": round(similarity, 2),
                "state_key": _state_hint(return_3d),
                "price_at_signal": round(closes[index], 0),
                "return_1d": round(return_1d or 0, 2),
                "return_3d": round(return_3d or 0, 2),
                "return_5d": round(return_5d or 0, 2),
                "mfe_pct": round(mfe_pct or 0, 2),
                "mae_pct": round(mae_pct or 0, 2),
                "hit_downside_t1": (return_3d or 0) <= downside_threshold,
                "hit_upside_t1": (return_3d or 0) >= upside_threshold,
            }
        )
    cases = sorted(cases, key=lambda item: item["similarity"], reverse=True)[:limit]
    if not cases:
        summary = _empty_summary()
        summary["searched_case_count"] = searched_case_count
        summary["min_similarity"] = min_similarity
        return summary

    up_cases = [case for case in cases if case["return_3d"] > 0]
    down_cases = [case for case in cases if case["return_3d"] < 0]
    direction_cases = up_cases if direction == "up" else down_cases if direction == "down" else []
    returns_1d = sorted(case["return_1d"] for case in cases)
    returns = sorted(case["return_3d"] for case in cases)
    returns_5d = sorted(case["return_5d"] for case in cases)
    mfe_values = sorted(case["mfe_pct"] for case in cases)
    mae_values = sorted(case["mae_pct"] for case in cases)
    return {
        "case_count": len(cases),
        "searched_case_count": searched_case_count,
        "used_case_count": len(cases),
        "min_similarity": min_similarity,
        "up_rate": round(len(up_cases) / len(cases), 2),
        "down_rate": round(len(down_cases) / len(cases), 2),
        "range_rate": round(
            len([case for case in cases if abs(case["return_3d"]) <= 0.3]) / len(cases),
            2,
        ),
        "average_return_pct": round(sum(returns) / len(returns), 2),
        "average_return_5d_pct": round(
            sum(case["return_5d"] for case in cases) / len(cases),
            2,
        ),
        "median_return_1d_pct": _median(returns_1d),
        "median_return_3d_pct": _median(returns),
        "median_return_5d_pct": _median(returns_5d),
        "best_case_pct": max(returns),
        "worst_case_pct": min(returns),
        "return_distribution": {
            "p10": _percentile(returns, 10),
            "p25": _percentile(returns, 25),
            "p50": _percentile(returns, 50),
            "p75": _percentile(returns, 75),
            "p90": _percentile(returns, 90),
        },
        "median_mfe_pct": _median(mfe_values),
        "median_mae_pct": _median(mae_values),
        "target_t1_hit_rate": round(
            len(
                [
                    case
                    for case in cases
                    if case["hit_downside_t1"] or case["hit_upside_t1"]
                ]
            )
            / len(cases),
            2,
        ),
        "invalidation_rate": round(
            len([case for case in cases if abs(case["mae_pct"]) >= 1.2]) / len(cases),
            2,
        ),
        "directional_accuracy": round(
            len(direction_cases) / len(cases)
            if direction in ("up", "down")
            else max(len(up_cases), len(down_cases)) / len(cases),
            2,
        ),
        "direction": direction,
        "cases": cases[:3],
    }


def _vector_from_features(features):
    return [
        _scaled(features.get("ema5_gap_pct"), 2),
        _scaled(features.get("ema20_gap_pct"), 2),
        _scaled(features.get("ema60_gap_pct"), 3),
        _scaled(features.get("vwap_gap_pct"), 2),
        _scaled(features.get("rsi14"), 100),
        _scaled(features.get("macd_histogram"), 2),
        _scaled(features.get("atr_ratio"), 2),
        _scaled(features.get("bb_width_pct"), 5),
        _scaled(features.get("change_3d_pct"), 4),
        _scaled(features.get("change_5d_pct"), 5),
        _scaled(features.get("distance_recent_high_pct"), 5),
        _scaled(features.get("distance_recent_low_pct"), 5),
        _scaled(features.get("structure_bias"), 1),
    ]


def _scaled(value, scale):
    try:
        return float(value or 0) / scale
    except (TypeError, ValueError):
        return 0.0


def _clean(values):
    return [float(value) for value in values or [] if isinstance(value, (int, float))]


def _pct(current, previous):
    if current is None or previous in (None, 0):
        return None
    return ((current - previous) / previous) * 100


def _atr_ratio(atr_values, index):
    value = atr_values[index]
    window = [item for item in atr_values[max(0, index - 20) : index + 1] if item]
    if not value or not window:
        return None
    average = sum(window) / len(window)
    return value / average if average else None


def _label_from_timestamp(timestamp):
    if not timestamp:
        return "N/A"
    try:
        import datetime

        return datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return "N/A"


def _state_hint(return_3d):
    if return_3d is None:
        return "range_neutral"
    if return_3d >= 0.8:
        return "bull_trend_continuation"
    if return_3d <= -0.8:
        return "bear_trend_continuation"
    return "range_neutral"


def _direction_from_score(score):
    if score is None:
        return "neutral"
    if score >= 15:
        return "up"
    if score <= -15:
        return "down"
    return "neutral"


def _median(values):
    if not values:
        return 0.0
    midpoint = len(values) // 2
    if len(values) % 2:
        return round(values[midpoint], 2)
    return round((values[midpoint - 1] + values[midpoint]) / 2, 2)


def _percentile(values, percentile):
    if not values:
        return 0.0
    values = sorted(values)
    index = (len(values) - 1) * percentile / 100
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    if lower == upper:
        return round(values[lower], 2)
    weight = index - lower
    return round(values[lower] * (1 - weight) + values[upper] * weight, 2)


def _empty_summary():
    return {
        "case_count": 0,
        "searched_case_count": 0,
        "used_case_count": 0,
        "min_similarity": 0.35,
        "up_rate": 0.0,
        "down_rate": 0.0,
        "range_rate": 0.0,
        "average_return_pct": 0.0,
        "average_return_5d_pct": 0.0,
        "median_return_1d_pct": 0.0,
        "median_return_3d_pct": 0.0,
        "median_return_5d_pct": 0.0,
        "best_case_pct": 0.0,
        "worst_case_pct": 0.0,
        "return_distribution": {"p10": 0.0, "p25": 0.0, "p50": 0.0, "p75": 0.0, "p90": 0.0},
        "median_mfe_pct": 0.0,
        "median_mae_pct": 0.0,
        "target_t1_hit_rate": 0.0,
        "invalidation_rate": 0.0,
        "directional_accuracy": 0.0,
        "direction": "neutral",
        "cases": [],
    }
