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
    sentiment_label,
    shock_label,
)
from .confidence import calculate_confidence_score
from .data_quality import evaluate_snapshot_quality
from .intermarket_technicals import build_us_index_technical_context
from .instrument import normalize_instrument
from .model_version import BASECALC_MODEL_VERSION
from .outcomes import (
    apply_sentiment_score_adjustment,
    confidence_adjustment_for_state,
)
from .similarity import find_similar_cases
from .state_machine import estimate_expected_returns, estimate_transition_probabilities
from .targets import build_targets
from .readiness import evaluate_indicator_validity, evaluate_world_model_readiness
from .scenario_engine import build_scenarios
from .signal_contract import build_basecalc_signal_contract
from .technical_regime import classify_technical_regime
from .technical_state import build_technical_state

JST = ZoneInfo("Asia/Tokyo")


def build_world_model(price, market_snapshot=None, intermarket_context=None, as_of=None):
    price = _to_float(price)
    snapshot = market_snapshot or {}
    intermarket_context = build_us_index_technical_context(intermarket_context or {})
    if price is None or price <= 0:
        data_quality = evaluate_snapshot_quality(snapshot)
        readiness = evaluate_world_model_readiness(
            price=price,
            snapshot=snapshot if isinstance(snapshot, dict) and snapshot else None,
            data_quality=data_quality,
            daily_ohlcv={},
        )
        return blocked_world_model(
            price,
            snapshot,
            data_quality,
            readiness,
            intermarket_context=intermarket_context,
        )

    daily_snapshot = _snapshot_for_timeframe(snapshot, "1d")
    data_quality = (snapshot.get("quality") if isinstance(snapshot, dict) else None) or evaluate_snapshot_quality(snapshot)
    ohlcv = _normalize_ohlcv(price, daily_snapshot, allow_synthetic=False)
    readiness = evaluate_world_model_readiness(
        price=price,
        snapshot=snapshot if isinstance(snapshot, dict) and snapshot else None,
        data_quality=data_quality,
        daily_ohlcv=ohlcv,
    )
    if readiness["level"] == "blocked":
        return blocked_world_model(
            price,
            snapshot,
            data_quality,
            readiness,
            ohlcv=ohlcv,
            intermarket_context=intermarket_context,
        )

    features = build_features(
        price,
        daily_snapshot,
        ohlcv,
        indicator_validity=readiness["indicator_validity"],
    )
    instrument = normalize_instrument(readiness.get("symbol"), readiness.get("source"))
    features.update(
        {
            "data_quality_score": data_quality["score"],
            "data_quality_level": data_quality["level"],
            "fallback_used": data_quality["fallback_used"],
            "instrument_key": readiness["instrument_key"],
            "instrument_type": readiness["instrument_type"],
            "source_symbol": readiness["symbol"],
            "source_name": readiness["source"],
            "data_quality": data_quality,
            "readiness_level": readiness["level"],
            "directional_allowed": readiness["directional_allowed"],
            "readiness_reason_codes": readiness["reason_codes"],
            "bar_counts": readiness["bar_counts"],
            "indicator_validity": readiness["indicator_validity"],
            "is_canonical_instrument": instrument.get("is_canonical"),
            "us_index_confirmation_score": intermarket_context["confirmation_score"],
            "us_index_confirmation_label": intermarket_context["confirmation_label"],
            "us_index_components": intermarket_context["components"],
        }
    )
    if readiness["level"] == "limited":
        return limited_world_model(
            price,
            snapshot,
            data_quality,
            readiness,
            features=features,
            ohlcv=ohlcv,
            intermarket_context=intermarket_context,
        )

    nikkei_features = {**features, "us_index_confirmation_score": 0}
    first_score = calculate_sentiment_score(nikkei_features)
    direction = _direction_from_score(first_score["sentiment_score"])
    features["sentiment_score"] = first_score["sentiment_score"]
    similar_summary = find_similar_cases(
        nikkei_features,
        ohlcv,
        instrument_key=readiness["instrument_key"],
        as_of=as_of,
        timeframe="1d",
    )
    sentiment = calculate_sentiment_score(nikkei_features, similar_summary)
    direction = _direction_from_score(sentiment["sentiment_score"])
    features["sentiment_score"] = sentiment["sentiment_score"]
    features["trend_score"] = sentiment["trend_score"]
    features["momentum_score"] = sentiment["momentum_score"]
    features["reversal_risk_score"] = sentiment["reversal_risk_score"]
    features["rebound_improvement_score"] = sentiment["rebound_improvement_score"]
    features["external_context_score"] = 0
    features["nikkei_technical_score"] = sentiment["sentiment_score"]

    continuation_score = calculate_continuation_score(features, direction)
    shock_score = calculate_shock_score(features)
    features["continuation_score"] = continuation_score
    features["shock_score"] = shock_score
    targets = build_targets(features, similar_summary)
    state_key, state_label, phase_label = classify_state(
        features,
        direction,
        continuation_score,
        shock_score,
    )
    technical_setup = classify_technical_regime(
        features,
        direction,
        state_key,
        intermarket_context,
    )
    performance_adjustment = confidence_adjustment_for_state(state_key)
    adjusted_score = apply_sentiment_score_adjustment(
        sentiment["sentiment_score"],
        performance_adjustment,
    )
    if adjusted_score != sentiment["sentiment_score"]:
        sentiment = _sentiment_with_score(sentiment, adjusted_score)
        direction = _direction_from_score(adjusted_score)
        features["sentiment_score"] = adjusted_score
        continuation_score = calculate_continuation_score(features, direction)
        features["continuation_score"] = continuation_score
        targets = build_targets(features, similar_summary)
        state_key, state_label, phase_label = classify_state(
            features,
            direction,
            continuation_score,
            shock_score,
        )
    transition_probs = estimate_transition_probabilities(
        state_key,
        features,
        similar_summary=similar_summary,
    )
    expected_returns = estimate_expected_returns(
        state_key,
        features,
        similar_summary=similar_summary,
    )
    dual_scenario = build_dual_scenario(
        direction,
        continuation_score,
        sentiment["reversal_risk_score"],
        sentiment["rebound_improvement_score"],
        shock_score,
    )
    confidence_result = calculate_confidence_score(
        features,
        sentiment["sentiment_score"],
        continuation_score,
        shock_score,
        similar_summary,
        performance_adjustment,
        data_quality,
    )
    confidence_result = _apply_intermarket_confidence_adjustment(
        confidence_result,
        intermarket_context,
    )
    confidence = confidence_result["label"]
    invalidation_price = _invalidation_price(direction, targets, price)
    evidence = build_evidence(features, similar_summary, direction)
    evidence.extend(_quality_evidence(data_quality))
    evidence.extend(_intermarket_evidence(intermarket_context))
    if transition_probs:
        evidence.append(
            f"次に移りやすい局面は{transition_probs[0]['label']}（{transition_probs[0]['probability']:.0%}）"
        )
    if confidence_result["warnings"]:
        evidence.extend(confidence_result["warnings"])
    if dual_scenario.get("counter_scenario"):
        evidence.append(dual_scenario["counter_scenario"])
    if performance_adjustment:
        evidence.append(_performance_adjustment_text(performance_adjustment))
    main_scenario = build_main_scenario(direction, targets)
    sub_scenario = build_sub_scenario(direction, invalidation_price)
    scenarios = build_scenarios(
        direction,
        technical_setup,
        targets,
        intermarket_context,
        _invalidation_text(direction, invalidation_price),
    )
    last_updated = _format_last_updated(snapshot)
    stale_minutes = _stale_minutes(snapshot)
    result = {
        "is_ready": True,
        "model_version": BASECALC_MODEL_VERSION,
        "price": round(price, 0),
        "as_of": as_of.isoformat() if hasattr(as_of, "isoformat") else "",
        "readiness": readiness,
        "readiness_display": _readiness_display(readiness),
        "readiness_level": readiness["level"],
        "directional_allowed": readiness["directional_allowed"],
        "direction": direction,
        "direction_label": _direction_label(direction),
        "strength_label": _strength_label(sentiment["sentiment_score"]),
        "state_key": state_key,
        "state_label": state_label,
        "phase_label": phase_label,
        "technical_regime": technical_setup["technical_regime"],
        "primary_setup": technical_setup["primary_setup"],
        "primary_setup_label": technical_setup["primary_setup_label"],
        "primary_scenario": dual_scenario["primary_scenario"],
        "counter_scenario": dual_scenario["counter_scenario"],
        "scenario_label": dual_scenario["scenario_label"],
        "trend_score": sentiment["trend_score"],
        "momentum_score": sentiment["momentum_score"],
        "reversal_risk_score": sentiment["reversal_risk_score"],
        "rebound_improvement_score": sentiment["rebound_improvement_score"],
        "external_context_score": sentiment["external_context_score"],
        "nikkei_technical_score": sentiment["sentiment_score"],
        "us_index_confirmation_score": intermarket_context["confirmation_score"],
        "us_index_confirmation_label": intermarket_context["confirmation_label"],
        "us_index_confirmation": intermarket_context,
        "intermarket_technicals": intermarket_context,
        "chase_risk": _chase_risk(intermarket_context),
        "sentiment_key": sentiment["sentiment_key"],
        "sentiment_label": sentiment["sentiment_label"],
        "sentiment_score": sentiment["sentiment_score"],
        "sentiment_score_abs": abs(sentiment["sentiment_score"]),
        "continuation_score": continuation_score,
        "shock_score": shock_score,
        "shock_label": shock_label(shock_score),
        "confidence": confidence,
        "confidence_score": confidence_result["score"],
        "confidence_components": confidence_result["components"],
        "confidence_warnings": confidence_result["warnings"],
        "data_quality": data_quality,
        "data_quality_score": data_quality["score"],
        "data_quality_level": data_quality["level"],
        "source_status": {
            "source": data_quality.get("source"),
            "symbol": data_quality.get("symbol"),
            "instrument_key": readiness.get("instrument_key"),
            "fallback_used": data_quality.get("fallback_used"),
            "instrument_type": readiness.get("instrument_type"),
            "is_stale": data_quality.get("is_stale"),
        },
        "transition_probs": transition_probs,
        "expected_returns": expected_returns,
        "expected_return_1d": _expected_return_value(expected_returns, "1d"),
        "expected_return_3d": _expected_return_value(expected_returns, "3d"),
        "expected_return_5d": _expected_return_value(expected_returns, "5d"),
        "expected_return_source": _expected_return_source(expected_returns, "3d"),
        "expected_return_label": _expected_return_label(expected_returns, "3d"),
        "main_scenario": main_scenario,
        "sub_scenario": sub_scenario,
        "scenarios": scenarios,
        "horizons": _horizon_signals(direction, expected_returns, technical_setup),
        "invalidation_price": invalidation_price,
        "invalidation": targets["invalidation"],
        "invalidation_reason": _invalidation_reason(direction, targets["invalidation"]),
        "invalidation_display": _price_display(invalidation_price),
        "invalidation_text": _invalidation_text(direction, invalidation_price),
        "upside_targets": targets["upside"],
        "downside_targets": targets["downside"],
        "near_levels": targets.get("near_levels") or {},
        "target_ranges": targets.get("target_ranges") or [],
        "target_1_display": _target_display(direction, targets, 0),
        "target_2_display": _target_display(direction, targets, 1),
        "similar_summary": similar_summary,
        "evidence": evidence[:6],
        "performance_adjustment": performance_adjustment or {"applied": False},
        "features": _json_safe_features(features),
        "nikkei_state_vector": _json_safe_features(build_technical_state(features)),
        "last_updated_display": last_updated,
        "is_stale": bool(snapshot.get("is_stale")) or stale_minutes > 15,
        "stale_minutes": stale_minutes,
        "data_warning": _data_warning(snapshot, stale_minutes),
        "components": sentiment["components"],
    }
    result["basecalc_signal"] = build_basecalc_signal_contract(result)
    return result


