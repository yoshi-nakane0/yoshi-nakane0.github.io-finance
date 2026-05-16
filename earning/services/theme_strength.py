from datetime import date, timedelta


THEME_BASKETS = {
    'AI半導体': [
        ('NASDAQ', 'NVDA'), ('NASDAQ', 'AMD'), ('NASDAQ', 'AVGO'),
        ('NYSE', 'TSM'), ('NASDAQ', 'ASML'), ('NASDAQ', 'AMAT'),
        ('NASDAQ', 'LRCX'), ('TSE', '8035'), ('TSE', '6857'),
    ],
    '半導体': [
        ('NASDAQ', 'NVDA'), ('NASDAQ', 'AMD'), ('NASDAQ', 'AVGO'),
        ('NYSE', 'TSM'), ('NASDAQ', 'ASML'), ('NASDAQ', 'AMAT'),
        ('NASDAQ', 'LRCX'), ('TSE', '8035'), ('TSE', '6857'),
    ],
    '半導体材料': [
        ('TSE', '4063'), ('NASDAQ', 'AMAT'), ('NASDAQ', 'LRCX'),
        ('NASDAQ', 'KLAC'), ('TSE', '6920'), ('TSE', '7735'),
    ],
    'テクノロジー': [
        ('NASDAQ', 'AAPL'), ('NASDAQ', 'MSFT'), ('NASDAQ', 'GOOGL'),
        ('NASDAQ', 'META'), ('NASDAQ', 'AMZN'), ('NASDAQ', 'NVDA'),
    ],
    '電子部品': [
        ('TSE', '6762'), ('TSE', '6981'), ('TSE', '6971'),
        ('TSE', '6758'), ('TSE', '6594'),
    ],
    'エンタメ': [
        ('NASDAQ', 'NFLX'), ('NYSE', 'DIS'), ('TSE', '6758'),
        ('TSE', '9766'), ('TSE', '7974'),
    ],
    '小売': [
        ('NASDAQ', 'COST'), ('NYSE', 'WMT'), ('NYSE', 'TGT'),
        ('NASDAQ', 'AMZN'),
    ],
    '医薬品': [
        ('NYSE', 'LLY'), ('NYSE', 'MRK'), ('NYSE', 'PFE'),
        ('TSE', '4519'), ('TSE', '4502'),
    ],
}

THEME_ALIASES = {
    'AI': 'AI半導体',
    '半導体、テクノロジー': 'AI半導体',
    '半導体装置': '半導体',
    '半導体関連': '半導体',
    'エレクトロニクス': '電子部品',
    '電子材料': '半導体材料',
    '小売り': '小売',
}

THEME_BASELINE_SCORES = {
    'AI半導体': 86.0,
    '半導体': 82.0,
    '半導体材料': 76.0,
    'テクノロジー': 70.0,
    '電子部品': 58.0,
    'エンタメ': 56.0,
    '小売': 55.0,
    '医薬品': 50.0,
}

BENCHMARKS = {
    'TSE': ('TSE', '1306'),
    'NASDAQ': ('NASDAQ', 'QQQ'),
    'NYSE': ('NYSE', 'SPY'),
}


def normalize_theme(theme):
    theme = (theme or '').strip()
    if not theme:
        return ''
    if theme in THEME_BASKETS or theme in THEME_BASELINE_SCORES:
        return theme
    if theme in THEME_ALIASES:
        return THEME_ALIASES[theme]
    for key in THEME_BASKETS:
        if key and key in theme:
            return key
    return theme


def fallback_theme_score(theme):
    return THEME_BASELINE_SCORES.get(normalize_theme(theme))


def _latest_return(rows, lookback):
    closes = [row.get('close') for row in rows if row.get('close') is not None]
    if len(closes) <= lookback:
        return None
    base = closes[-lookback - 1]
    latest = closes[-1]
    if base in (None, 0) or latest is None:
        return None
    return (latest / base - 1) * 100.0


def score_from_returns(ret_5=None, ret_20=None, ret_60=None, benchmark_20=None):
    parts = []
    if ret_5 is not None:
        parts.append((50.0 + ret_5 * 2.0, 0.20))
    if ret_20 is not None:
        parts.append((50.0 + ret_20 * 1.6, 0.40))
    if ret_60 is not None:
        parts.append((50.0 + ret_60 * 1.0, 0.20))
    if ret_20 is not None and benchmark_20 is not None:
        parts.append((50.0 + (ret_20 - benchmark_20) * 2.0, 0.20))
    if not parts:
        return None
    total_weight = sum(weight for _, weight in parts)
    raw = sum(score * weight for score, weight in parts) / total_weight
    return max(0.0, min(100.0, raw))


def fetch_theme_strength(theme, end_date=None):
    normalized = normalize_theme(theme)
    basket = THEME_BASKETS.get(normalized)
    if not basket:
        return fallback_theme_score(theme)

    from earning.services.yfinance import build_yahoo_symbol, fetch_daily_history

    end = end_date or date.today()
    start = end - timedelta(days=120)
    market_groups = {}
    scores = []

    for market, symbol in basket:
        yahoo_symbol = build_yahoo_symbol(market, symbol)
        if not yahoo_symbol:
            continue
        rows = fetch_daily_history(yahoo_symbol, start, end)
        if not rows:
            continue
        ret_5 = _latest_return(rows, 5)
        ret_20 = _latest_return(rows, 20)
        ret_60 = _latest_return(rows, 60)
        market_groups.setdefault(market, []).append(ret_20)
        score = score_from_returns(ret_5=ret_5, ret_20=ret_20, ret_60=ret_60)
        if score is not None:
            scores.append(score)

    if not scores:
        return fallback_theme_score(theme)

    benchmark_returns = []
    for market in market_groups:
        benchmark = BENCHMARKS.get(market)
        if not benchmark:
            continue
        yahoo_symbol = build_yahoo_symbol(*benchmark)
        if not yahoo_symbol:
            continue
        rows = fetch_daily_history(yahoo_symbol, start, end)
        ret_20 = _latest_return(rows, 20)
        if ret_20 is not None:
            benchmark_returns.append(ret_20)

    benchmark_20 = (
        sum(benchmark_returns) / len(benchmark_returns)
        if benchmark_returns else None
    )
    basket_score = sum(scores) / len(scores)
    if benchmark_20 is None:
        return basket_score

    basket_ret20_values = [
        value for values in market_groups.values() for value in values if value is not None
    ]
    if not basket_ret20_values:
        return basket_score
    basket_ret20 = sum(basket_ret20_values) / len(basket_ret20_values)
    relative_score = score_from_returns(ret_20=basket_ret20, benchmark_20=benchmark_20)
    if relative_score is None:
        return basket_score
    return basket_score * 0.7 + relative_score * 0.3
