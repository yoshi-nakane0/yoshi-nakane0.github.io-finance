import re
from datetime import date, datetime, time, timezone as dt_timezone

import requests
from bs4 import BeautifulSoup
from django.core.cache import cache
from django.utils import timezone

from .data_quality import evaluate_snapshot_quality
from .market_bars import attach_saved_daily_bars
from .models import MarketBar, MarketSnapshot
from .nikkei_bias import HEADERS, REQUEST_TIMEOUT_SEC
from .status import price_status_entry, write_basecalc_status
from .world_model import build_world_model

NAVI_DAILY_URL = "https://225navi.com/data/"
DEFAULT_SYMBOL = "NIY=F"
DEFAULT_TIMEFRAME = "1d"
DEFAULT_INSTRUMENT_KEY = "cme_nikkei_futures"
DEFAULT_INSTRUMENT_TYPE = "futures"
SOURCE_225NAVI = "225navi"
_NAVI_DATE_RE = re.compile(r"^\d{4}/\d{1,2}/\d{1,2}$")


def sync_nikkei_futures_daily(start=None, end=None, update_existing=False):
    rows, source, attempts = fetch_nikkei_futures_daily_rows(start=start, end=end)
    saved = save_daily_bars(rows, update_existing=update_existing)
    snapshot_bar = latest_synced_bar(rows) if rows else None
    snapshot = build_snapshot_from_market_bar(snapshot_bar) if snapshot_bar else None
    world_model = build_world_model(
        snapshot.get("price") if snapshot else 0,
        snapshot,
    )
    if snapshot:
        write_latest_market_snapshot(snapshot, world_model, latest_bar=snapshot_bar)
        cache.set("nikkei_price", snapshot["price"], timeout=300)
        cache.set("nikkei_futures_snapshot", snapshot, timeout=300)
        cache.set("nikkei_futures_snapshot_last_good", snapshot, timeout=None)
    basecalc_status = {
        "price_data": price_status_entry(
            snapshot,
            world_model.get("readiness_level"),
        )
    }
    write_basecalc_status(basecalc_status)
    sync_status = _sync_status(source, snapshot)
    return {
        "sync_status": sync_status,
        "source": source,
        "attempts": attempts,
        "rows_fetched": len(rows),
        "rows_created": saved["created"],
        "rows_updated": saved["updated"],
        "snapshot_created": bool(snapshot),
        "snapshot_source": snapshot.get("source") if snapshot else "",
        "snapshot_fetched_at": snapshot_bar.timestamp.isoformat() if snapshot_bar else "",
        "price": world_model.get("price"),
        "readiness_level": world_model.get("readiness_level"),
        "_snapshot": snapshot,
        "_world_model": world_model,
        "_basecalc_status": basecalc_status,
    }


def fetch_nikkei_futures_daily_rows(start=None, end=None):
    fetchers = (
        (SOURCE_225NAVI, fetch_225navi_daily_bars),
    )
    attempts = []
    for source_name, fetcher in fetchers:
        attempt = {"source": source_name, "rows": 0, "details": []}
        rows = fetcher(start=start, end=end, diagnostics=attempt)
        attempt["rows"] = len(rows)
        attempts.append(attempt)
        if rows:
            return rows, rows[0].get("source") or source_name, attempts
    return [], "", attempts


def save_daily_bars(rows, update_existing=False):
    created = 0
    updated = 0
    for row in rows:
        parsed = normalize_bar_row(row)
        if not parsed:
            continue
        lookup = {
            "symbol": DEFAULT_SYMBOL,
            "timeframe": DEFAULT_TIMEFRAME,
            "timestamp": parsed["timestamp"],
        }
        defaults = {
            "open": parsed["open"],
            "high": parsed["high"],
            "low": parsed["low"],
            "close": parsed["close"],
            "volume": parsed["volume"],
            "source": parsed["source"],
            "instrument_key": DEFAULT_INSTRUMENT_KEY,
            "instrument_type": DEFAULT_INSTRUMENT_TYPE,
        }
        if update_existing:
            _, was_created = MarketBar.objects.update_or_create(
                **lookup,
                defaults=defaults,
            )
            created += 1 if was_created else 0
            updated += 0 if was_created else 1
            continue
        existing, was_created = MarketBar.objects.get_or_create(
            **lookup,
            defaults=defaults,
        )
        created += 1 if was_created else 0
        if not was_created and _should_update_existing_bar(existing, parsed):
            for key, value in defaults.items():
                setattr(existing, key, value)
            existing.save(update_fields=list(defaults.keys()))
            updated += 1
    return {"created": created, "updated": updated}


