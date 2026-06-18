STATE_DEFINITIONS = {
    "data_unavailable": {
        "label": "データ不足",
        "phase_label": "判定停止",
        "base_bias": "neutral",
        "risk": "unknown",
        "next_states": ["limited_reference", "range_neutral"],
    },
    "limited_reference": {
        "label": "参考表示",
        "phase_label": "方向判定停止",
        "base_bias": "neutral",
        "risk": "data_quality",
        "next_states": ["range_neutral", "data_unavailable"],
    },
    "bull_trend_continuation": {
        "label": "上昇継続",
        "phase_label": "押し目買い優勢",
        "base_bias": "up",
        "risk": "trend_exhaustion",
        "next_states": ["exhaustion_top", "dip_buy", "range_neutral"],
    },
    "dip_buy": {
        "label": "押し目買い",
        "phase_label": "押し目買い優勢",
        "base_bias": "up",
        "risk": "failed_rebound",
        "next_states": ["bull_trend_continuation", "range_neutral", "return_sell"],
    },
    "short_covering": {
        "label": "買い戻し",
        "phase_label": "買い戻し優勢",
        "base_bias": "up",
        "risk": "rebound_failure",
        "next_states": ["range_neutral", "return_sell", "bull_trend_continuation"],
    },
    "bull_impulse": {
        "label": "突発上昇",
        "phase_label": "ブレイク買い警戒",
        "base_bias": "up",
        "risk": "fade_after_spike",
        "next_states": ["bull_trend_continuation", "exhaustion_top", "range_neutral"],
    },
    "exhaustion_top": {
        "label": "過熱・反落警戒",
        "phase_label": "利確・反落警戒",
        "base_bias": "neutral",
        "risk": "reversal_down",
        "next_states": ["return_sell", "range_neutral", "bull_trend_continuation"],
    },
    "bear_trend_continuation": {
        "label": "下落継続",
        "phase_label": "戻り売り優勢",
        "base_bias": "down",
        "risk": "short_covering",
        "next_states": ["exhaustion_bottom", "return_sell", "range_neutral"],
    },
    "return_sell": {
        "label": "戻り売り",
        "phase_label": "戻り売り優勢",
        "base_bias": "down",
        "risk": "break_above_resistance",
        "next_states": ["bear_trend_continuation", "range_neutral", "dip_buy"],
    },
    "bear_impulse": {
        "label": "突発下落",
        "phase_label": "ブレイク売り警戒",
        "base_bias": "down",
        "risk": "oversold_rebound",
        "next_states": ["bear_trend_continuation", "exhaustion_bottom", "range_neutral"],
    },
    "exhaustion_bottom": {
        "label": "売られすぎ・反発警戒",
        "phase_label": "反発警戒",
        "base_bias": "neutral",
        "risk": "rebound_up",
        "next_states": ["short_covering", "range_neutral", "bear_trend_continuation"],
    },
    "breakout_pending": {
        "label": "ブレイク待ち",
        "phase_label": "様子見",
        "base_bias": "neutral",
        "risk": "false_breakout",
        "next_states": ["bull_impulse", "bear_impulse", "range_neutral"],
    },
    "range_neutral": {
        "label": "レンジ中立",
        "phase_label": "様子見",
        "base_bias": "neutral",
        "risk": "range_break",
        "next_states": ["breakout_pending", "dip_buy", "return_sell"],
    },
}


def get_state_definition(state_key: str) -> dict:
    return STATE_DEFINITIONS.get(state_key) or STATE_DEFINITIONS["range_neutral"]


