from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone as dt_timezone

from django.core.cache import cache
from django.http import HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie

from .anchor_snapshot import (
    DEFAULT_ERP_METHOD,
    DEFAULT_GROWTH_CORE_RATIO,
    DEFAULT_GROWTH_WIDE_RATIO,
    calculate_erp_fixed,
    calculate_growth_center_percent,
    calculate_valuation_label,
    load_anchor_snapshot,
    normalize_erp_method,
    normalize_growth_percent,
    normalize_ratio,
)
from .market_shock import build_market_shock_context
from .futures_sentiment import get_nikkei_futures_snapshot
from .market_bars import attach_saved_daily_bars, save_market_bars_from_snapshot
from .market_context import calculate_context_score, get_market_context_snapshot
from .nikkei_bias import calculate_bias, get_jgb10y_yield_percent, get_nikkei_per_values
from .models import MarketSnapshot, WorldModelPrediction
from .outcomes import (
    evaluate_due_predictions,
    improvement_insights,
    performance_summary,
    save_prediction,
    state_performance_summary,
)
from .serializers import serialize_snapshot
from .status import load_basecalc_status, status_display_rows
from .world_model import build_world_model
from .data_quality import is_snapshot_stale

CACHE_KEY_FWD = "nikkei_forward_per"
CACHE_KEY_PRICE = "nikkei_price"
CACHE_KEY_FUTURES = "nikkei_futures_snapshot"
CACHE_KEY_FUTURES_LAST_GOOD = "nikkei_futures_snapshot_last_good"
CACHE_KEY_MARKET_CONTEXT = "basecalc_market_context"
CACHE_KEY_MARKET_CONTEXT_FETCHED_AT = "basecalc_market_context_fetched_at"
CACHE_KEY_JGB = "nikkei_jgb10y_yield_percent"
CACHE_KEY_DIVIDEND_INDEX = "nikkei_dividend_yield_index"
CACHE_KEY_PER_FETCHED_AT = "nikkei_per_fetched_at"
CACHE_KEY_JGB_FETCHED_AT = "nikkei_jgb10y_fetched_at"
CACHE_TTL_PRICE = 300
CACHE_TTL_JGB = 3600
PER_FETCH_MIN_INTERVAL_SEC = 21600
JGB_FETCH_MIN_INTERVAL_SEC = 3600
FUTURES_FETCH_MIN_INTERVAL_SEC = 60


@ensure_csrf_cookie
def index(request):
    can_update_basecalc_data = request.user.is_authenticated and request.user.is_staff
    if request.method == "POST":
        if request.POST.get("action") != "update":
            return HttpResponseBadRequest("Invalid action")
        if not can_update_basecalc_data:
            return HttpResponseForbidden("Forbidden")

    force_update = request.method == "POST"
    try:
        context = build_context(request, force_update=force_update)
    except Exception as exc:
        context = {
            "error": str(exc),
            "can_update_basecalc_data": can_update_basecalc_data,
        }
    return render(request, "basecalc/index.html", context)


def snapshot_api(request):
    try:
        context = build_context(request, force_update=False)
        return JsonResponse(serialize_snapshot(context["world_model"]))
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)


def performance_api(request):
    is_backtest = _parse_bool(request.GET.get("is_backtest"), default=False)
    summary = performance_summary(
        horizon=request.GET.get("horizon") or "1d",
        state_key=request.GET.get("state_key") or None,
        date_from=_parse_date(request.GET.get("from")),
        date_to=_parse_date(request.GET.get("to")),
        model_version=request.GET.get("model_version") or None,
        confidence_min=parse_float_param(request.GET.get("confidence_min")),
        instrument_key=request.GET.get("instrument_key") or "cme_nikkei_futures",
        readiness_level=request.GET.get("readiness_level") or "ready",
        is_backtest=is_backtest,
    )
    return JsonResponse(summary)


