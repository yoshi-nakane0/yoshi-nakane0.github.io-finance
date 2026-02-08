import argparse
import json
import logging
import os
import random
import re
import sys
import time
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from BaseCalc.anchor_snapshot import (
    ANCHOR_DATA_PATH,
    build_anchor_snapshot,
    load_anchor_snapshot,
    save_anchor_snapshot,
)
from BaseCalc.nikkei_bias import (
    HEADERS,
    REQUEST_TIMEOUT_SEC,
    get_jgb10y_yield_percent,
    get_nikkei_per_values,
)

logger = logging.getLogger(__name__)

INVESTING_NIKKEI_FUTURES_URL = "https://www.investing.com/indices/japan-225-futures"
INVESTING_WARMUP_URLS = (
    "https://www.investing.com/",
    "https://www.investing.com/indices/",
)
YAHOO_NIKKEI_CHART_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/%5EN225"
    "?interval=5m&range=1d"
)
STOOQ_NIKKEI_CSV_URL = "https://stooq.com/q/l/?s=%5Enkx&i=d"
HTTP_RETRY_STATUS = (403, 429, 500, 502, 503, 504)
HTTP_RETRY_TOTAL_DEFAULT = 3
HTTP_RETRY_BACKOFF_DEFAULT = 1.0
SOURCE_RETRY_TOTAL_DEFAULT = 3
SOURCE_RETRY_BASE_DELAY_DEFAULT = 2.0
SOURCE_RETRY_MAX_DELAY_DEFAULT = 20.0
SOURCE_RETRY_JITTER_DEFAULT = 1.0
SOURCE_SWITCH_DELAY_DEFAULT = 0.5
ANCHOR_API_URL_ENV = "BASECALC_ANCHOR_API_URL"
ANCHOR_API_TOKEN_ENV = "BASECALC_ANCHOR_API_TOKEN"
ANCHOR_API_TOKEN_HEADER_ENV = "BASECALC_ANCHOR_API_TOKEN_HEADER"
ANCHOR_API_JSON_PATH_ENV = "BASECALC_ANCHOR_API_JSON_PATH"
MANUAL_ANCHOR_PRICE_ENV = "BASECALC_MANUAL_ANCHOR_PRICE"
INVESTING_MOBILE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 "
    "Mobile/15E148 Safari/604.1"
)


def _read_env_int(name, default, minimum=0):
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid integer value for %s: %s", name, raw)
        return default
    if value < minimum:
        logger.warning("Out-of-range integer value for %s: %s", name, raw)
        return default
    return value


def _read_env_float(name, default, minimum=0.0):
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid float value for %s: %s", name, raw)
        return default
    if value < minimum:
        logger.warning("Out-of-range float value for %s: %s", name, raw)
        return default
    return value


HTTP_RETRY_TOTAL = _read_env_int(
    "BASECALC_HTTP_RETRY_TOTAL",
    HTTP_RETRY_TOTAL_DEFAULT,
    minimum=1,
)
HTTP_RETRY_BACKOFF = _read_env_float(
    "BASECALC_HTTP_RETRY_BACKOFF",
    HTTP_RETRY_BACKOFF_DEFAULT,
    minimum=0.0,
)
SOURCE_RETRY_TOTAL = _read_env_int(
    "BASECALC_SOURCE_RETRY_TOTAL",
    SOURCE_RETRY_TOTAL_DEFAULT,
    minimum=1,
)
SOURCE_RETRY_BASE_DELAY_SEC = _read_env_float(
    "BASECALC_SOURCE_RETRY_BASE_DELAY_SEC",
    SOURCE_RETRY_BASE_DELAY_DEFAULT,
    minimum=0.0,
)
SOURCE_RETRY_MAX_DELAY_SEC = _read_env_float(
    "BASECALC_SOURCE_RETRY_MAX_DELAY_SEC",
    SOURCE_RETRY_MAX_DELAY_DEFAULT,
    minimum=0.0,
)
SOURCE_RETRY_JITTER_SEC = _read_env_float(
    "BASECALC_SOURCE_RETRY_JITTER_SEC",
    SOURCE_RETRY_JITTER_DEFAULT,
    minimum=0.0,
)
SOURCE_SWITCH_DELAY_SEC = _read_env_float(
    "BASECALC_SOURCE_SWITCH_DELAY_SEC",
    SOURCE_SWITCH_DELAY_DEFAULT,
    minimum=0.0,
)
PRICE_PATTERNS = (
    re.compile(
        r"current\s+Nikkei 225 Futures price is\s*([0-9.,]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r'data-test="instrument-price-last"[^>]*>\s*([0-9.,]+)\s*<',
        re.IGNORECASE,
    ),
    re.compile(r'"last"\s*:\s*"([0-9.,]+)"', re.IGNORECASE),
)


