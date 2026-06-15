import logging
from datetime import timedelta

from django.db import DatabaseError, transaction
from django.db.models import Avg, Count, Q
from django.utils import timezone

from .models import (
    MarketSnapshot,
    PredictionOutcome,
    TechnicalSnapshot,
    WorldModelPrediction,
)
from .market_bars import (
    HORIZON_TOLERANCES,
    market_bars_between,
    nearest_bar_for_horizon,
)

logger = logging.getLogger(__name__)

HORIZONS = {
    "1d": timedelta(days=1),
    "3d": timedelta(days=3),
    "5d": timedelta(days=5),
}

CONFIDENCE_ORDER = ("Low", "Middle", "High")
SAVE_PREDICTION_MIN_INTERVAL_MINUTES = 30
SAVE_PREDICTION_MIN_PRICE_MOVE_PCT = 0.15
MAX_STORED_PREDICTIONS = 5000


def save_prediction(
    world_model,
    *,
    prediction_timestamp=None,
    is_backtest=False,
    min_interval_minutes=SAVE_PREDICTION_MIN_INTERVAL_MINUTES,
    allow_blocked=False,
):
    if not world_model or not world_model.get("price"):
        return None
    readiness_level = world_model.get("readiness_level") or "ready"
    if readiness_level == "blocked" and not allow_blocked:
        return None
    if not is_backtest and _recent_duplicate_prediction(world_model, min_interval_minutes):
        return None
    features = world_model.get("features") or {}
    readiness = world_model.get("readiness") or {}
    prediction_timestamp = prediction_timestamp or timezone.now()
    if timezone.is_naive(prediction_timestamp):
        prediction_timestamp = timezone.make_aware(
            prediction_timestamp,
            timezone=timezone.get_current_timezone(),
        )
    try:
        with transaction.atomic():
            market_snapshot = MarketSnapshot.objects.create(
                symbol=features.get("symbol") or "NIY=F",
                price=world_model["price"],
                open=features.get("open"),
                high=features.get("high"),
                low=features.get("low"),
                close=features.get("close") or world_model["price"],
                volume=features.get("volume"),
                timeframe="1d",
                source=features.get("source") or "yahoo",
                instrument_key=features.get("instrument_key") or readiness.get("instrument_key") or "unknown",
                instrument_type=features.get("instrument_type") or readiness.get("instrument_type") or "unknown",
                source_symbol=features.get("source_symbol") or features.get("symbol") or "",
                fetched_at=prediction_timestamp,
                data_quality_score=world_model.get("data_quality_score"),
                data_quality_level=world_model.get("data_quality_level") or "",
                readiness_level=readiness_level,
            )
            TechnicalSnapshot.objects.create(
                market_snapshot=market_snapshot,
                ema5=features.get("ema5"),
                ema20=features.get("ema20"),
                ema60=features.get("ema60"),
                vwap=features.get("vwap"),
                rsi14=features.get("rsi14"),
                macd=features.get("macd"),
                macd_signal=features.get("macd_signal"),
                adx14=features.get("adx14"),
                atr14=features.get("atr14"),
                bb_upper=features.get("bb_upper"),
                bb_mid=features.get("bb_mid"),
                bb_lower=features.get("bb_lower"),
            )
            return WorldModelPrediction.objects.create(
                prediction_timestamp=prediction_timestamp,
                model_version=world_model.get("model_version") or "wm_v1",
                price=world_model["price"],
                state_key=world_model["state_key"],
                state_label=world_model["state_label"],
                direction=world_model["direction"],
                sentiment_score=world_model["sentiment_score"],
                continuation_score=world_model["continuation_score"],
                shock_score=world_model["shock_score"],
                confidence=world_model["confidence"],
                confidence_score=world_model.get("confidence_score") or 0,
                data_quality_score=world_model.get("data_quality_score"),
                main_scenario=world_model["main_scenario"],
                sub_scenario=world_model.get("sub_scenario") or "",
                invalidation_price=world_model.get("invalidation_price"),
                upside_targets=world_model.get("upside_targets") or [],
                downside_targets=world_model.get("downside_targets") or [],
                evidence=world_model.get("evidence") or [],
                features=features,
                transition_probs=world_model.get("transition_probs") or [],
                expected_returns=world_model.get("expected_returns") or {},
                context=world_model.get("market_context") or world_model.get("context") or {},
                instrument_key=features.get("instrument_key") or readiness.get("instrument_key") or "unknown",
                instrument_type=features.get("instrument_type") or readiness.get("instrument_type") or "unknown",
                source_symbol=features.get("source_symbol") or features.get("symbol") or "",
                source_name=features.get("source_name") or features.get("source") or "",
                readiness_level=readiness_level,
                directional_allowed=bool(world_model.get("directional_allowed")),
                readiness_reason_codes=readiness.get("reason_codes") or world_model.get("readiness_reason_codes") or [],
                bar_counts=readiness.get("bar_counts") or features.get("bar_counts") or {},
                indicator_validity=readiness.get("indicator_validity") or features.get("indicator_validity") or {},
                is_backtest=is_backtest,
            )
    except DatabaseError:
        logger.exception("Failed to save basecalc prediction")
        return None


