from datetime import datetime, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.utils import timezone

from .indicators import (
    calculate_adx,
    calculate_atr,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_macd,
    calculate_pivots,
    calculate_rsi,
    calculate_vwap,
    detect_gap,
    detect_price_structure,
    latest,
)
from .scoring import (
    calculate_change_zscore,
    calculate_continuation_score,
    calculate_sentiment_score,
    calculate_shock_score,
    shock_label,
)
from .similarity import find_similar_cases
from .targets import build_targets

JST = ZoneInfo("Asia/Tokyo")


def build_world_model(price, market_snapshot=None):
    price = _to_float(price)
    snapshot = market_snapshot or {}
    if price is None or price <= 0:
        return empty_world_model()

    daily_snapshot = _snapshot_for_timeframe(snapshot, "1d")
    ohlcv = _normalize_ohlcv(price, daily_snapshot)
    intraday = build_intraday_context(price, snapshot)
    features = build_features(price, daily_snapshot, ohlcv)
    features.update(intraday["features"])
    first_score = calculate_sentiment_score(features)
    direction = _direction_from_score(first_score["sentiment_score"])
    features["sentiment_score"] = first_score["sentiment_score"]
    similar_summary = find_similar_cases(features, ohlcv)
    sentiment = calculate_sentiment_score(features, similar_summary)
    direction = _direction_from_score(sentiment["sentiment_score"])
    features["sentiment_score"] = sentiment["sentiment_score"]

    continuation_score = calculate_continuation_score(features, direction)
    shock_score = calculate_shock_score(features)
    targets = build_targets(features, similar_summary)
    state_key, state_label, phase_label = classify_state(
        features,
        direction,
        continuation_score,
        shock_score,
    )
    confidence = classify_confidence(
        sentiment["sentiment_score"],
        continuation_score,
        shock_score,
        similar_summary,
        len(ohlcv["closes"]),
    )
    invalidation_price = _invalidation_price(direction, targets, price)
    evidence = build_evidence(features, similar_summary, direction)
    main_scenario = build_main_scenario(direction, targets)
    sub_scenario = build_sub_scenario(direction, invalidation_price)
    chart_ohlcv = intraday["chart_ohlcv"] or ohlcv
    chart_timeframe = intraday["chart_timeframe"] or "1d"
    chart_points = build_chart_points(
        chart_ohlcv,
        features,
        targets,
        invalidation_price,
        timeframe=chart_timeframe,
    )
    last_updated = _format_last_updated(snapshot)
    stale_minutes = _stale_minutes(snapshot)

    return {
        "is_ready": True,
        "price": round(price, 0),
        "direction": direction,
        "direction_label": _direction_label(direction),
        "strength_label": _strength_label(sentiment["sentiment_score"]),
        "state_key": state_key,
        "state_label": state_label,
        "phase_label": phase_label,
        "sentiment_key": sentiment["sentiment_key"],
        "sentiment_label": sentiment["sentiment_label"],
        "sentiment_score": sentiment["sentiment_score"],
        "sentiment_score_abs": abs(sentiment["sentiment_score"]),
        "continuation_score": continuation_score,
        "shock_score": shock_score,
        "shock_label": shock_label(shock_score),
        "confidence": confidence,
        "main_scenario": main_scenario,
        "sub_scenario": sub_scenario,
        "invalidation_price": invalidation_price,
        "invalidation_display": _price_display(invalidation_price),
        "invalidation_text": _invalidation_text(direction, invalidation_price),
        "upside_targets": targets["upside"],
        "downside_targets": targets["downside"],
        "target_1_display": _target_display(direction, targets, 0),
        "target_2_display": _target_display(direction, targets, 1),
        "similar_summary": similar_summary,
        "evidence": evidence[:6],
        "features": _json_safe_features(features),
        "chart_points": chart_points,
        "timeframe_summary": intraday["summary"],
        "last_updated_display": last_updated,
        "is_stale": bool(snapshot.get("is_stale")) or stale_minutes > 15,
        "stale_minutes": stale_minutes,
        "data_warning": _data_warning(snapshot, stale_minutes),
        "components": sentiment["components"],
    }


