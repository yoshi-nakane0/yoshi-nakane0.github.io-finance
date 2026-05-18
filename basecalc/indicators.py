from math import sqrt


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_series(values):
    return [_to_float(value) for value in values or []]


def _safe_round(value, digits=4):
    value = _to_float(value)
    return round(value, digits) if value is not None else None


def calculate_ema(series, period):
    values = _clean_series(series)
    if period <= 0 or not values:
        return []
    multiplier = 2 / (period + 1)
    ema = []
    previous = None
    for value in values:
        if value is None:
            ema.append(previous)
            continue
        previous = value if previous is None else (value - previous) * multiplier + previous
        ema.append(previous)
    return ema


def calculate_rsi(series, period=14):
    values = _clean_series(series)
    if period <= 0 or len(values) < 2:
        return [None for _ in values]

    rsi = [None for _ in values]
    gains = []
    losses = []
    for previous, current in zip(values, values[1:]):
        if previous is None or current is None:
            gains.append(0.0)
            losses.append(0.0)
            continue
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(abs(min(change, 0.0)))

    if len(gains) < period:
        return rsi

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsi[period] = _rsi_from_averages(avg_gain, avg_loss)

    for index in range(period + 1, len(values)):
        avg_gain = ((avg_gain * (period - 1)) + gains[index - 1]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[index - 1]) / period
        rsi[index] = _rsi_from_averages(avg_gain, avg_loss)
    return rsi


def _rsi_from_averages(avg_gain, avg_loss):
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    relative_strength = avg_gain / avg_loss
    return 100 - (100 / (1 + relative_strength))


def calculate_macd(series, fast=12, slow=26, signal=9):
    values = _clean_series(series)
    fast_ema = calculate_ema(values, fast)
    slow_ema = calculate_ema(values, slow)
    macd_line = []
    for fast_value, slow_value in zip(fast_ema, slow_ema):
        if fast_value is None or slow_value is None:
            macd_line.append(None)
        else:
            macd_line.append(fast_value - slow_value)
    signal_line = calculate_ema(macd_line, signal)
    histogram = []
    for macd_value, signal_value in zip(macd_line, signal_line):
        if macd_value is None or signal_value is None:
            histogram.append(None)
        else:
            histogram.append(macd_value - signal_value)
    return {
        "macd": macd_line,
        "signal": signal_line,
        "histogram": histogram,
    }


def calculate_atr(high, low, close, period=14):
    highs = _clean_series(high)
    lows = _clean_series(low)
    closes = _clean_series(close)
    length = min(len(highs), len(lows), len(closes))
    if length == 0:
        return []

    true_ranges = []
    for index in range(length):
        high_value = highs[index]
        low_value = lows[index]
        close_value = closes[index]
        previous_close = closes[index - 1] if index else close_value
        if high_value is None or low_value is None or previous_close is None:
            true_ranges.append(None)
            continue
        true_ranges.append(
            max(
                high_value - low_value,
                abs(high_value - previous_close),
                abs(low_value - previous_close),
            )
        )

    atr = [None for _ in range(length)]
    clean_initial = [value for value in true_ranges[1 : period + 1] if value is not None]
    if len(clean_initial) < period:
        return atr
    previous_atr = sum(clean_initial) / period
    atr[period] = previous_atr
    for index in range(period + 1, length):
        true_range = true_ranges[index]
        if true_range is None:
            atr[index] = previous_atr
            continue
        previous_atr = ((previous_atr * (period - 1)) + true_range) / period
        atr[index] = previous_atr
    return atr


def calculate_adx(high, low, close, period=14):
    highs = _clean_series(high)
    lows = _clean_series(low)
    closes = _clean_series(close)
    length = min(len(highs), len(lows), len(closes))
    if length == 0:
        return {"adx": [], "plus_di": [], "minus_di": []}

    plus_dm = [0.0]
    minus_dm = [0.0]
    true_ranges = [0.0]
    for index in range(1, length):
        high_diff = (highs[index] or 0) - (highs[index - 1] or 0)
        low_diff = (lows[index - 1] or 0) - (lows[index] or 0)
        plus_dm.append(high_diff if high_diff > low_diff and high_diff > 0 else 0.0)
        minus_dm.append(low_diff if low_diff > high_diff and low_diff > 0 else 0.0)
        previous_close = closes[index - 1]
        if highs[index] is None or lows[index] is None or previous_close is None:
            true_ranges.append(0.0)
        else:
            true_ranges.append(
                max(
                    highs[index] - lows[index],
                    abs(highs[index] - previous_close),
                    abs(lows[index] - previous_close),
                )
            )

    plus_di = [None for _ in range(length)]
    minus_di = [None for _ in range(length)]
    dx = [None for _ in range(length)]
    for index in range(period, length):
        tr_sum = sum(true_ranges[index - period + 1 : index + 1])
        if tr_sum == 0:
            continue
        plus_value = 100 * sum(plus_dm[index - period + 1 : index + 1]) / tr_sum
        minus_value = 100 * sum(minus_dm[index - period + 1 : index + 1]) / tr_sum
        plus_di[index] = plus_value
        minus_di[index] = minus_value
        denominator = plus_value + minus_value
        dx[index] = 0 if denominator == 0 else 100 * abs(plus_value - minus_value) / denominator

    adx = [None for _ in range(length)]
    first_values = [value for value in dx[period : period * 2] if value is not None]
    if len(first_values) == period:
        adx[period * 2 - 1] = sum(first_values) / period
        for index in range(period * 2, length):
            current_dx = dx[index]
            previous = adx[index - 1]
            if current_dx is None or previous is None:
                adx[index] = previous
            else:
                adx[index] = ((previous * (period - 1)) + current_dx) / period
    return {"adx": adx, "plus_di": plus_di, "minus_di": minus_di}