def prune_prediction_history(max_predictions=MAX_STORED_PREDICTIONS):
    try:
        old_ids = list(
            WorldModelPrediction.objects.order_by("-created_at").values_list(
                "id",
                flat=True,
            )[max_predictions:]
        )
        if not old_ids:
            return 0
        return WorldModelPrediction.objects.filter(id__in=old_ids).delete()[0]
    except DatabaseError:
        logger.exception("Failed to prune basecalc predictions")
        return 0


def evaluate_due_predictions(current_price=None, now=None):
    now = now or timezone.now()
    created = 0
    try:
        oldest_due = now - min(HORIZONS.values())
        predictions = WorldModelPrediction.objects.filter(
            Q(prediction_timestamp__lte=oldest_due)
            | Q(prediction_timestamp__isnull=True, created_at__lte=oldest_due)
        ).order_by("-created_at")[:300]
        for prediction in predictions:
            base_time = _prediction_base_time(prediction)
            for horizon, delta in HORIZONS.items():
                if base_time + delta > now:
                    continue
                if PredictionOutcome.objects.filter(
                    prediction=prediction,
                    horizon=horizon,
                ).exists():
                    continue
                observation = _observation_for_horizon(prediction, horizon, now)
                if observation is None:
                    continue
                _create_outcome(prediction, horizon, observation)
                created += 1
    except DatabaseError:
        logger.exception("Failed to evaluate basecalc predictions")
    return created


def _recent_duplicate_prediction(world_model, min_interval_minutes):
    if not min_interval_minutes:
        return False
    latest = WorldModelPrediction.objects.filter(is_backtest=False).order_by("-created_at").first()
    if latest is None:
        return False
    if latest.created_at < timezone.now() - timedelta(minutes=min_interval_minutes):
        return False
    if latest.state_key != world_model.get("state_key"):
        return False
    if latest.direction != world_model.get("direction"):
        return False
    price_gap_pct = _price_gap_pct(world_model.get("price"), latest.price)
    return price_gap_pct is not None and price_gap_pct < SAVE_PREDICTION_MIN_PRICE_MOVE_PCT


def _price_gap_pct(current_price, previous_price):
    try:
        current_price = float(current_price)
        previous_price = float(previous_price)
    except (TypeError, ValueError):
        return None
    if previous_price <= 0:
        return None
    return abs((current_price - previous_price) / previous_price) * 100


