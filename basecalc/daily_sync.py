import csv
import io
import re
import subprocess
import tempfile
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

CME_DAILY_BULLETIN_URL = (
    "https://www.cmegroup.com/daily_bulletin/current/Section44_Nikkei_225_Options.pdf"
)
CME_SETTLEMENT_CSV_URL = "https://www.cmegroup.com/ftp/settle/cme.settle.{date}.csv"
INVESTING_HISTORICAL_URL = (
    "https://www.investing.com/indices/japan-225-futures-historical-data"
)
STOOQ_DAILY_URL = "https://stooq.com/q/d/l/"
DEFAULT_SYMBOL = "NIY=F"
DEFAULT_TIMEFRAME = "1d"
DEFAULT_INSTRUMENT_KEY = "cme_nikkei_futures"
DEFAULT_INSTRUMENT_TYPE = "futures"
SOURCE_CME = "cme_daily_bulletin"
SOURCE_INVESTING = "investing.com"
SOURCE_STOOQ = "stooq"
_CME_BULLETIN_DATE_RE = re.compile(
    r"PG44\s+(?P<date>[A-Z][a-z]{2},\s+[A-Z][a-z]{2}\s+\d{2},\s+\d{4})\s+PG44"
)
_CME_CONTRACT_ROW_RE = re.compile(
    r"^(?P<contract>[A-Z]{3}\d{2})\s+"
    r"(?P<open>[\d,]+(?:\.\d+)?)\s+"
    r"(?P<high>[\d,]+(?:\.\d+)?[A-Z#*]?)\s+"
    r"(?P<low>[\d,]+(?:\.\d+)?[A-Z#*]?)\s+"
    r"(?P<close>[\d,]+(?:\.\d+)?)\s+"
    r"(?P<change>[+-]?\s*[\d,]+(?:\.\d+)?|UNCH)\s+"
    r"(?P<rth_volume>----|[\d,]+)\s+"
    r"(?P<globex_volume>----|[\d,]+)"
)


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
    write_basecalc_status(
        {
            "price_data": price_status_entry(
                snapshot,
                world_model.get("readiness_level"),
            )
        }
    )
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
    }


