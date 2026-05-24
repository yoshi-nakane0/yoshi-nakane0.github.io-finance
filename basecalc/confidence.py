def calculate_confidence_score(
    features: dict,
    sentiment_score: int,
    continuation_score: int,
    shock_score: int,
    similar_summary: dict,
    performance_adjustment,
    data_quality,
) -> dict:
    data_quality = data_quality or {}
    similar_summary = similar_summary or {}
    quality_score = int(data_quality.get("score") or 0)
    case_count = int(similar_summary.get("case_count") or 0)
    similar_accuracy = float(similar_summary.get("directional_accuracy") or 0)
    readiness_level = features.get("readiness_level") or "blocked"
    bar_counts = features.get("bar_counts") or {}
    daily_bar_count = int(bar_counts.get("1d") or 0)
    indicator_validity = features.get("indicator_validity") or {}
    components = {
        "data_quality": round(quality_score * 0.2, 1),
        "technical_alignment": round(min(abs(sentiment_score), 100) * 0.2, 1),
        "continuation": round(min(max(continuation_score, 0), 100) * 0.15, 1),
        "similar_sample": round(min(case_count / 12, 1) * 10, 1),
        "similar_accuracy": round(similar_accuracy * 10, 1),
        "state_performance": _state_performance_component(performance_adjustment),
        "shock_penalty": -round(min(max(shock_score - 40, 0), 40) / 40 * 10, 1),
        "stale_penalty": _stale_penalty(data_quality),
    }
    score = int(round(sum(components.values())))
    caps = []
    warnings = []
    if data_quality.get("level") == "bad":
        caps.append(44)
        warnings.append("データ品質が低いため信頼度を抑えています")
    elif data_quality.get("level") == "warning":
        caps.append(74)
        warnings.append("データ鮮度または取得元に注意が必要です")
    if case_count == 0:
        caps.append(60)
        warnings.append("類似局面が不足しているため信頼度を抑えています")
    if not similar_summary.get("is_statistically_valid"):
        caps.append(60)
        warnings.append("類似局面の件数が不足しています")
    if readiness_level == "blocked":
        caps.append(20)
        warnings.append("判定不可のため信頼度を抑えています")
    elif readiness_level == "limited":
        caps.append(35)
        warnings.append("参考表示のため信頼度を抑えています")
    if data_quality.get("instrument_type") == "index_fallback" or features.get("instrument_type") == "index_fallback":
        caps.append(20)
        warnings.append("指数代替データのため信頼度を抑えています")
    if data_quality.get("fallback_used") or features.get("fallback_used"):
        caps.append(35)
        warnings.append("フォールバックデータのため信頼度を抑えています")
    if daily_bar_count and daily_bar_count < 60:
        caps.append(35)
        warnings.append("日足本数が不足しているため信頼度を抑えています")
    if shock_score >= 80:
        caps.append(44)
        warnings.append("突発性が高いため信頼度を抑えています")
    if caps:
        score = min(score, min(caps))
    score = max(0, min(100, score))
    major_indicators_ready = all(
        indicator_validity.get(key) for key in ("ema20", "ema60", "rsi14", "atr14")
    )
    middle_requirements = (
        readiness_level == "ready"
        and quality_score >= 80
        and daily_bar_count >= 60
        and features.get("directional_allowed") is True
        and major_indicators_ready
    )
    if not middle_requirements:
        score = min(score, 44)
    label = confidence_label_from_score(score)
    if label == "High":
        performance_total = int(features.get("performance_total_predictions") or 0)
        if (
            not similar_summary.get("is_statistically_valid")
            or int(similar_summary.get("used_case_count") or 0) < 30
            or performance_total < 30
        ):
            label = "Middle"
            score = min(score, 74)
            warnings.append("High判定に必要な検証件数が不足しています")
    if label == "Low" and not warnings:
        warnings.append("方向材料が十分に揃っていません")
    return {
        "score": score,
        "label": label,
        "components": components,
        "warnings": warnings,
    }


def confidence_label_from_score(score: int) -> str:
    if score >= 75:
        return "High"
    if score >= 45:
        return "Middle"
    return "Low"


def _state_performance_component(performance_adjustment):
    if not performance_adjustment:
        return 8
    if performance_adjustment.get("applied"):
        return max(0, 8 - int(performance_adjustment.get("downgrade") or 1) * 5)
    return 8


def _stale_penalty(data_quality):
    penalty = 0
    if data_quality.get("is_stale"):
        penalty -= 10
    if data_quality.get("fallback_used"):
        penalty -= 6
    if data_quality.get("instrument_type") == "index_fallback":
        penalty -= 8
    return max(-20, penalty)