def build_features(price, snapshot, ohlcv):
    closes = ohlcv["closes"]
    highs = ohlcv["highs"]
    lows = ohlcv["lows"]
    opens = ohlcv["opens"]
    volumes = ohlcv["volumes"]

    ema5 = calculate_ema(closes, 5)
    ema20 = calculate_ema(closes, 20)
    ema60 = calculate_ema(closes, 60)
    ema200 = calculate_ema(closes, 200)
    rsi14 = calculate_rsi(closes, 14)
    macd = calculate_macd(closes)
    atr14 = calculate_atr(highs, lows, closes, 14)
    adx = calculate_adx(highs, lows, closes, 14)
    bands = calculate_bollinger_bands(closes)
    vwap = calculate_vwap(ohlcv)
    pivots = calculate_pivots(highs, lows, closes)
    structure = detect_price_structure(ohlcv)
    gap = detect_gap(ohlcv)

    latest_atr = latest(atr14)
    atr_window = [value for value in atr14[-20:] if value]
    atr_ratio = latest_atr / (sum(atr_window) / len(atr_window)) if latest_atr and atr_window else None
    daily_change_pct = snapshot.get("change_pct")
    if daily_change_pct is None and len(closes) >= 2:
        daily_change_pct = _pct(price, closes[-2])
    close_changes = [_pct(current, previous) for previous, current in zip(closes, closes[1:])]

    features = {
        "symbol": snapshot.get("symbol") or "NIY=F",
        "source": snapshot.get("source") or "cache",
        "price": price,
        "open": opens[-1] if opens else None,
        "high": highs[-1] if highs else None,
        "low": lows[-1] if lows else None,
        "close": closes[-1] if closes else price,
        "volume": volumes[-1] if volumes else None,
        "previous_close": snapshot.get("previous_close") or (closes[-2] if len(closes) >= 2 else None),
        "previous_high": highs[-2] if len(highs) >= 2 else None,
        "previous_low": lows[-2] if len(lows) >= 2 else None,
        "recent_high": max(highs[-10:] or [price]),
        "recent_low": min(lows[-10:] or [price]),
        "high_20d": max(highs[-20:] or [price]),
        "low_20d": min(lows[-20:] or [price]),
        "ema5": latest(ema5),
        "ema20": latest(ema20),
        "ema60": latest(ema60),
        "ema200": latest(ema200),
        "vwap": latest(vwap),
        "rsi14": latest(rsi14),
        "macd": latest(macd["macd"]),
        "macd_signal": latest(macd["signal"]),
        "macd_histogram": latest(macd["histogram"]),
        "adx14": latest(adx["adx"]),
        "dmi_plus": latest(adx["plus_di"]),
        "dmi_minus": latest(adx["minus_di"]),
        "atr14": latest_atr,
        "atr_ratio": atr_ratio,
        "bb_upper": latest(bands["upper"]),
        "bb_mid": latest(bands["mid"]),
        "bb_lower": latest(bands["lower"]),
        "bb_width_pct": latest(bands["width"]),
        "pivots": pivots,
        "structure_key": structure["key"],
        "structure_label": structure["label"],
        "structure_bias": structure["bias"],
        "gap_key": gap["key"],
        "gap_pct": gap["gap_pct"],
        "daily_change_pct": daily_change_pct,
        "daily_change_z": calculate_change_zscore(close_changes[:-1], daily_change_pct),
        "change_1d_pct": daily_change_pct,
        "change_3d_pct": _change_from(closes, price, 3),
        "change_5d_pct": _change_from(closes, price, 5),
        "change_20d_pct": _change_from(closes, price, 20),
        "distance_recent_high_pct": _pct(price, max(highs[-10:] or [price])),
        "distance_recent_low_pct": _pct(price, min(lows[-10:] or [price])),
        "ema5_gap_pct": _pct(price, latest(ema5)),
        "ema20_gap_pct": _pct(price, latest(ema20)),
        "ema60_gap_pct": _pct(price, latest(ema60)),
        "vwap_gap_pct": _pct(price, latest(vwap)),
        "shock_candidate": abs(calculate_change_zscore(close_changes[:-1], daily_change_pct)) >= 2,
    }
    return features


