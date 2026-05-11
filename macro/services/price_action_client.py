"""Yahoo Finance から日次価格を取得し、価格アクション指標を算出する。

主要指数（^GSPC, ^N225, ^DJI, ^IXIC）から以下の派生指標を計算する。
- DD200 : 200日移動平均からの乖離率（%）
- DD52W : 52週高値からの下落率（%、負値ほど深い）
- MOM20 : 20営業日リターン（%）

series_id は `PA_<SYMBOL>_<METRIC>` 形式（例: PA_GSPC_DD200）。

クラッシュ警戒度サブスコアと指標サマリの両方で利用するため、Observation テーブルに
時系列で保存する。日次値のうち、最終的に保存するのは過去 2 年分程度。
"""

import logging
import time
from datetime import date, datetime, timedelta, timezone as dt_timezone
from typing import Dict, List, Tuple

import requests

logger = logging.getLogger(__name__)

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/118.0 Safari/537.36'
)
DEFAULT_TIMEOUT = 30

# DD52W に必要な約 1 年（252 営業日）と 200DMA に必要な 200 営業日を考慮し、
# 派生値を遡って算出するために生データは 3 年分取得する。
DAILY_HISTORY_DAYS = 365 * 3

SMA_WINDOW = 200
HIGH_WINDOW = 252
MOM_WINDOW = 20

# Indicator.fred_series_id に使う `PA_<SYMBOL>_<METRIC>` のシンボル部分 → Yahoo シンボル
SYMBOL_MAP: Dict[str, str] = {
    'GSPC': '^GSPC',
    'N225': '^N225',
    'DJI': '^DJI',
    'IXIC': '^IXIC',
}

METRICS = ('DD200', 'DD52W', 'MOM20')


class PriceActionError(Exception):
    """価格アクション取得・計算の失敗"""


# プロセス内キャッシュ。同一 sync 内で同じシンボルを複数回 fetch しないようにする。
_DAILY_CACHE: Dict[str, List[Tuple[date, float]]] = {}


def clear_cache() -> None:
    """テストや手動再取得用にキャッシュを破棄する。"""
    _DAILY_CACHE.clear()


def _fetch_daily_history(symbol: str) -> List[Tuple[date, float]]:
    """Yahoo Finance から symbol の日次終値を 3 年分取得し、(date, close) のソート済みリストを返す。"""
    cached = _DAILY_CACHE.get(symbol)
    if cached is not None:
        return cached

    end_ts = int(time.time())
    start_ts = end_ts - DAILY_HISTORY_DAYS * 86400

    params = {
        'period1': start_ts,
        'period2': end_ts,
        'interval': '1d',
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
        raise PriceActionError(f"Yahoo daily fetch failed for {symbol}: {exc}")

    chart = data.get('chart') or {}
    if chart.get('error'):
        raise PriceActionError(f"Yahoo error for {symbol}: {chart['error']}")
    results = chart.get('result') or []
    if not results:
        raise PriceActionError(f"Yahoo returned empty results for {symbol}")

    result = results[0]
    timestamps = result.get('timestamp') or []
    quotes = ((result.get('indicators') or {}).get('quote') or [{}])[0]
    closes = quotes.get('close') or []

    history: List[Tuple[date, float]] = []
    seen_dates = set()
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        d = datetime.fromtimestamp(ts, tz=dt_timezone.utc).date()
        if d in seen_dates:
            continue
        seen_dates.add(d)
        history.append((d, float(close)))

    history.sort(key=lambda x: x[0])
    _DAILY_CACHE[symbol] = history
    return history


def _compute_dd200(closes: List[float]) -> List[float]:
    """終値列から 200 日線乖離率（%）の列を返す。先頭 199 件は None。"""
    result: List[float] = []
    rolling_sum = 0.0
    for i, c in enumerate(closes):
        rolling_sum += c
        if i >= SMA_WINDOW:
            rolling_sum -= closes[i - SMA_WINDOW]
        if i + 1 < SMA_WINDOW:
            result.append(None)
            continue
        sma = rolling_sum / SMA_WINDOW
        if sma == 0:
            result.append(None)
        else:
            result.append((c - sma) / sma * 100.0)
    return result


def _compute_dd52w(closes: List[float]) -> List[float]:
    """終値列から 52 週高値（252 営業日）からの下落率（%、負値）の列を返す。"""
    result: List[float] = []
    for i, c in enumerate(closes):
        start = max(0, i - HIGH_WINDOW + 1)
        window_high = max(closes[start:i + 1])
        if window_high == 0:
            result.append(None)
        else:
            result.append((c - window_high) / window_high * 100.0)
    return result


def _compute_mom20(closes: List[float]) -> List[float]:
    """終値列から 20 営業日リターン（%）の列を返す。先頭 20 件は None。"""
    result: List[float] = []
    for i, c in enumerate(closes):
        if i < MOM_WINDOW:
            result.append(None)
            continue
        past = closes[i - MOM_WINDOW]
        if past == 0:
            result.append(None)
        else:
            result.append((c - past) / past * 100.0)
    return result


def _parse_series_id(series_id: str) -> Tuple[str, str]:
    """`PA_<SYMBOL>_<METRIC>` を (symbol_key, metric) に分解する。"""
    if not series_id.startswith('PA_'):
        raise PriceActionError(f"Invalid PA series id: {series_id}")
    parts = series_id.split('_')
    if len(parts) != 3:
        raise PriceActionError(f"Invalid PA series id format: {series_id}")
    symbol_key, metric = parts[1], parts[2]
    if symbol_key not in SYMBOL_MAP:
        raise PriceActionError(f"Unknown symbol key in PA series: {series_id}")
    if metric not in METRICS:
        raise PriceActionError(f"Unknown metric in PA series: {series_id}")
    return symbol_key, metric


def fetch_observations(
    series_id: str,
    observation_start: 'date | None' = None,
    observation_end: 'date | None' = None,
) -> List[Tuple[date, float]]:
    """`PA_<SYMBOL>_<METRIC>` 形式の派生指標を計算して (date, value) リストで返す。"""
    symbol_key, metric = _parse_series_id(series_id)
    history = _fetch_daily_history(SYMBOL_MAP[symbol_key])
    if not history:
        return []

    dates = [d for d, _ in history]
    closes = [c for _, c in history]

    if metric == 'DD200':
        values = _compute_dd200(closes)
    elif metric == 'DD52W':
        values = _compute_dd52w(closes)
    else:
        values = _compute_mom20(closes)

    out: List[Tuple[date, float]] = []
    for d, v in zip(dates, values):
        if v is None:
            continue
        if observation_start is not None and d < observation_start:
            continue
        if observation_end is not None and d > observation_end:
            continue
        out.append((d, v))
    return out
