import json
from pathlib import Path

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import MarketBar, MarketSnapshot, PredictionOutcome, WorldModelPrediction
from .outcomes import evaluate_due_predictions
from .instrument import normalize_instrument


def export_basecalc_history(output_path: str, limit_predictions: int = 5000) -> dict:
    """basecalc の予測履歴、検証結果、必要最小限の MarketBar を JSON に保存する。"""
    path = Path(output_path)
    predictions = list(WorldModelPrediction.objects.order_by("-created_at")[:limit_predictions])
    prediction_ids = [prediction.id for prediction in predictions]
    outcomes = PredictionOutcome.objects.filter(prediction_id__in=prediction_ids).order_by(
        "prediction_id",
        "horizon",
    )
    bars = MarketBar.objects.order_by("-timestamp")[: max(limit_predictions, 2000)]
    snapshots = MarketSnapshot.objects.order_by("-created_at")[: min(limit_predictions, 2000)]
    payload = {
        "schema": "basecalc_history_v2",
        "exported_at": _iso(timezone.now()),
        "predictions": [serialize_prediction(prediction) for prediction in predictions],
        "outcomes": [serialize_outcome(outcome) for outcome in outcomes],
        "market_bars": [serialize_market_bar(bar) for bar in bars],
        "market_snapshots": [serialize_market_snapshot(snapshot) for snapshot in snapshots],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "output_path": str(path),
        "predictions": len(payload["predictions"]),
        "outcomes": len(payload["outcomes"]),
        "market_bars": len(payload["market_bars"]),
        "market_snapshots": len(payload["market_snapshots"]),
    }


def import_basecalc_history(input_path: str) -> dict:
    """JSON から basecalc 履歴を復元する。既存行は重複登録しない。"""
    path = Path(input_path)
    if not path.exists():
        return {"skipped": True, "reason": "missing", "input_path": str(path)}
    payload = json.loads(path.read_text(encoding="utf-8"))
    schema = payload.get("schema") or "basecalc_history_v1"
    prediction_map = {}
    stats = {
        "skipped": False,
        "predictions_created": 0,
        "predictions_skipped": 0,
        "outcomes_created": 0,
        "outcomes_skipped": 0,
        "market_bars_created": 0,
        "market_bars_updated": 0,
        "market_bars_skipped": 0,
        "market_snapshots_created": 0,
        "market_snapshots_skipped": 0,
    }
    with transaction.atomic():
        for item in payload.get("predictions") or []:
            prediction, created = _import_prediction(item, schema=schema)
            prediction_map[item.get("key") or _prediction_key_from_item(item)] = prediction
            stats["predictions_created" if created else "predictions_skipped"] += 1
        for item in payload.get("outcomes") or []:
            prediction = prediction_map.get(item.get("prediction_key"))
            if prediction is None:
                stats["outcomes_skipped"] += 1
                continue
            _, created = _import_outcome(prediction, item)
            stats["outcomes_created" if created else "outcomes_skipped"] += 1
        for item in payload.get("market_bars") or []:
            _, created, updated = _import_market_bar(item)
            if created:
                stats["market_bars_created"] += 1
            elif updated:
                stats["market_bars_updated"] += 1
            else:
                stats["market_bars_skipped"] += 1
        for item in payload.get("market_snapshots") or []:
            _, created = _import_market_snapshot(item)
            stats["market_snapshots_created" if created else "market_snapshots_skipped"] += 1
    return stats


def serialize_prediction(prediction: WorldModelPrediction) -> dict:
    created_at = _iso(prediction.created_at)
    return {
        "key": _prediction_key(prediction),
        "created_at": created_at,
        "prediction_timestamp": _iso(getattr(prediction, "prediction_timestamp", None)),
        "price": prediction.price,
        "state_key": prediction.state_key,
        "state_label": prediction.state_label,
        "direction": prediction.direction,
        "sentiment_score": prediction.sentiment_score,
        "continuation_score": prediction.continuation_score,
        "shock_score": prediction.shock_score,
        "confidence": prediction.confidence,
        "main_scenario": prediction.main_scenario,
        "sub_scenario": prediction.sub_scenario,
        "invalidation_price": prediction.invalidation_price,
        "upside_targets": prediction.upside_targets,
        "downside_targets": prediction.downside_targets,
        "evidence": prediction.evidence,
        "features": prediction.features,
        "model_version": getattr(prediction, "model_version", "wm_v1"),
        "confidence_score": getattr(prediction, "confidence_score", 0),
        "data_quality_score": getattr(prediction, "data_quality_score", None),
        "transition_probs": getattr(prediction, "transition_probs", []),
        "expected_returns": getattr(prediction, "expected_returns", {}),
        "context": getattr(prediction, "context", {}),
        "instrument_key": getattr(prediction, "instrument_key", "unknown"),
        "instrument_type": getattr(prediction, "instrument_type", "unknown"),
        "source_symbol": getattr(prediction, "source_symbol", ""),
        "source_name": getattr(prediction, "source_name", ""),
        "readiness_level": getattr(prediction, "readiness_level", "blocked"),
        "directional_allowed": getattr(prediction, "directional_allowed", False),
        "readiness_reason_codes": getattr(prediction, "readiness_reason_codes", []),
        "bar_counts": getattr(prediction, "bar_counts", {}),
        "indicator_validity": getattr(prediction, "indicator_validity", {}),
        "is_backtest": getattr(prediction, "is_backtest", False),
    }


