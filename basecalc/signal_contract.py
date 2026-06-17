INCLUDED_INPUTS = [
    "nikkei_futures_ohlcv",
    "nikkei_futures_indicators",
    "nasdaq100_futures_price_action",
    "sp500_futures_price_action",
    "dow_futures_price_action",
]

EXCLUDED_INPUTS = [
    "macro",
    "valuation",
    "fx",
    "rates",
    "vix",
    "sox",
    "oil",
    "news",
]


def build_basecalc_signal_contract(outlook: dict) -> dict:
    outlook = outlook or {}
    return {
        "source": "basecalc",
        "scope": "technical_with_us_index_confirmation",
        "instrument": "nikkei_futures",
        "as_of": outlook.get("as_of") or outlook.get("last_updated_display") or "",
        "price": outlook.get("price"),
        "readiness_level": outlook.get("readiness_level"),
        "directional_allowed": outlook.get("directional_allowed"),
        "primary_direction": outlook.get("direction"),
        "primary_setup": outlook.get("primary_setup"),
        "technical_regime": outlook.get("technical_regime"),
        "nikkei_technical_score": outlook.get("nikkei_technical_score"),
        "us_index_confirmation_score": outlook.get("us_index_confirmation_score"),
        "confidence_score": outlook.get("confidence_score"),
        "confidence_label": outlook.get("confidence"),
        "horizons": outlook.get("horizons") or {},
        "scenarios": outlook.get("scenarios") or {},
        "levels": {
            "resistance_zones": outlook.get("upside_targets") or [],
            "support_zones": outlook.get("downside_targets") or [],
            "invalidation": outlook.get("invalidation") or {},
        },
        "included_inputs": INCLUDED_INPUTS,
        "excluded_inputs": EXCLUDED_INPUTS,
    }