def history(request):
    horizon = request.GET.get("horizon") or "1d"
    if horizon not in ("1d", "3d", "5d"):
        horizon = "1d"
    state_key = request.GET.get("state_key") or None
    date_from = _parse_date(request.GET.get("from"))
    date_to = _parse_date(request.GET.get("to"))
    model_version = request.GET.get("model_version") or None
    instrument_key = request.GET.get("instrument_key") or "cme_nikkei_futures"
    readiness_level = request.GET.get("readiness_level") or "ready"
    is_backtest = _parse_bool(request.GET.get("is_backtest"), default=False)
    confidence_min = parse_float_param(request.GET.get("confidence_min"))
    predictions = WorldModelPrediction.objects.all().order_by("-created_at")
    if state_key:
        predictions = predictions.filter(state_key=state_key)
    if date_from:
        predictions = predictions.filter(created_at__date__gte=date_from)
    if date_to:
        predictions = predictions.filter(created_at__date__lte=date_to)
    if model_version:
        predictions = predictions.filter(model_version=model_version)
    if instrument_key:
        predictions = predictions.filter(instrument_key=instrument_key)
    if readiness_level:
        predictions = predictions.filter(readiness_level=readiness_level)
    if is_backtest is not None:
        predictions = predictions.filter(is_backtest=is_backtest)
    if confidence_min is not None:
        predictions = predictions.filter(confidence_score__gte=confidence_min)
    predictions = predictions.prefetch_related("predictionoutcome_set")[:80]
    history_rows = [
        _prediction_history_row(prediction, horizon) for prediction in predictions
    ]
    state_options = (
        WorldModelPrediction.objects.values("state_key", "state_label")
        .distinct()
        .order_by("state_label")
    )
    model_versions = (
        WorldModelPrediction.objects.values_list("model_version", flat=True)
        .distinct()
        .order_by("model_version")
    )
    instrument_options = (
        WorldModelPrediction.objects.values_list("instrument_key", flat=True)
        .distinct()
        .order_by("instrument_key")
    )
    context = {
        "horizon": horizon,
        "state_key": state_key or "",
        "date_from": request.GET.get("from") or "",
        "date_to": request.GET.get("to") or "",
        "model_version": model_version or "",
        "instrument_key": instrument_key or "",
        "readiness_level": readiness_level or "",
        "is_backtest": "1" if is_backtest else "0",
        "confidence_min": request.GET.get("confidence_min") or "",
        "model_versions": model_versions,
        "instrument_options": instrument_options,
        "history_rows": history_rows,
        "state_options": state_options,
        "summary": performance_summary(
            horizon,
            state_key,
            date_from,
            date_to,
            model_version=model_version,
            confidence_min=confidence_min,
            instrument_key=instrument_key,
            readiness_level=readiness_level,
            is_backtest=is_backtest,
        ),
        "state_summaries": state_performance_summary(horizon),
        "improvement_insights": improvement_insights(horizon),
        "horizons": ("1d", "3d", "5d"),
    }
    return render(request, "basecalc/history.html", context)


