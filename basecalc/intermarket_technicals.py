from django.utils import timezone

from .indicators import calculate_atr


US_INDEX_SYMBOLS = {
    "nasdaq100_futures": "NQ=F",
    "sp500_futures": "ES=F",
    "dow_futures": "YM=F",
}

US_INDEX_WEIGHTS = {
    "nasdaq100_futures": 0.45,
    "sp500_futures": 0.35,
    "dow_futures": 0.20,
}


def evaluate_intermarket_readiness(us_context: dict) -> dict:
    assets = (us_context or {}).get("assets")
    if assets is None:
        assets = us_context or {}
    if not isinstance(assets, dict):
        assets = {}

    missing = [key for key in US_INDEX_SYMBOLS if key not in assets]
    if len(missing) == len(US_INDEX_SYMBOLS):
        return {
            "level": "blocked",
            "usable": False,
            "reason": "米国3指数データなし",
            "missing": missing,
        }
    if missing:
        return {
            "level": "limited",
            "usable": True,
            "reason": f"一部欠損: {missing}",
            "missing": missing,
        }
    return {
        "level": "ready",
        "usable": True,
        "reason": "米国3指数確認可",
        "missing": [],
    }


def build_us_index_technical_context(assets: dict) -> dict:
    if isinstance(assets, dict) and "confirmation_score" in assets:
        return _normalize_existing_context(assets)

    assets = (assets or {}).get("assets") if isinstance(assets, dict) and "assets" in assets else assets
    assets = assets if isinstance(assets, dict) else {}
    readiness = evaluate_intermarket_readiness({"assets": assets})
    components = {}
    weighted_score = 0.0
    weight_used = 0.0

    for key, symbol in US_INDEX_SYMBOLS.items():
        asset = assets.get(key)
        if not isinstance(asset, dict):
            continue
        component = _asset_component(key, symbol, asset)
        components[key] = component
        weight = US_INDEX_WEIGHTS[key]
        weighted_score += component["score"] * weight
        weight_used += weight

    confirmation_score = round(weighted_score / weight_used) if weight_used else 0
    confirmation_score = int(_clamp(confirmation_score, -100, 100))
    confirmation_label = _confirmation_label(confirmation_score, components)
    return {
        "confirmation_score": confirmation_score,
        "confirmation_label": confirmation_label,
        "risk_label": "technical_confirm",
        "components": components,
        "direction_agreement": _direction_agreement(components),
        "evidence": _evidence(confirmation_label, components, readiness),
        "readiness": readiness,
        "fetched_at": timezone.now(),
    }


def get_intermarket_technical_snapshot() -> dict:
    from .market_context import (
        _fetch_context_symbol,
        _price_action_fallback_assets,
        fetch_intraday_context,
    )

    assets = {}
    for key, symbol in US_INDEX_SYMBOLS.items():
        snapshot = fetch_intraday_context(symbol)
        if snapshot is None:
            snapshot = _fetch_context_symbol(symbol)
        if snapshot:
            assets[key] = snapshot
    fallback_assets = _price_action_fallback_assets()
    for key in US_INDEX_SYMBOLS:
        if key not in assets and key in fallback_assets:
            assets[key] = fallback_assets[key]
    return build_us_index_technical_context(assets)


def _normalize_existing_context(context):
    components = {
        key: value
        for key, value in (context.get("components") or {}).items()
        if key in US_INDEX_SYMBOLS and isinstance(value, dict)
    }
    readiness = context.get("readiness") or evaluate_intermarket_readiness(
        {"assets": components}
    )
    return {
        "confirmation_score": int(_clamp(_to_float(context.get("confirmation_score")) or 0, -100, 100)),
        "confirmation_label": context.get("confirmation_label") or "mixed",
        "risk_label": "technical_confirm",
        "components": components,
        "direction_agreement": context.get("direction_agreement") or _direction_agreement(components),
        "evidence": list(context.get("evidence") or []),
        "readiness": readiness,
        "fetched_at": context.get("fetched_at") or timezone.now(),
    }


