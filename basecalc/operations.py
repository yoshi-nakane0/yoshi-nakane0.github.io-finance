from django.core.cache import cache

from .outcomes import (
    evaluate_due_predictions,
    prune_prediction_history,
    save_prediction,
)
from .views import (
    CACHE_KEY_DIVIDEND_INDEX,
    CACHE_KEY_FWD,
    CACHE_KEY_JGB,
    CACHE_KEY_PRICE,
    get_cached_futures_snapshot,
    normalize_price,
    price_from_futures_snapshot,
    update_market_caches,
)
from .world_model import build_world_model


CACHE_KEY_REFRESH_LOCK = "basecalc_refresh_lock"
REFRESH_LOCK_TTL_SEC = 300


def refresh_basecalc_data(save=True, use_lock=True):
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
        (
            forward_per,
            jgb10y_yield_percent,
            dividend_yield_index_percent,
            futures_snapshot,
            price,
        ) = update_market_caches(
            forward_per,
            jgb10y_yield_percent,
            dividend_yield_index_percent,
            price,
            update_price_from_futures=True,
        )
        world_model = build_world_model(price or 0, futures_snapshot)
        outcomes_created = evaluate_due_predictions()
        prediction = save_prediction(world_model) if save else None
        pruned_predictions = prune_prediction_history()
        return {
            "updated": True,
            "price": world_model.get("price"),
            "state_key": world_model.get("state_key"),
            "direction": world_model.get("direction"),
            "confidence": world_model.get("confidence"),
            "prediction_saved": prediction is not None,
            "outcomes_created": outcomes_created,
            "pruned_predictions": pruned_predictions,
        }
    finally:
        if use_lock:
            cache.delete(CACHE_KEY_REFRESH_LOCK)