def build_intraday_context(price, snapshot):
    timeframe_data = snapshot.get("timeframes") if isinstance(snapshot, dict) else None
    timeframe_data = timeframe_data or {}
    summary = []
    features = {
        "change_1h_pct": None,
        "change_4h_pct": None,
        "intraday_impulse_z": 0,
        "intraday_trend_bias": 0,
        "multi_timeframe_alignment": 0,
    }
    chart_ohlcv = None
    chart_timeframe = None
    direction_votes = []

    for key in ("5m", "15m", "1h"):
        frame_snapshot = _snapshot_for_timeframe(snapshot, key)
        if not frame_snapshot or not frame_snapshot.get("closes"):
            continue
        frame_ohlcv = _normalize_ohlcv(price, frame_snapshot)
        closes = frame_ohlcv["closes"]
        if len(closes) < 2:
            continue
        periods_per_hour = {"5m": 12, "15m": 4, "1h": 1}[key]
        change_1h = _change_from(closes, price, periods_per_hour)
        change_4h = _change_from(closes, price, periods_per_hour * 4)
        changes = [_pct(current, previous) for previous, current in zip(closes, closes[1:])]
        impulse_z = abs(calculate_change_zscore(changes[:-1], changes[-1] if changes else None))
        ema5 = latest(calculate_ema(closes, 5))
        ema20 = latest(calculate_ema(closes, 20))
        trend_bias = 1 if ema5 and ema20 and ema5 > ema20 else -1 if ema5 and ema20 and ema5 < ema20 else 0
        if trend_bias:
            direction_votes.append(trend_bias)
        summary.append(
            {
                "key": key,
                "label": {"5m": "5分", "15m": "15分", "1h": "1時間"}[key],
                "change_1h_pct": _round(change_1h),
                "change_4h_pct": _round(change_4h),
                "impulse_z": _round(impulse_z),
                "trend_bias": trend_bias,
                "trend_label": "上向き" if trend_bias > 0 else "下向き" if trend_bias < 0 else "中立",
                "point_count": len(closes),
            }
        )
        if key == "1h":
            features["change_1h_pct"] = change_1h
            features["change_4h_pct"] = change_4h
        if impulse_z > (features.get("intraday_impulse_z") or 0):
            features["intraday_impulse_z"] = impulse_z
        if chart_ohlcv is None and key in ("15m", "1h") and len(closes) >= 10:
            chart_ohlcv = frame_ohlcv
            chart_timeframe = key

    if direction_votes:
        vote_sum = sum(direction_votes)
        features["intraday_trend_bias"] = 1 if vote_sum > 0 else -1 if vote_sum < 0 else 0
        features["multi_timeframe_alignment"] = abs(vote_sum) / len(direction_votes)
    return {
        "features": features,
        "summary": summary,
        "chart_ohlcv": chart_ohlcv,
        "chart_timeframe": chart_timeframe,
    }


