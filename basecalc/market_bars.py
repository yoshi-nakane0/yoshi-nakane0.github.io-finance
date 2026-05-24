from datetime import datetime, timedelta, timezone as dt_timezone

from django.db import DatabaseError, NotSupportedError, transaction
from django.utils import timezone

from .models import MarketBar
from .instrument import normalize_instrument


HORIZON_TIMEFRAME_CHOICES = {
    "1d": ("1d",),
    "3d": ("1d",),
    "5d": ("1d",),
}

HORIZON_TOLERANCES = {
    "1d": timedelta(hours=36),
    "3d": timedelta(hours=36),
    "5d": timedelta(hours=36),
}

RETENTION_BY_TIMEFRAME = {
    "1d": timedelta(days=365 * 15),
}

MAX_BARS_BY_TIMEFRAME = {
    "1d": 5000,
}

DAILY_HISTORY_LIMIT = 5000


def save_market_bars_from_snapshot(snapshot):
    if not isinstance(snapshot, dict):
        return 0
    rows = []
    for timeframe, frame in _snapshot_frames(snapshot):
        rows.extend(_bars_from_frame(snapshot, frame, timeframe))
    if not rows:
        return 0
    unique_rows = {
        (row["symbol"], row["timeframe"], row["timestamp"]): row for row in rows
    }
    rows = list(unique_rows.values())
    try:
        with transaction.atomic():
            _bulk_upsert_market_bars(rows)
            prune_market_bars({row["symbol"] for row in rows})
    except (DatabaseError, NotSupportedError):
        try:
            with transaction.atomic():
                _loop_upsert_market_bars(rows)
                prune_market_bars({row["symbol"] for row in rows})
        except DatabaseError:
            return 0
    return len(rows)


def attach_saved_daily_bars(snapshot, limit=DAILY_HISTORY_LIMIT):
    if not isinstance(snapshot, dict):
        return snapshot
    root = dict(snapshot)
    instrument = normalize_instrument(root.get("symbol"), root.get("source"))
    instrument_key = root.get("instrument_key") or instrument["instrument_key"]
    symbol = instrument["symbol"] or root.get("symbol") or "NIY=F"
    queryset = MarketBar.objects.filter(timeframe="1d")
    if instrument_key and instrument_key != "unknown":
        queryset = queryset.filter(instrument_key=instrument_key)
    else:
        queryset = queryset.filter(symbol=symbol)
    bars = list(queryset.order_by("-timestamp")[:limit])
    if not bars:
        return root
    bars.reverse()

    frame = {
        "symbol": symbol,
        "source": root.get("source") or "saved_market_bars",
        "instrument_key": instrument_key,
        "instrument_type": root.get("instrument_type") or instrument["instrument_type"],
        "timeframe": "1d",
        "interval": "1d",
        "opens": [bar.open or bar.close for bar in bars],
        "highs": [bar.high or bar.close for bar in bars],
        "lows": [bar.low or bar.close for bar in bars],
        "closes": [bar.close for bar in bars],
        "volumes": [bar.volume or 0 for bar in bars],
        "timestamps": [int(bar.timestamp.timestamp()) for bar in bars],
    }
    _append_newer_snapshot_bar(frame, root)
    closes = frame["closes"]
    highs = frame["highs"]
    lows = frame["lows"]
    root.update(
        {
            "symbol": symbol,
            "instrument_key": instrument_key,
            "instrument_type": frame["instrument_type"],
            "opens": frame["opens"],
            "highs": highs,
            "lows": lows,
            "closes": closes,
            "volumes": frame["volumes"],
            "timestamps": frame["timestamps"],
            "previous_close": closes[-2] if len(closes) >= 2 else root.get("previous_close"),
            "recent_high": max(highs[-10:] or [root.get("price") or closes[-1]]),
            "recent_low": min(lows[-10:] or [root.get("price") or closes[-1]]),
        }
    )
    timeframes = dict(root.get("timeframes") or {})
    timeframes["1d"] = frame
    root["timeframes"] = timeframes
    return root


def _bulk_upsert_market_bars(rows):
    MarketBar.objects.bulk_create(
        [
            MarketBar(
                symbol=row["symbol"],
                timeframe=row["timeframe"],
                timestamp=row["timestamp"],
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
                source=row["source"],
                instrument_key=row["instrument_key"],
                instrument_type=row["instrument_type"],
                data_quality_score=row.get("data_quality_score"),
            )
            for row in rows
        ],
        batch_size=500,
        update_conflicts=True,
        update_fields=[
            "open",
            "high",
            "low",
            "close",
            "volume",
            "source",
            "instrument_key",
            "instrument_type",
            "data_quality_score",
        ],
        unique_fields=["symbol", "timeframe", "timestamp"],
    )


def _loop_upsert_market_bars(rows):
    for row in rows:
        MarketBar.objects.update_or_create(
            symbol=row["symbol"],
            timeframe=row["timeframe"],
            timestamp=row["timestamp"],
            defaults={
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
                "source": row["source"],
                "instrument_key": row["instrument_key"],
                "instrument_type": row["instrument_type"],
                "data_quality_score": row.get("data_quality_score"),
            },
        )