def build_features(price, snapshot, ohlcv, indicator_validity=None):
    closes = ohlcv["closes"]
    highs = ohlcv["highs"]
    lows = ohlcv["lows"]
    opens = ohlcv["opens"]
    volumes = ohlcv["volumes"]
    indicator_validity = indicator_validity or evaluate_indicator_validity(ohlcv, snapshot)

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
        "indicator_validity": indicator_validity,
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
        "high_5d": max(highs[-5:] or [price]),
        "low_5d": min(lows[-5:] or [price]),
        "high_20d": max(highs[-20:] or [price]),
        "low_20d": min(lows[-20:] or [price]),
        "ema5": latest(ema5) if indicator_validity.get("ema5") else None,
        "ema20": latest(ema20) if indicator_validity.get("ema20") else None,
        "ema60": latest(ema60) if indicator_validity.get("ema60") else None,
        "ema200": latest(ema200) if indicator_validity.get("ema200") else None,
        "vwap": latest(vwap) if indicator_validity.get("vwap") else None,
        "rsi14": latest(rsi14) if indicator_validity.get("rsi14") else None,
        "macd": latest(macd["macd"]) if indicator_validity.get("macd") else None,
        "macd_signal": latest(macd["signal"]) if indicator_validity.get("macd") else None,
        "macd_histogram": latest(macd["histogram"]) if indicator_validity.get("macd") else None,
        "adx14": latest(adx["adx"]) if indicator_validity.get("adx14") else None,
        "dmi_plus": latest(adx["plus_di"]) if indicator_validity.get("adx14") else None,
        "dmi_minus": latest(adx["minus_di"]) if indicator_validity.get("adx14") else None,
        "atr14": latest_atr if indicator_validity.get("atr14") else None,
        "atr_ratio": atr_ratio if indicator_validity.get("atr14") else None,
        "bb_upper": latest(bands["upper"]) if indicator_validity.get("bollinger") else None,
        "bb_mid": latest(bands["mid"]) if indicator_validity.get("bollinger") else None,
        "bb_lower": latest(bands["lower"]) if indicator_validity.get("bollinger") else None,
        "bb_width_pct": latest(bands["width"]) if indicator_validity.get("bollinger") else None,
        "pivots": pivots if indicator_validity.get("pivot") else {},
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
        "ema5_gap_pct": _pct(price, latest(ema5)) if indicator_validity.get("ema5") else None,
        "ema20_gap_pct": _pct(price, latest(ema20)) if indicator_validity.get("ema20") else None,
        "ema60_gap_pct": _pct(price, latest(ema60)) if indicator_validity.get("ema60") else None,
        "vwap_gap_pct": _pct(price, latest(vwap)) if indicator_validity.get("vwap") else None,
        "shock_candidate": abs(calculate_change_zscore(close_changes[:-1], daily_change_pct)) >= 2,
    }
    return features


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