def serialize_outcome(outcome: PredictionOutcome) -> dict:
    return {
        "prediction_key": _prediction_key(outcome.prediction),
        "horizon": outcome.horizon,
        "evaluated_at": _iso(outcome.evaluated_at),
        "price_at_evaluation": outcome.price_at_evaluation,
        "realized_return_pct": outcome.realized_return_pct,
        "direction_hit": outcome.direction_hit,
        "upside_t1_hit": outcome.upside_t1_hit,
        "upside_t2_hit": outcome.upside_t2_hit,
        "downside_t1_hit": outcome.downside_t1_hit,
        "downside_t2_hit": outcome.downside_t2_hit,
        "invalidation_hit": outcome.invalidation_hit,
        "mfe_pct": outcome.mfe_pct,
        "mae_pct": outcome.mae_pct,
    }


def serialize_market_bar(bar: MarketBar) -> dict:
    return {
        "symbol": bar.symbol,
        "timeframe": bar.timeframe,
        "timestamp": _iso(bar.timestamp),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "source": bar.source,
        "instrument_key": getattr(bar, "instrument_key", "unknown"),
        "instrument_type": getattr(bar, "instrument_type", "unknown"),
        "data_quality_score": getattr(bar, "data_quality_score", None),
    }


def serialize_market_snapshot(snapshot: MarketSnapshot) -> dict:
    return {
        "created_at": _iso(snapshot.created_at),
        "symbol": snapshot.symbol,
        "price": snapshot.price,
        "open": snapshot.open,
        "high": snapshot.high,
        "low": snapshot.low,
        "close": snapshot.close,
        "volume": snapshot.volume,
        "timeframe": snapshot.timeframe,
        "source": snapshot.source,
        "instrument_key": getattr(snapshot, "instrument_key", "unknown"),
        "instrument_type": getattr(snapshot, "instrument_type", "unknown"),
        "source_symbol": getattr(snapshot, "source_symbol", ""),
        "fetched_at": _iso(getattr(snapshot, "fetched_at", None)),
        "data_quality_score": getattr(snapshot, "data_quality_score", None),
        "data_quality_level": getattr(snapshot, "data_quality_level", ""),
        "readiness_level": getattr(snapshot, "readiness_level", ""),
    }


def evaluate_imported_history():
    return evaluate_due_predictions()


def _import_prediction(item, schema="basecalc_history_v1"):
    created_at = _dt(item.get("created_at"))
    prediction_timestamp = _dt(item.get("prediction_timestamp")) or created_at
    existing = _find_prediction(item, created_at)
    if existing:
        return existing, False
    features = item.get("features") or {}
    instrument = normalize_instrument(
        item.get("source_symbol") or features.get("symbol"),
        item.get("source_name") or features.get("source"),
    )
    readiness_level = _readiness_level_from_item(item, schema)
    prediction = WorldModelPrediction.objects.create(
        prediction_timestamp=prediction_timestamp,
        price=item["price"],
        state_key=item.get("state_key") or "range_neutral",
        state_label=item.get("state_label") or "レンジ中立",
        direction=item.get("direction") or "neutral",
        sentiment_score=item.get("sentiment_score") or 0,
        continuation_score=item.get("continuation_score") or 0,
        shock_score=item.get("shock_score") or 0,
        confidence=item.get("confidence") or "Low",
        main_scenario=item.get("main_scenario") or "",
        sub_scenario=item.get("sub_scenario") or "",
        invalidation_price=item.get("invalidation_price"),
        upside_targets=item.get("upside_targets") or [],
        downside_targets=item.get("downside_targets") or [],
        evidence=item.get("evidence") or [],
        features=features,
        model_version=item.get("model_version") or "wm_v1",
        confidence_score=item.get("confidence_score") or 0,
        data_quality_score=item.get("data_quality_score"),
        transition_probs=item.get("transition_probs") or [],
        expected_returns=item.get("expected_returns") or {},
        context=item.get("context") or {},
        instrument_key=item.get("instrument_key") or instrument["instrument_key"],
        instrument_type=item.get("instrument_type") or instrument["instrument_type"],
        source_symbol=item.get("source_symbol") or instrument["symbol"] or features.get("symbol") or "",
        source_name=item.get("source_name") or instrument["source"] or features.get("source") or "",
        readiness_level=readiness_level,
        directional_allowed=bool(item.get("directional_allowed")) and readiness_level == "ready",
        readiness_reason_codes=item.get("readiness_reason_codes") or [],
        bar_counts=item.get("bar_counts") or {},
        indicator_validity=item.get("indicator_validity") or features.get("indicator_validity") or {},
        is_backtest=bool(item.get("is_backtest")),
    )
    if created_at:
        WorldModelPrediction.objects.filter(id=prediction.id).update(created_at=created_at)
        prediction.created_at = created_at
    return prediction, True


