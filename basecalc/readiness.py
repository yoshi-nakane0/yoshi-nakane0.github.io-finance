from datetime import datetime, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.utils import timezone

from .instrument import normalize_instrument

JST = ZoneInfo("Asia/Tokyo")

MIN_INDICATOR_BARS = {
    "ema5": 10,
    "ema20": 25,
    "ema60": 65,
    "ema200": 210,
    "rsi14": 30,
    "macd": 35,
    "atr14": 30,
    "adx14": 30,
    "bollinger": 25,
}

CANONICAL_FUTURES_SOURCES = {"225navi", "cme_daily_bulletin", "yahoo"}


def evaluate_world_model_readiness(
    *,
    price=None,
    snapshot=None,
    data_quality=None,
    daily_ohlcv=None,
) -> dict:
    snapshot = snapshot if isinstance(snapshot, dict) else None
    quality = data_quality if isinstance(data_quality, dict) else {}
    source = quality.get("source") or (snapshot or {}).get("source")
    symbol = quality.get("symbol") or (snapshot or {}).get("symbol")
    instrument = normalize_instrument(symbol, source)
    if quality.get("instrument_type") and instrument["instrument_type"] == "unknown":
        instrument["instrument_type"] = quality["instrument_type"]

    bar_counts = _bar_counts(snapshot, daily_ohlcv)
    indicator_validity = evaluate_indicator_validity(daily_ohlcv or {}, snapshot or {})
    warnings = list(quality.get("warnings") or [])
    reason_codes = []

    price_value = _to_float(price)
    quality_score = int(quality.get("score") or 0)
    quality_level = quality.get("level") or "bad"
    source_name = instrument.get("source") or "unknown"
    symbol_name = instrument.get("symbol") or ""
    fallback_used = bool(quality.get("fallback_used") or (snapshot or {}).get("fallback_used"))
    stale = bool(quality.get("is_stale") or (snapshot or {}).get("is_stale"))
    if stale and _market_closed_allows_snapshot((snapshot or {}).get("fetched_at")):
        stale = False
        if source_name == "yahoo" and symbol_name == "NIY=F":
            quality_score = max(quality_score, 80)
            quality_level = "good"

    if price_value is None or price_value <= 0:
        reason_codes.append("invalid_price")
    if snapshot is None:
        reason_codes.append("missing_snapshot")
    if source_name == "unknown":
        reason_codes.append("unknown_source")
    if quality_score < 50:
        reason_codes.append("quality_below_50")
    if instrument.get("instrument_type") == "index_fallback" or symbol_name == "^NKX":
        reason_codes.append("index_fallback")
    if bar_counts["1d"] < 35:
        reason_codes.append("daily_bars_below_35")
    if stale:
        reason_codes.append("stale_snapshot")
    if "価格が不正です" in warnings or "前日終値が不正です" in warnings or "取得時刻が未来です" in warnings:
        reason_codes.append("snapshot_anomaly")

    level = "blocked" if reason_codes else "ready"
    if level == "ready":
        ready_requirements = [
            source_name in CANONICAL_FUTURES_SOURCES,
            symbol_name == "NIY=F",
            quality_score >= 80,
            quality_level == "good",
            not fallback_used,
            not stale,
            instrument.get("instrument_type") == "futures",
            bar_counts["1d"] >= 60,
            indicator_validity.get("ema20"),
            indicator_validity.get("ema60"),
            indicator_validity.get("rsi14"),
            indicator_validity.get("atr14"),
        ]
        if not all(ready_requirements):
            level = "limited"
            reason_codes.extend(
                _limited_reason_codes(
                    quality_score,
                    fallback_used,
                    source_name,
                    symbol_name,
                    bar_counts,
                    indicator_validity,
                )
            )

    if level == "limited" and quality_score < 50:
        level = "blocked"
        reason_codes.append("quality_below_50")

    reason_codes = _dedupe(reason_codes)
    return {
        "level": level,
        "directional_allowed": level == "ready" and bool(instrument.get("directional_allowed_by_default")),
        "score": quality_score,
        "reason_codes": reason_codes,
        "warnings": _dedupe(warnings + _reason_texts(reason_codes)),
        "instrument_key": instrument.get("instrument_key") or "unknown",
        "instrument_type": instrument.get("instrument_type") or "unknown",
        "instrument_label": instrument.get("label") or "不明",
        "source": source_name,
        "symbol": symbol_name,
        "bar_counts": bar_counts,
        "indicator_validity": indicator_validity,
    }


