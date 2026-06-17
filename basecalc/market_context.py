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
    "us2y": "ZT=F",
    "us10y": "^TNX",
    "vix": "^VIX",
    "vvix": "^VVIX",
    "sox": "^SOX",
    "semiconductor_etf": "SMH",
    "crude_oil": "CL=F",
    "hyg": "HYG",
    "lqd": "LQD",
}

CONTEXT_SYMBOLS_INTRADAY = {
    "nasdaq100_futures": "NQ=F",
    "sp500_futures": "ES=F",
    "dow_futures": "YM=F",
    "usd_jpy": "JPY=X",
    "us2y": "ZT=F",
    "vix": "^VIX",
    "vvix": "^VVIX",
    "sox": "^SOX",
    "semiconductor_etf": "SMH",
    "crude_oil": "CL=F",
    "hyg": "HYG",
    "lqd": "LQD",
}

PRICE_ACTION_FALLBACKS = {
    "sp500_futures": ("PA_GSPC_MOM20", "S&P500"),
    "dow_futures": ("PA_DJI_MOM20", "NYダウ"),
    "nasdaq100_futures": ("PA_IXIC_MOM20", "NASDAQ"),
}

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


def get_market_context_snapshot() -> dict:
    """無料データソースから外部市場コンテキストを取得する。失敗しても空 dict を返す。"""
    assets = {}
    for key, symbol in CONTEXT_SYMBOLS.items():
        snapshot = None
        if key in CONTEXT_SYMBOLS_INTRADAY:
            snapshot = fetch_intraday_context(symbol)
        if snapshot is None:
            snapshot = _fetch_context_symbol(symbol)
        if snapshot:
            assets[key] = snapshot
    if not assets:
        assets = _price_action_fallback_assets()
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
        lead_market = context.get("lead_market") or judge_nikkei_lead_context({})
        return {
            "risk_score": int(context.get("risk_score") or 0),
            "risk_label": context.get("risk_label") or "neutral",
            "components": context.get("components") or {},
            "evidence": context.get("evidence") or ["外部市場は中立寄り"],
            "lead_market": lead_market,
        }
    assets = (context or {}).get("assets") or context or {}
    if not isinstance(assets, dict) or not assets:
        return {
            "risk_score": 0,
            "risk_label": "neutral",
            "components": {},
            "evidence": ["外部市場データ待ち"],
            "lead_market": judge_nikkei_lead_context({}),
        }
    weights = {
        "nasdaq100_futures": 1.3,
        "sp500_futures": 1.1,
        "dow_futures": 0.7,
        "usd_jpy": 0.9,
        "us2y": -0.7,
        "us10y": -0.45,
        "vix": -1.1,
        "vvix": -0.5,
        "sox": 1.0,
        "semiconductor_etf": 0.8,
        "crude_oil": -0.25,
        "hyg": 0.5,
        "lqd": 0.3,
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
    lead_market = judge_nikkei_lead_context(_lead_components_from_assets(assets))
    return {
        "risk_score": risk_score,
        "risk_label": risk_label,
        "components": components,
        "evidence": evidence[:4],
        "lead_market": lead_market,
    }


def fetch_intraday_context(symbol: str, interval: str = "5m", range_: str = "1d"):
    try:
        response = requests.get(
            YAHOO_CHART_URL.format(symbol=symbol),
            params={"range": range_, "interval": interval},
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT_SEC,
        )
        response.raise_for_status()
        result = (response.json().get("chart", {}).get("result") or [None])[0]
    except (requests.RequestException, ValueError, AttributeError, IndexError):
        logger.info("basecalc intraday market context fetch failed for %s", symbol)
        return None
    if not result:
        return None
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes = [
        _to_float(value)
        for value in (quote.get("close") or [])
        if _to_float(value) is not None
    ]
    if len(closes) < 2:
        return None
    latest = closes[-1]
    previous = closes[-2]
    snapshot = {
        "symbol": symbol,
        "price": latest,
        "previous_close": previous,
        "change_pct": _pct_change(latest, closes[0]),
        "change_5m_pct": _pct_change(latest, closes[-2]),
        "change_15m_pct": _window_pct_change(closes, 3),
        "change_1h_pct": _window_pct_change(closes, 12),
        "source": "yahoo_intraday",
        "fetched_at": timezone.now(),
    }
    if symbol in ("ZT=F", "^IRX", "^TNX"):
        snapshot["change_1h_bp"] = (latest - closes[-min(len(closes), 12)]) * 100.0
    return snapshot


def judge_nikkei_lead_context(components):
    components = components or {}
    nq_15m = _to_float(components.get("nq_15m")) or 0.0
    es_15m = _to_float(components.get("es_15m")) or 0.0
    usd_jpy_15m = _to_float(components.get("usd_jpy_15m")) or 0.0
    vix_15m = _to_float(components.get("vix_15m")) or 0.0
    us2y_1h_bp = _to_float(components.get("us2y_1h_bp")) or 0.0
    nq_1h = _to_float(components.get("nq_1h")) or 0.0

    risk_on = nq_15m > 0 and es_15m > 0 and usd_jpy_15m > 0 and vix_15m <= 0
    risk_off = nq_15m < 0 and es_15m < 0 and vix_15m > 0
    policy_headwind = us2y_1h_bp > 5 and nq_1h < 0
    yen_headwind = usd_jpy_15m < -0.2
    alerts = []
    if yen_headwind:
        alerts.append("円高が日経先物の上値を抑制")
    if policy_headwind:
        alerts.append("米2年金利上昇とNASDAQ先物下落が政策逆風")
    if risk_off:
        alerts.append("NQ先物・ES先物・VIXがリスクオフ方向")
    if risk_on:
        alerts.append("NQ先物・ES先物・ドル円・VIXがリスクオン方向")
    if not alerts:
        alerts.append("先行マーケットは中立寄り")
    validation = _lead_lag_validation_metrics(
        {
            "risk_on": risk_on,
            "risk_off": risk_off,
            "policy_headwind": policy_headwind,
            "yen_headwind": yen_headwind,
        },
        components,
    )
    return {
        "summary": "先行マーケット: " + alerts[0],
        "risk_on": risk_on,
        "risk_off": risk_off,
        "policy_headwind": policy_headwind,
        "yen_headwind": yen_headwind,
        "alerts": alerts,
        "components": components,
        **validation,
    }


def _lead_components_from_assets(assets):
    def pct(key, field):
        return _to_float((assets.get(key) or {}).get(field)) or 0.0

    return {
        "nq_15m": pct("nasdaq100_futures", "change_15m_pct"),
        "nq_1h": pct("nasdaq100_futures", "change_1h_pct"),
        "es_15m": pct("sp500_futures", "change_15m_pct"),
        "usd_jpy_15m": pct("usd_jpy", "change_15m_pct"),
        "vix_15m": pct("vix", "change_15m_pct"),
        "us2y_1h_bp": _to_float((assets.get("us2y") or {}).get("change_1h_bp")) or 0.0,
    }


def _lead_lag_validation_metrics(flags, components):
    monthly = _historical_lead_lag_validation()
    if monthly.get("sample_count", 0) >= 5:
        return monthly

    lead_strength = 0
    if flags.get("risk_on") or flags.get("risk_off"):
        lead_strength += 35
    if flags.get("policy_headwind"):
        lead_strength += 25
    if flags.get("yen_headwind"):
        lead_strength += 20
    lead_strength += min(
        20,
        int(
            abs(_to_float(components.get("nq_15m")) or 0) * 10
            + abs(_to_float(components.get("es_15m")) or 0) * 8
            + abs(_to_float(components.get("usd_jpy_15m")) or 0) * 20
            + abs(_to_float(components.get("vix_15m")) or 0) * 4
        ),
    )
    score = max(0, min(100, lead_strength))
    hit_rate = round(0.50 + (score / 100.0) * 0.20, 3)
    false_signal_rate = round(1.0 - hit_rate, 3)
    return {
        "lead_lag_score": score,
        "hit_rate": hit_rate,
        "false_signal_rate": false_signal_rate,
        "validation_status": "検証履歴不足のため現在シグナルで暫定評価",
        "validation_month": timezone.localdate().replace(day=1).isoformat(),
        "validation_sample_count": monthly.get("sample_count", 0),
    }


def _historical_lead_lag_validation():
    try:
        from .models import PredictionOutcome, WorldModelPrediction
    except Exception:
        return {"sample_count": 0}
    month_start = timezone.localdate().replace(day=1)
    rows = []
    try:
        predictions = WorldModelPrediction.objects.filter(
            is_backtest=False,
            prediction_timestamp__date__gte=month_start,
        ).exclude(context={}).order_by("-prediction_timestamp")[:200]
        prediction_ids = [prediction.id for prediction in predictions]
        outcomes = {
            outcome.prediction_id: outcome
            for outcome in PredictionOutcome.objects.filter(
                prediction_id__in=prediction_ids,
                horizon="1d",
            )
        }
    except Exception:
        return {"sample_count": 0}
    for prediction in predictions:
        outcome = outcomes.get(prediction.id)
        if outcome is None:
            continue
        signal = _lead_signal((prediction.context or {}).get("lead_market") or {})
        if signal == 0:
            continue
        realized = _to_float(outcome.realized_return_pct)
        if realized is None:
            continue
        rows.append((signal, realized))
    if not rows:
        return {"sample_count": 0}
    hits = sum(
        1
        for signal, realized in rows
        if (signal > 0 and realized > 0) or (signal < 0 and realized < 0)
    )
    hit_rate = hits / len(rows)
    false_signal_rate = 1.0 - hit_rate
    score = max(0, min(100, int(round(50 + (hit_rate - 0.5) * 120))))
    return {
        "lead_lag_score": score,
        "hit_rate": round(hit_rate, 3),
        "false_signal_rate": round(false_signal_rate, 3),
        "validation_status": "月次検証あり",
        "validation_month": month_start.isoformat(),
        "validation_sample_count": len(rows),
        "sample_count": len(rows),
    }


def _lead_signal(lead_market):
    if not isinstance(lead_market, dict):
        return 0
    if lead_market.get("risk_on") and not (
        lead_market.get("policy_headwind") or lead_market.get("yen_headwind")
    ):
        return 1
    if (
        lead_market.get("risk_off")
        or lead_market.get("policy_headwind")
        or lead_market.get("yen_headwind")
    ):
        return -1
    return 0


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


def _price_action_fallback_assets():
    try:
        from macro.models import Observation
    except Exception:
        return {}
    assets = {}
    latest_date = None
    for key, (series_id, label) in PRICE_ACTION_FALLBACKS.items():
        try:
            observation = (
                Observation.objects.filter(indicator__fred_series_id=series_id)
                .order_by("-observation_date")
                .first()
            )
        except Exception:
            return {}
        if observation is None:
            continue
        latest_date = max(latest_date, observation.observation_date) if latest_date else observation.observation_date
        assets[key] = {
            "symbol": label,
            "price": None,
            "previous_close": None,
            "change_pct": round(float(observation.value or 0) / 5, 2),
            "source": "macro_price_action",
            "fetched_at": timezone.make_aware(
                timezone.datetime.combine(
                    observation.observation_date,
                    timezone.datetime.min.time(),
                ),
                timezone=timezone.get_current_timezone(),
            ),
        }
    if assets and latest_date:
        fetched_at = timezone.make_aware(
            timezone.datetime.combine(latest_date, timezone.datetime.min.time()),
            timezone=timezone.get_current_timezone(),
        )
        for asset in assets.values():
            asset["fetched_at"] = fetched_at
    return assets


def _label(key):
    return {
        "nasdaq100_futures": "NASDAQ100先物",
        "sp500_futures": "S&P500先物",
        "dow_futures": "NYダウ先物",
        "usd_jpy": "ドル円",
        "us2y": "米2年金利",
        "us10y": "米10年金利",
        "vix": "VIX",
        "vvix": "VVIX",
        "sox": "SOX",
        "semiconductor_etf": "SMH",
        "crude_oil": "原油",
        "hyg": "HYG",
        "lqd": "LQD",
    }.get(key, key)


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct_change(latest, previous):
    if previous in (None, 0) or latest is None:
        return None
    return (latest - previous) / abs(previous) * 100.0


def _window_pct_change(values, bars):
    if len(values) <= 1:
        return None
    index = max(0, len(values) - bars - 1)
    return _pct_change(values[-1], values[index])
