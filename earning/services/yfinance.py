import logging
import time

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