def _parse_positive_float(text):
    if text is None:
        return None
    cleaned = str(text).replace(",", "").strip()
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if value <= 0:
        return None
    return value


def _parse_price_from_html(html):
    if not html:
        return None
    for pattern in PRICE_PATTERNS:
        match = pattern.search(html)
        if not match:
            continue
        parsed = _parse_positive_float(match.group(1))
        if parsed is not None:
            return parsed
    return None


def _extract_value_by_path(payload, path):
    if not path:
        return payload
    current = payload
    for segment in str(path).split("."):
        part = segment.strip()
        if not part:
            continue
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current.get(part)
            continue
        if isinstance(current, list):
            try:
                index = int(part)
            except ValueError:
                return None
            if index < 0 or index >= len(current):
                return None
            current = current[index]
            continue
        return None
    return current


def _sleep_before_retry(source_name, attempt_index):
    if attempt_index >= SOURCE_RETRY_TOTAL:
        return
    base_delay = SOURCE_RETRY_BASE_DELAY_SEC * (2 ** (attempt_index - 1))
    delay = min(base_delay, SOURCE_RETRY_MAX_DELAY_SEC)
    if SOURCE_RETRY_JITTER_SEC > 0:
        delay += random.uniform(0.0, SOURCE_RETRY_JITTER_SEC)
    if delay <= 0:
        return
    logger.info(
        "Retry %s in %.1f sec (%d/%d).",
        source_name,
        delay,
        attempt_index + 1,
        SOURCE_RETRY_TOTAL,
    )
    time.sleep(delay)


def _fetch_with_retries(source_name, fetcher):
    for attempt in range(1, SOURCE_RETRY_TOTAL + 1):
        price = fetcher()
        if price is not None:
            return price
        _sleep_before_retry(source_name, attempt)
    return None