def confidence_adjustment_for_state(state_key, horizon="1d", min_samples=5):
    if not state_key:
        return None
    try:
        outcomes = PredictionOutcome.objects.filter(
            horizon=horizon,
            prediction__state_key=state_key,
            prediction__instrument_key="cme_nikkei_futures",
            prediction__readiness_level="ready",
            prediction__is_backtest=False,
        )
        total = outcomes.count()
        if total < min_samples:
            return None
        aggregate = outcomes.aggregate(avg_return=Avg("realized_return_pct"))
        direction_accuracy = outcomes.filter(direction_hit=True).count() / total
        invalidation_rate = outcomes.filter(invalidation_hit=True).count() / total
        avg_return = aggregate["avg_return"] or 0
        reasons = []
        if direction_accuracy < 0.45:
            reasons.append("方向一致率が低い")
        if invalidation_rate > 0.35:
            reasons.append("無効化到達が多い")
        if avg_return < -0.2:
            reasons.append("平均損益が弱い")
        if not reasons:
            return None
        downgrade = 2 if direction_accuracy < 0.35 or invalidation_rate > 0.5 else 1
        score_penalty = 10 * downgrade
        if direction_accuracy < 0.35:
            score_penalty += 8
        if invalidation_rate > 0.5:
            score_penalty += 8
        return {
            "applied": True,
            "horizon": horizon,
            "sample_count": total,
            "directional_accuracy": round(direction_accuracy, 2),
            "invalidation_rate": round(invalidation_rate, 2),
            "avg_return_pct": round(avg_return, 2),
            "downgrade": downgrade,
            "score_penalty": min(score_penalty, 35),
            "reasons": reasons,
        }
    except DatabaseError:
        logger.exception("Failed to read basecalc confidence adjustment")
        return None


def apply_confidence_adjustment(confidence, adjustment):
    if not adjustment:
        return confidence
    try:
        index = CONFIDENCE_ORDER.index(confidence)
    except ValueError:
        return confidence
    next_index = max(0, index - int(adjustment.get("downgrade") or 1))
    return CONFIDENCE_ORDER[next_index]


def apply_sentiment_score_adjustment(score, adjustment):
    if not adjustment:
        return score
    penalty = int(adjustment.get("score_penalty") or 10)
    if score > 0:
        return max(0, score - penalty)
    if score < 0:
        return min(0, score + penalty)
    return score


def performance_summary(
    horizon="1d",
    state_key=None,
    date_from=None,
    date_to=None,
    model_version=None,
    confidence_min=None,
    instrument_key="cme_nikkei_futures",
    readiness_level="ready",
    is_backtest=False,
):
    try:
        outcomes = PredictionOutcome.objects.filter(horizon=horizon)
        if instrument_key:
            outcomes = outcomes.filter(prediction__instrument_key=instrument_key)
        if readiness_level:
            outcomes = outcomes.filter(prediction__readiness_level=readiness_level)
        if is_backtest is not None:
            outcomes = outcomes.filter(prediction__is_backtest=is_backtest)
        if state_key:
            outcomes = outcomes.filter(prediction__state_key=state_key)
        if date_from:
            outcomes = outcomes.filter(prediction__prediction_timestamp__date__gte=date_from)
        if date_to:
            outcomes = outcomes.filter(prediction__prediction_timestamp__date__lte=date_to)
        if model_version:
            outcomes = outcomes.filter(prediction__model_version=model_version)
        if confidence_min is not None:
            outcomes = outcomes.filter(prediction__confidence_score__gte=confidence_min)
        aggregate = outcomes.aggregate(
            total=Count("id"),
            avg_return=Avg("realized_return_pct"),
            avg_mfe=Avg("mfe_pct"),
            avg_mae=Avg("mae_pct"),
            avg_confidence=Avg("prediction__confidence_score"),
        )
        total = aggregate["total"] or 0
        if total == 0:
            return _empty_performance_summary()
        direction_hits = outcomes.filter(direction_hit=True).count()
        target_t1_hits = outcomes.filter(
            Q(upside_t1_hit=True) | Q(downside_t1_hit=True)
        ).count()
        target_t2_hits = outcomes.filter(
            Q(upside_t2_hit=True) | Q(downside_t2_hit=True)
        ).count()
        invalidations = outcomes.filter(invalidation_hit=True).count()
        return_values = list(outcomes.values_list("realized_return_pct", flat=True))
        mfe_values = [value for value in outcomes.values_list("mfe_pct", flat=True) if value is not None]
        mae_values = [value for value in outcomes.values_list("mae_pct", flat=True) if value is not None]
        return {
            "total_predictions": total,
            "directional_accuracy": round(direction_hits / total, 2),
            "target_t1_hit_rate": round(target_t1_hits / total, 2),
            "target_t2_hit_rate": round(target_t2_hits / total, 2),
            "invalidation_rate": round(invalidations / total, 2),
            "avg_return_pct": round(aggregate["avg_return"] or 0, 2),
            "median_return_pct": _median(return_values),
            "avg_mfe_pct": round(aggregate["avg_mfe"] or 0, 2),
            "avg_mae_pct": round(aggregate["avg_mae"] or 0, 2),
            "avg_confidence_score": round(aggregate["avg_confidence"] or 0, 1),
            "median_mae_pct": _median(mae_values),
            "median_mfe_pct": _median(mfe_values),
            "sample_quality": _sample_quality(total),
            "statistical_warning": "" if total >= 30 else "サンプル数が不足しています",
        }
    except DatabaseError:
        logger.exception("Failed to read basecalc performance")
        return _empty_performance_summary()