def _sentiment_with_score(sentiment, score):
    key, label = sentiment_label(score)
    updated = dict(sentiment)
    updated["sentiment_score"] = score
    updated["sentiment_key"] = key
    updated["sentiment_label"] = label
    updated["components"] = dict(sentiment.get("components") or {})
    updated["components"]["performance"] = -abs(
        (sentiment.get("sentiment_score") or 0) - score
    )
    return updated


def _performance_adjustment_text(adjustment):
    return (
        f"過去{adjustment['sample_count']}件の検証が弱いため信頼度を"
        f"{adjustment['downgrade']}段階下げ、方向スコアも抑えています"
    )


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


def build_dual_scenario(
    direction,
    continuation_score,
    reversal_risk_score,
    rebound_improvement_score,
    shock_score,
):
    if direction == "up":
        if reversal_risk_score >= 60:
            return {
                "primary_scenario": "上昇優勢",
                "counter_scenario": f"反落警戒 {reversal_risk_score}/100",
                "scenario_label": "上昇優勢だが反落警戒点灯",
            }
        return {
            "primary_scenario": "上昇継続",
            "counter_scenario": "反落警戒は限定的",
            "scenario_label": "上昇継続優勢",
        }
    if direction == "down":
        if rebound_improvement_score >= 60:
            return {
                "primary_scenario": "下落優勢",
                "counter_scenario": f"買い戻し警戒 {rebound_improvement_score}/100",
                "scenario_label": "下落優勢だが買い戻し警戒点灯",
            }
        return {
            "primary_scenario": "下落継続",
            "counter_scenario": "改善シグナルは限定的",
            "scenario_label": "下落継続優勢",
        }
    if max(reversal_risk_score, rebound_improvement_score, shock_score) >= 55:
        label = "反転候補"
    elif continuation_score < 45:
        label = "レンジ・判定保留"
    else:
        label = "レンジ中立"
    return {
        "primary_scenario": label,
        "counter_scenario": "上下どちらも決め手不足",
        "scenario_label": label,
    }


