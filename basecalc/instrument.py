CANONICAL_INSTRUMENT_KEY = "cme_nikkei_futures"


INSTRUMENT_DEFINITIONS = {
    "NIY=F": {
        "instrument_key": "cme_nikkei_futures",
        "instrument_type": "futures",
        "label": "CME日経先物",
        "is_canonical": True,
        "directional_allowed_by_default": True,
    },
    "NK.F": {
        "instrument_key": "stooq_nikkei_futures",
        "instrument_type": "futures_proxy",
        "label": "代替先物",
        "is_canonical": False,
        "directional_allowed_by_default": False,
    },
    "^NKX": {
        "instrument_key": "nikkei_index_fallback",
        "instrument_type": "index_fallback",
        "label": "指数代替",
        "is_canonical": False,
        "directional_allowed_by_default": False,
    },
}


def normalize_instrument(symbol=None, source=None) -> dict:
    normalized_symbol = normalize_symbol(symbol)
    normalized_source = (source or "").strip().lower() or "unknown"
    definition = INSTRUMENT_DEFINITIONS.get(normalized_symbol)
    if definition is None:
        return {
            "symbol": normalized_symbol or "",
            "source": normalized_source,
            "instrument_key": "unknown",
            "instrument_type": "unknown",
            "label": "不明",
            "is_canonical": False,
            "directional_allowed_by_default": False,
        }
    return {
        "symbol": normalized_symbol,
        "source": normalized_source,
        **definition,
    }


def normalize_symbol(symbol=None) -> str:
    value = (symbol or "").strip()
    upper = value.upper()
    if upper in INSTRUMENT_DEFINITIONS:
        return upper
    if value.lower() == "nk.f":
        return "NK.F"
    if value.lower() == "^nkx":
        return "^NKX"
    if value.lower() == "niy=f":
        return "NIY=F"
    return upper