def state_performance_summary(horizon="1d", limit=12):
    try:
        rows = (
            PredictionOutcome.objects.filter(horizon=horizon)
            .values("prediction__state_key", "prediction__state_label")
            .annotate(
                total_predictions=Count("id"),
                avg_return_pct=Avg("realized_return_pct"),
                avg_mfe_pct=Avg("mfe_pct"),
                avg_mae_pct=Avg("mae_pct"),
            )
            .order_by("-total_predictions")[:limit]
        )
        result = []
        for row in rows:
            outcomes = PredictionOutcome.objects.filter(
                horizon=horizon,
                prediction__state_key=row["prediction__state_key"],
            )
            expected_returns = [
                _expected_return_value((prediction.expected_returns or {}).get(horizon))
                for prediction in WorldModelPrediction.objects.filter(
                    state_key=row["prediction__state_key"],
                )
            ]
            expected_returns = [float(value) for value in expected_returns if isinstance(value, (int, float))]
            total = row["total_predictions"] or 0
            if total == 0:
                continue
            target_t1_hits = outcomes.filter(
                Q(upside_t1_hit=True) | Q(downside_t1_hit=True)
            ).count()
            result.append(
                {
                    "state_key": row["prediction__state_key"],
                    "state_label": row["prediction__state_label"],
                    "total_predictions": total,
                    "directional_accuracy": round(
                        outcomes.filter(direction_hit=True).count() / total,
                        2,
                    ),
                    "target_t1_hit_rate": round(target_t1_hits / total, 2),
                    "invalidation_rate": round(
                        outcomes.filter(invalidation_hit=True).count() / total,
                        2,
                    ),
                    "avg_return_pct": round(row["avg_return_pct"] or 0, 2),
                    "expected_return_pct": round(
                        sum(expected_returns) / len(expected_returns),
                        2,
                    )
                    if expected_returns
                    else 0,
                    "avg_mfe_pct": round(row["avg_mfe_pct"] or 0, 2),
                    "avg_mae_pct": round(row["avg_mae_pct"] or 0, 2),
                }
            )
        return result
    except DatabaseError:
        logger.exception("Failed to read basecalc state performance")
        return []