def blocked_world_model(
    price=None,
    snapshot=None,
    data_quality=None,
    readiness=None,
    *,
    ohlcv=None,
    intermarket_context=None,
):
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    intermarket_context = build_us_index_technical_context(intermarket_context or {})
    data_quality = data_quality or evaluate_snapshot_quality(snapshot)
    readiness = readiness or evaluate_world_model_readiness(
        price=price,
        snapshot=snapshot or None,
        data_quality=data_quality,
        daily_ohlcv=ohlcv or {},
    )
    evidence = readiness.get("warnings") or ["データ品質が不足しています"]
    result = {
        "is_ready": False,
        "model_version": BASECALC_MODEL_VERSION,
        "price": round(price, 0) if _to_float(price) else None,
        "readiness": readiness,
        "readiness_display": _readiness_display(readiness),
        "readiness_level": "blocked",
        "directional_allowed": False,
        "direction": "neutral",
        "direction_label": "判定不可",
        "strength_label": "弱い",
        "state_key": "data_unavailable",
        "state_label": "データ不足",
        "phase_label": "判定停止",
        "primary_scenario": "判定停止",
        "counter_scenario": "価格データ、取得元、足数を確認してください",
        "scenario_label": "判定停止",
        "trend_score": 0,
        "momentum_score": 0,
        "reversal_risk_score": 0,
        "rebound_improvement_score": 0,
        "external_context_score": 0,
        "nikkei_technical_score": 0,
        "us_index_confirmation_score": intermarket_context["confirmation_score"],
        "us_index_confirmation_label": intermarket_context["confirmation_label"],
        "us_index_confirmation": intermarket_context,
        "intermarket_technicals": intermarket_context,
        "technical_regime": "data_unavailable",
        "primary_setup": "range_wait",
        "primary_setup_label": "判定停止",
        "chase_risk": "unknown",
        "sentiment_key": "neutral",
        "sentiment_label": "判定不可",
        "sentiment_score": 0,
        "sentiment_score_abs": 0,
        "continuation_score": 0,
        "shock_score": 0,
        "shock_label": "N/A",
        "confidence": "Low",
        "confidence_score": 0,
        "confidence_components": {},
        "confidence_warnings": evidence,
        "data_quality": data_quality,
        "data_quality_score": data_quality.get("score", 0),
        "data_quality_level": data_quality.get("level", "bad"),
        "source_status": _source_status(data_quality, readiness),
        "transition_probs": [],
        "expected_returns": {},
        "expected_return_1d": 0,
        "expected_return_3d": 0,
        "expected_return_5d": 0,
        "expected_return_source": "",
        "expected_return_label": "判定停止",
        "main_scenario": "データ品質が不足しているため判定できません",
        "sub_scenario": "価格データ、取得元、足数を確認してください",
        "scenarios": build_scenarios(
            "neutral",
            {"primary_setup_label": "判定停止"},
            _empty_targets(),
            intermarket_context,
            "方向判定停止中",
        ),
        "horizons": {},
        "invalidation_price": None,
        "invalidation": {},
        "invalidation_reason": "",
        "invalidation_display": "",
        "invalidation_text": "方向判定停止中",
        "upside_targets": [],
        "downside_targets": [],
        "near_levels": {},
        "target_ranges": [],
        "target_1_display": "",
        "target_2_display": "",
        "similar_summary": _empty_similar_summary(),
        "evidence": evidence[:6],
        "performance_adjustment": {"applied": False},
        "features": {
            "symbol": readiness.get("symbol"),
            "source": readiness.get("source"),
            "instrument_key": readiness.get("instrument_key"),
            "instrument_type": readiness.get("instrument_type"),
            "readiness_level": "blocked",
            "directional_allowed": False,
            "bar_counts": readiness.get("bar_counts") or {},
            "indicator_validity": readiness.get("indicator_validity") or {},
        },
        "last_updated_display": _format_last_updated(snapshot),
        "is_stale": True,
        "stale_minutes": _stale_minutes(snapshot),
        "data_warning": "現在のデータは判定条件を満たしていないため、方向予測を停止しています。",
        "components": {},
    }
    result["basecalc_signal"] = build_basecalc_signal_contract(result)
    return result


