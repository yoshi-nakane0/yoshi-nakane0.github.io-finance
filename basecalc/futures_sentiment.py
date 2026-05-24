import logging
import csv
import datetime

import requests
from django.utils import timezone

from .nikkei_bias import HEADERS, REQUEST_TIMEOUT_SEC
from .data_sources import normalize_chart_payload, snapshot_from_quote_row
from .data_quality import evaluate_snapshot_quality
from .instrument import normalize_instrument

logger = logging.getLogger(__name__)

YAHOO_CHART_URLS = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
    "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}",
)
NIKKEI_FUTURES_SYMBOL = "NIY=F"
STOOQ_QUOTE_URL = "https://stooq.com/q/l/"
STOOQ_FALLBACK_SYMBOLS = ("nk.f", "^nkx")
YAHOO_DAILY_CONFIG = ("6mo", "1d", "1d")


def _to_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct_change(current, previous):
    current = _to_float(current)
    previous = _to_float(previous)
    if current is None or previous in (None, 0):
        return None
    return ((current - previous) / previous) * 100.0


def _clean_numbers(values):
    return [
        float(value)
        for value in values or []
        if isinstance(value, (int, float)) and value > 0
    ]


def get_nikkei_futures_snapshot(symbol=NIKKEI_FUTURES_SYMBOL):
    snapshot = _get_yahoo_chart_snapshot(symbol, *YAHOO_DAILY_CONFIG)
    if snapshot is None:
        fallback = _get_stooq_snapshot()
        if fallback:
            return _attach_snapshot_quality(fallback)
        return None

    snapshot["timeframes"] = {"1d": dict(snapshot)}
    return _attach_snapshot_quality(snapshot)


def _get_yahoo_chart_snapshot(symbol, range_value, interval, timeframe):
    params = {
        "range": range_value,
        "interval": interval,
        "includePrePost": "true",
    }
    payload = None
    last_error = None
    for url in YAHOO_CHART_URLS:
        try:
            response = requests.get(
                url.format(symbol=symbol),
                params=params,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT_SEC,
            )
            response.raise_for_status()
            payload = response.json()
            break
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
    if payload is None:
        logger.warning("Nikkei futures fetch failed: %s", last_error)
        return None

    snapshot = normalize_chart_payload(
        payload,
        symbol,
        timeframe=timeframe,
        interval=interval,
    )
    if snapshot is None:
        return None
    snapshot["fetched_at"] = timezone.now()
    instrument = normalize_instrument(symbol, "yahoo")
    snapshot["instrument_key"] = instrument["instrument_key"]
    snapshot["instrument_type"] = instrument["instrument_type"]
    snapshot["fallback_used"] = False
    return snapshot


def _get_stooq_snapshot():
    today = datetime.date.today().isoformat()
    stale_candidate = None
    for symbol in STOOQ_FALLBACK_SYMBOLS:
        try:
            response = requests.get(
                STOOQ_QUOTE_URL,
                params={
                    "s": symbol,
                    "f": "sd2t2ohlcv",
                    "h": "",
                    "e": "csv",
                },
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT_SEC,
            )
            response.raise_for_status()
        except requests.RequestException:
            continue
        rows = list(csv.DictReader(response.text.splitlines()))
        if not rows:
            continue
        row = rows[0]
        snapshot = snapshot_from_quote_row(row, symbol, today)
        if snapshot is None:
            continue
        snapshot["fetched_at"] = timezone.now()
        snapshot["fallback_used"] = True
        instrument = normalize_instrument(symbol, "stooq")
        snapshot["instrument_key"] = instrument["instrument_key"]
        snapshot["instrument_type"] = instrument["instrument_type"]
        snapshot["timeframes"] = {"1d": dict(snapshot)}
        if not snapshot.get("is_stale"):
            return snapshot
        stale_candidate = stale_candidate or snapshot
    return stale_candidate


def _attach_snapshot_quality(snapshot):
    if not isinstance(snapshot, dict):
        return snapshot
    fetched_at = snapshot.get("fetched_at") or timezone.now()
    snapshot.setdefault("fetched_at", fetched_at)
    instrument = normalize_instrument(snapshot.get("symbol"), snapshot.get("source"))
    snapshot.setdefault("instrument_key", instrument["instrument_key"])
    snapshot.setdefault("instrument_type", instrument["instrument_type"])
    timeframes = snapshot.get("timeframes")
    if isinstance(timeframes, dict):
        for key, frame in timeframes.items():
            if not isinstance(frame, dict):
                continue
            frame.setdefault("symbol", snapshot.get("symbol"))
            frame.setdefault("source", snapshot.get("source"))
            frame.setdefault("fetched_at", fetched_at)
            frame.setdefault("fallback_used", snapshot.get("fallback_used", False))
            frame.setdefault("instrument_key", snapshot.get("instrument_key"))
            frame.setdefault("instrument_type", snapshot.get("instrument_type"))
            frame["quality"] = evaluate_snapshot_quality(frame)
    snapshot["quality"] = evaluate_snapshot_quality(snapshot)
    return snapshot


