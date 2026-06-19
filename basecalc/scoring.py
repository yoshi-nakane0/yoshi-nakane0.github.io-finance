from statistics import pstdev


SENTIMENT_LABELS = (
    (-70, "strong_bearish", "下落センチメント非常に強い"),
    (-40, "bearish", "下落センチメント強い"),
    (-15, "slightly_bearish", "やや下落優勢"),
    (15, "neutral", "中立"),
    (40, "slightly_bullish", "やや上昇優勢"),
    (70, "bullish", "上昇センチメント強い"),
    (101, "strong_bullish", "上昇センチメント非常に強い"),
)


def clamp(value, low, high):
    return max(low, min(high, value))


def _direction_sign(direction):
    if direction == "up":
        return 1
    if direction == "down":
        return -1
    return 0


def calculate_sentiment_score(features, similar_summary=None):
    trend_score = _trend_score(features)
    momentum_score = _momentum_score(features)
    reversal_risk_score = calculate_reversal_risk_score(features)
    rebound_improvement_score = calculate_rebound_improvement_score(features)
    volatility_score = _volatility_score(features)
    structure_score = _structure_score(features)
    similar_score = _similar_case_score(similar_summary)
    context_score = calculate_us_index_confirmation_component(features)
    base_score = round(
        trend_score
        + momentum_score
        + volatility_score
        + structure_score
        + similar_score
        + context_score
    )
    counter_bias_adjustment = calculate_counter_bias_adjustment(
        base_score,
        reversal_risk_score,
        rebound_improvement_score,
    )
    raw_score = round(base_score + counter_bias_adjustment)
    conflict = resolve_conflicting_signals(features, raw_score)
    after_conflict = _reduce_absolute(raw_score, conflict["penalty"])
    score = apply_quality_penalty(after_conflict, features.get("data_quality"))
    key, label = sentiment_label(score)
    quality_penalty = -abs(after_conflict - score)
    warnings = list(conflict["warnings"])
    if counter_bias_adjustment < 0:
        warnings.append("上昇優勢ですが反落警戒を主判定に反映しています")
    elif counter_bias_adjustment > 0:
        warnings.append("下落優勢ですが買い戻し警戒を主判定に反映しています")
    return {
        "sentiment_score": score,
        "sentiment_key": key,
        "sentiment_label": label,
        "trend_score": round(trend_score, 1),
        "momentum_score": round(momentum_score, 1),
        "reversal_risk_score": reversal_risk_score,
        "rebound_improvement_score": rebound_improvement_score,
        "external_context_score": round(context_score, 1),
        "components": {
            "trend": round(trend_score, 1),
            "momentum": round(momentum_score, 1),
            "reversal_risk": reversal_risk_score,
            "rebound_improvement": rebound_improvement_score,
            "volatility": round(volatility_score, 1),
            "structure": round(structure_score, 1),
            "similar": round(similar_score, 1),
            "us_index_confirmation": round(context_score, 1),
            "counter_bias_adjustment": round(counter_bias_adjustment, 1),
            "quality_penalty": round(quality_penalty, 1),
            "conflict_penalty": -round(conflict["penalty"], 1),
        },
        "warnings": warnings,
    }


def sentiment_label(score):
    for limit, key, label in SENTIMENT_LABELS:
        if score < limit:
            return key, label
    return "strong_bullish", "上昇センチメント非常に強い"


def calculate_continuation_score(features, direction):
    sign = _direction_sign(direction)
    if sign == 0:
        return 30 if abs(features.get("sentiment_score", 0)) < 15 else 45

    checks = [
        sign * _ema_alignment(features),
        sign * _vwap_alignment(features),
        sign * _macd_alignment(features),
        1 if (features.get("adx14") or 0) >= 20 else 0,
        _rsi_continuation(features, direction),
        sign * (features.get("structure_bias") or 0),
        _atr_continuation(features),
    ]
    score = sum(1 for value in checks if value > 0) / len(checks) * 100
    if features.get("shock_candidate"):
        score -= 12
    return round(clamp(score, 0, 100))