def limited_world_model(
    price,
    snapshot,
    data_quality,
    readiness,
    *,
    features,
    ohlcv,
    intermarket_context=None,
):
    intermarket_context = build_us_index_technical_context(intermarket_context or {})
    evidence = (readiness.get("warnings") or []) + _quality_evidence(data_quality)
    result = {
        "is_ready": True,
        "model_version": BASECALC_MODEL_VERSION,
        "price": round(price, 0),
        "readiness": readiness,
        "readiness_display": _readiness_display(readiness),
        "readiness_level": "limited",
        "directional_allowed": False,
        "direction": "neutral",
        "direction_label": "参考表示",
        "strength_label": "弱い",
        "state_key": "limited_reference",
        "state_label": "参考表示",
        "phase_label": "方向判定停止",
        "primary_scenario": "方向判定停止",
        "counter_scenario": "価格、取得元、足数、フォールバック有無を確認してください",
        "scenario_label": "参考表示",
        "trend_score": 0,
        "momentum_score": 0,
        "reversal_risk_score": 0,
        "rebound_improvement_score": 0,
        "external_context_score": 0,
        "nikkei_technical_score": 0,
        "us_index_confirmation_score": intermarket_context["confirmation_score"],
        "us_index_confirmation_label": intermarket_context["confirmation_label"],
        "us_index_confirmation": intermarket_context,
        "intermarket_technicals": intermarket_context,
        "technical_regime": "limited",
        "primary_setup": "range_wait",
        "primary_setup_label": "参考表示",
        "chase_risk": "unknown",
        "sentiment_key": "neutral",
        "sentiment_label": "参考表示",
        "sentiment_score": 0,
        "sentiment_score_abs": 0,
        "continuation_score": 0,
        "shock_score": 0,
        "shock_label": "N/A",
        "confidence": "Low",
        "confidence_score": min(20, int((data_quality or {}).get("score") or 0)),
        "confidence_components": {},
        "confidence_warnings": evidence,
        "data_quality": data_quality,
        "data_quality_score": data_quality.get("score", 0),
        "data_quality_level": data_quality.get("level", "warning"),
        "source_status": _source_status(data_quality, readiness),
        "transition_probs": estimate_transition_probabilities("limited_reference", features),
        "expected_returns": {},
        "expected_return_1d": 0,
        "expected_return_3d": 0,
        "expected_return_5d": 0,
        "expected_return_source": "",
        "expected_return_label": "方向判定停止",
        "main_scenario": "データ品質が限定的なため方向判定は停止しています",
        "sub_scenario": "価格、取得元、足数、フォールバック有無を確認してください",
        "scenarios": build_scenarios(
            "neutral",
            {"primary_setup_label": "参考表示"},
            _empty_targets(),
            intermarket_context,
            "方向判定停止中",
        ),
        "horizons": {},
        "invalidation_price": None,
        "invalidation": {},
        "invalidation_reason": "",
        "invalidation_display": "",
        "invalidation_text": "方向判定停止中",
        "upside_targets": [],
        "downside_targets": [],
        "near_levels": {},
        "target_ranges": [],
        "target_1_display": "",
        "target_2_display": "",
        "similar_summary": _empty_similar_summary(),
        "evidence": (evidence or ["方向判定を停止しています"])[:6],
        "performance_adjustment": {"applied": False},
        "features": _json_safe_features(features),
        "last_updated_display": _format_last_updated(snapshot),
        "is_stale": bool(snapshot.get("is_stale")),
        "stale_minutes": _stale_minutes(snapshot),
        "data_warning": "現在のデータは判定条件を満たしていないため、方向予測を停止しています。",
        "components": {},
    }
    result["basecalc_signal"] = build_basecalc_signal_contract(result)
    return result


