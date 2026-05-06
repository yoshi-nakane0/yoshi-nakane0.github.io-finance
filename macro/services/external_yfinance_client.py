"""Yahoo Finance から指数値を月次で取得（Indicator 用）。

既存の yfinance_client は PriceObservation 用（日経・S&P）。
こちらは Indicator/Observation に保存するための汎用 fetcher。
"""

import logging
import time
from datetime import date, datetime, timezone as dt_timezone
from typing import List, Tuple

import requests

logger = logging.getLogger(__name__)

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/118.0 Safari/537.36'
)
DEFAULT_TIMEOUT = 30
DEFAULT_HISTORY_YEARS = 15

# Indicator.fred_series_id → Yahoo Finance シンボル
SERIES_TO_SYMBOL = {
    'RUT_INDEX': '^RUT',
}


class ExternalYfinanceError(Exception):
    """Yahoo Finance 指数取得失敗"""


def fetch_monthly_index(
    series_id: str,
    observation_start: 'date | None' = None,
    observation_end: 'date | None' = None,
) -> List[Tuple[date, float]]:
    """series_id（独自ID） → Yahoo Finance シンボルに変換し、月次終値の (date, value) リストを返す。"""
    symbol = SERIES_TO_SYMBOL.get(series_id)
    if not symbol:
        raise ExternalYfinanceError(f"Yahoo Finance シンボル未定義: {series_id}")

    end_ts = int(time.time())
    if observation_start is not None:
        start_ts = int(
            datetime(
                observation_start.year, observation_start.month, observation_start.day,
                tzinfo=dt_timezone.utc,
            ).timestamp()
        )
    else:
        start_ts = end_ts - DEFAULT_HISTORY_YEARS * 366 * 86400

    params = {
        'period1': start_ts,
        'period2': end_ts,
        'interval': '1mo',
        'events': 'history',
        'includeAdjustedClose': 'false',
    }
    headers = {'User-Agent': USER_AGENT}

    try:
        response = requests.get(
            YAHOO_CHART_URL.format(symbol=symbol),
            params=params,
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ExternalYfinanceError(f"Yahoo Finance fetch failed for {symbol}: {exc}")

    chart = data.get('chart') or {}
    if chart.get('error'):
        raise ExternalYfinanceError(f"Yahoo Finance error: {chart['error']}")
    results = chart.get('result') or []
    if not results:
        return []

    result = results[0]
    timestamps = result.get('timestamp') or []
    indicators_block = result.get('indicators') or {}
    quotes = (indicators_block.get('quote') or [{}])[0]
    closes = quotes.get('close') or []

    history: List[Tuple[date, float]] = []
    seen_months = set()
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        month_start = datetime.fromtimestamp(ts, tz=dt_timezone.utc).date().replace(day=1)
        if month_start in seen_months:
            continue
        seen_months.add(month_start)
        history.append((month_start, float(close)))

    history.sort(key=lambda x: x[0])
    return history