def latest_synced_bar(rows):
    timestamps = []
    for row in rows:
        parsed = normalize_bar_row(row)
        if parsed:
            timestamps.append(parsed["timestamp"])
    if not timestamps:
        return None
    return (
        MarketBar.objects.filter(
            symbol=DEFAULT_SYMBOL,
            timeframe=DEFAULT_TIMEFRAME,
            instrument_key=DEFAULT_INSTRUMENT_KEY,
            timestamp=max(timestamps),
        )
        .order_by("-timestamp")
        .first()
    )


def build_snapshot_from_market_bar(latest):
    if latest is None:
        return None
    previous = (
        MarketBar.objects.filter(
            symbol=DEFAULT_SYMBOL,
            timeframe=DEFAULT_TIMEFRAME,
            instrument_key=DEFAULT_INSTRUMENT_KEY,
            timestamp__lt=latest.timestamp,
        )
        .order_by("-timestamp")
        .first()
    )
    previous_close = previous.close if previous else latest.open or latest.close
    snapshot = {
        "symbol": DEFAULT_SYMBOL,
        "name": "Nikkei 225 Futures (225navi reference)",
        "source": latest.source,
        "instrument_key": DEFAULT_INSTRUMENT_KEY,
        "instrument_type": DEFAULT_INSTRUMENT_TYPE,
        "price": latest.close,
        "previous_close": previous_close,
        "change_pct": _pct_change(latest.close, previous_close),
        "open": latest.open,
        "high": latest.high,
        "low": latest.low,
        "close": latest.close,
        "opens": [latest.open or latest.close],
        "highs": [latest.high or latest.close],
        "lows": [latest.low or latest.close],
        "closes": [latest.close],
        "volumes": [latest.volume or 0],
        "timestamps": [int(latest.timestamp.timestamp())],
        "fetched_at": timezone.now(),
        "fallback_used": False,
    }
    snapshot = attach_saved_daily_bars(snapshot)
    snapshot["quality"] = evaluate_snapshot_quality(snapshot)
    return snapshot


def write_latest_market_snapshot(snapshot, world_model, latest_bar=None):
    latest_bar = latest_bar or (
        MarketBar.objects.filter(
            symbol=DEFAULT_SYMBOL,
            timeframe=DEFAULT_TIMEFRAME,
            instrument_key=DEFAULT_INSTRUMENT_KEY,
        )
        .order_by("-timestamp")
        .first()
    )
    if latest_bar is None:
        return None
    existing = MarketSnapshot.objects.filter(
        symbol=DEFAULT_SYMBOL,
        timeframe=DEFAULT_TIMEFRAME,
        fetched_at=latest_bar.timestamp,
        source=snapshot.get("source") or "",
    ).first()
    defaults = {
        "price": snapshot["price"],
        "open": latest_bar.open,
        "high": latest_bar.high,
        "low": latest_bar.low,
        "close": latest_bar.close,
        "volume": latest_bar.volume,
        "instrument_key": DEFAULT_INSTRUMENT_KEY,
        "instrument_type": DEFAULT_INSTRUMENT_TYPE,
        "source_symbol": DEFAULT_SYMBOL,
        "data_quality_score": world_model.get("data_quality_score"),
        "data_quality_level": world_model.get("data_quality_level") or "",
        "readiness_level": world_model.get("readiness_level") or "",
    }
    if existing:
        for key, value in defaults.items():
            setattr(existing, key, value)
        existing.save(update_fields=list(defaults.keys()))
        return existing
    return MarketSnapshot.objects.create(
        symbol=DEFAULT_SYMBOL,
        timeframe=DEFAULT_TIMEFRAME,
        source=snapshot.get("source") or "",
        fetched_at=latest_bar.timestamp,
        **defaults,
    )


