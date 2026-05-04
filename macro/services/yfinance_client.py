"""Yahoo Finance API による月次価格履歴取得。

主要指数（^N225 / ^GSPC）の月次終値を取得し PriceObservation に保存する。
差分取得方式: 既存データがあれば直近の数ヶ月分のみ取得し、既存値とマージする。
"""

import logging
import time
from datetime import date, datetime, timedelta, timezone as dt_timezone
from typing import Dict, List, Optional, Tuple

import requests
from django.db import transaction

from ..models import PriceObservation

logger = logging.getLogger(__name__)

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/118.0 Safari/537.36'
)
DEFAULT_TIMEOUT = 30
DEFAULT_HISTORY_YEARS = 20
# 差分取得時に直近何ヶ月分を取り直すか（最新月の終値が確定するまでの揺らぎを吸収）
REFRESH_BUFFER_MONTHS = 3

# (ticker_code, yahoo_symbol) のマッピング
TICKER_TO_SYMBOL = {
    PriceObservation.Ticker.NIKKEI: '^N225',
    PriceObservation.Ticker.SP500: '^GSPC',
}


class YahooFinanceError(Exception):
    """Yahoo Finance 取得失敗"""


def _to_month_start(timestamp: int) -> date:
    dt = datetime.fromtimestamp(timestamp, tz=dt_timezone.utc).date()
    return dt.replace(day=1)


def fetch_monthly_history(
    symbol: str,
    years: int = DEFAULT_HISTORY_YEARS,
    start_date: Optional[date] = None,
) -> List[Tuple[date, float]]:
    """指定銘柄の月次終値履歴を取得する。

    start_date が指定されていればそこから今日まで、未指定なら years 年分取得する。
    """
    end_ts = int(time.time())
    if start_date is not None:
        start_ts = int(
            datetime(start_date.year, start_date.month, start_date.day,
                     tzinfo=dt_timezone.utc).timestamp()
        )
    else:
        start_ts = end_ts - years * 366 * 86400

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
        raise YahooFinanceError(f"Yahoo Finance fetch failed for {symbol}: {exc}")

    chart = data.get('chart') or {}
    if chart.get('error'):
        raise YahooFinanceError(f"Yahoo Finance error: {chart['error']}")
    results = chart.get('result') or []
    if not results:
        raise YahooFinanceError(f"Yahoo Finance returned empty results for {symbol}")

    result = results[0]
    timestamps = result.get('timestamp') or []
    indicators = result.get('indicators') or {}
    quotes = (indicators.get('quote') or [{}])[0]
    closes = quotes.get('close') or []

    history: List[Tuple[date, float]] = []
    seen_months = set()
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        month_start = _to_month_start(ts)
        if month_start in seen_months:
            continue
        seen_months.add(month_start)
        history.append((month_start, float(close)))

    history.sort(key=lambda x: x[0])
    return history


def _resolve_price_fetch_start(ticker: str) -> Tuple[Optional[date], bool]:
    """既存データから差分取得の開始日を決める。
    既存があれば最新月から REFRESH_BUFFER_MONTHS 遡る。
    なければ None（フル取得）。
    """
    latest = (
        PriceObservation.objects
        .filter(ticker=ticker)
        .order_by('-observation_month')
        .values_list('observation_month', flat=True)
        .first()
    )
    if latest is None:
        return None, True
    year = latest.year
    month = latest.month - REFRESH_BUFFER_MONTHS
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1), False


def sync_price_history(
    ticker: str,
    years: int = DEFAULT_HISTORY_YEARS,
) -> dict:
    """1銘柄の月次終値を Yahoo Finance から差分取得し既存とマージする。"""
    symbol = TICKER_TO_SYMBOL[ticker]
    start_date, is_initial = _resolve_price_fetch_start(ticker)
    history = fetch_monthly_history(symbol, years=years, start_date=start_date)

    existing_qs = (
        PriceObservation.objects
        .filter(ticker=ticker)
        .values_list('observation_month', 'close_price')
    )
    merged: Dict[date, float] = {m: c for m, c in existing_qs}
    for month, close in history:
        merged[month] = close

    rows = [
        PriceObservation(
            ticker=ticker,
            observation_month=month,
            close_price=close,
        )
        for month, close in sorted(merged.items())
    ]

    with transaction.atomic():
        PriceObservation.objects.filter(ticker=ticker).delete()
        PriceObservation.objects.bulk_create(rows, batch_size=500)

    return {
        'ticker': ticker,
        'fetched': len(history),
        'stored': len(rows),
        'latest_month': rows[-1].observation_month if rows else None,
        'mode': 'initial' if is_initial else 'incremental',
    }


def sync_all_price_histories(years: int = DEFAULT_HISTORY_YEARS) -> dict:
    """全主要指数の月次価格を更新する。"""
    results: Dict[str, dict] = {'success': [], 'failed': []}
    for ticker in TICKER_TO_SYMBOL:
        try:
            summary = sync_price_history(ticker, years=years)
            results['success'].append(summary)
        except YahooFinanceError as exc:
            logger.warning("Price sync failed for %s: %s", ticker, exc)
            results['failed'].append({'ticker': ticker, 'error': str(exc)})
    return results


def get_monthly_close(ticker: str, month_start: date):
    """ある月（month_start を月初日とする月）の終値を返す。なければ None。"""
    obs = PriceObservation.objects.filter(
        ticker=ticker,
        observation_month=month_start,
    ).first()
    if obs is None:
        return None
    return obs.close_price


def get_next_month_return(ticker: str, month_start: date):
    """指定月の終値から翌月終値までの騰落率（%）を返す。"""
    from dateutil.relativedelta import relativedelta

    this_close = get_monthly_close(ticker, month_start)
    next_month = month_start + relativedelta(months=1)
    next_close = get_monthly_close(ticker, next_month)

    if this_close is None or next_close is None or this_close == 0:
        return None
    return (next_close - this_close) / this_close * 100.0
