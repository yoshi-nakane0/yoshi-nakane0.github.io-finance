"""Yahoo Finance から日次データを取得し、複数の派生指標を算出する。

対応する系列:
1. 主要指数の価格アクション（series_id: `PA_<SYMBOL>_<METRIC>`）
   主要指数（^GSPC, ^N225, ^DJI, ^IXIC）から以下の派生指標を計算する。
   - DD200 : 200日移動平均からの乖離率（%）
   - DD52W : 52週高値からの下落率（%、負値ほど深い）
   - MOM20 : 20営業日リターン（%）

2. 単発の Yahoo シンボル（series_id: 任意の独自ID）
   ^MOVE などを生値のまま観測値として返す。

3. 比率系（series_id: 任意の独自ID）
   VIX_VIX3M_RATIO のように 2 つの Yahoo シンボルの比を返す。

すべて Observation テーブルに時系列で保存し、クラッシュ警戒度サブスコアと指標サマリの
両方で利用する。
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

# 通常の差分更新では直近数年で十分。full-history 時は observation_start に合わせて
# Yahoo の取得開始日を広げる。
DAILY_HISTORY_DAYS = 365 * 3
WARMUP_DAYS = 365

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

# 単発系列: series_id → Yahoo シンボル。価格そのものを観測値として返す。
RAW_SYMBOL_MAP: Dict[str, str] = {
    'MOVE_INDEX': '^MOVE',
}

# 比率系列: series_id → (分子の Yahoo シンボル, 分母の Yahoo シンボル)
RATIO_DEFS: Dict[str, Tuple[str, str]] = {
    'VIX_VIX3M_RATIO': ('^VIX', '^VIX3M'),
}


class PriceActionError(Exception):
    """価格アクション取得・計算の失敗"""


# プロセス内キャッシュ。同一 sync 内で同じシンボルを複数回 fetch しないようにする。
_DAILY_CACHE: Dict[Tuple[str, date], List[Tuple[date, float]]] = {}


def clear_cache() -> None:
    """テストや手動再取得用にキャッシュを破棄する。"""
    _DAILY_CACHE.clear()


def _fetch_daily_history(
    symbol: str,
    observation_start: 'date | None' = None,
) -> List[Tuple[date, float]]:
    """Yahoo Finance から symbol の日次終値を取得し、(date, close) のソート済みリストを返す。"""
    cache_start = observation_start or date.min
    cache_key = (symbol, cache_start)
    cached = _DAILY_CACHE.get(cache_key)
    if cached is not None:
        return cached

    end_ts = int(time.time())
    if observation_start is not None:
        fetch_start = observation_start - timedelta(days=WARMUP_DAYS)
        start_ts = int(
            datetime(
                fetch_start.year,
                fetch_start.month,
                fetch_start.day,
                tzinfo=dt_timezone.utc,
            ).timestamp()
        )
    else:
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
    _DAILY_CACHE[cache_key] = history
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


def _within_range(d: date, start, end) -> bool:
    if start is not None and d < start:
        return False
    if end is not None and d > end:
        return False
    return True


def _fetch_price_action(
    series_id: str,
    start: 'date | None',
    end: 'date | None',
) -> List[Tuple[date, float]]:
    symbol_key, metric = _parse_series_id(series_id)
    history = _fetch_daily_history(SYMBOL_MAP[symbol_key], start)
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

    return [
        (d, v) for d, v in zip(dates, values)
        if v is not None and _within_range(d, start, end)
    ]


def _fetch_raw_symbol(
    series_id: str,
    start: 'date | None',
    end: 'date | None',
) -> List[Tuple[date, float]]:
    symbol = RAW_SYMBOL_MAP[series_id]
    history = _fetch_daily_history(symbol, start)
    return [(d, v) for d, v in history if _within_range(d, start, end)]


def _fetch_ratio(
    series_id: str,
    start: 'date | None',
    end: 'date | None',
) -> List[Tuple[date, float]]:
    num_symbol, den_symbol = RATIO_DEFS[series_id]
    num_history = _fetch_daily_history(num_symbol, start)
    den_history = _fetch_daily_history(den_symbol, start)
    den_lookup = dict(den_history)

    out: List[Tuple[date, float]] = []
    for d, num in num_history:
        den = den_lookup.get(d)
        if den is None or den == 0:
            continue
        if not _within_range(d, start, end):
            continue
        out.append((d, num / den))
    return out


def fetch_observations(
    series_id: str,
    observation_start: 'date | None' = None,
    observation_end: 'date | None' = None,
) -> List[Tuple[date, float]]:
    """series_id 種別に応じて Yahoo Finance から派生値・生値・比率を取得する。"""
    if series_id.startswith('PA_'):
        return _fetch_price_action(series_id, observation_start, observation_end)
    if series_id in RAW_SYMBOL_MAP:
        return _fetch_raw_symbol(series_id, observation_start, observation_end)
    if series_id in RATIO_DEFS:
        return _fetch_ratio(series_id, observation_start, observation_end)
    raise PriceActionError(f"Unknown yfinance_daily series id: {series_id}")