def fetch_225navi_daily_bars(start=None, end=None, diagnostics=None):
    text = _get_text(
        NAVI_DAILY_URL,
        diagnostics=diagnostics,
        label="history",
    )
    if not text:
        return []
    soup = BeautifulSoup(text, "html.parser")
    rows = parse_225navi_daily_text(soup.get_text("\n", strip=True))
    return filter_rows_by_date(rows, start, end)


def parse_225navi_daily_text(text):
    if not text:
        return []
    tokens = [line.strip() for line in text.splitlines() if line.strip()]
    rows = []
    for index, token in enumerate(tokens):
        if not _NAVI_DATE_RE.match(token):
            continue
        prices = []
        for value in tokens[index + 1 :]:
            if _NAVI_DATE_RE.match(value):
                break
            parsed = _parse_number(value)
            if parsed is not None:
                prices.append(parsed)
            if len(prices) >= 8:
                break
        if len(prices) < 4:
            continue
        rows.append(
            {
                "date": _parse_date(token),
                "open": prices[0],
                "high": prices[1],
                "low": prices[2],
                "close": prices[3],
                "volume": None,
                "source": SOURCE_225NAVI,
            }
        )
    return rows


def normalize_bar_row(row):
    parsed_date = _parse_date(row.get("date"))
    close = _parse_number(row.get("close"))
    if parsed_date is None or close is None:
        return None
    timestamp = datetime.combine(parsed_date, time.min, tzinfo=dt_timezone.utc)
    return {
        "timestamp": timestamp,
        "open": _parse_number(row.get("open")) or close,
        "high": _parse_number(row.get("high")) or close,
        "low": _parse_number(row.get("low")) or close,
        "close": close,
        "volume": _parse_volume(row.get("volume")),
        "source": row.get("source") or SOURCE_225NAVI,
    }


def filter_rows_by_date(rows, start=None, end=None):
    result = []
    for row in rows:
        parsed_date = _parse_date(row.get("date"))
        if parsed_date is None:
            continue
        if start and parsed_date < start:
            continue
        if end and parsed_date > end:
            continue
        result.append({**row, "date": parsed_date})
    return result


def _get_text(url, params=None, diagnostics=None, label="http"):
    try:
        response = requests.get(
            url,
            params=params,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT_SEC,
        )
        _record_http_response(diagnostics, label, response)
        response.raise_for_status()
        return response.text
    except requests.RequestException as exc:
        _record_fetch_error(diagnostics, label, exc)
        return None


def _parse_date(value):
    if isinstance(value, date):
        return value
    if not value:
        return None
    text = str(value).strip()
    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%b %d, %Y",
        "%a, %b %d, %Y",
        "%d.%m.%Y",
    ):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _parse_number(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    text = text.rstrip("ABNPR#*")
    if not text or text == "-":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_volume(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text or text == "-":
        return None
    suffix = text[-1:].upper()
    multiplier = 1
    if suffix == "K":
        multiplier = 1_000
        text = text[:-1]
    elif suffix == "M":
        multiplier = 1_000_000
        text = text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def _pct_change(current, previous):
    current = _parse_number(current)
    previous = _parse_number(previous)
    if current is None or previous in (None, 0):
        return None
    return ((current - previous) / previous) * 100.0


def _record_http_response(diagnostics, label, response):
    status_code = getattr(response, "status_code", "unknown")
    _record_fetch_detail(diagnostics, label, f"http={status_code}")


def _record_fetch_error(diagnostics, label, exc):
    details = diagnostics.get("details") if isinstance(diagnostics, dict) else []
    prefix = f"{label}:http="
    if any(str(detail).startswith(prefix) for detail in details):
        return
    _record_fetch_detail(diagnostics, label, f"error={exc.__class__.__name__}")


def _record_fetch_detail(diagnostics, label, detail):
    if not isinstance(diagnostics, dict):
        return
    details = diagnostics.setdefault("details", [])
    value = f"{label}:{detail}"
    if value not in details:
        details.append(value)


def _sync_status(source, snapshot):
    if not snapshot:
        return "failed"
    return "fallback"


def _should_update_existing_bar(existing, parsed):
    if parsed.get("source") != SOURCE_225NAVI:
        return False
    if existing.source == SOURCE_225NAVI:
        return False
    return True