def build_context(request, force_update=False):
    params = request.POST if force_update else request.GET
    can_update_basecalc_data = request.user.is_authenticated and request.user.is_staff
    forward_per = cache.get(CACHE_KEY_FWD)
    price_override = normalize_price(parse_float_param(params.get("price")))
    futures_snapshot = get_cached_futures_snapshot()
    price = (
        price_override
        if price_override is not None
        else normalize_price(cache.get(CACHE_KEY_PRICE))
    )
    if price_override is None and price is None:
        price = price_from_futures_snapshot(futures_snapshot)
    jgb10y_yield_percent = cache.get(CACHE_KEY_JGB)
    dividend_yield_index_percent = cache.get(CACHE_KEY_DIVIDEND_INDEX)

    if force_update:
        forward_per, jgb10y_yield_percent, dividend_yield_index_percent, futures_snapshot, price = update_market_caches(
            forward_per,
            jgb10y_yield_percent,
            dividend_yield_index_percent,
            price,
            update_price_from_futures=price_override is None,
        )
        market_context = update_market_context_cache()
    else:
        market_context = get_cached_market_context()

    if price_override is not None:
        price = price_override
        cache.set(CACHE_KEY_PRICE, price, timeout=CACHE_TTL_PRICE)
    elif force_update and price is not None:
        cache.set(CACHE_KEY_PRICE, price, timeout=CACHE_TTL_PRICE)

    if forward_per is None:
        forward_per = 0.0
    if price is None:
        price = 0.0
    if jgb10y_yield_percent is None:
        jgb10y_yield_percent = 0.0

    anchor_snapshot = load_anchor_snapshot()
    anchor_enabled = anchor_snapshot is not None
    default_erp_method = (
        anchor_snapshot.get("erp_method", DEFAULT_ERP_METHOD)
        if anchor_enabled
        else DEFAULT_ERP_METHOD
    )
    erp_method = normalize_erp_method(params.get("erp_method", default_erp_method))
    erp_growth_input, erp_growth_percent = normalize_growth_input(
        params,
        erp_method,
        anchor_snapshot,
        anchor_enabled,
    )
    growth_core_ratio, growth_wide_ratio = normalize_growth_ratios(
        params,
        anchor_snapshot,
        anchor_enabled,
    )

    calc_price = anchor_snapshot.get("anchor_price") if anchor_enabled else price
    calc_forward_per = anchor_snapshot.get("forward_per") if anchor_enabled else forward_per
    calc_jgb10y_yield_percent = (
        anchor_snapshot.get("jgb10y_yield_percent")
        if anchor_enabled
        else jgb10y_yield_percent
    )
    calc_dividend_yield_index_percent = (
        anchor_snapshot.get("dividend_yield_index_percent")
        if anchor_enabled
        else dividend_yield_index_percent
    )
    erp_fixed = calculate_erp_fixed(
        erp_method,
        calc_forward_per,
        calc_jgb10y_yield_percent,
        calc_dividend_yield_index_percent,
        erp_growth_percent,
    )
    growth_center_percent = calculate_growth_center_percent(
        erp_method,
        erp_growth_percent,
    )

    data = calculate_bias(
        calc_price,
        calc_forward_per,
        dividend_yield_index_percent=calc_dividend_yield_index_percent,
        jgb10y_yield_percent=calc_jgb10y_yield_percent,
        erp_fixed=erp_fixed,
        growth_center_percent=growth_center_percent,
        growth_core_ratio=growth_core_ratio,
        growth_wide_ratio=growth_wide_ratio,
    )
    data = decorate_valuation_data(
        data,
        price,
        calc_forward_per,
        calc_jgb10y_yield_percent,
        calc_dividend_yield_index_percent,
        anchor_snapshot,
        anchor_enabled,
    )

    futures_snapshot = attach_saved_daily_bars(futures_snapshot)
    world_model = build_world_model(price, futures_snapshot, market_context)
    basecalc_status = load_basecalc_status()
    data["world_model"] = world_model
    data.update(world_model)
    performance = performance_summary("1d")
    performance_by_horizon = {
        horizon: performance_summary(horizon) for horizon in ("1d", "3d", "5d")
    }
    if force_update:
        evaluate_due_predictions(price)
        save_prediction(world_model)

    return {
        "data": data,
        "world_model": world_model,
        "market_shock": build_market_shock_context(),
        "market_context": world_model.get("market_context") or {},
        "basecalc_status": basecalc_status,
        "basecalc_status_rows": status_display_rows(basecalc_status, world_model),
        "performance": performance,
        "performance_by_horizon": performance_by_horizon,
        "updated": force_update,
        "erp_method": erp_method,
        "erp_growth_input": erp_growth_input,
        "price_param": format_price_param(price),
        "growth_core_ratio_input": f"{growth_core_ratio:.1f}",
        "growth_wide_ratio_input": f"{growth_wide_ratio:.1f}",
        "can_update_basecalc_data": can_update_basecalc_data,
    }


def update_market_caches(
    forward_per,
    jgb10y_yield_percent,
    dividend_yield_index_percent,
    price,
    update_price_from_futures=False,
):
    with ThreadPoolExecutor() as executor:
        futures = {
            "per_values": executor.submit(get_nikkei_per_values_for_update),
            "jgb": executor.submit(get_jgb10y_yield_for_update),
            "futures_snapshot": executor.submit(get_futures_snapshot_for_update),
        }

        per_vals = futures["per_values"].result()
        if per_vals:
            if per_vals.get("index_based"):
                forward_per = per_vals["index_based"]
                cache.set(CACHE_KEY_FWD, forward_per, timeout=None)
            if per_vals.get("dividend_yield_index_based") is not None:
                dividend_yield_index_percent = per_vals[
                    "dividend_yield_index_based"
                ]
                cache.set(
                    CACHE_KEY_DIVIDEND_INDEX,
                    dividend_yield_index_percent,
                    timeout=None,
                )

        val = futures["jgb"].result()
        if val is not None:
            jgb10y_yield_percent = val
            cache.set(CACHE_KEY_JGB, jgb10y_yield_percent, timeout=CACHE_TTL_JGB)

        futures_snapshot = futures["futures_snapshot"].result()
        if isinstance(futures_snapshot, dict):
            futures_snapshot["_market_bars_saved"] = save_market_bars_from_snapshot(
                futures_snapshot
            )
            futures_snapshot = attach_saved_daily_bars(futures_snapshot)
            cache.set(CACHE_KEY_FUTURES, futures_snapshot, timeout=CACHE_TTL_PRICE)
            cache.set(CACHE_KEY_FUTURES_LAST_GOOD, futures_snapshot, timeout=None)
            snapshot_price = price_from_futures_snapshot(futures_snapshot)
            if update_price_from_futures and snapshot_price is not None:
                price = snapshot_price
                cache.set(CACHE_KEY_PRICE, price, timeout=CACHE_TTL_PRICE)
        else:
            futures_snapshot = get_cached_futures_snapshot()
            snapshot_price = price_from_futures_snapshot(futures_snapshot)
            if update_price_from_futures and price is None and snapshot_price is not None:
                price = snapshot_price

    return (
        forward_per,
        jgb10y_yield_percent,
        dividend_yield_index_percent,
        futures_snapshot,
        price,
    )