def empty_world_model():
    return blocked_world_model()


def _normalize_ohlcv(price, snapshot, allow_synthetic=False):
    snapshot = snapshot or {}
    closes = _positive_numbers(snapshot.get("closes"))
    highs = _positive_numbers(snapshot.get("highs"))
    lows = _positive_numbers(snapshot.get("lows"))
    opens = _positive_numbers(snapshot.get("opens"))
    volumes = _numbers(snapshot.get("volumes"))
    timestamps = list(snapshot.get("timestamps") or [])
    real_counts = {
        "opens": len(opens),
        "highs": len(highs),
        "lows": len(lows),
        "closes": len(closes),
        "volumes": len(volumes),
    }

    if not closes:
        if not allow_synthetic:
            return {
                "opens": [],
                "highs": [],
                "lows": [],
                "closes": [],
                "volumes": [],
                "timestamps": [],
                "real_counts": real_counts,
                "synthetic": False,
            }
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
        "real_counts": real_counts,
        "synthetic": allow_synthetic and real_counts["closes"] == 0,
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
    for key in ("symbol", "name", "source", "fetched_at", "is_stale", "fallback_used", "instrument_key", "instrument_type"):
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


def _invalidation_reason(direction, invalidation):
    if direction == "up":
        return invalidation.get("bullish_reason") or ""
    if direction == "down":
        return invalidation.get("bearish_reason") or ""
    return ""


