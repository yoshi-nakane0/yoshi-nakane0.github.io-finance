from collections import Counter, defaultdict

from django.db import DatabaseError

from .models import WorldModelPrediction


BASELINE_LABELS = {
    "always_up": "常に上昇",
    "always_down": "常に下落",
    "always_neutral": "常に中立",
    "continuation": "前日方向継続",
    "ema_cross": "EMAクロス",
    "vwap_side": "VWAP位置",
    "atr_range": "単純ATRレンジ",
    "random": "ランダム基準",
    "model": "現行モデル",
}


def baseline_comparison_summary(outcomes, horizon="1d"):
    if hasattr(outcomes, "select_related"):
        outcomes = outcomes.select_related("prediction")[:5000]
    rows = list(outcomes)
    if not rows:
        return {"sample_count": 0, "rows": [], "best_baseline": {}}

    buckets = {key: _empty_row(key) for key in BASELINE_LABELS}
    for index, outcome in enumerate(rows):
        realized = _to_float(getattr(outcome, "realized_return_pct", None))
        if realized is None:
            continue
        prediction = outcome.prediction
        actual_direction = _direction_from_return(realized)
        predicted = {
            "always_up": "up",
            "always_down": "down",
            "always_neutral": "neutral",
            "continuation": _continuation_direction(prediction),
            "ema_cross": _ema_cross_direction(prediction),
            "vwap_side": _vwap_direction(prediction),
            "atr_range": _atr_range_direction(prediction, realized),
            "random": "up" if index % 2 == 0 else "down",
            "model": getattr(prediction, "direction", None),
        }
        model_expected = _expected_return_value(
            (getattr(prediction, "expected_returns", None) or {}).get(horizon)
        )
        for key, direction in predicted.items():
            _accumulate(
                buckets[key],
                direction,
                actual_direction,
                realized,
                model_expected if key == "model" else None,
            )

    finalized = [_finalize(row) for row in buckets.values()]
    finalized = [row for row in finalized if row["sample_count"]]
    best = max(
        finalized,
        key=lambda row: (
            row["risk_adjusted_return_pct"],
            row["balanced_accuracy"],
            row["directional_accuracy"],
        ),
        default={},
    )
    return {
        "sample_count": max((row["sample_count"] for row in finalized), default=0),
        "rows": finalized,
        "best_baseline": best,
    }


def learned_transition_stats(state_key=None, *, limit=1000, min_samples=5):
    try:
        predictions = list(
            WorldModelPrediction.objects.filter(readiness_level="ready")
            .order_by("created_at")
            .only("state_key", "created_at")[:limit]
        )
    except DatabaseError:
        return {}
    counts = defaultdict(Counter)
    for current, nxt in zip(predictions, predictions[1:]):
        if not current.state_key or not nxt.state_key:
            continue
        counts[current.state_key][nxt.state_key] += 1
    matrix = {}
    sample_count = 0
    for source_state, next_counts in counts.items():
        total = sum(next_counts.values())
        if total < min_samples:
            continue
        matrix[source_state] = {
            next_state: {
                "count": count,
                "probability": round(count / total, 2),
            }
            for next_state, count in next_counts.items()
        }
        if source_state == state_key:
            sample_count = total
    return {
        "transition_matrix": matrix,
        "transition_sample_count": sample_count,
    }


def _empty_row(key):
    return {
        "key": key,
        "label": BASELINE_LABELS[key],
        "sample_count": 0,
        "hits": 0,
        "actual_counts": Counter(),
        "hit_counts": Counter(),
        "brier_values": [],
        "pnl_values": [],
        "mae_values": [],
    }


def _accumulate(row, predicted_direction, actual_direction, realized, expected_return):
    if predicted_direction not in {"up", "down", "neutral"} or actual_direction is None:
        return
    row["sample_count"] += 1
    row["actual_counts"][actual_direction] += 1
    hit = predicted_direction == actual_direction
    if hit:
        row["hits"] += 1
        row["hit_counts"][actual_direction] += 1
    row["brier_values"].append(
        (_up_probability(predicted_direction) - _up_actual(actual_direction)) ** 2
    )
    row["pnl_values"].append(_strategy_return(predicted_direction, realized))
    if expected_return is not None:
        row["mae_values"].append(abs(realized - float(expected_return)))


def _finalize(row):
    total = row["sample_count"]
    pnl = row["pnl_values"]
    balanced_values = []
    for direction, count in row["actual_counts"].items():
        if count:
            balanced_values.append(row["hit_counts"][direction] / count)
    return {
        "key": row["key"],
        "label": row["label"],
        "sample_count": total,
        "directional_accuracy": round(row["hits"] / total, 2) if total else 0,
        "balanced_accuracy": round(sum(balanced_values) / len(balanced_values), 2)
        if balanced_values
        else 0,
        "brier_score": _average(row["brier_values"]),
        "avg_strategy_return_pct": _average(pnl),
        "risk_adjusted_return_pct": round(_average_raw(pnl) - 0.05, 2) if pnl else 0,
        "max_drawdown_pct": _max_drawdown(pnl),
        "model_mae": _average(row["mae_values"]),
    }


def _direction_from_return(value):
    if value > 0.3:
        return "up"
    if value < -0.3:
        return "down"
    return "neutral"


def _continuation_direction(prediction):
    features = getattr(prediction, "features", None) or {}
    previous = _to_float(features.get("previous_close"))
    current = _to_float(features.get("close")) or _to_float(
        getattr(prediction, "price", None)
    )
    if previous is None or current is None:
        return None
    return (
        _direction_from_return(((current - previous) / previous) * 100)
        if previous
        else None
    )


def _ema_cross_direction(prediction):
    features = getattr(prediction, "features", None) or {}
    ema5 = _to_float(features.get("ema5"))
    ema20 = _to_float(features.get("ema20"))
    if ema5 is None or ema20 is None:
        return None
    if ema5 > ema20:
        return "up"
    if ema5 < ema20:
        return "down"
    return "neutral"


def _vwap_direction(prediction):
    features = getattr(prediction, "features", None) or {}
    close = _to_float(features.get("close")) or _to_float(
        getattr(prediction, "price", None)
    )
    vwap = _to_float(features.get("vwap"))
    if close is None or vwap is None:
        return None
    if close > vwap:
        return "up"
    if close < vwap:
        return "down"
    return "neutral"


def _atr_range_direction(prediction, realized):
    features = getattr(prediction, "features", None) or {}
    atr = _to_float(features.get("atr14"))
    price = _to_float(getattr(prediction, "price", None))
    if not atr or not price:
        return "neutral"
    threshold_pct = max(0.3, min(1.2, atr / price * 100))
    if realized > threshold_pct:
        return "up"
    if realized < -threshold_pct:
        return "down"
    return "neutral"


def _expected_return_value(value):
    if isinstance(value, dict):
        return _to_float(value.get("value"))
    return _to_float(value)


def _up_probability(direction):
    return {"up": 0.75, "neutral": 0.5, "down": 0.25}.get(direction, 0.5)


def _up_actual(direction):
    return {"up": 1.0, "neutral": 0.5, "down": 0.0}.get(direction, 0.5)


def _strategy_return(direction, realized):
    if direction == "up":
        return realized - 0.05
    if direction == "down":
        return -realized - 0.05
    return -0.01


def _average(values):
    values = [value for value in values if value is not None]
    return round(sum(values) / len(values), 2) if values else 0


def _average_raw(values):
    values = [value for value in values if value is not None]
    return sum(values) / len(values) if values else 0


def _max_drawdown(values):
    equity = 0
    peak = 0
    drawdown = 0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return round(drawdown, 2)


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
