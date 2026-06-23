from datetime import datetime, timezone as dt_timezone
from typing import Optional
from zoneinfo import ZoneInfo

from django.utils import timezone

from .instrument import normalize_instrument

JST = ZoneInfo("Asia/Tokyo")


def evaluate_snapshot_quality(snapshot: Optional[dict], now=None) -> dict:
    """価格スナップショットの品質を 0-100 で評価する。"""
    now = now or timezone.now()
    source = (snapshot or {}).get("source") or "unknown"
    symbol = (snapshot or {}).get("symbol")
    instrument_type = _instrument_type(source, symbol, snapshot)
    warnings = detect_snapshot_anomaly(snapshot)
    stale = is_snapshot_stale(snapshot, now=now)
    if stale:
        warnings.append("価格データが古い可能性があります")

    score = source_quality_weight(source, symbol)
    if not snapshot:
        score = 0
        warnings.append("価格データがありません")
    if instrument_type == "index_fallback":
        score -= 25
    if (snapshot or {}).get("fallback_used") or (snapshot or {}).get("is_stale"):
        score -= 18
    if stale:
        score -= 22
    score -= min(len(warnings) * 8, 32)
    score = _clamp_int(score, 0, 100)
    return {
        "score": score,
        "level": _level_from_score(score),
        "is_stale": stale,
        "source": source,
        "symbol": symbol,
        "warnings": _dedupe(warnings),
        "fallback_used": bool((snapshot or {}).get("fallback_used") or source in {"stooq", "saved_snapshot", "last_good_cache"}),
        "instrument_type": instrument_type,
    }


def is_snapshot_stale(snapshot: Optional[dict], max_age_minutes: int = 15, now=None) -> bool:
    if not snapshot:
        return True
    source = (snapshot or {}).get("source")
    if source in {"cme_daily_bulletin", "225navi"}:
        max_age_minutes = max(max_age_minutes, 96 * 60)
    if source == "matsui":
        max_age_minutes = max(max_age_minutes, 90)
    fetched_at = _parse_timestamp(snapshot.get("fetched_at"))
    if fetched_at is None:
        return True
    now = now or timezone.now()
    if timezone.is_naive(now):
        now = timezone.make_aware(now, timezone=dt_timezone.utc)
    if _same_jst_date_matsui_quote(source, fetched_at, now):
        return False
    return (now - fetched_at).total_seconds() > max_age_minutes * 60


def detect_snapshot_anomaly(snapshot: Optional[dict], previous_snapshot: Optional[dict] = None) -> list:
    if not snapshot:
        return ["価格データがありません"]
    warnings = []
    price = _to_float(snapshot.get("price"))
    previous_close = _to_float(snapshot.get("previous_close"))
    if price is None or price <= 0:
        warnings.append("価格が不正です")
    if previous_close is not None and previous_close <= 0:
        warnings.append("前日終値が不正です")
    change_pct = _to_float(snapshot.get("change_pct"))
    if change_pct is None and price is not None and previous_close:
        change_pct = ((price - previous_close) / previous_close) * 100
    if change_pct is not None and abs(change_pct) > 8:
        warnings.append("1日変化率が大きすぎます")
    fetched_at = _parse_timestamp(snapshot.get("fetched_at"))
    if fetched_at and fetched_at > timezone.now() + timezone.timedelta(minutes=2):
        warnings.append("取得時刻が未来です")
    previous_price = _to_float((previous_snapshot or {}).get("price"))
    if price is not None and previous_price and abs((price - previous_price) / previous_price) > 0.08:
        warnings.append("前回取得値からの変化が大きすぎます")
    return _dedupe(warnings)


def source_quality_weight(source: str, symbol: Optional[str] = None) -> int:
    source = (source or "").lower()
    symbol = (symbol or "").lower()
    if source == "225navi" and symbol == "niy=f":
        return 96
    if source == "matsui" and symbol == "niy=f":
        return 90
    if source == "cme_daily_bulletin" and symbol == "niy=f":
        return 96
    if source == "yahoo" and symbol == "niy=f":
        return 96
    if source == "yahoo":
        return 88
    if source == "stooq" and symbol == "^nkx":
        return 58
    if source == "stooq":
        return 74
    if source in {"saved_snapshot", "last_good_cache"}:
        return 52
    if source == "cache":
        return 48
    return 42


def _same_jst_date_matsui_quote(source, fetched_at, now):
    if source != "matsui":
        return False
    fetched_local = timezone.localtime(fetched_at, JST)
    now_local = timezone.localtime(now, JST)
    return fetched_local.date() == now_local.date() and fetched_at <= now


def _instrument_type(source, symbol, snapshot):
    explicit = (snapshot or {}).get("instrument_type")
    if explicit:
        return explicit
    return normalize_instrument(symbol, source).get("instrument_type") or "unknown"


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


def _level_from_score(score):
    if score >= 80:
        return "good"
    if score >= 50:
        return "warning"
    return "bad"


def _clamp_int(value, low, high):
    return max(low, min(high, int(round(value))))


def _dedupe(values):
    result = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result
