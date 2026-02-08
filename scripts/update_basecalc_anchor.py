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

from BaseCalc.anchor_snapshot import build_anchor_snapshot, save_anchor_snapshot
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
HTTP_RETRY_STATUS = (403, 429, 500, 502, 503, 504)
HTTP_RETRY_TOTAL = 3
HTTP_RETRY_BACKOFF = 1.0
PRICE_PATTERN = re.compile(
    r'data-test="instrument-price-last"[^>]*>\s*([0-9][0-9,]*(?:\.\d+)?)\s*<',
    re.IGNORECASE,
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


def _parse_price_from_investing_html(html):
    if not html:
        return None
    match = PRICE_PATTERN.search(html)
    if not match:
        return None
    return _parse_positive_float(match.group(1))


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
    headers = dict(HEADERS)
    headers.update(
        {
            "Referer": "https://www.investing.com/",
            "Origin": "https://www.investing.com",
            "Upgrade-Insecure-Requests": "1",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
    )
    return headers


def _warmup_investing_session(session):
    for url in INVESTING_WARMUP_URLS:
        try:
            session.get(url, timeout=REQUEST_TIMEOUT_SEC, allow_redirects=True)
        except requests.RequestException:
            continue


def _fetch_anchor_price():
    session = _build_retry_session(_build_investing_headers())
    try:
        _warmup_investing_session(session)
        response = session.get(
            INVESTING_NIKKEI_FUTURES_URL,
            timeout=REQUEST_TIMEOUT_SEC,
            allow_redirects=True,
        )
        response.raise_for_status()
        price = _parse_price_from_investing_html(response.text)
        if price is None:
            logger.warning(
                "Failed to parse Nikkei futures price from data-test=instrument-price-last.",
            )
        return price
    except requests.RequestException as exc:
        logger.warning("Failed to fetch Nikkei futures page: %s", exc)
        return None
    finally:
        session.close()


def _build_argument_parser():
    parser = argparse.ArgumentParser(
        description="Update BaseCalc monthly anchor snapshot.",
    )
    parser.add_argument(
        "--anchor-price",
        help="Override anchor price. If omitted, price is fetched from Investing.",
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
        logger.error("Anchor price is unavailable from Investing.")
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