def calculate_shock_score(features):
    score = 0
    daily_z = abs(features.get("daily_change_z") or 0)
    vwap_gap = abs(features.get("vwap_gap_pct") or 0)
    atr_ratio = features.get("atr_ratio") or 1
    rsi = features.get("rsi14")

    if daily_z >= 2:
        score += 35
    elif daily_z >= 1.2:
        score += 18
    if vwap_gap >= 1.5:
        score += 20
    elif vwap_gap >= 0.8:
        score += 10
    if atr_ratio >= 1.5:
        score += 20
    elif atr_ratio >= 1.2:
        score += 10
    if rsi is not None and (rsi >= 75 or rsi <= 25):
        score += 15
    if _trend_mismatch(features):
        score += 10
    return round(clamp(score, 0, 100))


def calculate_reversal_risk_score(features):
    score = 0
    price = features.get("price")
    open_value = features.get("open")
    rsi = features.get("rsi14")
    bb_upper = features.get("bb_upper")
    macd_histogram = features.get("macd_histogram") or 0
    change_5d = features.get("change_5d_pct") or 0
    distance_high = features.get("distance_recent_high_pct")
    ema20_gap = features.get("ema20_gap_pct")
    ema60_gap = features.get("ema60_gap_pct")
    vwap_gap = features.get("vwap_gap_pct")
    atr_ratio = features.get("atr_ratio")
    gap_key = features.get("gap_key")

    if rsi is not None:
        if rsi >= 75:
            score += 35
        elif rsi >= 68:
            score += 22
    if bb_upper and price:
        if price >= bb_upper * 0.995:
            score += 25
        elif price >= bb_upper * 0.985:
            score += 12
    if change_5d >= 2.0:
        score += 18
    elif change_5d >= 1.0:
        score += 10
    if distance_high is not None and distance_high >= -0.2:
        score += 10
    if macd_histogram < 0 and _ema_alignment(features) > 0:
        score += 12
    if ema20_gap is not None:
        if ema20_gap >= 3:
            score += 12
        elif ema20_gap >= 2:
            score += 8
    if ema60_gap is not None and ema60_gap >= 4:
        score += 8
    if vwap_gap is not None:
        if vwap_gap >= 2:
            score += 10
        elif vwap_gap >= 1.2:
            score += 6
    if atr_ratio is not None and atr_ratio >= 1.6:
        score += 8
    if gap_key == "gap_up":
        score += 12 if open_value and price and price < open_value else 5
    return round(clamp(score, 0, 100))


def calculate_rebound_improvement_score(features):
    score = 0
    price = features.get("price")
    open_value = features.get("open")
    rsi = features.get("rsi14")
    bb_lower = features.get("bb_lower")
    macd_histogram = features.get("macd_histogram") or 0
    change_5d = features.get("change_5d_pct") or 0
    distance_low = features.get("distance_recent_low_pct")
    ema20_gap = features.get("ema20_gap_pct")
    ema60_gap = features.get("ema60_gap_pct")
    vwap_gap = features.get("vwap_gap_pct")
    atr_ratio = features.get("atr_ratio")
    gap_key = features.get("gap_key")

    if rsi is not None:
        if rsi <= 25:
            score += 35
        elif rsi <= 32:
            score += 22
    if bb_lower and price:
        if price <= bb_lower * 1.005:
            score += 25
        elif price <= bb_lower * 1.015:
            score += 12
    if change_5d <= -2.0:
        score += 14
    elif change_5d <= -1.0:
        score += 8
    if distance_low is not None and distance_low <= 0.2:
        score += 10
    if macd_histogram > 0 and _ema_alignment(features) < 0:
        score += 12
    if vwap_gap is not None and vwap_gap >= -0.15:
        score += 7
    if ema20_gap is not None:
        if ema20_gap <= -3:
            score += 12
        elif ema20_gap <= -2:
            score += 8
    if ema60_gap is not None and ema60_gap <= -4:
        score += 8
    if vwap_gap is not None:
        if vwap_gap <= -2:
            score += 10
        elif vwap_gap <= -1.2:
            score += 6
    if atr_ratio is not None and atr_ratio >= 1.6:
        score += 8
    if gap_key == "gap_down":
        score += 12 if open_value and price and price > open_value else 5
    return round(clamp(score, 0, 100))