def classify_state(features, direction, continuation_score, shock_score):
    rsi = features.get("rsi14")
    price = features.get("price")
    bb_upper = features.get("bb_upper")
    bb_lower = features.get("bb_lower")
    change_5d = features.get("change_5d_pct") or 0
    ema5 = features.get("ema5")
    ema20 = features.get("ema20")
    ema60 = features.get("ema60")

    if direction == "up":
        if rsi is not None and rsi >= 72 and bb_upper and price >= bb_upper * 0.995:
            return "exhaustion_top", "過熱・反落警戒", "利確・反落警戒"
        if shock_score >= 60 and continuation_score < 60:
            return "bull_impulse", "突発上昇", "ブレイク買い警戒"
        if change_5d < -0.8 and ema5 and price >= ema5:
            return "short_covering", "買い戻し", "買い戻し優勢"
        if ema5 and ema20 and ema60 and ema5 > ema20 > ema60:
            return "bull_trend_continuation", "上昇継続", "押し目買い優勢"
        return "dip_buy", "押し目買い", "押し目買い優勢"

    if direction == "down":
        if rsi is not None and rsi <= 28 and bb_lower and price <= bb_lower * 1.005:
            return "exhaustion_bottom", "売られすぎ・反発警戒", "反発警戒"
        if shock_score >= 60 and continuation_score < 60:
            return "bear_impulse", "突発下落", "ブレイク売り警戒"
        if ema5 and ema20 and ema60 and ema5 < ema20 < ema60:
            return "bear_trend_continuation", "下落継続", "戻り売り優勢"
        return "return_sell", "戻り売り", "戻り売り優勢"

    recent_high = features.get("recent_high")
    recent_low = features.get("recent_low")
    if recent_high and recent_low and price:
        range_width = recent_high - recent_low
        if range_width and min(recent_high - price, price - recent_low) / range_width <= 0.2:
            return "breakout_pending", "ブレイク待ち", "様子見"
    return "range_neutral", "レンジ中立", "様子見"


def classify_confidence(sentiment_score, continuation_score, shock_score, similar_summary, data_count):
    similar_accuracy = similar_summary.get("directional_accuracy") or 0
    if data_count < 25 or abs(sentiment_score) < 15 or shock_score >= 80:
        return "Low"
    if abs(sentiment_score) >= 40 and continuation_score >= 65 and similar_accuracy >= 0.6:
        return "High"
    return "Middle"


def build_evidence(features, similar_summary, direction):
    evidence = []
    ema5 = features.get("ema5")
    ema20 = features.get("ema20")
    ema60 = features.get("ema60")
    price = features.get("price")
    vwap = features.get("vwap")
    rsi = features.get("rsi14")
    macd = features.get("macd")
    signal = features.get("macd_signal")
    atr_ratio = features.get("atr_ratio")

    if ema5 and ema20 and ema60:
        if ema5 > ema20 > ema60:
            evidence.append("EMA5 > EMA20 > EMA60で短中期が上向き")
        elif ema5 < ema20 < ema60:
            evidence.append("EMA5 < EMA20 < EMA60で短中期が下向き")
        else:
            evidence.append("EMAの並びが揃わず方向は限定的")
    if price and vwap:
        evidence.append("現在値がVWAP上" if price >= vwap else "現在値がVWAP下")
    if rsi is not None:
        if 45 <= rsi <= 65:
            evidence.append(f"RSI14は{rsi:.1f}で過熱感は限定的")
        elif rsi > 65:
            evidence.append(f"RSI14は{rsi:.1f}で上昇過熱に注意")
        else:
            evidence.append(f"RSI14は{rsi:.1f}で下落過熱に注意")
    if macd is not None and signal is not None:
        evidence.append("MACDがSignal上" if macd >= signal else "MACDがSignal下")
    if atr_ratio is not None:
        evidence.append(f"ATR比率は{atr_ratio:.2f}で変動幅を確認")
    if features.get("intraday_impulse_z"):
        evidence.append(f"短時間足の突発度は{features['intraday_impulse_z']:.1f}")
    if features.get("multi_timeframe_alignment"):
        label = "上向き" if features.get("intraday_trend_bias") > 0 else "下向き"
        evidence.append(
            f"5分・15分・1時間の方向一致は{features['multi_timeframe_alignment']:.0%}で{label}"
        )
    if features.get("structure_label"):
        evidence.append(features["structure_label"])
    if similar_summary.get("case_count"):
        rate = similar_summary["up_rate"] if direction == "up" else similar_summary["down_rate"]
        label = "上昇" if direction == "up" else "下落"
        evidence.append(
            f"過去類似局面{similar_summary['case_count']}件の{label}率は{rate:.0%}"
        )
    while len(evidence) < 3:
        evidence.append("足数不足の指標は総合判定から除外")
    return evidence


