SETUP_LABELS = {
    "trend_follow_long": "上昇トレンド継続",
    "pullback_long": "上昇トレンド中の押し目待ち",
    "breakout_long": "高値突破待ち",
    "failed_breakout_short": "上抜け失敗による反落警戒",
    "trend_follow_short": "下落トレンド継続",
    "pullback_short": "下落トレンド中の戻り売り",
    "range_wait": "レンジで方向感不足",
    "exhaustion_wait": "過熱で追いかけ不可",
}


def classify_technical_regime(features: dict, direction: str, state_key: str, intermarket: dict = None) -> dict:
    features = features or {}
    intermarket = intermarket or {}
    rsi = features.get("rsi14")
    confirmation = intermarket.get("confirmation_label")

    if rsi is not None and (rsi >= 72 or rsi <= 28):
        setup = "exhaustion_wait"
    elif state_key == "breakout_pending":
        setup = "breakout_long" if confirmation == "confirm_up" else "range_wait"
    elif direction == "up":
        if state_key == "bull_trend_continuation":
            setup = "trend_follow_long"
        else:
            setup = "pullback_long"
    elif direction == "down":
        if state_key == "bear_trend_continuation":
            setup = "trend_follow_short"
        elif confirmation == "confirm_up":
            setup = "pullback_short"
        else:
            setup = "pullback_short"
    else:
        setup = "range_wait"

    if direction == "up" and confirmation == "confirm_down" and setup == "breakout_long":
        setup = "range_wait"
    if direction == "down" and confirmation == "confirm_up" and setup == "trend_follow_short":
        setup = "pullback_short"

    return {
        "technical_regime": _regime_from_setup(setup),
        "primary_setup": setup,
        "primary_setup_label": SETUP_LABELS[setup],
    }


def _regime_from_setup(setup):
    if setup in {"trend_follow_long", "pullback_long", "breakout_long"}:
        return "bullish_technical"
    if setup in {"trend_follow_short", "pullback_short", "failed_breakout_short"}:
        return "bearish_technical"
    if setup == "exhaustion_wait":
        return "exhaustion"
    return "range"