def _target_display(direction, targets, index):
    target_list = targets["upside"] if direction == "up" else targets["downside"]
    if direction == "neutral":
        target_list = targets["upside"]
    return _price_display(_target_price(target_list, index))


def _target_price(targets, index):
    if len(targets) <= index:
        return None
    return targets[index].get("price")


def _expected_return_value(expected_returns, horizon):
    row = (expected_returns or {}).get(horizon)
    if isinstance(row, dict):
        return row.get("value")
    return row


def _expected_return_source(expected_returns, horizon):
    row = (expected_returns or {}).get(horizon)
    if isinstance(row, dict):
        return row.get("source") or ""
    return ""


def _expected_return_label(expected_returns, horizon):
    row = (expected_returns or {}).get(horizon)
    if isinstance(row, dict):
        return row.get("display_label") or ""
    return ""


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
    quality = snapshot.get("quality") if isinstance(snapshot, dict) else None
    if quality and quality.get("warnings"):
        return " / ".join(quality["warnings"][:2])
    if not snapshot:
        return "価格データの取得に失敗しました。現在の判定は前回取得データに基づいています。"
    if snapshot.get("is_stale") or stale_minutes > 15:
        return "価格データが15分以上古い可能性があります。"
    return ""


def _quality_evidence(quality):
    if not quality:
        return []
    evidence = [f"データ品質は{quality['level']}（{quality['score']}/100）"]
    if quality.get("fallback_used"):
        evidence.append("フォールバックデータを使用しています")
    for warning in quality.get("warnings") or []:
        evidence.append(warning)
    return evidence[:3]


def _intermarket_evidence(intermarket_context):
    context = intermarket_context or {}
    readiness = context.get("readiness") or {}
    if not readiness.get("usable"):
        return [readiness.get("reason") or "米国3指数確認なし"]
    return list(context.get("evidence") or ["米国3指数確認はデータ待ち"])[:3]


def _apply_intermarket_confidence_adjustment(confidence_result, intermarket_context):
    result = dict(confidence_result or {})
    score = int(result.get("score") or 0)
    components = dict(result.get("components") or {})
    us_score = int((intermarket_context or {}).get("confirmation_score") or 0)
    if us_score >= 25:
        adjustment = 8
    elif us_score <= -25:
        adjustment = -10
    else:
        adjustment = 0
    score = max(0, min(100, score + adjustment))
    components["us_index_confirmation"] = adjustment
    result["score"] = score
    result["label"] = _confidence_label(score)
    result["components"] = components
    if us_score <= -25:
        warnings = list(result.get("warnings") or [])
        warnings.append("米国3指数確認が弱く、追いかけリスクを上げています")
        result["warnings"] = warnings
    return result