def estimate_transition_probabilities(
    state_key: str,
    features: dict,
    performance_stats=None,
    similar_summary=None,
) -> list[dict]:
    definition = get_state_definition(state_key)
    next_states = list(definition["next_states"])
    sentiment = int(features.get("sentiment_score") or 0)
    continuation = int(features.get("continuation_score") or 0)
    shock = int(features.get("shock_score") or 0)
    similar_summary = similar_summary or {}
    learned = _learned_transitions(state_key, performance_stats)
    weights = []
    for index, next_state in enumerate(next_states):
        next_definition = get_state_definition(next_state)
        weight = 1.0 / (index + 1)
        bias = next_definition.get("base_bias")
        if bias == "up" and sentiment > 15:
            weight += abs(sentiment) / 100
        if bias == "down" and sentiment < -15:
            weight += abs(sentiment) / 100
        if "continuation" in next_state and continuation >= 60:
            weight += 0.35
        if "impulse" in next_state and shock >= 60:
            weight += 0.35
        if next_state == "range_neutral" and abs(sentiment) < 20:
            weight += 0.3
        if similar_summary.get("directional_accuracy") and bias in {"up", "down"}:
            weight += float(similar_summary["directional_accuracy"]) * 0.2
        if next_state in learned:
            weight += learned[next_state]["probability"] * 2
        weights.append(max(weight, 0.05))
    for next_state, meta in learned.items():
        if next_state not in next_states:
            next_states.append(next_state)
            weights.append(max(meta["probability"] * 2, 0.05))
    total = sum(weights) or 1
    rows = []
    for next_state, weight in zip(next_states, weights):
        state_def = get_state_definition(next_state)
        rows.append(
            {
                "state_key": next_state,
                "label": state_def["label"],
                "probability": round(weight / total, 2),
                "source": "learned" if next_state in learned else "rule",
                "sample_count": learned.get(next_state, {}).get("count", 0),
            }
        )
    if learned:
        rows = sorted(rows, key=lambda row: row["probability"], reverse=True)
    return _normalize_probabilities(rows)


def estimate_expected_returns(
    state_key: str,
    features: dict,
    similar_summary=None,
    performance_stats=None,
) -> dict:
    similar_summary = similar_summary or {}
    case_count = int(similar_summary.get("case_count") or 0)
    base = similar_summary.get("median_return_3d_pct")
    if base is None:
        base = similar_summary.get("average_return_pct")
    source = "empirical" if similar_summary.get("is_statistically_valid") and case_count >= 30 else "similarity_low_sample"
    reliability = "middle" if source == "empirical" else "low"
    display_label = "検証ベース" if source == "empirical" else "参考値"
    if not base:
        score = int(features.get("sentiment_score") or 0)
        base = max(-1.2, min(1.2, score / 100))
        source = "sentiment_fallback"
        reliability = "low"
        display_label = "未検証の参考値"
    performance_stats = performance_stats or {}
    if performance_stats.get("avg_return_pct") is not None:
        base = (float(base) + float(performance_stats.get("avg_return_pct") or 0)) / 2
    multipliers = {"1d": 0.6, "3d": 1.0, "5d": 1.25}
    return {
        horizon: {
            "value": round(float(base) * multiplier, 2),
            "source": source,
            "reliability": reliability,
            "sample_count": case_count,
            "display_label": display_label,
        }
        for horizon, multiplier in multipliers.items()
    }


def _normalize_probabilities(rows):
    if not rows:
        return []
    total = sum(row["probability"] for row in rows)
    if total == 0:
        return rows
    normalized = []
    running = 0
    for row in rows[:-1]:
        probability = round(row["probability"] / total, 2)
        running += probability
        normalized.append({**row, "probability": probability})
    normalized.append({**rows[-1], "probability": round(max(0, 1 - running), 2)})
    return normalized


def _learned_transitions(state_key, performance_stats):
    performance_stats = performance_stats or {}
    matrix = performance_stats.get("transition_matrix") or {}
    rows = matrix.get(state_key) or {}
    if performance_stats.get("transition_sample_count", 0) < 5:
        return {}
    learned = {}
    for next_state, meta in rows.items():
        probability = meta.get("probability") if isinstance(meta, dict) else None
        count = meta.get("count", 0) if isinstance(meta, dict) else 0
        if probability is None:
            continue
        learned[next_state] = {
            "probability": float(probability),
            "count": int(count or 0),
        }
    return learned
