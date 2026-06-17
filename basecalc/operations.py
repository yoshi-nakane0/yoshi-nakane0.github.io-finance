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
from .market_shock import build_market_shock_context
from .market_context import get_market_context_snapshot
from .services.decision_context import build_basecalc_decision_context
from .snapshot import write_basecalc_snapshot
from .status import (
    external_market_status_entry,
    jgb_status_entry,
    per_status_entry,
    price_status_entry,
    status_display_rows,
    write_basecalc_status,
)
from .views import (
    CACHE_KEY_DIVIDEND_INDEX,
    CACHE_KEY_FWD,
    CACHE_KEY_JGB,
    CACHE_KEY_PRICE,
    get_cached_futures_snapshot,
    get_jgb10y_yield_for_update,
    get_nikkei_per_values_for_update,
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
        forward_per = cache.get(CACHE_KEY_FWD)
        jgb10y_yield_percent = cache.get(CACHE_KEY_JGB)
        dividend_yield_index_percent = cache.get(CACHE_KEY_DIVIDEND_INDEX)
        futures_snapshot = get_cached_futures_snapshot()
        price = normalize_price(cache.get(CACHE_KEY_PRICE))
        if price is None:
            price = price_from_futures_snapshot(futures_snapshot)
        per_values = get_nikkei_per_values_for_update()
        if per_values:
            if per_values.get("index_based"):
                forward_per = per_values["index_based"]
                cache.set(CACHE_KEY_FWD, forward_per, timeout=None)
            if per_values.get("dividend_yield_index_based") is not None:
                dividend_yield_index_percent = per_values["dividend_yield_index_based"]
                cache.set(
                    CACHE_KEY_DIVIDEND_INDEX,
                    dividend_yield_index_percent,
                    timeout=None,
                )
        refreshed_jgb = get_jgb10y_yield_for_update()
        if refreshed_jgb is not None:
            jgb10y_yield_percent = refreshed_jgb
            cache.set(CACHE_KEY_JGB, jgb10y_yield_percent, timeout=None)
        futures_snapshot = get_cached_futures_snapshot()
        snapshot_price = price_from_futures_snapshot(futures_snapshot)
        if snapshot_price is not None:
            price = snapshot_price
            cache.set(CACHE_KEY_PRICE, price, timeout=None)
        market_context = get_market_context_snapshot()
        world_model = build_world_model(price or 0, futures_snapshot, market_context)
        basecalc_status = {
            "price_data": price_status_entry(
                futures_snapshot,
                world_model.get("readiness_level"),
            ),
            "per": per_status_entry(
                {
                    "index_based": forward_per,
                    "dividend_yield_index_based": dividend_yield_index_percent,
                },
                success=forward_per is not None
                or dividend_yield_index_percent is not None,
            ),
            "jgb": jgb_status_entry(
                jgb10y_yield_percent,
                success=jgb10y_yield_percent is not None,
            ),
            "external_market": external_market_status_entry(market_context),
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
    export_snapshot_path,
    job_duration_sec,
):
    price = world_model.get("price") or 0
    market_shock_context = build_market_shock_context()
    basecalc_status_rows = status_display_rows(basecalc_status, world_model)
    backtest_performance_by_horizon = {
        horizon: performance_summary(horizon, is_backtest=True)
        for horizon in ("1d", "3d", "5d")
    }
    decision = build_basecalc_decision_context(
        world_model,
        market_shock_context,
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
        "world_model": world_model,
        "market_shock": market_shock_context,
        "market_context": world_model.get("market_context") or {},
        "basecalc_status": basecalc_status,
        "basecalc_status_rows": basecalc_status_rows,
        "performance": performance_summary("1d"),
        "performance_by_horizon": {
            horizon: performance_summary(horizon) for horizon in ("1d", "3d", "5d")
        },
        "backtest_performance_by_horizon": backtest_performance_by_horizon,
        "detail_mode": False,
        "updated": False,
        "erp_method": "fixed",
        "erp_growth_input": "",
        "price_param": f"{price:.0f}" if price else "",
        "growth_core_ratio_input": "0.6",
        "growth_wide_ratio_input": "0.7",
    }
    write_basecalc_snapshot(payload, export_snapshot_path)
    return payload