def _confidence_label(score):
    if score >= 75:
        return "High"
    if score >= 45:
        return "Middle"
    return "Low"


def _chase_risk(intermarket_context):
    score = int((intermarket_context or {}).get("confirmation_score") or 0)
    label = (intermarket_context or {}).get("confirmation_label")
    if score <= -25 or label == "divergent":
        return "high"
    if score >= 25:
        return "low"
    return "medium"


def _horizon_signals(direction, expected_returns, setup):
    rows = {}
    for horizon in ("1d", "3d", "5d"):
        expected = (expected_returns or {}).get(horizon)
        value = expected.get("value") if isinstance(expected, dict) else expected
        rows[horizon] = {
            "main_bias": "up" if direction == "up" else "down" if direction == "down" else "range",
            "setup_label": (setup or {}).get("primary_setup_label") or "",
            "expected_return_pct": value,
        }
    return rows


def _json_safe_features(features):
    safe = {}
    for key, value in features.items():
        if isinstance(value, dict):
            safe[key] = {
                nested_key: nested_value
                if isinstance(nested_value, bool)
                else _round(nested_value)
                if isinstance(nested_value, (int, float))
                else nested_value
                for nested_key, nested_value in value.items()
                if isinstance(nested_value, (int, float, str, bool)) or nested_value is None
            }
        elif isinstance(value, bool):
            safe[key] = value
        elif isinstance(value, (int, float, str)) or value is None:
            safe[key] = _round(value) if isinstance(value, (int, float)) else value
    return safe


def _json_safe_context(context):
    if not isinstance(context, dict):
        return {}
    safe = {}
    for key, value in context.items():
        if isinstance(value, dict):
            safe[key] = _json_safe_context(value)
        elif isinstance(value, list):
            safe[key] = [
                _json_safe_context(item)
                if isinstance(item, dict)
                else item.isoformat()
                if hasattr(item, "isoformat")
                else item
                for item in value
                if isinstance(item, (dict, int, float, str, bool)) or item is None or hasattr(item, "isoformat")
            ]
        elif hasattr(value, "isoformat"):
            safe[key] = value.isoformat()
        elif isinstance(value, (int, float, str, bool)) or value is None:
            safe[key] = value
    return safe


def _source_status(data_quality, readiness):
    return {
        "source": readiness.get("source") or data_quality.get("source"),
        "symbol": readiness.get("symbol") or data_quality.get("symbol"),
        "instrument_key": readiness.get("instrument_key"),
        "fallback_used": data_quality.get("fallback_used"),
        "instrument_type": readiness.get("instrument_type") or data_quality.get("instrument_type"),
        "is_stale": data_quality.get("is_stale"),
    }


def _empty_targets():
    return {"upside": [], "downside": [], "invalidation": {}}


def _empty_similar_summary():
    return {
        "case_count": 0,
        "searched_case_count": 0,
        "used_case_count": 0,
        "is_statistically_valid": False,
        "sample_warning": "類似局面の件数が不足しています",
        "up_rate": 0,
        "down_rate": 0,
        "range_rate": 0,
        "average_return_pct": 0,
        "upside_t1_hit_rate": 0,
        "downside_t1_hit_rate": 0,
        "target_t1_hit_rate": 0,
        "invalidation_rate": 0,
        "directional_accuracy": 0,
        "cases": [],
    }


def _readiness_display(readiness):
    bar_counts = readiness.get("bar_counts") or {}
    indicator_validity = readiness.get("indicator_validity") or {}
    valid_major = sum(
        1
        for key in ("ema20", "ema60", "rsi14", "atr14", "vwap", "pivot")
        if indicator_validity.get(key)
    )
    return {
        "daily_bars": bar_counts.get("1d", 0),
        "valid_major_indicators": valid_major,
    }


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
