import logging
import time
from datetime import datetime, timezone as dt_timezone

import requests

logger = logging.getLogger(__name__)


SUFFIX_BY_MARKET = {'TSE': '.T'}
US_MARKETS = {'NYSE', 'NASDAQ'}


class YahooFetchError(Exception):
    """Raised when Yahoo Finance fetch fails after retries."""


def build_yahoo_symbol(market, symbol):
    market = (market or '').strip().upper()
    symbol = (symbol or '').strip()
    if not symbol:
        return None
    if market in SUFFIX_BY_MARKET:
        return f'{symbol}{SUFFIX_BY_MARKET[market]}'
    if market in US_MARKETS:
        return symbol
    return None


YAHOO_CHART_URL = 'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}'
USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/118.0 Safari/537.36'
)
DEFAULT_TIMEOUT = 30
RETRY_BACKOFF_SECONDS = (0.5, 1.0, 2.0)


def _fetch_chart_json(url, params=None):
    headers = {'User-Agent': USER_AGENT}
    last_exc = None
    for attempt, backoff in enumerate(RETRY_BACKOFF_SECONDS):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=DEFAULT_TIMEOUT)
            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                status = getattr(exc.response, 'status_code', None)
                if status is not None and 400 <= status < 500:
                    raise YahooFetchError(f'HTTP {status} from Yahoo: {url}') from exc
                last_exc = exc
            else:
                return response.json()
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc

        if attempt < len(RETRY_BACKOFF_SECONDS) - 1:
            time.sleep(backoff)

    raise YahooFetchError(f'Yahoo fetch failed after retries: {url}') from last_exc


def fetch_daily_history(yahoo_symbol, start_date, end_date):
    url = YAHOO_CHART_URL.format(symbol=yahoo_symbol)
    start_ts = int(datetime(start_date.year, start_date.month, start_date.day, tzinfo=dt_timezone.utc).timestamp())
    end_ts = int(datetime(end_date.year, end_date.month, end_date.day, tzinfo=dt_timezone.utc).timestamp()) + 86400
    params = {
        'interval': '1d',
        'period1': start_ts,
        'period2': end_ts,
        'events': 'history',
    }

    try:
        payload = _fetch_chart_json(url, params=params)
    except YahooFetchError as exc:
        logger.warning('yfinance fetch failed for %s: %s', yahoo_symbol, exc)
        return []

    chart = payload.get('chart', {}) if isinstance(payload, dict) else {}
    if chart.get('error'):
        logger.warning('yfinance chart error for %s: %s', yahoo_symbol, chart.get('error'))
        return []

    result_list = chart.get('result') or []
    if not result_list:
        return []
    result = result_list[0]

    timestamps = result.get('timestamp') or []
    indicators = result.get('indicators', {})
    quotes = indicators.get('quote') or [{}]
    quote = quotes[0] if quotes else {}
    adj_list = indicators.get('adjclose') or [{}]
    adj_closes = adj_list[0].get('adjclose', []) if adj_list else []

    opens = quote.get('open', [])
    highs = quote.get('high', [])
    lows = quote.get('low', [])
    closes = quote.get('close', [])
    volumes = quote.get('volume', [])

    rows = []
    for i, ts in enumerate(timestamps):
        d = datetime.fromtimestamp(ts, tz=dt_timezone.utc).date()
        adj = adj_closes[i] if i < len(adj_closes) else None
        close_val = adj if adj is not None else (closes[i] if i < len(closes) else None)
        rows.append({
            'date': d,
            'open': opens[i] if i < len(opens) else None,
            'high': highs[i] if i < len(highs) else None,
            'low': lows[i] if i < len(lows) else None,
            'close': close_val,
            'volume': volumes[i] if i < len(volumes) else None,
        })
    return rows
