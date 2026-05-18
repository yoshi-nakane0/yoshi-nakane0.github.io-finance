def serialize_snapshot(world_model):
    return {
        "price": world_model.get("price"),
        "direction": world_model.get("direction"),
        "sentiment_label": world_model.get("sentiment_label"),
        "sentiment_score": world_model.get("sentiment_score"),
        "state_label": world_model.get("state_label"),
        "continuation_score": world_model.get("continuation_score"),
        "shock_score": world_model.get("shock_score"),
        "confidence": world_model.get("confidence"),
        "targets": {
            "upside": world_model.get("upside_targets") or [],
            "downside": world_model.get("downside_targets") or [],
        },
        "invalidation_price": world_model.get("invalidation_price"),
        "evidence": world_model.get("evidence") or [],
        "last_updated": world_model.get("last_updated_display"),
        "data_warning": world_model.get("data_warning") or "",
    }