def calculate_bollinger_bands(close, period=20, sigma=2):
    closes = _clean_series(close)
    upper = [None for _ in closes]
    mid = [None for _ in closes]
    lower = [None for _ in closes]
    width = [None for _ in closes]
    if period <= 0:
        return {"upper": upper, "mid": mid, "lower": lower, "width": width}
    for index in range(period - 1, len(closes)):
        window = [value for value in closes[index - period + 1 : index + 1] if value is not None]
        if len(window) != period:
            continue
        average = sum(window) / period
        variance = sum((value - average) ** 2 for value in window) / period
        deviation = sqrt(variance)
        mid[index] = average
        upper[index] = average + sigma * deviation
        lower[index] = average - sigma * deviation
        width[index] = ((upper[index] - lower[index]) / average) * 100 if average else None
    return {"upper": upper, "mid": mid, "lower": lower, "width": width}


def calculate_vwap(ohlcv):
    highs = _clean_series(ohlcv.get("highs") or ohlcv.get("high"))
    lows = _clean_series(ohlcv.get("lows") or ohlcv.get("low"))
    closes = _clean_series(ohlcv.get("closes") or ohlcv.get("close"))
    volumes = _clean_series(ohlcv.get("volumes") or ohlcv.get("volume"))
    length = min(len(highs), len(lows), len(closes))
    if length == 0:
        return []
    if len(volumes) < length or not any(value and value > 0 for value in volumes[:length]):
        volumes = [1.0 for _ in range(length)]
    cumulative_price_volume = 0.0
    cumulative_volume = 0.0
    result = []
    for high_value, low_value, close_value, volume in zip(
        highs[:length],
        lows[:length],
        closes[:length],
        volumes[:length],
    ):
        if high_value is None or low_value is None or close_value is None:
            result.append(None)
            continue
        typical = (high_value + low_value + close_value) / 3
        volume = volume if volume and volume > 0 else 1.0
        cumulative_price_volume += typical * volume
        cumulative_volume += volume
        result.append(cumulative_price_volume / cumulative_volume)
    return result


def calculate_pivots(high, low, close):
    highs = _clean_series(high if isinstance(high, (list, tuple)) else [high])
    lows = _clean_series(low if isinstance(low, (list, tuple)) else [low])
    closes = _clean_series(close if isinstance(close, (list, tuple)) else [close])
    if len(highs) >= 2 and len(lows) >= 2 and len(closes) >= 2:
        high_value = highs[-2]
        low_value = lows[-2]
        close_value = closes[-2]
    else:
        high_value = highs[-1] if highs else None
        low_value = lows[-1] if lows else None
        close_value = closes[-1] if closes else None
    if high_value is None or low_value is None or close_value is None:
        return {}
    pivot = (high_value + low_value + close_value) / 3
    r1 = 2 * pivot - low_value
    s1 = 2 * pivot - high_value
    range_value = high_value - low_value
    return {
        "pivot": pivot,
        "r1": r1,
        "r2": pivot + range_value,
        "r3": high_value + 2 * (pivot - low_value),
        "s1": s1,
        "s2": pivot - range_value,
        "s3": low_value - 2 * (high_value - pivot),
    }


def detect_price_structure(ohlcv):
    highs = _clean_series(ohlcv.get("highs") or ohlcv.get("high"))
    lows = _clean_series(ohlcv.get("lows") or ohlcv.get("low"))
    if len(highs) < 6 or len(lows) < 6:
        return {"key": "unknown", "label": "足数不足", "bias": 0}
    previous_high = max(value for value in highs[-10:-5] if value is not None)
    recent_high = max(value for value in highs[-5:] if value is not None)
    previous_low = min(value for value in lows[-10:-5] if value is not None)
    recent_low = min(value for value in lows[-5:] if value is not None)
    higher_high = recent_high > previous_high
    higher_low = recent_low > previous_low
    lower_high = recent_high < previous_high
    lower_low = recent_low < previous_low
    if higher_high and higher_low:
        return {"key": "higher_high_low", "label": "高値・安値切り上げ", "bias": 1}
    if lower_high and lower_low:
        return {"key": "lower_high_low", "label": "高値・安値切り下げ", "bias": -1}
    return {"key": "range", "label": "レンジ内", "bias": 0}


def detect_gap(ohlcv):
    opens = _clean_series(ohlcv.get("opens") or ohlcv.get("open"))
    closes = _clean_series(ohlcv.get("closes") or ohlcv.get("close"))
    if len(opens) < 2 or len(closes) < 2:
        return {"key": "none", "label": "N/A", "gap_pct": None}
    previous_close = closes[-2]
    open_value = opens[-1]
    if previous_close in (None, 0) or open_value is None:
        return {"key": "none", "label": "N/A", "gap_pct": None}
    gap_pct = ((open_value - previous_close) / previous_close) * 100
    if gap_pct >= 0.4:
        return {"key": "gap_up", "label": "ギャップアップ", "gap_pct": gap_pct}
    if gap_pct <= -0.4:
        return {"key": "gap_down", "label": "ギャップダウン", "gap_pct": gap_pct}
    return {"key": "none", "label": "ギャップ小", "gap_pct": gap_pct}


def latest(values, digits=4):
    for value in reversed(values or []):
        if value is not None:
            return _safe_round(value, digits)
    return None
