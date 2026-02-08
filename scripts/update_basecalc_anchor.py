import argparse
import logging
import re
import sys
from pathlib import Path

import requests

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from BaseCalc.anchor_snapshot import build_anchor_snapshot, save_anchor_snapshot
from BaseCalc.nikkei_bias import (
    REQUEST_TIMEOUT_SEC,
    get_jgb10y_yield_percent,
    get_nikkei_per_values,
)

logger = logging.getLogger(__name__)

NIKKEI225JP_URL = "https://nikkei225jp.com/"
REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": NIKKEI225JP_URL}
PRICE_PATTERN = re.compile(
    r'id=["\']V191["\'][^>]*>\s*([0-9][0-9,]*)',
    re.IGNORECASE | re.DOTALL,
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


def _parse_price_from_nikkei225jp_html(html):
    if not html:
        return None
    match = PRICE_PATTERN.search(html)
    if not match:
        return None
    return _parse_positive_float(match.group(1))


def _fetch_anchor_price():
    try:
        response = requests.get(
            NIKKEI225JP_URL,
            headers=REQUEST_HEADERS,
            timeout=REQUEST_TIMEOUT_SEC,
            allow_redirects=True,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Failed to fetch nikkei225jp page: %s", exc)
        return None
    return _parse_price_from_nikkei225jp_html(response.text)


def _build_argument_parser():
    parser = argparse.ArgumentParser(
        description="Update BaseCalc monthly anchor snapshot.",
    )
    parser.add_argument(
        "--anchor-price",
        help="Override anchor price. If omitted, price is fetched from nikkei225jp.",
    )
    return parser


def main():
    parser = _build_argument_parser()
    args = parser.parse_args()

    anchor_price = _parse_positive_float(args.anchor_price)
    if anchor_price is None:
        anchor_price = _fetch_anchor_price()
    if anchor_price is None:
        logger.error("Anchor price is unavailable from nikkei225jp.")
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
    )
    if not snapshot:
        logger.error("Failed to build anchor snapshot.")
        return 1

    save_anchor_snapshot(snapshot)
    logger.info("Updated BaseCalc anchor snapshot.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
