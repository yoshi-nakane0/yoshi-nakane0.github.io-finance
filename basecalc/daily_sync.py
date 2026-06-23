import re
from datetime import date, datetime, time, timedelta, timezone as dt_timezone

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
MATSUI_FUTURES_URL = "https://finance.matsui.co.jp/future/3OSE11/index"
DEFAULT_SYMBOL = "NIY=F"
DEFAULT_TIMEFRAME = "1d"
DEFAULT_INSTRUMENT_KEY = "cme_nikkei_futures"
DEFAULT_INSTRUMENT_TYPE = "futures"
SOURCE_225NAVI = "225navi"
SOURCE_MATSUI = "matsui"
_NAVI_DATE_RE = re.compile(r"^\d{4}/\d{1,2}/\d{1,2}$")
_MATSUI_DATETIME_RE = re.compile(r"^\d{4}/\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2}$")
JST = dt_timezone(timedelta(hours=9))


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
    attempts = []
    history_attempt = {"source": SOURCE_225NAVI, "rows": 0, "details": []}
    rows = fetch_225navi_daily_bars(start=start, end=end, diagnostics=history_attempt)
    history_attempt["rows"] = len(rows)
    attempts.append(history_attempt)

    if _should_fetch_intraday_quote(start=start, end=end):
        intraday_attempt = {"source": SOURCE_MATSUI, "rows": 0, "details": []}
        intraday_rows = fetch_matsui_futures_quote(diagnostics=intraday_attempt)
        intraday_rows = filter_rows_by_date(intraday_rows, start, end)
        intraday_attempt["rows"] = len(intraday_rows)
        attempts.append(intraday_attempt)
        rows = merge_price_rows(rows, intraday_rows)

    if rows:
        return rows, latest_row_source(rows), attempts
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
        "fetched_at": latest.timestamp,
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


def fetch_matsui_futures_quote(diagnostics=None):
    text = _get_text(
        MATSUI_FUTURES_URL,
        diagnostics=diagnostics,
        label="intraday",
    )
    if not text:
        return []
    soup = BeautifulSoup(text, "html.parser")
    row = parse_matsui_futures_text(soup.get_text("\n", strip=True))
    return [row] if row else []


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


def parse_matsui_futures_text(text):
    if not text:
        return None
    tokens = [line.strip() for line in text.splitlines() if line.strip()]
    updated_at = next(
        (_parse_datetime(token) for token in tokens if _MATSUI_DATETIME_RE.match(token)),
        None,
    )
    current = _value_after_label(tokens, "現在値")
    if updated_at is None or current is None:
        return None
    return {
        "date": updated_at.date(),
        "timestamp": updated_at,
        "open": _value_after_label(tokens, "始値") or current,
        "high": _value_after_label(tokens, "高値") or current,
        "low": _value_after_label(tokens, "安値") or current,
        "close": current,
        "volume": _value_after_label(tokens, "出来高"),
        "source": SOURCE_MATSUI,
    }


def normalize_bar_row(row):
    timestamp = _parse_datetime(row.get("timestamp"))
    parsed_date = _parse_date(row.get("date")) or (timestamp.date() if timestamp else None)
    close = _parse_number(row.get("close"))
    if parsed_date is None or close is None:
        return None
    if timestamp is None:
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


def merge_price_rows(history_rows, intraday_rows):
    rows_by_date = {}
    for row in [*(history_rows or []), *(intraday_rows or [])]:
        parsed_date = _parse_date(row.get("date"))
        if parsed_date is None:
            continue
        existing = rows_by_date.get(parsed_date)
        if existing is None or row_sort_timestamp(row) >= row_sort_timestamp(existing):
            rows_by_date[parsed_date] = {**row, "date": parsed_date}
    return sorted(rows_by_date.values(), key=row_sort_timestamp, reverse=True)


def latest_row_source(rows):
    latest = max((row for row in rows if row), key=row_sort_timestamp, default=None)
    return (latest or {}).get("source") or SOURCE_225NAVI


def row_sort_timestamp(row):
    parsed = normalize_bar_row(row)
    if parsed:
        return parsed["timestamp"]
    parsed_date = _parse_date((row or {}).get("date"))
    if parsed_date:
        return datetime.combine(parsed_date, time.min, tzinfo=dt_timezone.utc)
    return datetime.min.replace(tzinfo=dt_timezone.utc)


def _should_fetch_intraday_quote(start=None, end=None):
    today = timezone.localdate()
    if start and start > today:
        return False
    if end and end < today:
        return False
    return True


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


def _parse_datetime(value):
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=JST)
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=JST)
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


def _value_after_label(tokens, label):
    try:
        index = tokens.index(label)
    except ValueError:
        return None
    for token in tokens[index + 1 : index + 6]:
        value = _parse_number(token)
        if value is not None:
            return value
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