def get_cached_futures_snapshot():
    snapshot = cache.get(CACHE_KEY_FUTURES)
    if isinstance(snapshot, dict):
        return snapshot
    return get_stale_futures_snapshot()


def price_from_futures_snapshot(snapshot):
    if not isinstance(snapshot, dict):
        return None
    return normalize_price(snapshot.get("price"))


def get_futures_snapshot_for_update():
    snapshot = cache.get(CACHE_KEY_FUTURES)
    if isinstance(snapshot, dict) and not futures_snapshot_needs_refresh(snapshot):
        return snapshot
    return get_nikkei_futures_snapshot()


def get_cached_market_context():
    context = cache.get(CACHE_KEY_MARKET_CONTEXT)
    if isinstance(context, dict):
        return context
    latest_prediction = (
        WorldModelPrediction.objects.exclude(context={}).order_by("-created_at").first()
    )
    if latest_prediction and isinstance(latest_prediction.context, dict):
        return latest_prediction.context
    return calculate_context_score({})


def update_market_context_cache():
    context = get_market_context_snapshot()
    if not context:
        context = calculate_context_score({})
    cache.set(CACHE_KEY_MARKET_CONTEXT, context, timeout=CACHE_TTL_JGB)
    cache.set(CACHE_KEY_MARKET_CONTEXT_FETCHED_AT, timezone.now(), timeout=None)
    return context


def get_nikkei_per_values_for_update():
    forward_per = cache.get(CACHE_KEY_FWD)
    dividend_yield_index_percent = cache.get(CACHE_KEY_DIVIDEND_INDEX)
    if (
        forward_per is not None
        and dividend_yield_index_percent is not None
        and not cache_value_needs_refresh(
            CACHE_KEY_PER_FETCHED_AT,
            PER_FETCH_MIN_INTERVAL_SEC,
        )
    ):
        return {
            "index_based": forward_per,
            "dividend_yield_index_based": dividend_yield_index_percent,
        }
    values = get_nikkei_per_values()
    if values:
        cache.set(CACHE_KEY_PER_FETCHED_AT, timezone.now(), timeout=None)
    return values


def get_jgb10y_yield_for_update():
    cached_value = cache.get(CACHE_KEY_JGB)
    if cached_value is not None and not cache_value_needs_refresh(
        CACHE_KEY_JGB_FETCHED_AT,
        JGB_FETCH_MIN_INTERVAL_SEC,
    ):
        return cached_value
    value = get_jgb10y_yield_percent()
    if value is not None:
        cache.set(CACHE_KEY_JGB_FETCHED_AT, timezone.now(), timeout=None)
    return value


def cache_value_needs_refresh(cache_key, min_interval_sec, now=None):
    fetched_at = cache.get(cache_key)
    if not fetched_at:
        return True
    return timestamp_needs_refresh(fetched_at, min_interval_sec, now=now)


def futures_snapshot_needs_refresh(snapshot, now=None):
    fetched_at = snapshot.get("fetched_at") if isinstance(snapshot, dict) else None
    if not fetched_at:
        return True
    return timestamp_needs_refresh(
        fetched_at,
        FUTURES_FETCH_MIN_INTERVAL_SEC,
        now=now,
    )


def timestamp_needs_refresh(fetched_at, min_interval_sec, now=None):
    if isinstance(fetched_at, str):
        try:
            fetched_at = datetime.fromisoformat(fetched_at)
        except ValueError:
            return True
    if timezone.is_naive(fetched_at):
        fetched_at = timezone.make_aware(fetched_at, timezone=dt_timezone.utc)
    now = now or timezone.now()
    return (now - fetched_at).total_seconds() >= min_interval_sec