def build_main_scenario(direction, targets):
    if direction == "up":
        first = _target_price(targets["upside"], 0)
        second = _target_price(targets["upside"], 1)
        return f"{_price_display(first)}から{_price_display(second)}方向を試しやすい"
    if direction == "down":
        first = _target_price(targets["downside"], 0)
        second = _target_price(targets["downside"], 1)
        return f"{_price_display(first)}から{_price_display(second)}方向を試しやすい"
    return "上下の重要価格帯を抜けるまで方向感は限定的"


def build_sub_scenario(direction, invalidation_price):
    if direction == "up":
        return f"{_price_display(invalidation_price)}を割ると上昇判定は弱まる"
    if direction == "down":
        return f"{_price_display(invalidation_price)}を超えると下落判定は弱まる"
    return "レンジ上限または下限を終値で抜けるか確認"


def build_chart_points(
    ohlcv,
    features,
    targets,
    invalidation_price,
    limit=100,
    timeframe="1d",
):
    closes = ohlcv["closes"][-limit:]
    opens = ohlcv["opens"][-limit:]
    highs = ohlcv["highs"][-limit:]
    lows = ohlcv["lows"][-limit:]
    timestamps = ohlcv["timestamps"][-limit:]
    ema5 = calculate_ema(ohlcv["closes"], 5)[-limit:]
    ema20 = calculate_ema(ohlcv["closes"], 20)[-limit:]
    ema60 = calculate_ema(ohlcv["closes"], 60)[-limit:]
    vwap = calculate_vwap(ohlcv)[-limit:]
    bands = calculate_bollinger_bands(ohlcv["closes"])
    bb_upper = bands["upper"][-limit:]
    bb_lower = bands["lower"][-limit:]
    points = []
    for index, close in enumerate(closes):
        points.append(
            {
                "time": _chart_time(
                    timestamps[index] if index < len(timestamps) else None,
                    index,
                    timeframe,
                ),
                "label": _chart_label(timestamps[index] if index < len(timestamps) else None, index),
                "open": _round(opens[index] if index < len(opens) else close),
                "high": _round(highs[index] if index < len(highs) else close),
                "low": _round(lows[index] if index < len(lows) else close),
                "close": _round(close),
                "ema5": _round(ema5[index] if index < len(ema5) else None),
                "ema20": _round(ema20[index] if index < len(ema20) else None),
                "ema60": _round(ema60[index] if index < len(ema60) else None),
                "vwap": _round(vwap[index] if index < len(vwap) else None),
                "bbUpper": _round(bb_upper[index] if index < len(bb_upper) else None),
                "bbLower": _round(bb_lower[index] if index < len(bb_lower) else None),
            }
        )
    return {
        "points": points,
        "timeframe": timeframe,
        "upsideTargets": targets["upside"][:2],
        "downsideTargets": targets["downside"][:2],
        "invalidation": invalidation_price,
        "recentHigh": features.get("recent_high"),
        "recentLow": features.get("recent_low"),
    }