def fetch_nikkei_futures_daily_rows(start=None, end=None):
    fetchers = (
        (SOURCE_CME, fetch_cme_daily_bulletin_bars),
        (SOURCE_INVESTING, fetch_investing_daily_bars),
        (SOURCE_STOOQ, fetch_stooq_daily_bars),
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
        "name": "CME Nikkei 225 Yen Futures",
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
        "fallback_used": latest.source != SOURCE_CME,
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


def fetch_cme_daily_bulletin_bars(start=None, end=None, diagnostics=None):
    rows = fetch_cme_daily_bulletin_pdf_bars(
        start=start,
        end=end,
        diagnostics=diagnostics,
    )
    if rows:
        return rows
    return fetch_cme_settlement_csv_bars(
        start=start,
        end=end,
        diagnostics=diagnostics,
    )


def fetch_cme_settlement_csv_bars(start=None, end=None, diagnostics=None):
    trade_date = end or timezone.localdate()
    text = _get_text(
        CME_SETTLEMENT_CSV_URL.format(date=trade_date.strftime("%Y%m%d")),
        diagnostics=diagnostics,
        label="settlement_csv",
    )
    if not text:
        return []
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for item in reader:
        product_code = str(item.get("Product Code") or item.get("Clearing Code") or "")
        if product_code.upper() != "NIY":
            continue
        settle = _parse_number(
            item.get("Settlement Price")
            or item.get("Settle")
            or item.get("Settlement")
        )
        if settle is None:
            continue
        rows.append(
            {
                "date": trade_date,
                "open": settle,
                "high": settle,
                "low": settle,
                "close": settle,
                "volume": _parse_number(item.get("Volume")),
                "source": SOURCE_CME,
            }
        )
    return filter_rows_by_date(rows, start, end)


def fetch_cme_daily_bulletin_pdf_bars(start=None, end=None, diagnostics=None):
    content = _get_bytes(
        CME_DAILY_BULLETIN_URL,
        diagnostics=diagnostics,
        label="pdf",
    )
    if not content:
        return []
    text = _pdf_text(content)
    if not text:
        return []
    return filter_rows_by_date(parse_cme_daily_bulletin_text(text), start, end)


def parse_cme_daily_bulletin_text(text):
    if not text:
        return []
    bulletin_date = _parse_cme_bulletin_date(text)
    if bulletin_date is None:
        return []
    in_nikkei_yen_section = False
    rows = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if line.upper().startswith("NIKKEI (YEN) F"):
            in_nikkei_yen_section = True
            continue
        if in_nikkei_yen_section and (
            line.startswith("TOTAL ")
            or line.upper().startswith("THE INFORMATION")
            or line.upper().startswith("ADDITIONAL ")
        ):
            break
        if not in_nikkei_yen_section:
            continue
        match = _CME_CONTRACT_ROW_RE.match(line)
        if not match:
            continue
        rows.append(
            {
                "date": bulletin_date,
                "open": _parse_number(match.group("open")),
                "high": _parse_number(match.group("high")),
                "low": _parse_number(match.group("low")),
                "close": _parse_number(match.group("close")),
                "volume": _parse_cme_volume(
                    match.group("rth_volume"),
                    match.group("globex_volume"),
                ),
                "source": SOURCE_CME,
            }
        )
        break
    return rows


def fetch_investing_daily_bars(start=None, end=None, diagnostics=None):
    text = _get_text(
        INVESTING_HISTORICAL_URL,
        diagnostics=diagnostics,
        label="historical",
    )
    if not text:
        return []
    soup = BeautifulSoup(text, "html.parser")
    rows = []
    for tr in soup.find_all("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["td", "th"])]
        if len(cells) < 6:
            continue
        parsed_date = _parse_date(cells[0])
        if parsed_date is None:
            continue
        rows.append(
            {
                "date": parsed_date,
                "open": _parse_number(cells[2]),
                "high": _parse_number(cells[3]),
                "low": _parse_number(cells[4]),
                "close": _parse_number(cells[1]),
                "volume": _parse_volume(cells[5]),
                "source": SOURCE_INVESTING,
            }
        )
    return filter_rows_by_date(rows, start, end)


def fetch_stooq_daily_bars(start=None, end=None, diagnostics=None):
    params = {"s": "nk.f", "i": "d"}
    if start:
        params["d1"] = start.strftime("%Y%m%d")
    if end:
        params["d2"] = end.strftime("%Y%m%d")
    text = _get_text(
        STOOQ_DAILY_URL,
        params=params,
        diagnostics=diagnostics,
        label="daily_csv",
    )
    if not text or "<html" in text.lower():
        if text and "<html" in text.lower():
            _record_fetch_detail(diagnostics, "daily_csv", "html_response")
        return []
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for item in reader:
        parsed_date = _parse_date(item.get("Date"))
        close = _parse_number(item.get("Close"))
        if parsed_date is None or close is None:
            continue
        rows.append(
            {
                "date": parsed_date,
                "open": _parse_number(item.get("Open")),
                "high": _parse_number(item.get("High")),
                "low": _parse_number(item.get("Low")),
                "close": close,
                "volume": _parse_volume(item.get("Volume")),
                "source": SOURCE_STOOQ,
            }
        )
    return filter_rows_by_date(rows, start, end)


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
        "source": row.get("source") or SOURCE_CME,
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


def _get_bytes(url, diagnostics=None, label="http"):
    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT_SEC,
        )
        _record_http_response(diagnostics, label, response)
        response.raise_for_status()
        return response.content
    except requests.RequestException as exc:
        _record_fetch_error(diagnostics, label, exc)
        return None


def _pdf_text(content):
    with tempfile.NamedTemporaryFile(suffix=".pdf") as handle:
        handle.write(content)
        handle.flush()
        try:
            result = subprocess.run(
                ["pdftotext", handle.name, "-"],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return ""
    return result.stdout


def _parse_date(value):
    if isinstance(value, date):
        return value
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y", "%a, %b %d, %Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _parse_cme_bulletin_date(text):
    match = _CME_BULLETIN_DATE_RE.search(text)
    if not match:
        return None
    return _parse_date(match.group("date"))


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


def _parse_cme_volume(*values):
    total = 0.0
    found = False
    for value in values:
        parsed = _parse_volume(value)
        if parsed is None:
            continue
        total += parsed
        found = True
    return total if found else None


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
    if source == SOURCE_CME and snapshot.get("source") == SOURCE_CME:
        return "success"
    return "fallback"


def _should_update_existing_bar(existing, parsed):
    if parsed.get("source") != SOURCE_CME:
        return False
    if existing.source == SOURCE_CME:
        return False
    return True