def _find_prediction(item, created_at):
    queryset = WorldModelPrediction.objects.filter(
        state_key=item.get("state_key") or "range_neutral",
        direction=item.get("direction") or "neutral",
        price=item.get("price"),
    )
    if created_at:
        found = queryset.filter(created_at=created_at).first()
        if found:
            return found
    return queryset.filter(main_scenario=item.get("main_scenario") or "").order_by("-created_at").first()


def _import_outcome(prediction, item):
    defaults = {
        "evaluated_at": _dt(item.get("evaluated_at")),
        "price_at_evaluation": item.get("price_at_evaluation") or 0,
        "realized_return_pct": item.get("realized_return_pct") or 0,
        "direction_hit": bool(item.get("direction_hit")),
        "upside_t1_hit": bool(item.get("upside_t1_hit")),
        "upside_t2_hit": bool(item.get("upside_t2_hit")),
        "downside_t1_hit": bool(item.get("downside_t1_hit")),
        "downside_t2_hit": bool(item.get("downside_t2_hit")),
        "invalidation_hit": bool(item.get("invalidation_hit")),
        "mfe_pct": item.get("mfe_pct"),
        "mae_pct": item.get("mae_pct"),
    }
    outcome, created = PredictionOutcome.objects.get_or_create(
        prediction=prediction,
        horizon=item.get("horizon") or "1d",
        defaults=defaults,
    )
    return outcome, created


def _import_market_bar(item):
    timestamp = _dt(item.get("timestamp"))
    if timestamp is None:
        return None, False, False
    instrument = normalize_instrument(item.get("symbol"), item.get("source"))
    lookup = {
        "symbol": instrument["symbol"] or item.get("symbol") or "NIY=F",
        "timeframe": item.get("timeframe") or "1d",
        "timestamp": timestamp,
    }
    defaults = {
        "open": item.get("open"),
        "high": item.get("high"),
        "low": item.get("low"),
        "close": item.get("close") or 0,
        "volume": item.get("volume"),
        "source": item.get("source") or "history_import",
        "instrument_key": item.get("instrument_key") or instrument["instrument_key"],
        "instrument_type": item.get("instrument_type") or instrument["instrument_type"],
        "data_quality_score": item.get("data_quality_score"),
    }
    market_bar, created = MarketBar.objects.get_or_create(
        **lookup,
        defaults=defaults,
    )
    if created:
        return market_bar, True, False
    if defaults["source"] == "225navi" and market_bar.source != "225navi":
        for key, value in defaults.items():
            setattr(market_bar, key, value)
        market_bar.save(update_fields=list(defaults.keys()))
        return market_bar, False, True
    return market_bar, False, False


def _import_market_snapshot(item):
    created_at = _dt(item.get("created_at"))
    instrument = normalize_instrument(item.get("symbol"), item.get("source"))
    existing = MarketSnapshot.objects.filter(
        symbol=instrument["symbol"] or item.get("symbol") or "NIY=F",
        created_at=created_at,
        source=item.get("source") or "history_import",
    ).first()
    if existing:
        return existing, False
    snapshot = MarketSnapshot.objects.create(
        symbol=instrument["symbol"] or item.get("symbol") or "NIY=F",
        price=item.get("price") or 0,
        open=item.get("open"),
        high=item.get("high"),
        low=item.get("low"),
        close=item.get("close"),
        volume=item.get("volume"),
        timeframe=item.get("timeframe") or "1d",
        source=item.get("source") or "history_import",
        instrument_key=item.get("instrument_key") or instrument["instrument_key"],
        instrument_type=item.get("instrument_type") or instrument["instrument_type"],
        source_symbol=item.get("source_symbol") or item.get("symbol") or "",
        fetched_at=_dt(item.get("fetched_at")) or created_at,
        data_quality_score=item.get("data_quality_score"),
        data_quality_level=item.get("data_quality_level") or "",
        readiness_level=item.get("readiness_level") or "",
    )
    if created_at:
        MarketSnapshot.objects.filter(id=snapshot.id).update(created_at=created_at)
        snapshot.created_at = created_at
    return snapshot, True


def _prediction_key(prediction):
    return "|".join(
        [
            _iso(prediction.created_at),
            prediction.state_key or "",
            prediction.direction or "",
            str(round(float(prediction.price), 4)),
        ]
    )


def _prediction_key_from_item(item):
    return "|".join(
        [
            item.get("created_at") or "",
            item.get("state_key") or "",
            item.get("direction") or "",
            str(round(float(item.get("price") or 0), 4)),
        ]
    )


def _readiness_level_from_item(item, schema):
    if item.get("readiness_level"):
        return item["readiness_level"]
    if schema == "basecalc_history_v2":
        return "blocked"
    score = item.get("data_quality_score")
    try:
        score = int(score)
    except (TypeError, ValueError):
        score = 0
    if score >= 50:
        return "limited"
    return "blocked"


def _iso(value):
    return value.isoformat() if value else None


def _dt(value):
    if not value:
        return None
    return parse_datetime(value) if isinstance(value, str) else value
