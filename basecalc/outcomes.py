import logging
from datetime import timedelta

from django.db import DatabaseError, transaction
from django.db.models import Avg, Count
from django.utils import timezone

from .models import (
    MarketSnapshot,
    PredictionOutcome,
    TechnicalSnapshot,
    WorldModelPrediction,
)

logger = logging.getLogger(__name__)

HORIZONS = {
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
    "3d": timedelta(days=3),
    "5d": timedelta(days=5),
}


def save_prediction(world_model):
    if not world_model or not world_model.get("price"):
        return None
    features = world_model.get("features") or {}
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
                price=world_model["price"],
                state_key=world_model["state_key"],
                state_label=world_model["state_label"],
                direction=world_model["direction"],
                sentiment_score=world_model["sentiment_score"],
                continuation_score=world_model["continuation_score"],
                shock_score=world_model["shock_score"],
                confidence=world_model["confidence"],
                main_scenario=world_model["main_scenario"],
                sub_scenario=world_model.get("sub_scenario") or "",
                invalidation_price=world_model.get("invalidation_price"),
                upside_targets=world_model.get("upside_targets") or [],
                downside_targets=world_model.get("downside_targets") or [],
                evidence=world_model.get("evidence") or [],
                features=features,
            )
    except DatabaseError:
        logger.exception("Failed to save basecalc prediction")
        return None


def evaluate_due_predictions(current_price, now=None):
    if not current_price:
        return 0
    now = now or timezone.now()
    created = 0
    try:
        predictions = WorldModelPrediction.objects.filter(
            created_at__lte=now - min(HORIZONS.values())
        ).order_by("-created_at")[:300]
        for prediction in predictions:
            for horizon, delta in HORIZONS.items():
                if prediction.created_at + delta > now:
                    continue
                if PredictionOutcome.objects.filter(
                    prediction=prediction,
                    horizon=horizon,
                ).exists():
                    continue
                _create_outcome(prediction, horizon, current_price, now)
                created += 1
    except DatabaseError:
        logger.exception("Failed to evaluate basecalc predictions")
    return created


def performance_summary(horizon="1d", state_key=None, date_from=None, date_to=None):
    try:
        outcomes = PredictionOutcome.objects.filter(horizon=horizon)
        if state_key:
            outcomes = outcomes.filter(prediction__state_key=state_key)
        if date_from:
            outcomes = outcomes.filter(evaluated_at__date__gte=date_from)
        if date_to:
            outcomes = outcomes.filter(evaluated_at__date__lte=date_to)
        aggregate = outcomes.aggregate(
            total=Count("id"),
            avg_return=Avg("realized_return_pct"),
            avg_mfe=Avg("mfe_pct"),
            avg_mae=Avg("mae_pct"),
        )
        total = aggregate["total"] or 0
        if total == 0:
            return {
                "total_predictions": 0,
                "directional_accuracy": 0,
                "target_t1_hit_rate": 0,
                "target_t2_hit_rate": 0,
                "invalidation_rate": 0,
                "avg_return_pct": 0,
                "median_mae_pct": 0,
                "median_mfe_pct": 0,
            }
        direction_hits = outcomes.filter(direction_hit=True).count()
        target_t1_hits = outcomes.filter(upside_t1_hit=True).count() + outcomes.filter(
            downside_t1_hit=True
        ).count()
        target_t2_hits = outcomes.filter(upside_t2_hit=True).count() + outcomes.filter(
            downside_t2_hit=True
        ).count()
        invalidations = outcomes.filter(invalidation_hit=True).count()
        return {
            "total_predictions": total,
            "directional_accuracy": round(direction_hits / total, 2),
            "target_t1_hit_rate": round(target_t1_hits / total, 2),
            "target_t2_hit_rate": round(target_t2_hits / total, 2),
            "invalidation_rate": round(invalidations / total, 2),
            "avg_return_pct": round(aggregate["avg_return"] or 0, 2),
            "median_mae_pct": round(aggregate["avg_mae"] or 0, 2),
            "median_mfe_pct": round(aggregate["avg_mfe"] or 0, 2),
        }
    except DatabaseError:
        logger.exception("Failed to read basecalc performance")
        return {
            "total_predictions": 0,
            "directional_accuracy": 0,
            "target_t1_hit_rate": 0,
            "target_t2_hit_rate": 0,
            "invalidation_rate": 0,
            "avg_return_pct": 0,
            "median_mae_pct": 0,
            "median_mfe_pct": 0,
        }


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
            total = row["total_predictions"] or 0
            if total == 0:
                continue
            target_t1_hits = outcomes.filter(upside_t1_hit=True).count() + outcomes.filter(
                downside_t1_hit=True
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
                    "avg_mfe_pct": round(row["avg_mfe_pct"] or 0, 2),
                    "avg_mae_pct": round(row["avg_mae_pct"] or 0, 2),
                }
            )
        return result
    except DatabaseError:
        logger.exception("Failed to read basecalc state performance")
        return []


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
                        "suggestion": "EMA・VWAP・短時間足の重みがこの局面に合っているか確認してください。",
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


def _create_outcome(prediction, horizon, current_price, now):
    start_price = prediction.price
    realized_return_pct = ((current_price - start_price) / start_price) * 100
    observed_prices = list(
        MarketSnapshot.objects.filter(
            symbol=prediction.features.get("symbol") or "NIY=F",
            created_at__gte=prediction.created_at,
            created_at__lte=now,
        ).values_list("price", flat=True)
    )
    observed_prices.append(current_price)
    max_price = max(observed_prices)
    min_price = min(observed_prices)
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
        evaluated_at=now,
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


def _target_hit(current_price, targets, index, above):
    if len(targets) <= index:
        return False
    target = targets[index].get("price") if isinstance(targets[index], dict) else targets[index]
    return current_price >= target if above else current_price <= target
