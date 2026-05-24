from collections import Counter

from django.utils import timezone

from .instrument import normalize_instrument
from .models import MarketBar
from .model_version import BASECALC_MODEL_VERSION
from .outcomes import evaluate_due_predictions, performance_summary, save_prediction
from .world_model import build_world_model


def run_basecalc_backtest(
    *,
    symbol="NIY=F",
    instrument_key="cme_nikkei_futures",
    date_from=None,
    date_to=None,
    timeframe="1d",
    min_bars=80,
    limit=None,
    write=False,
    model_version=None,
) -> dict:
    queryset = MarketBar.objects.filter(
        symbol=symbol,
        timeframe=timeframe,
        instrument_key=instrument_key,
    ).order_by("timestamp")
    if date_from:
        queryset = queryset.filter(timestamp__date__gte=date_from)
    if date_to:
        queryset = queryset.filter(timestamp__date__lte=date_to)
    if limit:
        queryset = queryset[: int(limit)]
    bars = list(queryset)
    skip_reasons = Counter()
    if len(bars) < min_bars:
        skip_reasons["insufficient_bars"] = 1
        return _result(0, 0, 0, skip_reasons)

    created = 0
    evaluated = 0
    for index in range(min_bars - 1, len(bars)):
        window = bars[: index + 1]
        current_bar = window[-1]
        snapshot = _snapshot_from_bars(window, symbol, instrument_key)
        world_model = build_world_model(
            current_bar.close,
            snapshot,
            as_of=current_bar.timestamp,
        )
        if model_version:
            world_model["model_version"] = model_version
        evaluated += 1
        if world_model.get("readiness_level") != "ready":
            skip_reasons[world_model.get("readiness_level") or "not_ready"] += 1
            continue
        if write:
            prediction = save_prediction(
                world_model,
                prediction_timestamp=current_bar.timestamp,
                is_backtest=True,
                min_interval_minutes=None,
            )
            if prediction:
                created += 1

    if write:
        evaluate_due_predictions()
    return _result(evaluated, created, evaluated - created, skip_reasons, model_version=model_version)


def _snapshot_from_bars(bars, symbol, instrument_key):
    instrument = normalize_instrument(symbol, "yahoo")
    if instrument["instrument_key"] != instrument_key:
        instrument["instrument_key"] = instrument_key
    return {
        "symbol": instrument["symbol"] or symbol,
        "source": "yahoo" if instrument_key == "cme_nikkei_futures" else "stooq",
        "instrument_key": instrument["instrument_key"],
        "instrument_type": instrument["instrument_type"],
        "price": bars[-1].close,
        "previous_close": bars[-2].close if len(bars) >= 2 else bars[-1].close,
        "change_pct": (
            ((bars[-1].close - bars[-2].close) / bars[-2].close) * 100
            if len(bars) >= 2 and bars[-2].close
            else 0
        ),
        "opens": [bar.open or bar.close for bar in bars],
        "highs": [bar.high or bar.close for bar in bars],
        "lows": [bar.low or bar.close for bar in bars],
        "closes": [bar.close for bar in bars],
        "volumes": [bar.volume or 0 for bar in bars],
        "timestamps": [int(bar.timestamp.timestamp()) for bar in bars],
        "fetched_at": timezone.now(),
        "bar_timestamp": bars[-1].timestamp,
        "fallback_used": instrument_key != "cme_nikkei_futures",
    }


def _result(evaluated, created, skipped, skip_reasons, model_version=None):
    return {
        "evaluated": evaluated,
        "created": created,
        "skipped": skipped,
        "skip_reasons": dict(skip_reasons),
        "metrics": {
            horizon: performance_summary(
                horizon=horizon,
                model_version=model_version or BASECALC_MODEL_VERSION,
                is_backtest=True,
            )
            for horizon in ("1d", "3d", "5d")
        },
    }