def _build_retry_session(headers):
    retry = Retry(
        total=HTTP_RETRY_TOTAL,
        connect=HTTP_RETRY_TOTAL,
        read=HTTP_RETRY_TOTAL,
        status=HTTP_RETRY_TOTAL,
        backoff_factor=HTTP_RETRY_BACKOFF,
        status_forcelist=HTTP_RETRY_STATUS,
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(headers)
    return session


def _build_investing_headers():
    desktop = dict(HEADERS)
    desktop.update(
        {
            "Referer": "https://www.investing.com/",
            "Origin": "https://www.investing.com",
            "Upgrade-Insecure-Requests": "1",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
    )
    mobile = dict(desktop)
    mobile["User-Agent"] = INVESTING_MOBILE_USER_AGENT
    mobile["Accept-Language"] = "ja,en-US;q=0.9,en;q=0.8"
    return (
        ("desktop", desktop),
        ("mobile", mobile),
    )


def _warmup_investing_session(session):
    for url in INVESTING_WARMUP_URLS:
        try:
            session.get(url, timeout=REQUEST_TIMEOUT_SEC, allow_redirects=True)
        except requests.RequestException:
            continue


def _fetch_anchor_price_from_investing_profile(profile_name, headers):
    session = _build_retry_session(headers)
    try:
        _warmup_investing_session(session)
        response = session.get(
            INVESTING_NIKKEI_FUTURES_URL,
            timeout=REQUEST_TIMEOUT_SEC,
            allow_redirects=True,
        )
        if response.status_code == 403:
            logger.warning(
                "Nikkei futures request blocked by 403 (%s).",
                profile_name,
            )
            return None
        response.raise_for_status()
        price = _parse_price_from_html(response.text)
        if price is None:
            logger.warning(
                "Failed to parse Nikkei futures price (%s).",
                profile_name,
            )
        return price
    except requests.RequestException as exc:
        logger.warning(
            "Failed to fetch Nikkei futures page (%s): %s",
            profile_name,
            exc,
        )
        return None
    finally:
        session.close()


def _fetch_anchor_price_from_investing():
    for profile_name, headers in _build_investing_headers():
        price = _fetch_anchor_price_from_investing_profile(
            profile_name,
            headers,
        )
        if price is not None:
            return price
    return None


def _build_external_api_headers():
    headers = dict(HEADERS)
    headers["Accept"] = "application/json,text/plain,*/*"
    token = os.getenv(ANCHOR_API_TOKEN_ENV)
    if token:
        header_name = (
            os.getenv(ANCHOR_API_TOKEN_HEADER_ENV, "Authorization").strip()
            or "Authorization"
        )
        if (
            header_name.lower() == "authorization"
            and not token.lower().startswith("bearer ")
        ):
            headers[header_name] = f"Bearer {token}"
        else:
            headers[header_name] = token
    return headers


def _fetch_anchor_price_from_external_api():
    url = (os.getenv(ANCHOR_API_URL_ENV) or "").strip()
    if not url:
        return None
    json_path = (os.getenv(ANCHOR_API_JSON_PATH_ENV) or "price").strip() or "price"
    session = _build_retry_session(_build_external_api_headers())
    try:
        response = session.get(
            url,
            timeout=REQUEST_TIMEOUT_SEC,
            allow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        logger.warning("Failed to fetch external anchor API: %s", exc)
        return None
    except ValueError as exc:
        logger.warning("Failed to decode external anchor API response: %s", exc)
        return None
    finally:
        session.close()

    candidate = _extract_value_by_path(payload, json_path)
    price = _parse_positive_float(candidate)
    if price is None:
        logger.warning(
            "Failed to parse external anchor API price (json path: %s).",
            json_path,
        )
    return price


def _parse_stooq_csv_price(text):
    if not text:
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    header = [column.strip().lower() for column in lines[0].split(",")]
    latest_row = [column.strip() for column in lines[-1].split(",")]
    close_index = 6
    if "close" in header:
        close_index = header.index("close")
    if close_index >= len(latest_row):
        return None
    close_text = latest_row[close_index]
    if close_text in {"", "-", "N/D"}:
        return None
    return _parse_positive_float(close_text)


def _fetch_anchor_price_from_stooq():
    headers = dict(HEADERS)
    headers.update(
        {
            "Accept": "text/csv,text/plain,*/*",
            "Referer": "https://stooq.com/",
        }
    )
    session = _build_retry_session(headers)
    try:
        response = session.get(
            STOOQ_NIKKEI_CSV_URL,
            timeout=REQUEST_TIMEOUT_SEC,
            allow_redirects=True,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Failed to fetch Stooq Nikkei CSV: %s", exc)
        return None
    finally:
        session.close()

    price = _parse_stooq_csv_price(response.text)
    if price is None:
        logger.warning("Failed to parse Stooq Nikkei close price.")
    return price


def _parse_yahoo_chart_price(payload):
    if not isinstance(payload, dict):
        return None
    chart = payload.get("chart")
    if not isinstance(chart, dict):
        return None
    results = chart.get("result")
    if not isinstance(results, list) or not results:
        return None
    first = results[0]
    if not isinstance(first, dict):
        return None
    meta = first.get("meta")
    if isinstance(meta, dict):
        for key in ("regularMarketPrice", "previousClose"):
            value = _parse_positive_float(meta.get(key))
            if value is not None:
                return value
    indicators = first.get("indicators")
    if not isinstance(indicators, dict):
        return None
    quote_items = indicators.get("quote")
    if not isinstance(quote_items, list) or not quote_items:
        return None
    first_quote = quote_items[0]
    if not isinstance(first_quote, dict):
        return None
    close_values = first_quote.get("close")
    if not isinstance(close_values, list):
        return None
    for candidate in reversed(close_values):
        value = _parse_positive_float(candidate)
        if value is not None:
            return value
    return None


def _fetch_anchor_price_from_yahoo():
    headers = dict(HEADERS)
    headers.update(
        {
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://finance.yahoo.com/quote/%5EN225",
        }
    )
    session = _build_retry_session(headers)
    response = None
    try:
        response = session.get(
            YAHOO_NIKKEI_CHART_URL,
            timeout=REQUEST_TIMEOUT_SEC,
            allow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        logger.warning("Failed to fetch Yahoo Nikkei chart: %s", exc)
        return None
    except ValueError as exc:
        logger.warning("Failed to decode Yahoo Nikkei chart response: %s", exc)
        return None
    finally:
        session.close()

    if response is None:
        return None
    price = _parse_yahoo_chart_price(payload)
    if price is None:
        logger.warning("Failed to parse Yahoo Nikkei chart price.")
    return price


def _load_existing_anchor_price():
    snapshot = load_anchor_snapshot()
    if snapshot:
        parsed = _parse_positive_float(snapshot.get("anchor_price"))
        if parsed is not None:
            return parsed
    try:
        with open(ANCHOR_DATA_PATH, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.warning("Failed to read existing anchor snapshot file: %s", exc)
        return None
    if not isinstance(payload, dict):
        return None
    return _parse_positive_float(payload.get("anchor_price"))


def _load_manual_anchor_price():
    raw = os.getenv(MANUAL_ANCHOR_PRICE_ENV)
    if raw is None or str(raw).strip() == "":
        return None
    value = _parse_positive_float(raw)
    if value is None:
        logger.warning(
            "Invalid manual anchor price in %s: %s",
            MANUAL_ANCHOR_PRICE_ENV,
            raw,
        )
    return value


def _fetch_anchor_price():
    sources = (
        ("external API", _fetch_anchor_price_from_external_api, False),
        ("Investing Nikkei futures", _fetch_anchor_price_from_investing, False),
        ("Yahoo Nikkei chart", _fetch_anchor_price_from_yahoo, True),
        ("Stooq Nikkei close", _fetch_anchor_price_from_stooq, True),
    )
    for index, (source_name, fetcher, is_fallback) in enumerate(sources):
        price = _fetch_with_retries(source_name, fetcher)
        if price is not None:
            if is_fallback:
                logger.warning("Fallback source in use: %s.", source_name)
            return price
        has_next = index < (len(sources) - 1)
        if has_next and SOURCE_SWITCH_DELAY_SEC > 0:
            time.sleep(SOURCE_SWITCH_DELAY_SEC)

    price = _load_existing_anchor_price()
    if price is not None:
        logger.warning(
            "Fallback source in use: existing BaseCalc anchor snapshot.",
        )
        return price
    price = _load_manual_anchor_price()
    if price is not None:
        logger.warning(
            "Fallback source in use: manual anchor price (%s).",
            MANUAL_ANCHOR_PRICE_ENV,
        )
        return price
    return None


def _build_argument_parser():
    parser = argparse.ArgumentParser(
        description="Update BaseCalc monthly anchor snapshot.",
    )
    parser.add_argument(
        "--anchor-price",
        help="Override anchor price. If omitted, latest futures price is fetched.",
    )
    parser.add_argument(
        "--erp-method",
        default="method_a",
        choices=["method_a", "method_b", "method_c"],
        help="ERP method for monthly anchor generation.",
    )
    parser.add_argument(
        "--erp-growth",
        default=None,
        help="Growth percent for method_b or method_c (e.g. 2.1).",
    )
    parser.add_argument(
        "--growth-core-ratio",
        default="0.6",
        help="Core range tuning ratio.",
    )
    parser.add_argument(
        "--growth-wide-ratio",
        default="0.7",
        help="Wide range tuning ratio.",
    )
    return parser


def main():
    parser = _build_argument_parser()
    args = parser.parse_args()

    anchor_price = _parse_positive_float(args.anchor_price)
    if anchor_price is None:
        anchor_price = _fetch_anchor_price()
    if anchor_price is None:
        logger.error("Anchor price is unavailable after retries and fallbacks.")
        return 1

    per_values = get_nikkei_per_values()
    if not per_values:
        logger.error("Nikkei PER data is unavailable.")
        return 1
    forward_per = _parse_positive_float(per_values.get("index_based"))
    if forward_per is None:
        logger.error("Forward PER is unavailable.")
        return 1
    dividend_yield = per_values.get("dividend_yield_index_based")

    jgb10y_yield_percent = get_jgb10y_yield_percent()
    if jgb10y_yield_percent is None:
        logger.error("JGB 10Y yield is unavailable.")
        return 1

    snapshot = build_anchor_snapshot(
        anchor_price=anchor_price,
        forward_per=forward_per,
        jgb10y_yield_percent=jgb10y_yield_percent,
        dividend_yield_index_percent=dividend_yield,
        erp_method=args.erp_method,
        erp_growth_percent=_parse_positive_float(args.erp_growth)
        if args.erp_growth is not None
        else None,
        growth_core_ratio=_parse_positive_float(args.growth_core_ratio),
        growth_wide_ratio=_parse_positive_float(args.growth_wide_ratio),
    )
    if not snapshot:
        logger.error("Failed to build anchor snapshot.")
        return 1

    save_anchor_snapshot(snapshot)
    logger.info("Updated BaseCalc anchor snapshot.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