def empty_world_model():
    return {
        "is_ready": False,
        "price": None,
        "direction": "neutral",
        "direction_label": "判定不可",
        "strength_label": "弱い",
        "state_key": "range_neutral",
        "state_label": "価格待ち",
        "phase_label": "様子見",
        "sentiment_key": "neutral",
        "sentiment_label": "価格データ待ち",
        "sentiment_score": 0,
        "sentiment_score_abs": 0,
        "continuation_score": 0,
        "shock_score": 0,
        "shock_label": "N/A",
        "confidence": "Low",
        "main_scenario": "価格更新後に判定します",
        "sub_scenario": "",
        "invalidation_price": None,
        "invalidation_display": "",
        "invalidation_text": "N/A",
        "upside_targets": [],
        "downside_targets": [],
        "target_1_display": "",
        "target_2_display": "",
        "similar_summary": {
            "case_count": 0,
            "up_rate": 0,
            "down_rate": 0,
            "range_rate": 0,
            "average_return_pct": 0,
            "target_t1_hit_rate": 0,
            "invalidation_rate": 0,
            "directional_accuracy": 0,
            "cases": [],
        },
        "evidence": ["価格データの取得後に判定します"],
        "features": {},
        "chart_points": {"points": [], "upsideTargets": [], "downsideTargets": []},
        "timeframe_summary": [],
        "last_updated_display": _format_last_updated({}),
        "is_stale": True,
        "stale_minutes": 0,
        "data_warning": "価格データの取得に失敗しました。現在の判定は前回取得データに基づいています。",
        "components": {},
    }


def _normalize_ohlcv(price, snapshot):
    closes = _positive_numbers(snapshot.get("closes"))
    highs = _positive_numbers(snapshot.get("highs"))
    lows = _positive_numbers(snapshot.get("lows"))
    opens = _positive_numbers(snapshot.get("opens"))
    volumes = _numbers(snapshot.get("volumes"))
    timestamps = list(snapshot.get("timestamps") or [])

    if not closes:
        previous_close = _to_float(snapshot.get("previous_close")) or price
        closes = [previous_close, price]
    if abs(closes[-1] - price) > 1:
        closes[-1] = price
    length = len(closes)
    highs = _fit_length(highs, closes, "high")
    lows = _fit_length(lows, closes, "low")
    opens = _fit_length(opens, closes, "open")
    volumes = _fit_length(volumes, [1.0 for _ in closes], "volume")
    highs[-1] = max(highs[-1], price)
    lows[-1] = min(lows[-1], price)
    if len(timestamps) < length:
        timestamps = [None] * (length - len(timestamps)) + timestamps
    return {
        "opens": opens[-length:],
        "highs": highs[-length:],
        "lows": lows[-length:],
        "closes": closes[-length:],
        "volumes": volumes[-length:],
        "timestamps": timestamps[-length:],
    }


def _snapshot_for_timeframe(snapshot, timeframe):
    if not isinstance(snapshot, dict):
        return {}
    if timeframe == "1d" and not snapshot.get("timeframes"):
        return snapshot
    timeframes = snapshot.get("timeframes") or {}
    frame = timeframes.get(timeframe)
    if not isinstance(frame, dict):
        return snapshot if timeframe == "1d" else {}
    merged = dict(frame)
    for key in ("symbol", "name", "source", "fetched_at", "is_stale"):
        if key not in merged and key in snapshot:
            merged[key] = snapshot[key]
    return merged


def _fit_length(values, closes, kind):
    if len(values) >= len(closes):
        return values[-len(closes) :]
    filled = []
    for close in closes:
        if kind == "high":
            filled.append(close)
        elif kind == "low":
            filled.append(close)
        elif kind == "open":
            filled.append(close)
        else:
            filled.append(1.0)
    start = len(filled) - len(values)
    if values:
        filled[start:] = values
    return filled


def _direction_from_score(score):
    if score >= 15:
        return "up"
    if score <= -15:
        return "down"
    return "neutral"


def _direction_label(direction):
    return {"up": "上昇優勢", "down": "下落優勢"}.get(direction, "中立")


def _strength_label(score):
    absolute = abs(score)
    if absolute >= 70:
        return "非常に強い"
    if absolute >= 40:
        return "強い"
    if absolute >= 15:
        return "やや強い"
    return "弱い"