def calibration_summary(
    horizon="1d",
    *,
    instrument_key="cme_nikkei_futures",
    readiness_level="ready",
    is_backtest=False,
):
    try:
        outcomes = PredictionOutcome.objects.filter(horizon=horizon)
        if instrument_key:
            outcomes = outcomes.filter(prediction__instrument_key=instrument_key)
        if readiness_level:
            outcomes = outcomes.filter(prediction__readiness_level=readiness_level)
        if is_backtest is not None:
            outcomes = outcomes.filter(prediction__is_backtest=is_backtest)
        buckets = {}
        for outcome in outcomes.select_related("prediction")[:2000]:
            expected = _expected_return_value(
                (outcome.prediction.expected_returns or {}).get(horizon)
            )
            if expected is None:
                continue
            bucket = _calibration_bucket(float(expected))
            row = buckets.setdefault(
                bucket,
                {
                    "bucket": bucket,
                    "sample_count": 0,
                    "expected_total": 0.0,
                    "realized_total": 0.0,
                    "error_total": 0.0,
                },
            )
            realized = float(outcome.realized_return_pct or 0)
            row["sample_count"] += 1
            row["expected_total"] += float(expected)
            row["realized_total"] += realized
            row["error_total"] += realized - float(expected)
        rows = []
        for row in buckets.values():
            count = row["sample_count"]
            if not count:
                continue
            rows.append(
                {
                    "bucket": row["bucket"],
                    "sample_count": count,
                    "avg_expected_pct": round(row["expected_total"] / count, 2),
                    "avg_realized_pct": round(row["realized_total"] / count, 2),
                    "avg_error_pct": round(row["error_total"] / count, 2),
                    "sample_quality": _sample_quality(count),
                }
            )
        return sorted(rows, key=lambda item: _calibration_sort_key(item["bucket"]))
    except DatabaseError:
        logger.exception("Failed to build basecalc calibration summary")
        return []


def _calibration_bucket(value):
    if value < -1.0:
        return "-1.0%未満"
    if value < -0.5:
        return "-1.0%〜-0.5%"
    if value < 0:
        return "-0.5%〜0.0%"
    if value < 0.5:
        return "0.0%〜0.5%"
    if value < 1.0:
        return "0.5%〜1.0%"
    return "1.0%以上"


def _calibration_sort_key(bucket):
    order = {
        "-1.0%未満": 0,
        "-1.0%〜-0.5%": 1,
        "-0.5%〜0.0%": 2,
        "0.0%〜0.5%": 3,
        "0.5%〜1.0%": 4,
        "1.0%以上": 5,
    }
    return order.get(bucket, 99)


def _empty_performance_summary():
    return {
        "total_predictions": 0,
        "directional_accuracy": 0,
        "target_t1_hit_rate": 0,
        "target_t2_hit_rate": 0,
        "invalidation_rate": 0,
        "avg_return_pct": 0,
        "median_return_pct": 0,
        "avg_mfe_pct": 0,
        "avg_mae_pct": 0,
        "avg_confidence_score": 0,
        "median_mae_pct": 0,
        "median_mfe_pct": 0,
        "sample_quality": "insufficient",
        "statistical_warning": "サンプル数が不足しています",
    }


def _sample_quality(total):
    if total >= 30:
        return "reliable"
    if total >= 10:
        return "usable"
    return "insufficient"


def _median(values):
    values = sorted(value for value in values if value is not None)
    if not values:
        return 0
    midpoint = len(values) // 2
    if len(values) % 2:
        return round(values[midpoint], 2)
    return round((values[midpoint - 1] + values[midpoint]) / 2, 2)


def _expected_return_value(value):
    if isinstance(value, dict):
        return value.get("value")
    return value