def get_stale_futures_snapshot():
    snapshot = cache.get(CACHE_KEY_FUTURES_LAST_GOOD)
    if isinstance(snapshot, dict):
        snapshot = dict(snapshot)
        snapshot["is_stale"] = True
        snapshot["fallback_reason"] = "last_good_cache"
        return snapshot
    latest_snapshot = MarketSnapshot.objects.order_by("-created_at").first()
    if latest_snapshot is None:
        return None
    fetched_at = latest_snapshot.fetched_at or latest_snapshot.created_at
    snapshot = {
        "source": latest_snapshot.source or "saved_snapshot",
        "fetched_at": fetched_at,
    }
    is_stale = is_snapshot_stale(snapshot)
    return {
        "symbol": latest_snapshot.symbol,
        "name": latest_snapshot.symbol,
        "source": latest_snapshot.source or "saved_snapshot",
        "instrument_key": latest_snapshot.instrument_key,
        "instrument_type": latest_snapshot.instrument_type,
        "price": latest_snapshot.price,
        "previous_close": latest_snapshot.close or latest_snapshot.price,
        "change_pct": None,
        "opens": [latest_snapshot.open or latest_snapshot.price],
        "highs": [latest_snapshot.high or latest_snapshot.price],
        "lows": [latest_snapshot.low or latest_snapshot.price],
        "closes": [latest_snapshot.close or latest_snapshot.price],
        "volumes": [latest_snapshot.volume or 0],
        "timestamps": [int(latest_snapshot.created_at.timestamp())],
        "fetched_at": fetched_at,
        "is_stale": is_stale,
        "fallback_reason": "saved_snapshot",
    }


def normalize_growth_input(params, erp_method, anchor_snapshot, anchor_enabled):
    growth_param = params.get("erp_growth")
    default_growth_percent = (
        anchor_snapshot.get("erp_growth_percent") if anchor_enabled else None
    )
    growth_value = (
        parse_float_param(growth_param)
        if growth_param is not None
        else default_growth_percent
    )
    erp_growth_percent = normalize_growth_percent(growth_value, erp_method)
    erp_growth_input = None
    if erp_method == "method_b" and erp_growth_percent is not None:
        erp_growth_input = f"{erp_growth_percent:.1f}"
    return erp_growth_input, erp_growth_percent


def normalize_growth_ratios(params, anchor_snapshot, anchor_enabled):
    default_growth_core_ratio = (
        anchor_snapshot.get("growth_core_ratio", DEFAULT_GROWTH_CORE_RATIO)
        if anchor_enabled
        else DEFAULT_GROWTH_CORE_RATIO
    )
    default_growth_wide_ratio = (
        anchor_snapshot.get("growth_wide_ratio", DEFAULT_GROWTH_WIDE_RATIO)
        if anchor_enabled
        else DEFAULT_GROWTH_WIDE_RATIO
    )
    growth_core_ratio = normalize_ratio(
        parse_float_param(params.get("growth_core_ratio")),
        default_value=default_growth_core_ratio,
    )
    growth_wide_ratio = normalize_ratio(
        parse_float_param(params.get("growth_wide_ratio")),
        default_value=default_growth_wide_ratio,
    )
    return growth_core_ratio, growth_wide_ratio


