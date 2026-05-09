import logging

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
