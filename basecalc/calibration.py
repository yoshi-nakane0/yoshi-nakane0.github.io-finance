import logging

from django.db import DatabaseError

from .models import PredictionOutcome

logger = logging.getLogger(__name__)


def confidence_calibration_summary(
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
        buckets = {}
        for outcome in outcomes[:2000]:
            score = getattr(outcome.prediction, "confidence_score", None)
            bucket = _confidence_bucket(score)
            if bucket is None:
                continue
            row = buckets.setdefault(
                bucket,
                {
                    "bucket": bucket,
                    "sample_count": 0,
                    "direction_hits": 0,
                    "target_t1_hits": 0,
                    "return_total": 0.0,
                },
            )
            row["sample_count"] += 1
            row["direction_hits"] += 1 if outcome.direction_hit else 0
            row["target_t1_hits"] += (
                1 if outcome.upside_t1_hit or outcome.downside_t1_hit else 0
            )
            row["return_total"] += float(outcome.realized_return_pct or 0)
        return [
            _finalize(row)
            for row in sorted(
                buckets.values(),
                key=lambda item: _bucket_sort_key(item["bucket"]),
            )
        ]
    except DatabaseError:
        logger.exception("Failed to build basecalc confidence calibration")
        return []


def _finalize(row):
    total = row["sample_count"]
    return {
        "bucket": row["bucket"],
        "sample_count": total,
        "directional_accuracy": round(row["direction_hits"] / total, 2)
        if total
        else 0,
        "target_t1_hit_rate": round(row["target_t1_hits"] / total, 2) if total else 0,
        "avg_return_pct": round(row["return_total"] / total, 2) if total else 0,
        "sample_quality": _sample_quality(total),
    }


def _confidence_bucket(score):
    try:
        value = int(score)
    except (TypeError, ValueError):
        return None
    if value < 50:
        return "50未満"
    if value < 60:
        return "50台"
    if value < 70:
        return "60台"
    if value < 80:
        return "70台"
    return "80台"


def _bucket_sort_key(bucket):
    return {
        "50未満": 0,
        "50台": 1,
        "60台": 2,
        "70台": 3,
        "80台": 4,
    }.get(bucket, 99)


def _sample_quality(total):
    if total >= 30:
        return "reliable"
    if total >= 10:
        return "usable"
    return "insufficient"
