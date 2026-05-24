import logging

import requests
from django.utils import timezone

from .data_sources import normalize_chart_payload
from .nikkei_bias import HEADERS, REQUEST_TIMEOUT_SEC

logger = logging.getLogger(__name__)

CONTEXT_SYMBOLS = {
    "nasdaq100_futures": "NQ=F",
    "sp500_futures": "ES=F",
    "dow_futures": "YM=F",
    "usd_jpy": "JPY=X",
    "us10y": "^TNX",
    "vix": "^VIX",
    "sox": "^SOX",
    "crude_oil": "CL=F",
}

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


def get_market_context_snapshot() -> dict:
    """無料データソースから外部市場コンテキストを取得する。失敗しても空 dict を返す。"""
    assets = {}
    for key, symbol in CONTEXT_SYMBOLS.items():
        snapshot = _fetch_context_symbol(symbol)
        if snapshot:
            assets[key] = snapshot
    if not assets:
        return {}
    score = calculate_context_score({"assets": assets})
    return {
        "assets": assets,
        "fetched_at": timezone.now(),
        **score,
    }


def calculate_context_score(context: dict) -> dict:
    """日経先物に対して risk_on / risk_off / neutral の補助スコアを返す。"""
    if context and "risk_score" in context and "risk_label" in context:
        return {
            "risk_score": int(context.get("risk_score") or 0),
            "risk_label": context.get("risk_label") or "neutral",
            "components": context.get("components") or {},
            "evidence": context.get("evidence") or ["外部市場は中立寄り"],
        }
    assets = (context or {}).get("assets") or context or {}
    if not isinstance(assets, dict) or not assets:
        return {
            "risk_score": 0,
            "risk_label": "neutral",
            "components": {},
            "evidence": ["外部市場データ待ち"],
        }
    weights = {
        "nasdaq100_futures": 1.3,
        "sp500_futures": 1.1,
        "dow_futures": 0.7,
        "usd_jpy": 0.9,
        "us10y": -0.45,
        "vix": -1.1,
        "sox": 1.0,
        "crude_oil": -0.25,
    }
    components = {}
    evidence = []
    for key, asset in assets.items():
        if not isinstance(asset, dict):
            continue
        change = _to_float((asset or {}).get("change_pct"))
        if change is None:
            continue
        component = int(round(max(-12, min(12, change * weights.get(key, 0.4) * 4))))
        components[key] = component
        if component >= 5:
            evidence.append(f"{_label(key)}が日経先物には追い風")
        elif component <= -5:
            evidence.append(f"{_label(key)}が日経先物には重し")
    risk_score = max(-100, min(100, sum(components.values())))
    risk_label = "risk_on" if risk_score >= 15 else "risk_off" if risk_score <= -15 else "neutral"
    if not evidence:
        evidence.append("外部市場は中立寄り")
    return {
        "risk_score": risk_score,
        "risk_label": risk_label,
        "components": components,
        "evidence": evidence[:4],
    }


def _fetch_context_symbol(symbol):
    try:
        response = requests.get(
            YAHOO_CHART_URL.format(symbol=symbol),
            params={"range": "5d", "interval": "1d"},
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT_SEC,
        )
        response.raise_for_status()
        snapshot = normalize_chart_payload(
            response.json(),
            symbol,
            timeframe="1d",
            interval="1d",
        )
    except (requests.RequestException, ValueError):
        logger.info("basecalc market context fetch failed for %s", symbol)
        return None
    if not snapshot:
        return None
    return {
        "symbol": symbol,
        "price": snapshot.get("price"),
        "previous_close": snapshot.get("previous_close"),
        "change_pct": snapshot.get("change_pct"),
        "source": "yahoo",
        "fetched_at": timezone.now(),
    }


def _label(key):
    return {
        "nasdaq100_futures": "NASDAQ100先物",
        "sp500_futures": "S&P500先物",
        "dow_futures": "NYダウ先物",
        "usd_jpy": "ドル円",
        "us10y": "米10年金利",
        "vix": "VIX",
        "sox": "SOX",
        "crude_oil": "原油",
    }.get(key, key)


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
