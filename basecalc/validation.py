import logging

from django.db import DatabaseError
from django.utils import timezone

from .models import PredictionOutcome

logger = logging.getLogger(__name__)


def validation_design_summary(
    horizon="1d",
    *,
    instrument_key="cme_nikkei_futures",
    readiness_level="ready",
    is_backtest=False,
):
    try:
        outcomes = PredictionOutcome.objects.filter(horizon=horizon).select_related(
            "prediction"
        )
        if instrument_key:
            outcomes = outcomes.filter(prediction__instrument_key=instrument_key)
        if readiness_level:
            outcomes = outcomes.filter(prediction__readiness_level=readiness_level)
        if is_backtest is not None:
            outcomes = outcomes.filter(prediction__is_backtest=is_backtest)
        rows = list(outcomes.order_by("prediction__prediction_timestamp", "prediction__created_at")[:3000])
    except DatabaseError:
        logger.exception("Failed to build basecalc validation design summary")
        return _empty_summary()
    if not rows:
        return _empty_summary()
    return {
        "walk_forward": _walk_forward(rows),
        "period_splits": _period_splits(rows),
        "recent_window": _recent_window(rows),
        "volatility_regimes": _volatility_regimes(rows),
        "market_regimes": _market_regimes(rows),
    }


def _walk_forward(rows, folds=3):
    if len(rows) < folds:
        return [_metric_row("全期間", rows)]
    size = max(1, len(rows) // folds)
    result = []
    for index in range(folds):
        start = index * size
        end = None if index == folds - 1 else (index + 1) * size
        result.append(_metric_row(f"WF{index + 1}", rows[start:end]))
    return result


def _period_splits(rows):
    midpoint = len(rows) // 2
    return [
        _metric_row("前半", rows[:midpoint]),
        _metric_row("後半", rows[midpoint:]),
    ]


def _recent_window(rows, days=60):
    cutoff = timezone.now() - timezone.timedelta(days=days)
    recent = [
        row
        for row in rows
        if _prediction_time(row) is not None and _prediction_time(row) >= cutoff
    ]
    return _metric_row(f"直近{days}日", recent or rows[-min(len(rows), 30) :])


def _volatility_regimes(rows):
    buckets = {"低ボラ": [], "通常": [], "高ボラ": []}
    for row in rows:
        features = row.prediction.features or {}
        price = float(row.prediction.price or 0)
        atr = _to_float(features.get("atr14"))
        ratio = atr / price if atr is not None and price else None
        if ratio is None or ratio < 0.01:
            buckets["低ボラ"].append(row)
        elif ratio < 0.018:
            buckets["通常"].append(row)
        else:
            buckets["高ボラ"].append(row)
    return [_metric_row(label, bucket) for label, bucket in buckets.items()]


def _market_regimes(rows):
    buckets = {}
    for row in rows:
        label = row.prediction.state_label or row.prediction.state_key or "不明"
        buckets.setdefault(label, []).append(row)
    return [_metric_row(label, bucket) for label, bucket in sorted(buckets.items())]


def _metric_row(label, rows):
    rows = list(rows or [])
    total = len(rows)
    if not total:
        return {
            "label": label,
            "sample_count": 0,
            "directional_accuracy": 0,
            "avg_return_pct": 0,
            "target_t1_hit_rate": 0,
            "sample_quality": "insufficient",
        }
    direction_hits = sum(1 for row in rows if row.direction_hit)
    target_hits = sum(1 for row in rows if row.upside_t1_hit or row.downside_t1_hit)
    avg_return = sum(float(row.realized_return_pct or 0) for row in rows) / total
    return {
        "label": label,
        "sample_count": total,
        "directional_accuracy": round(direction_hits / total, 2),
        "avg_return_pct": round(avg_return, 2),
        "target_t1_hit_rate": round(target_hits / total, 2),
        "sample_quality": _sample_quality(total),
    }


def _prediction_time(outcome):
    return outcome.prediction.prediction_timestamp or outcome.prediction.created_at


def _sample_quality(total):
    if total >= 30:
        return "reliable"
    if total >= 10:
        return "usable"
    return "insufficient"


def _empty_summary():
    return {
        "walk_forward": [],
        "period_splits": [],
        "recent_window": _metric_row("直近60日", []),
        "volatility_regimes": [],
        "market_regimes": [],
    }


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
