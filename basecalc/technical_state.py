TECHNICAL_STATE_FIELDS = (
    "price",
    "ema5",
    "ema20",
    "ema60",
    "ema200",
    "vwap",
    "rsi14",
    "macd",
    "macd_signal",
    "macd_histogram",
    "adx14",
    "atr14",
    "atr_ratio",
    "bb_upper",
    "bb_mid",
    "bb_lower",
    "daily_change_pct",
    "change_5d_pct",
    "change_20d_pct",
    "previous_high",
    "previous_low",
    "high_5d",
    "low_5d",
    "high_20d",
    "low_20d",
)


def build_technical_state(features: dict) -> dict:
    features = features or {}
    return {
        key: features.get(key)
        for key in TECHNICAL_STATE_FIELDS
        if key in features
    }