def _invalidation_price(direction, targets, price):
    if direction == "up":
        return targets["invalidation"]["bullish"]
    if direction == "down":
        return targets["invalidation"]["bearish"]
    return None


def _invalidation_text(direction, invalidation_price):
    if invalidation_price is None:
        return "レンジ抜けを確認"
    if direction == "up":
        return f"{_price_display(invalidation_price)}割れで上昇判定を撤回"
    if direction == "down":
        return f"{_price_display(invalidation_price)}超えで下落判定を撤回"
    return "重要価格帯の終値突破を確認"


def _target_display(direction, targets, index):
    target_list = targets["upside"] if direction == "up" else targets["downside"]
    if direction == "neutral":
        target_list = targets["upside"]
    return _price_display(_target_price(target_list, index))


def _target_price(targets, index):
    if len(targets) <= index:
        return None
    return targets[index].get("price")


def _price_display(value):
    value = _to_float(value)
    if value is None:
        return "N/A"
    return f"{value:,.0f}"


def _format_last_updated(snapshot):
    timestamp = snapshot.get("fetched_at") or timezone.now()
    if isinstance(timestamp, str):
        try:
            timestamp = datetime.fromisoformat(timestamp)
        except ValueError:
            timestamp = timezone.now()
    if timezone.is_naive(timestamp):
        timestamp = timezone.make_aware(timestamp, timezone=dt_timezone.utc)
    return timezone.localtime(timestamp, JST).strftime("%Y-%m-%d %H:%M JST")


def _stale_minutes(snapshot):
    timestamp = snapshot.get("fetched_at")
    if not timestamp or isinstance(timestamp, str):
        return 0
    if timezone.is_naive(timestamp):
        timestamp = timezone.make_aware(timestamp, timezone=dt_timezone.utc)
    delta = timezone.now() - timestamp
    return max(0, int(delta.total_seconds() // 60))


def _data_warning(snapshot, stale_minutes):
    if not snapshot:
        return "価格データの取得に失敗しました。現在の判定は前回取得データに基づいています。"
    if snapshot.get("is_stale") or stale_minutes > 15:
        return "価格データが15分以上古い可能性があります。"
    return ""


def _chart_label(timestamp, index):
    if timestamp:
        try:
            return datetime.fromtimestamp(timestamp, JST).strftime("%m/%d")
        except (TypeError, ValueError, OSError):
            pass
    return str(index + 1)


def _chart_time(timestamp, index, timeframe):
    if timestamp:
        try:
            if timeframe == "1d":
                return datetime.fromtimestamp(timestamp, JST).strftime("%Y-%m-%d")
            return int(timestamp)
        except (TypeError, ValueError, OSError):
            pass
    if timeframe == "1d":
        return f"2000-01-{index + 1:02d}" if index < 28 else f"2000-02-{index - 27:02d}"
    return 946684800 + index * 60


def _json_safe_features(features):
    safe = {}
    for key, value in features.items():
        if isinstance(value, dict):
            safe[key] = {nested_key: _round(nested_value) for nested_key, nested_value in value.items()}
        elif isinstance(value, (int, float, str)) or value is None:
            safe[key] = _round(value) if isinstance(value, (int, float)) else value
    return safe


def _change_from(closes, price, periods):
    if len(closes) <= periods:
        return None
    return _pct(price, closes[-periods - 1])


def _pct(current, previous):
    current = _to_float(current)
    previous = _to_float(previous)
    if current is None or previous in (None, 0):
        return None
    return ((current - previous) / previous) * 100


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _positive_numbers(values):
    return [float(value) for value in values or [] if isinstance(value, (int, float)) and value > 0]


def _numbers(values):
    return [float(value) for value in values or [] if isinstance(value, (int, float))]


def _round(value):
    value = _to_float(value)
    return round(value, 4) if value is not None else None