def calculate_counter_bias_adjustment(base_score, reversal_risk_score, rebound_improvement_score):
    if base_score > 0 and reversal_risk_score >= 55:
        return -min(30, (reversal_risk_score - 45) * 0.65)
    if base_score < 0 and rebound_improvement_score >= 55:
        return min(30, (rebound_improvement_score - 45) * 0.65)
    return 0


def shock_label(score):
    if score >= 80:
        return "突発性が非常に高い"
    if score >= 60:
        return "突発性が高い"
    if score >= 40:
        return "やや突発的"
    return "通常変動"


def _trend_score(features):
    score = 0
    ema_bias = _ema_alignment(features)
    score += 22 * ema_bias
    score += 10 * _vwap_alignment(features)
    score += 8 * _macd_alignment(features)
    return clamp(score, -40, 40)


def _momentum_score(features):
    score = 0
    rsi = features.get("rsi14")
    if rsi is not None and _indicator_valid(features, "rsi14"):
        if 55 <= rsi < 70:
            score += 9
        elif 45 <= rsi < 55:
            score += 2
        elif 30 < rsi < 45:
            score -= 8
        elif rsi >= 70:
            score += 3
        elif rsi <= 30:
            score -= 3
    score += clamp((features.get("change_5d_pct") or 0) * 1.3, -8, 8)
    if _indicator_valid(features, "macd") and _indicator_valid(features, "atr14"):
        score += clamp((features.get("macd_histogram") or 0) / max(features.get("atr14") or 1, 1) * 8, -8, 8)
    return clamp(score, -25, 25)


def _volatility_score(features):
    if not _indicator_valid(features, "atr14"):
        return 0
    change = features.get("daily_change_pct") or 0
    atr_ratio = features.get("atr_ratio") or 1
    if atr_ratio > 2.0:
        return 0
    base = 6 if 1.0 <= atr_ratio <= 1.5 else 2
    return clamp(base * (1 if change >= 0 else -1), -15, 15)


def _structure_score(features):
    return clamp((features.get("structure_bias") or 0) * 10, -10, 10)


def _similar_case_score(similar_summary):
    if not similar_summary or not similar_summary.get("case_count"):
        return 0
    if not similar_summary.get("is_statistically_valid"):
        return 0
    up_rate = similar_summary.get("up_rate") or 0.5
    down_rate = similar_summary.get("down_rate") or 0.5
    return clamp((up_rate - down_rate) * 10, -10, 10)


def calculate_us_index_confirmation_component(features: dict) -> float:
    risk_score = features.get("us_index_confirmation_score")
    try:
        risk_score = float(risk_score or 0)
    except (TypeError, ValueError):
        risk_score = 0
    return clamp(risk_score / 100 * 12, -12, 12)


def calculate_volatility_regime(features: dict) -> dict:
    atr_ratio = features.get("atr_ratio")
    bb_width = features.get("bb_width_pct")
    if atr_ratio is None:
        return {"label": "unknown", "atr_ratio": None, "bb_width_pct": bb_width}
    if atr_ratio >= 1.6:
        label = "high"
    elif atr_ratio <= 0.75:
        label = "low"
    else:
        label = "normal"
    return {"label": label, "atr_ratio": round(atr_ratio, 2), "bb_width_pct": bb_width}


