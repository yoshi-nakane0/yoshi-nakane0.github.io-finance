import argparse
import logging
import re
import sys
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from BaseCalc.anchor_snapshot import (
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
HTTP_RETRY_STATUS = (403, 429, 500, 502, 503, 504)
HTTP_RETRY_TOTAL = 3
HTTP_RETRY_BACKOFF = 1.0
INVESTING_MOBILE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 "
    "Mobile/15E148 Safari/604.1"
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
    if not snapshot:
        return None
    return _parse_positive_float(snapshot.get("anchor_price"))


def _fetch_anchor_price():
    price = _fetch_anchor_price_from_investing()
    if price is not None:
        return price
    price = _fetch_anchor_price_from_yahoo()
    if price is not None:
        logger.warning(
            "Fallback source in use: Yahoo Nikkei index price.",
        )
        return price
    price = _load_existing_anchor_price()
    if price is not None:
        logger.warning(
            "Fallback source in use: existing BaseCalc anchor snapshot.",
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
