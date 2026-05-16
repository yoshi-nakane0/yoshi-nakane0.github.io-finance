def linear_to_100(value, low, high):
    if value is None:
        return None
    try:
        v = float(value)
    except (ValueError, TypeError):
        return None
    if high == low:
        return 50.0
    score = (v - low) / (high - low) * 100.0
    return max(0.0, min(100.0, score))


def compute_risk_score(event):
    if event is None:
        return None
    market_parts = []
    vix_risk = linear_to_100(event.vix_at_event, 10.0, 30.0)
    if vix_risk is not None:
        market_parts.append(vix_risk)
    hy_risk = linear_to_100(event.hy_spread_at_event, 2.5, 6.0)
    if hy_risk is not None:
        market_parts.append(hy_risk)
    skew_risk = linear_to_100(event.skew_at_event, 120.0, 150.0)
    if skew_risk is not None:
        market_parts.append(skew_risk)
    market_risk = sum(market_parts) / len(market_parts) if market_parts else None

    valid_reactions = [r for r in (event.past_reactions or []) if r is not None]
    individual_risk = None
    if valid_reactions:
        abs_avg = sum(abs(r) for r in valid_reactions) / len(valid_reactions)
        individual_risk = linear_to_100(abs_avg, 0.0, 10.0)

    if market_risk is None and individual_risk is None:
        return None
    if market_risk is None:
        return individual_risk
    if individual_risk is None:
        return market_risk
    return market_risk * 0.6 + individual_risk * 0.4