def improvement_insights(horizon="1d", min_samples=5, limit=6):
    try:
        state_rows = state_performance_summary(horizon, limit=50)
        insights = []
        if not state_rows:
            return [
                {
                    "severity": "info",
                    "title": "検証データ待ち",
                    "detail": "予測履歴が増えると、弱い局面を自動で表示します。",
                    "suggestion": "まずは手動更新で予測を保存してください。",
                    "metric": "0件",
                }
            ]
        for row in state_rows:
            if row["total_predictions"] < min_samples:
                insights.append(
                    {
                        "severity": "info",
                        "title": f"{row['state_label']}は件数不足",
                        "detail": f"検証数が{row['total_predictions']}件で、まだ判断がぶれやすい状態です。",
                        "suggestion": "この局面は配点変更より、先にサンプルを増やしてください。",
                        "metric": f"{row['total_predictions']}件",
                    }
                )
                continue
            if row["directional_accuracy"] < 0.45:
                insights.append(
                    {
                        "severity": "high",
                        "title": f"{row['state_label']}の方向判定を見直し",
                        "detail": f"方向一致率が{row['directional_accuracy']:.0%}で低めです。",
                        "suggestion": "EMA・VWAP・値動きの重みがこの局面に合っているか確認してください。",
                        "metric": f"方向 {row['directional_accuracy']:.0%}",
                    }
                )
            if row["invalidation_rate"] > 0.35:
                insights.append(
                    {
                        "severity": "high",
                        "title": f"{row['state_label']}の無効化ラインを見直し",
                        "detail": f"無効化到達率が{row['invalidation_rate']:.0%}で高めです。",
                        "suggestion": "ATRや直近高安値を使った無効化ラインが近すぎないか確認してください。",
                        "metric": f"無効化 {row['invalidation_rate']:.0%}",
                    }
                )
            if row["target_t1_hit_rate"] < 0.35:
                insights.append(
                    {
                        "severity": "middle",
                        "title": f"{row['state_label']}のT1設定を見直し",
                        "detail": f"T1到達率が{row['target_t1_hit_rate']:.0%}で低めです。",
                        "suggestion": "第1ターゲットを近い支持線・抵抗線やVWAP寄りに調整してください。",
                        "metric": f"T1 {row['target_t1_hit_rate']:.0%}",
                    }
                )
            if row["avg_return_pct"] < -0.2:
                insights.append(
                    {
                        "severity": "middle",
                        "title": f"{row['state_label']}の期待値を確認",
                        "detail": f"平均損益が{row['avg_return_pct']:.2f}%です。",
                        "suggestion": "この局面では信頼度を下げるか、様子見に寄せる条件を追加してください。",
                        "metric": f"平均 {row['avg_return_pct']:.2f}%",
                    }
                )
        if not insights:
            return [
                {
                    "severity": "good",
                    "title": "大きな弱点は未検出",
                    "detail": "現在の検証範囲では、明確に悪い局面は見つかっていません。",
                    "suggestion": "サンプルを増やしながら、局面別の変化を確認してください。",
                    "metric": f"{horizon}",
                }
            ]
        return sorted(
            insights,
            key=lambda item: {"high": 0, "middle": 1, "info": 2, "good": 3}.get(
                item["severity"],
                4,
            ),
        )[:limit]
    except DatabaseError:
        logger.exception("Failed to build basecalc improvement insights")
        return []


def _observation_for_horizon(prediction, horizon, now):
    base_time = _prediction_base_time(prediction)
    target_at = base_time + HORIZONS[horizon]
    if target_at > now:
        return None
    symbol = prediction.features.get("symbol") or "NIY=F"
    bar = nearest_bar_for_horizon(
        symbol,
        horizon,
        target_at,
        instrument_key=getattr(prediction, "instrument_key", None),
    )
    if bar is not None:
        return {
            "price": bar.close,
            "evaluated_at": bar.timestamp,
            "timeframe": bar.timeframe,
        }
    snapshot = _nearest_market_snapshot(
        symbol,
        target_at,
        HORIZON_TOLERANCES.get(horizon, timedelta(hours=36)),
        instrument_key=getattr(prediction, "instrument_key", None),
    )
    if snapshot is None:
        return None
    return {
        "price": snapshot.price,
        "evaluated_at": snapshot.created_at,
        "timeframe": snapshot.timeframe,
    }


