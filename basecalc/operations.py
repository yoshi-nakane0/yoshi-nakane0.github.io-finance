from django.core.cache import cache

from .outcomes import (
    evaluate_due_predictions,
    prune_prediction_history,
    save_prediction,
)
from .persistence import export_basecalc_history
from .market_context import get_market_context_snapshot
from .status import (
    external_market_status_entry,
    jgb_status_entry,
    per_status_entry,
    price_status_entry,
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
):
    if use_lock and not cache.add(CACHE_KEY_REFRESH_LOCK, "1", REFRESH_LOCK_TTL_SEC):
        return {
            "updated": False,
            "skipped_reason": "locked",
        }
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
        write_basecalc_status(
            {
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
        )
        prediction = save_prediction(world_model) if save else None
        outcomes_created = evaluate_due_predictions()
        pruned_predictions = prune_prediction_history()
        exported = False
        if export_history:
            export_basecalc_history(export_path)
            exported = True
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
            "data_quality_score": world_model.get("data_quality_score"),
            "readiness_level": world_model.get("readiness_level"),
            "source_status": world_model.get("source_status") or {},
        }
    finally:
        if use_lock:
            cache.delete(CACHE_KEY_REFRESH_LOCK)
