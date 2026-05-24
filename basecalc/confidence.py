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
        caps.append(74)
        warnings.append("類似局面が不足しているため信頼度を抑えています")
    if shock_score >= 80:
        caps.append(44)
        warnings.append("突発性が高いため信頼度を抑えています")
    if caps:
        score = min(score, min(caps))
    score = max(0, min(100, score))
    label = confidence_label_from_score(score)
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