def _asset_component(key, symbol, asset):
    price = _to_float(asset.get("price") or asset.get("close"))
    previous_close = _to_float(asset.get("previous_close"))
    closes = _numbers(asset.get("closes"))
    highs = _numbers(asset.get("highs"))
    lows = _numbers(asset.get("lows"))
    if price is None and closes:
        price = closes[-1]
    if previous_close is None and len(closes) >= 2:
        previous_close = closes[-2]

    change_pct = _to_float(asset.get("change_pct"))
    if change_pct is None:
        change_pct = _pct(price, previous_close)

    momentum_5d = _momentum(closes, price, 5)
    momentum_20d = _momentum(closes, price, 20)
    close_position = _close_position(price, highs, lows)
    atr_ratio = _atr_ratio(highs, lows, closes)
    high_breakout = price is not None and highs and price >= max(highs[-20:] or highs) * 0.995
    low_breakdown = price is not None and lows and price <= min(lows[-20:] or lows) * 1.005

    score = 0
    score += _clamp((change_pct or 0) * 12, -20, 20)
    score += _clamp((momentum_5d or 0) * 4, -18, 18)
    score += _clamp((momentum_20d or 0) * 2, -16, 16)
    if close_position is not None:
        if close_position >= 0.65:
            score += 12
        elif close_position <= 0.35:
            score -= 12
    if atr_ratio is not None and atr_ratio >= 1.25:
        score += 6 if (change_pct or 0) >= 0 else -6
    if high_breakout:
        score += 14
    if low_breakdown:
        score -= 14
    score = int(round(_clamp(score, -100, 100)))

    return {
        "symbol": asset.get("symbol") or symbol,
        "price": price,
        "change_pct": round(change_pct, 4) if change_pct is not None else None,
        "close_position": round(close_position, 4) if close_position is not None else None,
        "momentum_5d_pct": round(momentum_5d, 4) if momentum_5d is not None else None,
        "momentum_20d_pct": round(momentum_20d, 4) if momentum_20d is not None else None,
        "atr_ratio": round(atr_ratio, 4) if atr_ratio is not None else None,
        "high_breakout": bool(high_breakout),
        "low_breakdown": bool(low_breakdown),
        "direction": "up" if score >= 15 else "down" if score <= -15 else "flat",
        "score": score,
    }


def _confirmation_label(score, components):
    directions = {item.get("direction") for item in components.values()}
    if "up" in directions and "down" in directions:
        return "divergent"
    if score >= 25:
        return "confirm_up"
    if score <= -25:
        return "confirm_down"
    return "mixed"


def _direction_agreement(components):
    if not components:
        return 0.0
    directions = [item.get("direction") for item in components.values()]
    up = directions.count("up")
    down = directions.count("down")
    return round(max(up, down) / len(directions), 3)


def _evidence(label, components, readiness):
    if not readiness.get("usable"):
        return [readiness.get("reason") or "米国3指数確認なし"]
    names = {
        "nasdaq100_futures": "NASDAQ100",
        "sp500_futures": "S&P500",
        "dow_futures": "NYダウ",
    }
    up = [names[key] for key, item in components.items() if item.get("direction") == "up"]
    down = [names[key] for key, item in components.items() if item.get("direction") == "down"]
    if label == "confirm_up":
        return ["米国3指数の価格テクニカルは上昇確認"] + _asset_evidence(up, "上向き")
    if label == "confirm_down":
        return ["米国3指数の価格テクニカルは下落確認"] + _asset_evidence(down, "下向き")
    if label == "divergent":
        return ["米国3指数の方向が分裂"] + _asset_evidence(up, "上向き") + _asset_evidence(down, "下向き")
    return ["米国3指数確認はまちまち"]


def _asset_evidence(items, suffix):
    if not items:
        return []
    return [f"{'、'.join(items)}が{suffix}"]


def _momentum(closes, price, periods):
    if len(closes) <= periods or price in (None, 0):
        return None
    return _pct(price, closes[-periods - 1])


def _close_position(price, highs, lows):
    if price is None or not highs or not lows:
        return None
    high = max(highs[-20:] or highs)
    low = min(lows[-20:] or lows)
    if high <= low:
        return None
    return (price - low) / (high - low)


def _atr_ratio(highs, lows, closes):
    if len(highs) < 5 or len(lows) < 5 or len(closes) < 5:
        return None
    atr = [value for value in calculate_atr(highs, lows, closes, 3) if value]
    if len(atr) < 3:
        return None
    window = atr[-10:] or atr
    average = sum(window) / len(window)
    return atr[-1] / average if average else None


def _pct(current, previous):
    current = _to_float(current)
    previous = _to_float(previous)
    if current is None or previous in (None, 0):
        return None
    return ((current - previous) / previous) * 100


def _numbers(values):
    return [float(value) for value in values or [] if isinstance(value, (int, float))]


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value, low, high):
    return max(low, min(high, value))
