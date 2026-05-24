from .instrument import normalize_instrument


def normalize_chart_payload(payload, symbol, timeframe="1d", interval="1d"):
    result = (payload.get("chart", {}).get("result") or [None])[0]
    if not isinstance(result, dict):
        return None

    meta = result.get("meta") or {}
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    timestamps = result.get("timestamp") or []
    opens = _clean_numbers(quote.get("open"))
    highs = _clean_numbers(quote.get("high"))
    lows = _clean_numbers(quote.get("low"))
    closes = _clean_numbers(quote.get("close"))
    volumes = _clean_numbers(quote.get("volume"), allow_zero=True)

    price = _to_float(meta.get("regularMarketPrice"))
    if price is None and closes:
        price = closes[-1]
    if price is None:
        return None

    previous_close = _to_float(meta.get("chartPreviousClose"))
    if previous_close is None:
        previous_close = _to_float(meta.get("regularMarketPreviousClose"))
    if previous_close is None and len(closes) >= 2:
        previous_close = closes[-2]

    changes = [
        abs(_pct_change(current, previous))
        for previous, current in zip(closes, closes[1:])
    ]
    changes = [change for change in changes if change is not None]

    instrument = normalize_instrument(symbol, "yahoo")
    return {
        "symbol": symbol,
        "name": meta.get("shortName") or meta.get("symbol") or symbol,
        "source": "yahoo",
        "instrument_key": instrument["instrument_key"],
        "instrument_type": instrument["instrument_type"],
        "timeframe": timeframe,
        "interval": interval,
        "price": round(price, 0),
        "previous_close": previous_close,
        "change_pct": _pct_change(price, previous_close),
        "opens": opens,
        "highs": highs,
        "lows": lows,
        "closes": closes,
        "volumes": volumes,
        "timestamps": timestamps,
        "recent_high": max(highs[-10:] or closes[-10:] or [price]),
        "recent_low": min(lows[-10:] or closes[-10:] or [price]),
        "avg_abs_move_pct": (
            sum(changes[-5:]) / len(changes[-5:]) if changes[-5:] else None
        ),
    }


def snapshot_from_quote_row(row, symbol, today):
    close = _to_float(row.get("Close"))
    open_price = _to_float(row.get("Open"))
    high = _to_float(row.get("High"))
    low = _to_float(row.get("Low"))
    volume = _to_float(row.get("Volume"))
    if close is None:
        return None
    instrument = normalize_instrument(symbol, "stooq")
    snapshot = {
        "symbol": instrument["symbol"] or row.get("Symbol") or symbol.upper(),
        "name": "Nikkei quote fallback",
        "source": "stooq",
        "instrument_key": instrument["instrument_key"],
        "instrument_type": instrument["instrument_type"],
        "price": round(close, 0),
        "previous_close": open_price,
        "change_pct": _pct_change(close, open_price),
        "opens": [value for value in (open_price,) if value is not None],
        "highs": [value for value in (high,) if value is not None],
        "lows": [value for value in (low,) if value is not None],
        "closes": [value for value in (open_price, close) if value is not None],
        "volumes": [value for value in (volume,) if value is not None],
        "timestamps": [],
        "recent_high": high or close,
        "recent_low": low or close,
        "avg_abs_move_pct": _pct_change(high, low) if high and low else None,
        "is_stale": row.get("Date") != today,
    }
    return snapshot


def _to_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct_change(current, previous):
    current = _to_float(current)
    previous = _to_float(previous)
    if current is None or previous in (None, 0):
        return None
    return ((current - previous) / previous) * 100.0


def _clean_numbers(values, allow_zero=False):
    cleaned = []
    for value in values or []:
        if not isinstance(value, (int, float)):
            continue
        if value > 0 or (allow_zero and value == 0):
            cleaned.append(float(value))
    return cleaned
