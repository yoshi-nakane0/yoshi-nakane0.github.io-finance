from datetime import datetime, timedelta, timezone as dt_timezone

from django.db import DatabaseError, NotSupportedError, transaction
from django.utils import timezone

from .models import MarketBar


HORIZON_TIMEFRAME_CHOICES = {
    "1h": ("1h", "15m", "5m"),
    "4h": ("1h", "15m", "5m"),
    "1d": ("1d", "1h"),
    "3d": ("1d", "1h"),
    "5d": ("1d", "1h"),
}

HORIZON_TOLERANCES = {
    "1h": timedelta(hours=2),
    "4h": timedelta(hours=3),
    "1d": timedelta(hours=36),
    "3d": timedelta(hours=36),
    "5d": timedelta(hours=36),
}

RETENTION_BY_TIMEFRAME = {
    "5m": timedelta(days=14),
    "15m": timedelta(days=45),
    "1h": timedelta(days=180),
    "1d": timedelta(days=365 * 5),
}

MAX_BARS_BY_TIMEFRAME = {
    "5m": 5000,
    "15m": 5000,
    "1h": 6000,
    "1d": 2000,
}


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
            )
            for row in rows
        ],
        batch_size=500,
        update_conflicts=True,
        update_fields=["open", "high", "low", "close", "volume", "source"],
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
            },
        )


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


def nearest_bar_for_horizon(symbol, horizon, target_at):
    for timeframe in HORIZON_TIMEFRAME_CHOICES.get(horizon, ("1d",)):
        bar = nearest_market_bar(
            symbol=symbol,
            timeframe=timeframe,
            target_at=target_at,
            tolerance=HORIZON_TOLERANCES.get(horizon, timedelta(hours=36)),
        )
        if bar is not None:
            return bar
    return None


def nearest_market_bar(symbol, timeframe, target_at, tolerance):
    start = target_at - tolerance
    end = target_at + tolerance
    queryset = MarketBar.objects.filter(
        symbol=symbol,
        timeframe=timeframe,
        timestamp__gte=start,
        timestamp__lte=end,
    )
    before = queryset.filter(timestamp__lte=target_at).order_by("-timestamp").first()
    after = queryset.filter(timestamp__gte=target_at).order_by("timestamp").first()
    candidates = [bar for bar in (before, after) if bar is not None]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda bar: abs((bar.timestamp - target_at).total_seconds()),
    )


def market_bars_between(symbol, timeframe, start_at, end_at):
    return list(
        MarketBar.objects.filter(
            symbol=symbol,
            timeframe=timeframe,
            timestamp__gte=start_at,
            timestamp__lte=end_at,
        ).order_by("timestamp")
    )


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
        rows.append(
            {
                "symbol": frame.get("symbol") or root_snapshot.get("symbol") or "NIY=F",
                "timeframe": timeframe,
                "timestamp": bar_time,
                "open": _number_at(frame.get("opens"), index) or close,
                "high": _number_at(frame.get("highs"), index) or close,
                "low": _number_at(frame.get("lows"), index) or close,
                "close": close,
                "volume": _number_at(frame.get("volumes"), index, allow_zero=True),
                "source": frame.get("source") or root_snapshot.get("source") or "unknown",
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