def calculate_futures_sentiment(
    price,
    fair_price_mid,
    fair_price_core_low,
    fair_price_core_high,
    fair_price_wide_low,
    fair_price_wide_high,
    market_snapshot=None,
):
    price = _to_float(price)
    fair_price_mid = _to_float(fair_price_mid)
    if price is None or price <= 0:
        return _empty_sentiment()

    snapshot = market_snapshot or {}
    closes = _clean_numbers(snapshot.get("closes"))
    previous_close = _to_float(snapshot.get("previous_close"))
    daily_change_pct = _to_float(snapshot.get("change_pct"))
    if daily_change_pct is None:
        daily_change_pct = _pct_change(price, previous_close)
    momentum_3d_pct = _pct_change(price, closes[-4]) if len(closes) >= 4 else None
    avg_abs_move_pct = _to_float(snapshot.get("avg_abs_move_pct")) or 0.9
    recent_high = _to_float(snapshot.get("recent_high")) or price
    recent_low = _to_float(snapshot.get("recent_low")) or price

    score = 0.0
    if daily_change_pct is not None:
        if daily_change_pct >= 0.7:
            score += 1
        elif daily_change_pct <= -0.7:
            score -= 1
    if momentum_3d_pct is not None:
        if momentum_3d_pct >= 1.0:
            score += 1
        elif momentum_3d_pct <= -1.0:
            score -= 1
    if fair_price_mid:
        score += 0.5 if price >= fair_price_mid else -0.5
    if price >= recent_high:
        score += 0.5
    elif price <= recent_low:
        score -= 0.5

    if score >= 1.5:
        sentiment_label = "上目線強め"
        sentiment_key = "bullish"
    elif score <= -1.5:
        sentiment_label = "下目線強め"
        sentiment_key = "bearish"
    else:
        sentiment_label = "中立"
        sentiment_key = "neutral"

    continuity_label = "方向感待ち"
    continuity_detail = "短期の勢いがまだ揃っていません"
    if daily_change_pct is not None and momentum_3d_pct is not None:
        same_up = daily_change_pct > 0 and momentum_3d_pct > 0
        same_down = daily_change_pct < 0 and momentum_3d_pct < 0
        if (same_up or same_down) and abs(momentum_3d_pct) >= 1.0:
            continuity_label = "継続しやすい"
            continuity_detail = "当日と数日方向が揃っています"
        elif abs(daily_change_pct) >= 1.5 and (
            momentum_3d_pct == 0
            or (daily_change_pct > 0) != (momentum_3d_pct > 0)
        ):
            continuity_label = "突発色が強い"
            continuity_detail = "当日の動きが先行しています"

    fair_price_core_low = _to_float(fair_price_core_low)
    fair_price_core_high = _to_float(fair_price_core_high)
    fair_price_wide_low = _to_float(fair_price_wide_low)
    fair_price_wide_high = _to_float(fair_price_wide_high)
    fair_gap_pct = _pct_change(price, fair_price_mid)

    if sentiment_key == "bullish":
        strategy_label = "買い戻し優勢"
        strategy_detail = "売りは深追いせず、押し目確認を優先"
    elif sentiment_key == "bearish":
        strategy_label = "戻り売り優勢"
        strategy_detail = "反発は上値の重さを確認"
    else:
        strategy_label = "様子見"
        strategy_detail = "レンジ上下どちらを抜けるか確認"

    if (
        fair_price_wide_high is not None
        and price > fair_price_wide_high
        and sentiment_key != "bullish"
    ):
        strategy_label = "戻り売り警戒"
        strategy_detail = "Anchor上限を大きく超え、上値追いは慎重"
    elif (
        fair_price_wide_low is not None
        and price < fair_price_wide_low
        and sentiment_key != "bearish"
    ):
        strategy_label = "買い戻し警戒"
        strategy_detail = "Anchor下限を下回り、売りの踏み上げに注意"

    target_step = max(250.0, price * max(avg_abs_move_pct, 0.6) / 100.0)
    upper_target = max(recent_high, price + target_step)
    lower_target = min(recent_low, price - target_step)

    return {
        "sentiment_label": sentiment_label,
        "sentiment_key": sentiment_key,
        "sentiment_score": round(score, 2),
        "continuity_label": continuity_label,
        "continuity_detail": continuity_detail,
        "strategy_label": strategy_label,
        "strategy_detail": strategy_detail,
        "upper_target": round(upper_target, 0),
        "lower_target": round(lower_target, 0),
        "daily_change_pct": round(daily_change_pct, 2)
        if daily_change_pct is not None
        else None,
        "momentum_3d_pct": round(momentum_3d_pct, 2)
        if momentum_3d_pct is not None
        else None,
        "fair_gap_pct": round(fair_gap_pct, 2) if fair_gap_pct is not None else None,
        "recent_high": round(recent_high, 0),
        "recent_low": round(recent_low, 0),
        "fair_price_core_low": fair_price_core_low,
        "fair_price_core_high": fair_price_core_high,
    }


def _empty_sentiment():
    return {
        "sentiment_label": "判定不可",
        "sentiment_key": "neutral",
        "sentiment_score": 0.0,
        "continuity_label": "価格待ち",
        "continuity_detail": "価格を更新すると判定できます",
        "strategy_label": "判定不可",
        "strategy_detail": "価格を更新すると判定できます",
        "upper_target": None,
        "lower_target": None,
        "daily_change_pct": None,
        "momentum_3d_pct": None,
        "fair_gap_pct": None,
        "recent_high": None,
        "recent_low": None,
        "fair_price_core_low": None,
        "fair_price_core_high": None,
    }