def apply_quality_penalty(score: int, data_quality) -> int:
    quality = data_quality or {}
    quality_score = quality.get("score")
    if quality_score is None:
        penalty = 18
    elif quality_score < 50:
        penalty = 30
    elif quality_score < 80:
        penalty = 14
    else:
        penalty = 0
    if quality.get("instrument_type") == "index_fallback":
        penalty = max(penalty, 22)
    if quality.get("is_stale"):
        penalty = max(penalty, 18)
    return clamp(_reduce_absolute(score, penalty), -100, 100)


def resolve_conflicting_signals(features: dict, raw_score: int) -> dict:
    warnings = []
    penalty = 0
    price = features.get("price")
    rsi = features.get("rsi14")
    bb_upper = features.get("bb_upper")
    bb_lower = features.get("bb_lower")
    ema_bias = _ema_alignment(features)
    if raw_score > 0 and ema_bias > 0 and rsi is not None and rsi >= 75 and bb_upper and price and price >= bb_upper * 0.995:
        penalty += 15
        warnings.append("上昇材料と過熱感が競合しています")
    if raw_score < 0 and ema_bias < 0 and rsi is not None and rsi <= 25 and bb_lower and price and price <= bb_lower * 1.005:
        penalty += 15
        warnings.append("下落材料と売られすぎが競合しています")
    if (features.get("shock_score") or 0) >= 70 and (features.get("continuation_score") or 0) < 50:
        penalty += 10
        warnings.append("突発性が高く継続判定は控えめです")
    return {"penalty": min(penalty, 20), "warnings": warnings}


def _reduce_absolute(score, penalty):
    if score > 0:
        return max(0, score - penalty)
    if score < 0:
        return min(0, score + penalty)
    return 0


def _ema_alignment(features):
    ema5 = features.get("ema5")
    ema20 = features.get("ema20")
    ema60 = features.get("ema60")
    if ema5 is None or ema20 is None or ema60 is None:
        return 0
    if ema5 > ema20 > ema60:
        return 1
    if ema5 < ema20 < ema60:
        return -1
    if ema5 > ema20:
        return 0.5
    if ema5 < ema20:
        return -0.5
    return 0


def _vwap_alignment(features):
    if not _indicator_valid(features, "vwap"):
        return 0
    price = features.get("price")
    vwap = features.get("vwap")
    if price is None or vwap is None:
        return 0
    if price > vwap:
        return 1
    if price < vwap:
        return -1
    return 0


def _macd_alignment(features):
    if not _indicator_valid(features, "macd"):
        return 0
    macd = features.get("macd")
    signal = features.get("macd_signal")
    if macd is None or signal is None:
        return 0
    if macd > signal:
        return 1
    if macd < signal:
        return -1
    return 0


def _rsi_continuation(features, direction):
    if not _indicator_valid(features, "rsi14"):
        return 0
    rsi = features.get("rsi14")
    if rsi is None:
        return 0
    if direction == "up" and 45 <= rsi < 70:
        return 1
    if direction == "down" and 30 < rsi <= 55:
        return 1
    return 0


def _atr_continuation(features):
    if not _indicator_valid(features, "atr14"):
        return 0
    atr_ratio = features.get("atr_ratio")
    if atr_ratio is None:
        return 0
    return 1 if 0.75 <= atr_ratio <= 1.6 else 0


def _trend_mismatch(features):
    sign = 1 if (features.get("daily_change_pct") or 0) > 0 else -1
    return sign * _ema_alignment(features) < 0


def _indicator_valid(features, key):
    validity = features.get("indicator_validity") or {}
    return validity.get(key, True)


def calculate_change_zscore(changes, current_change):
    values = [value for value in changes or [] if value is not None]
    if current_change is None or len(values) < 5:
        return 0
    deviation = pstdev(values[-20:]) if len(values[-20:]) >= 2 else 0
    if deviation == 0:
        return 0
    average = sum(values[-20:]) / len(values[-20:])
    return (current_change - average) / deviation