def _nearest_market_snapshot(symbol, target_at, tolerance, instrument_key=None):
    start = target_at - tolerance
    end = target_at + tolerance
    queryset = MarketSnapshot.objects.filter(
        symbol=symbol,
        created_at__gte=start,
        created_at__lte=end,
    )
    if instrument_key:
        queryset = queryset.filter(instrument_key=instrument_key)
    before = queryset.filter(created_at__lte=target_at).order_by("-created_at").first()
    after = queryset.filter(created_at__gte=target_at).order_by("created_at").first()
    candidates = [snapshot for snapshot in (before, after) if snapshot is not None]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda snapshot: abs((snapshot.created_at - target_at).total_seconds()),
    )


def _create_outcome(prediction, horizon, observation):
    start_price = prediction.price
    current_price = observation["price"]
    realized_return_pct = ((current_price - start_price) / start_price) * 100
    max_price, min_price = _observed_price_range(prediction, observation)
    upside_targets = prediction.upside_targets or []
    downside_targets = prediction.downside_targets or []
    invalidation = prediction.invalidation_price

    direction_hit = (
        realized_return_pct > 0
        if prediction.direction == "up"
        else realized_return_pct < 0
        if prediction.direction == "down"
        else abs(realized_return_pct) < 0.3
    )
    upside_t1_hit = _target_hit(max_price, upside_targets, 0, above=True)
    upside_t2_hit = _target_hit(max_price, upside_targets, 1, above=True)
    downside_t1_hit = _target_hit(min_price, downside_targets, 0, above=False)
    downside_t2_hit = _target_hit(min_price, downside_targets, 1, above=False)
    invalidation_hit = False
    if invalidation:
        invalidation_hit = (
            min_price <= invalidation
            if prediction.direction == "up"
            else max_price >= invalidation
            if prediction.direction == "down"
            else False
        )

    if prediction.direction == "down":
        mfe_pct = ((start_price - min_price) / start_price) * 100
        mae_pct = -((max_price - start_price) / start_price) * 100
    else:
        mfe_pct = ((max_price - start_price) / start_price) * 100
        mae_pct = -((start_price - min_price) / start_price) * 100

    return PredictionOutcome.objects.create(
        prediction=prediction,
        horizon=horizon,
        evaluated_at=observation["evaluated_at"],
        price_at_evaluation=current_price,
        realized_return_pct=realized_return_pct,
        direction_hit=direction_hit,
        upside_t1_hit=upside_t1_hit,
        upside_t2_hit=upside_t2_hit,
        downside_t1_hit=downside_t1_hit,
        downside_t2_hit=downside_t2_hit,
        invalidation_hit=invalidation_hit,
        mfe_pct=max(mfe_pct, 0),
        mae_pct=min(mae_pct, 0),
    )


def _observed_price_range(prediction, observation):
    symbol = prediction.features.get("symbol") or "NIY=F"
    start_at = _prediction_base_time(prediction)
    end_at = observation["evaluated_at"]
    high_values = [observation["price"]]
    low_values = [observation["price"]]
    if end_at >= start_at and observation.get("timeframe"):
        bars = market_bars_between(
            symbol,
            observation["timeframe"],
            start_at,
            end_at,
            instrument_key=getattr(prediction, "instrument_key", None),
        )
        if bars:
            high_values.extend((bar.high or bar.close) for bar in bars)
            low_values.extend((bar.low or bar.close) for bar in bars)
    snapshots = MarketSnapshot.objects.filter(
        symbol=symbol,
        created_at__gte=start_at,
        created_at__lte=end_at,
    )
    high_values.extend(snapshot.price for snapshot in snapshots)
    low_values.extend(snapshot.price for snapshot in snapshots)
    return max(high_values), min(low_values)


def _prediction_base_time(prediction):
    return prediction.prediction_timestamp or prediction.created_at


def _target_hit(current_price, targets, index, above):
    if len(targets) <= index:
        return False
    target = targets[index].get("price") if isinstance(targets[index], dict) else targets[index]
    if target is None:
        return False
    return current_price >= target if above else current_price <= target