def decorate_valuation_data(
    data,
    price,
    calc_forward_per,
    calc_jgb10y_yield_percent,
    calc_dividend_yield_index_percent,
    anchor_snapshot,
    anchor_enabled,
):
    data["price"] = round(price, 0)
    data["forward_per"] = calc_forward_per
    data["jgb10y_yield_percent"] = calc_jgb10y_yield_percent
    data["dividend_yield_index_percent"] = calc_dividend_yield_index_percent
    data["valuation_label"] = calculate_valuation_label(
        price,
        data.get("fair_price_core_low"),
        data.get("fair_price_core_high"),
        data.get("fair_price_wide_low"),
        data.get("fair_price_wide_high"),
    )
    fair_price_mid = data.get("fair_price_mid")
    data["fair_price_gap_pct"] = (
        round(((price - fair_price_mid) / fair_price_mid) * 100.0, 2)
        if fair_price_mid
        else None
    )
    data["price_display"] = format_price(data.get("price"), decimals=0)
    data["fair_price_gap_pct_display"] = format_percent(data.get("fair_price_gap_pct"))
    data["valuation_class"] = valuation_class(data.get("valuation_label"))
    data["fair_price_gap_class"] = gap_class(data.get("fair_price_gap_pct"))
    data["forward_eps_display"] = format_price(data.get("forward_eps"), decimals=2)
    data["fair_price_core_low_display"] = format_price(
        data.get("fair_price_core_low"),
        decimals=0,
    )
    data["fair_price_core_high_display"] = format_price(
        data.get("fair_price_core_high"),
        decimals=0,
    )
    data["fair_price_wide_low_display"] = format_price(
        data.get("fair_price_wide_low"),
        decimals=0,
    )
    data["fair_price_wide_high_display"] = format_price(
        data.get("fair_price_wide_high"),
        decimals=0,
    )
    if anchor_enabled:
        data["anchor_status_display"] = "ACTIVE"
        data["anchor_date_display"] = str(anchor_snapshot.get("anchor_date") or "")
        data["anchor_price_display"] = format_price(
            anchor_snapshot.get("anchor_price"),
            decimals=0,
        )
        data["anchor_forward_per_display"] = format_price(
            anchor_snapshot.get("forward_per"),
            decimals=2,
        )
    else:
        data["anchor_status_display"] = "NOT SET"
        data["anchor_date_display"] = ""
        data["anchor_price_display"] = ""
        data["anchor_forward_per_display"] = ""
    return data


def _prediction_history_row(prediction, horizon):
    outcomes = list(prediction.predictionoutcome_set.all())
    outcome = next((item for item in outcomes if item.horizon == horizon), None)
    next_prediction = (
        WorldModelPrediction.objects.filter(created_at__gt=prediction.created_at)
        .order_by("created_at")
        .first()
    )
    transition_keys = {
        item.get("state_key")
        for item in (prediction.transition_probs or [])
        if isinstance(item, dict)
    }
    return {
        "prediction": prediction,
        "outcome": outcome,
        "target_1": _target_price(
            prediction.upside_targets
            if prediction.direction == "up"
            else prediction.downside_targets,
            0,
        ),
        "target_2": _target_price(
            prediction.upside_targets
            if prediction.direction == "up"
            else prediction.downside_targets,
            1,
        ),
        "direction_label": {
            "up": "上昇",
            "down": "下落",
            "neutral": "中立",
        }.get(prediction.direction, prediction.direction),
        "target_1_probability": _target_probability(
            prediction.upside_targets
            if prediction.direction == "up"
            else prediction.downside_targets,
            0,
        ),
        "transition_top": (prediction.transition_probs or [{}])[0]
        if prediction.transition_probs
        else {},
        "actual_next_state": next_prediction.state_label if next_prediction else "",
        "transition_matched": bool(
            next_prediction and next_prediction.state_key in transition_keys
        ),
        "expected_return": (prediction.expected_returns or {}).get(horizon),
        "bar_count_1d": (prediction.bar_counts or {}).get("1d", 0),
    }


def _target_price(targets, index):
    if not targets or len(targets) <= index:
        return None
    target = targets[index]
    if isinstance(target, dict):
        return target.get("price")
    return target


def _target_probability(targets, index):
    if not targets or len(targets) <= index:
        return None
    target = targets[index]
    if isinstance(target, dict):
        return target.get("probability")
    return None


def parse_float_param(value):
    if not value:
        return None
    cleaned = str(value).replace(",", "").replace("%", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def normalize_price(value):
    if value is None:
        return None
    try:
        normalized = int(float(value))
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def format_price_param(value):
    if value is None:
        return None
    return f"{int(value)}"


def format_price(value, decimals=0):
    if value is None:
        return ""
    try:
        return f"{value:,.{decimals}f}"
    except (TypeError, ValueError):
        return ""


def format_percent(value):
    if value is None:
        return ""
    try:
        return f"{value:+.2f}%"
    except (TypeError, ValueError):
        return ""


def valuation_class(label):
    if label in ("Over", "Over +"):
        return "value text-red"
    if label in ("Under", "Deep Under"):
        return "value text-green"
    if label == "Fair":
        return "value text-blue"
    return "value text-muted"


def gap_class(value):
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "value text-muted"
    if numeric > 0:
        return "value text-red"
    if numeric < 0:
        return "value text-green"
    return "value text-blue"


def _parse_date(value):
    if not value:
        return None


def _parse_bool(value, default=None):
    if value in (None, ""):
        return default
    return str(value).lower() in {"1", "true", "yes", "on"}
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None
