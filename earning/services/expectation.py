def clamp_score(value):
    if value is None:
        return None
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return None


def numeric_score(value, low, high):
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if high == low:
        return 50.0
    return clamp_score((v - low) / (high - low) * 100.0)


def parse_number(value):
    if value is None:
        return None
    try:
        text = str(value).strip().replace('%', '').replace(',', '')
    except (TypeError, ValueError):
        return None
    if not text or text.lower() == 'nan' or text == '—':
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def guidance_score(value):
    return {
        'up': 82.0,
        'flat': 55.0,
        'down': 25.0,
    }.get((value or '').strip().lower())


def surprise_score(*values):
    scores = []
    for value in values:
        parsed = parse_number(value)
        if parsed is not None:
            scores.append(numeric_score(parsed, -20.0, 20.0))
    if not scores:
        return None
    return sum(scores) / len(scores)


def past_reaction_score(values):
    valid = [float(v) for v in (values or []) if v is not None]
    if not valid:
        return None
    avg = sum(valid) / len(valid)
    return numeric_score(avg, -5.0, 5.0)


def risk_quality_score(value):
    value = clamp_score(value)
    if value is None:
        return None
    return 100.0 - value


def compute_expectation_score(
    *,
    theme_score=None,
    risk_score=None,
    eps_surprise=None,
    sales_surprise=None,
    guidance_revision=None,
    past_reactions=None,
):
    parts = [
        (clamp_score(theme_score), 0.30),
        (past_reaction_score(past_reactions), 0.20),
        (surprise_score(eps_surprise, sales_surprise), 0.20),
        (guidance_score(guidance_revision), 0.15),
        (risk_quality_score(risk_score), 0.15),
    ]
    valid = [(score, weight) for score, weight in parts if score is not None]
    if not valid:
        return None
    weight_total = sum(weight for _, weight in valid)
    if weight_total <= 0:
        return None
    return sum(score * weight for score, weight in valid) / weight_total


def expectation_level(score):
    score = clamp_score(score)
    if score is None:
        return {
            'label': '判定なし',
            'class': 'expectation-muted',
            'scale': None,
        }
    if score >= 75:
        return {'label': '強気', 'class': 'expectation-strong', 'scale': 5}
    if score >= 60:
        return {'label': 'やや強気', 'class': 'expectation-positive', 'scale': 4}
    if score >= 45:
        return {'label': '中立', 'class': 'expectation-neutral', 'scale': 3}
    if score >= 30:
        return {'label': '警戒', 'class': 'expectation-caution', 'scale': 2}
    return {'label': '強い警戒', 'class': 'expectation-danger', 'scale': 1}