def _append_newer_snapshot_bar(frame, snapshot):
    timestamps = snapshot.get("timestamps") or []
    closes = snapshot.get("closes") or []
    try:
        timestamp = int(timestamps[-1])
    except (TypeError, ValueError, IndexError):
        return
    if frame["timestamps"] and timestamp <= frame["timestamps"][-1]:
        return
    close = _number_at(closes, len(closes) - 1) or _number_at(
        [snapshot.get("price")],
        0,
    )
    if close is None:
        return
    frame["timestamps"].append(timestamp)
    frame["closes"].append(close)
    frame["opens"].append(_number_at(snapshot.get("opens"), len(closes) - 1) or close)
    frame["highs"].append(_number_at(snapshot.get("highs"), len(closes) - 1) or close)
    frame["lows"].append(_number_at(snapshot.get("lows"), len(closes) - 1) or close)
    frame["volumes"].append(_number_at(snapshot.get("volumes"), len(closes) - 1, allow_zero=True) or 0)


def prune_market_bars(symbols=None, now=None):
    now = now or timezone.now()
    symbols = symbols or MarketBar.objects.values_list("symbol", flat=True).distinct()
    deleted = 0
    for symbol in symbols:
        for timeframe, retention in RETENTION_BY_TIMEFRAME.items():
            queryset = MarketBar.objects.filter(symbol=symbol, timeframe=timeframe)
            old_ids = list(
                queryset.filter(timestamp__lt=now - retention).values_list(
                    "id",
                    flat=True,
                )
            )
            if old_ids:
                deleted += MarketBar.objects.filter(id__in=old_ids).delete()[0]
            max_rows = MAX_BARS_BY_TIMEFRAME.get(timeframe)
            if not max_rows:
                continue
            overflow_ids = list(
                queryset.order_by("-timestamp").values_list("id", flat=True)[max_rows:]
            )
            if overflow_ids:
                deleted += MarketBar.objects.filter(id__in=overflow_ids).delete()[0]
    return deleted


def nearest_bar_for_horizon(symbol, horizon, target_at, instrument_key=None):
    for timeframe in HORIZON_TIMEFRAME_CHOICES.get(horizon, ("1d",)):
        bar = nearest_market_bar(
            symbol=symbol,
            timeframe=timeframe,
            target_at=target_at,
            tolerance=HORIZON_TOLERANCES.get(horizon, timedelta(hours=36)),
            instrument_key=instrument_key,
        )
        if bar is not None:
            return bar
    return None


def nearest_market_bar(symbol, timeframe, target_at, tolerance, instrument_key=None):
    start = target_at - tolerance
    end = target_at + tolerance
    queryset = MarketBar.objects.filter(
        symbol=symbol,
        timeframe=timeframe,
        timestamp__gte=start,
        timestamp__lte=end,
    )
    if instrument_key:
        queryset = queryset.filter(instrument_key=instrument_key)
    before = queryset.filter(timestamp__lte=target_at).order_by("-timestamp").first()
    after = queryset.filter(timestamp__gte=target_at).order_by("timestamp").first()
    candidates = [bar for bar in (before, after) if bar is not None]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda bar: abs((bar.timestamp - target_at).total_seconds()),
    )


def market_bars_between(symbol, timeframe, start_at, end_at, instrument_key=None):
    queryset = MarketBar.objects.filter(
        symbol=symbol,
        timeframe=timeframe,
        timestamp__gte=start_at,
        timestamp__lte=end_at,
    )
    if instrument_key:
        queryset = queryset.filter(instrument_key=instrument_key)
    return list(queryset.order_by("timestamp"))


def _snapshot_frames(snapshot):
    timeframes = snapshot.get("timeframes")
    if isinstance(timeframes, dict) and timeframes:
        for timeframe, frame in timeframes.items():
            if isinstance(frame, dict):
                yield timeframe, frame
        return
    yield snapshot.get("timeframe") or "1d", snapshot


def _bars_from_frame(root_snapshot, frame, timeframe):
    closes = frame.get("closes") or []
    timestamps = frame.get("timestamps") or []
    rows = []
    for index, timestamp in enumerate(timestamps):
        close = _number_at(closes, index)
        bar_time = _bar_time(timestamp)
        if close is None or bar_time is None:
            continue
        symbol = frame.get("symbol") or root_snapshot.get("symbol") or "NIY=F"
        source = frame.get("source") or root_snapshot.get("source") or "unknown"
        instrument = normalize_instrument(symbol, source)
        quality = frame.get("quality") or root_snapshot.get("quality") or {}
        rows.append(
            {
                "symbol": instrument["symbol"] or symbol,
                "timeframe": timeframe,
                "timestamp": bar_time,
                "open": _number_at(frame.get("opens"), index) or close,
                "high": _number_at(frame.get("highs"), index) or close,
                "low": _number_at(frame.get("lows"), index) or close,
                "close": close,
                "volume": _number_at(frame.get("volumes"), index, allow_zero=True),
                "source": source,
                "instrument_key": instrument["instrument_key"],
                "instrument_type": instrument["instrument_type"],
                "data_quality_score": quality.get("score"),
            }
        )
    return rows


def _bar_time(value):
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp, tz=dt_timezone.utc)


def _number_at(values, index, allow_zero=False):
    if not values or len(values) <= index:
        return None
    try:
        value = float(values[index])
    except (TypeError, ValueError):
        return None
    if value > 0 or (allow_zero and value == 0):
        return value
    return None
