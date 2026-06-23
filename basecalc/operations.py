import time

from django.core.cache import cache
from django.utils import timezone

from .outcomes import (
    evaluate_due_predictions,
    performance_summary,
    prune_prediction_history,
    save_prediction,
)
from .persistence import export_basecalc_history
from .intermarket_technicals import get_intermarket_technical_snapshot
from .market_shock import build_market_shock_context
from .services.decision_context import (
    build_basecalc_decision_context,
    build_basecalc_top_context,
    enrich_basecalc_context,
)
from .output_contract import apply_output_contract
from .signal_contract import build_basecalc_signal_contract
from .snapshot import write_basecalc_snapshot
from .status import (
    intermarket_status_entry,
    price_status_entry,
    status_display_rows,
    write_basecalc_status,
)
from .views import (
    CACHE_KEY_PRICE,
    _attach_practical_lines_from_latest_snapshot,
    get_cached_futures_snapshot,
    hydrate_saved_snapshot_context,
    normalize_price,
    price_from_futures_snapshot,
)
from .world_model import build_world_model


CACHE_KEY_REFRESH_LOCK = "basecalc_refresh_lock"
REFRESH_LOCK_TTL_SEC = 300


def refresh_basecalc_data(
    save=True,
    use_lock=True,
    export_history=False,
    export_path="basecalc/data/basecalc_history.json",
    export_snapshot_path="basecalc/data/latest_snapshot.json",
):
    if use_lock and not cache.add(CACHE_KEY_REFRESH_LOCK, "1", REFRESH_LOCK_TTL_SEC):
        return {
            "updated": False,
            "skipped_reason": "locked",
        }
    started = time.monotonic()
    try:
        futures_snapshot = get_cached_futures_snapshot()
        price = normalize_price(cache.get(CACHE_KEY_PRICE))
        if price is None:
            price = price_from_futures_snapshot(futures_snapshot)
        futures_snapshot = get_cached_futures_snapshot()
        snapshot_price = price_from_futures_snapshot(futures_snapshot)
        if snapshot_price is not None:
            price = snapshot_price
            cache.set(CACHE_KEY_PRICE, price, timeout=None)
        intermarket_context = get_intermarket_technical_snapshot()
        world_model = build_world_model(price or 0, futures_snapshot, intermarket_context)
        basecalc_status = {
            "price_data": price_status_entry(
                futures_snapshot,
                world_model.get("readiness_level"),
            ),
            "intermarket": intermarket_status_entry(intermarket_context),
        }
        write_basecalc_status(basecalc_status)
        prediction = save_prediction(world_model) if save else None
        outcomes_created = evaluate_due_predictions()
        pruned_predictions = prune_prediction_history()
        exported = False
        if export_history:
            export_basecalc_history(export_path)
            exported = True
        export_basecalc_snapshot(
            world_model=world_model,
            basecalc_status=basecalc_status,
            futures_snapshot=futures_snapshot,
            intermarket_context=intermarket_context,
            export_snapshot_path=export_snapshot_path,
            job_duration_sec=round(time.monotonic() - started, 3),
        )
        return {
            "updated": True,
            "price": world_model.get("price"),
            "state_key": world_model.get("state_key"),
            "direction": world_model.get("direction"),
            "confidence": world_model.get("confidence"),
            "prediction_saved": prediction is not None,
            "outcomes_created": outcomes_created,
            "pruned_predictions": pruned_predictions,
            "market_bars_saved": (futures_snapshot or {}).get("_market_bars_saved", 0)
            if isinstance(futures_snapshot, dict)
            else 0,
            "exported": exported,
            "export_path": export_path if exported else None,
            "snapshot_exported": True,
            "snapshot_path": export_snapshot_path,
            "data_quality_score": world_model.get("data_quality_score"),
            "readiness_level": world_model.get("readiness_level"),
            "source_status": world_model.get("source_status") or {},
        }
    finally:
        if use_lock:
            cache.delete(CACHE_KEY_REFRESH_LOCK)


def export_basecalc_snapshot(
    *,
    world_model,
    basecalc_status,
    futures_snapshot=None,
    intermarket_context=None,
    export_snapshot_path,
    job_duration_sec,
):
    price = world_model.get("price") or 0
    market_shock_context = _safe_market_shock_context(
        futures_snapshot,
        intermarket_context,
    )
    basecalc_status_rows = status_display_rows(basecalc_status, world_model)
    backtest_performance_by_horizon = {
        horizon: performance_summary(horizon, is_backtest=True)
        for horizon in ("1d", "3d", "5d")
    }
    apply_output_contract(
        world_model,
        display_price=price,
        performance_by_horizon=backtest_performance_by_horizon,
    )
    _attach_practical_lines_from_latest_snapshot(
        world_model,
        futures_snapshot,
        price_from_futures_snapshot(futures_snapshot) or price,
    )
    world_model["basecalc_signal"] = build_basecalc_signal_contract(world_model)
    decision = build_basecalc_decision_context(
        world_model,
        market_shock_context,
        basecalc_status_rows,
        backtest_performance_by_horizon.get("1d"),
    )
    basecalc_top = build_basecalc_top_context(
        world_model,
        decision,
        basecalc_status_rows,
        backtest_performance_by_horizon.get("1d"),
    )
    payload = {
        "generated_at": timezone.localtime().isoformat(),
        "source": "github_actions",
        "stale": False,
        "data_quality": world_model.get("data_quality_score", 0),
        "model_version": world_model.get("model_version", ""),
        "job_duration_sec": job_duration_sec,
        "warnings": [],
        "data": {
            "price_display": f"{price:,.0f}" if price else "N/A",
            "world_model": world_model,
        },
        "decision": decision,
        "basecalc_top": basecalc_top,
        "world_model": world_model,
        "market_shock": market_shock_context,
        "intermarket_technicals": world_model.get("intermarket_technicals") or {},
        "basecalc_status": basecalc_status,
        "basecalc_status_rows": basecalc_status_rows,
        "performance": performance_summary("1d"),
        "performance_by_horizon": {
            horizon: performance_summary(horizon) for horizon in ("1d", "3d", "5d")
        },
        "backtest_performance_by_horizon": backtest_performance_by_horizon,
        "detail_mode": False,
        "updated": False,
        "price_param": f"{price:.0f}" if price else "",
    }
    hydrate_saved_snapshot_context(payload)
    enrich_basecalc_context(payload)
    write_basecalc_snapshot(payload, export_snapshot_path)
    return payload


def _safe_market_shock_context(futures_snapshot=None, intermarket_context=None):
    try:
        return build_market_shock_context(
            base_snapshot=futures_snapshot,
            intermarket_context=intermarket_context,
        )
    except Exception:
        return {
            "has_data": False,
            "summary": "市場ショック判定データなし",
            "tone": "unknown",
            "rows": [],
        }