def evaluate_indicator_validity(daily_ohlcv: dict, snapshot=None) -> dict:
    closes = _clean_positive(daily_ohlcv.get("closes"))
    highs = _clean_positive(daily_ohlcv.get("highs"))
    lows = _clean_positive(daily_ohlcv.get("lows"))
    opens = _clean_positive(daily_ohlcv.get("opens"))
    volumes = _clean_numbers(daily_ohlcv.get("volumes"))
    real_counts = daily_ohlcv.get("real_counts") or {}
    close_count = _count(real_counts, "closes", len(closes))
    high_count = _count(real_counts, "highs", len(highs))
    low_count = _count(real_counts, "lows", len(lows))
    open_count = _count(real_counts, "opens", len(opens))
    volume_count = _count(real_counts, "volumes", len(volumes))
    has_hlc = min(high_count, low_count, close_count)
    all_synthetic_volume = bool(volumes) and all(value in (0, 1) for value in volumes)
    previous_close = _to_float((snapshot or {}).get("previous_close"))
    return {
        "ema5": close_count >= MIN_INDICATOR_BARS["ema5"],
        "ema20": close_count >= MIN_INDICATOR_BARS["ema20"],
        "ema60": close_count >= MIN_INDICATOR_BARS["ema60"],
        "ema200": close_count >= MIN_INDICATOR_BARS["ema200"],
        "rsi14": close_count >= MIN_INDICATOR_BARS["rsi14"],
        "macd": close_count >= MIN_INDICATOR_BARS["macd"],
        "atr14": has_hlc >= MIN_INDICATOR_BARS["atr14"],
        "adx14": has_hlc >= MIN_INDICATOR_BARS["adx14"],
        "bollinger": close_count >= MIN_INDICATOR_BARS["bollinger"],
        "pivot": has_hlc >= 2,
        "vwap": has_hlc >= 1 and volume_count >= close_count and not all_synthetic_volume,
        "gap": open_count >= 1 and previous_close is not None,
    }


def _bar_counts(snapshot, daily_ohlcv):
    counts = {
        "1d": len(_clean_positive((daily_ohlcv or {}).get("closes"))),
    }
    if isinstance(snapshot, dict):
        for key, frame in (snapshot.get("timeframes") or {}).items():
            if key in counts and isinstance(frame, dict):
                counts[key] = len(_clean_positive(frame.get("closes")))
    return counts


def _count(real_counts, key, fallback):
    if key in real_counts:
        return int(real_counts.get(key) or 0)
    return int(fallback or 0)


def _limited_reason_codes(quality_score, fallback_used, source, symbol, bar_counts, indicator_validity):
    codes = []
    if 50 <= quality_score <= 79:
        codes.append("quality_50_79")
    if fallback_used:
        codes.append("fallback_used")
    if source not in CANONICAL_FUTURES_SOURCES and source != "unknown":
        codes.append("non_canonical_source")
    if source == "stooq" or symbol == "NK.F":
        codes.append("futures_proxy")
    if 35 <= bar_counts["1d"] < 60:
        codes.append("daily_bars_35_59")
    if not all(indicator_validity.get(key) for key in ("ema20", "ema60", "rsi14", "atr14")):
        codes.append("major_indicator_missing")
    return codes or ["not_all_ready_conditions_met"]


def _reason_texts(reason_codes):
    labels = {
        "invalid_price": "価格が不正です",
        "missing_snapshot": "価格データがありません",
        "unknown_source": "取得元が不明です",
        "quality_below_50": "データ品質が不足しています",
        "index_fallback": "指数代替データのため方向判定を停止しています",
        "daily_bars_below_35": "日足本数が35本未満です",
        "daily_bars_35_59": "日足本数が60本未満です",
        "stale_snapshot": "価格データが古い可能性があります",
        "snapshot_anomaly": "価格データに異常があります",
        "fallback_used": "フォールバックデータを使用しています",
        "futures_proxy": "代替先物データのため参考表示です",
        "non_canonical_source": "公式CMEまたは標準先物データではないため参考表示です",
        "major_indicator_missing": "主要指標の一部が不足しています",
        "quality_50_79": "データ品質が限定的です",
        "not_all_ready_conditions_met": "判定条件をすべて満たしていません",
    }
    return [labels[code] for code in reason_codes if code in labels]


def _market_closed_allows_snapshot(fetched_at) -> bool:
    timestamp = _parse_timestamp(fetched_at)
    if timestamp is None:
        return False
    now = timezone.localtime(timezone.now(), JST)
    fetched_local = timezone.localtime(timestamp, JST)
    if (now - fetched_local).total_seconds() > 72 * 3600:
        return False
    weekday = now.weekday()
    return weekday == 5 or weekday == 6 or (weekday == 0 and now.hour < 8)


def _parse_timestamp(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        value = datetime.fromtimestamp(value, tz=dt_timezone.utc)
    elif isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone=dt_timezone.utc)
    return value


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_positive(values):
    return [float(value) for value in values or [] if isinstance(value, (int, float)) and value > 0]


def _clean_numbers(values):
    return [float(value) for value in values or [] if isinstance(value, (int, float))]


def _dedupe(values):
    result = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result
