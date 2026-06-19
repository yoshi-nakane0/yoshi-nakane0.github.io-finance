from datetime import datetime, timezone as dt_timezone

from django.core.cache import cache
from django.http import HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie

from .intermarket_technicals import get_intermarket_technical_snapshot
from .intermarket_technicals import build_us_index_technical_context
from .market_bars import attach_saved_daily_bars
from .market_shock import build_market_shock_context
from .market_context import _price_action_fallback_assets
from .nikkei_bias import calculate_bias, get_jgb10y_yield_percent, get_nikkei_per_values
from .models import MarketSnapshot, WorldModelPrediction
from .outcomes import (
    evaluate_due_predictions,
    performance_summary,
    save_prediction,
)
from .serializers import serialize_snapshot
from .services.decision_context import (
    build_basecalc_decision_context,
    enrich_basecalc_context,
)
from .snapshot import load_basecalc_snapshot
from .status import load_basecalc_status, status_display_rows
from .status import (
    intermarket_status_entry,
    price_status_entry,
)
from .validation_report import load_validation_report
from .world_model import build_world_model
from .data_quality import is_snapshot_stale
from .github_actions import dispatch_refresh_workflow, get_refresh_workflow_state

CACHE_KEY_FWD = "nikkei_forward_per"
CACHE_KEY_PRICE = "nikkei_price"
CACHE_KEY_FUTURES = "nikkei_futures_snapshot"
CACHE_KEY_FUTURES_LAST_GOOD = "nikkei_futures_snapshot_last_good"
CACHE_KEY_INTERMARKET_CONTEXT = "basecalc_intermarket_technicals"
CACHE_KEY_INTERMARKET_FETCHED_AT = "basecalc_intermarket_technicals_fetched_at"
CACHE_KEY_JGB = "nikkei_jgb10y_yield_percent"
CACHE_KEY_DIVIDEND_INDEX = "nikkei_dividend_yield_index"
CACHE_KEY_PER_FETCHED_AT = "nikkei_per_fetched_at"
CACHE_KEY_PER_LATEST_VALUES = "nikkei_per_latest_values"
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
    if not force_update:
        snapshot = load_basecalc_snapshot()
        if snapshot:
            context = dict(snapshot)
            context["can_update_basecalc_data"] = can_update_basecalc_data
            if can_update_basecalc_data:
                context["refresh_workflow_state"] = get_refresh_workflow_state()
            hydrate_saved_snapshot_context(context)
            enrich_basecalc_context(context)
            return render(request, "basecalc/index.html", context)
        return render(
            request,
            "basecalc/index.html",
            {
                "error": "事前計算データがありません。更新ジョブを確認してください。",
                "can_update_basecalc_data": can_update_basecalc_data,
                "refresh_workflow_state": get_refresh_workflow_state()
                if can_update_basecalc_data
                else None,
            },
        )

    try:
        context = build_context(request, force_update=force_update)
    except Exception as exc:
        context = {
            "error": str(exc),
            "can_update_basecalc_data": can_update_basecalc_data,
        }
    if can_update_basecalc_data:
        context["refresh_workflow_state"] = get_refresh_workflow_state()
    return render(request, "basecalc/index.html", context)


def dispatch_basecalc_refresh_workflow(request):
    if request.method != "POST":
        return HttpResponseBadRequest("Invalid method")
    if not (request.user.is_authenticated and request.user.is_staff):
        return HttpResponseForbidden("Forbidden")
    dispatch_refresh_workflow()
    return redirect("basecalc:index")


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
        "horizons": ("1d", "3d", "5d"),
    }
    return render(request, "basecalc/history.html", context)


def validation(request):
    report = load_validation_report()
    horizon_reports = []
    if report:
        for horizon, payload in (report.get("horizons") or {}).items():
            if isinstance(payload, dict):
                horizon_reports.append(
                    {
                        "horizon": horizon,
                        **payload,
                    }
                )
    context = {
        "report": report or {},
        "has_report": bool(report),
        "horizon_reports": horizon_reports,
    }
    return render(request, "basecalc/validation.html", context)


def build_context(request, force_update=False):
    params = request.POST if force_update else request.GET
    can_update_basecalc_data = request.user.is_authenticated and request.user.is_staff
    price_override = normalize_price(parse_float_param(params.get("price")))
    futures_snapshot = get_cached_futures_snapshot()
    price = (
        price_override
        if price_override is not None
        else normalize_price(cache.get(CACHE_KEY_PRICE))
    )
    if price_override is None and price is None:
        price = price_from_futures_snapshot(futures_snapshot)

    if force_update:
        futures_snapshot, price = update_market_caches(
            price,
            update_price_from_futures=price_override is None,
        )
        intermarket_context = update_intermarket_technical_cache()
    else:
        intermarket_context = get_cached_intermarket_technical_context()

    if price_override is not None:
        price = price_override
        cache.set(CACHE_KEY_PRICE, price, timeout=CACHE_TTL_PRICE)
    elif force_update and price is not None:
        cache.set(CACHE_KEY_PRICE, price, timeout=CACHE_TTL_PRICE)

    if price is None:
        price = 0.0

    futures_snapshot = attach_saved_daily_bars(futures_snapshot)
    world_model = build_world_model(price, futures_snapshot, intermarket_context)
    basecalc_status = _status_with_current_values(
        load_basecalc_status(),
        futures_snapshot=futures_snapshot,
        world_model=world_model,
        intermarket_context=intermarket_context,
    )
    data = build_technical_data(price)
    data["world_model"] = world_model
    data.update(world_model)
    performance = performance_summary("1d")
    performance_by_horizon = {
        horizon: performance_summary(horizon) for horizon in ("1d", "3d", "5d")
    }
    backtest_performance_by_horizon = {
        horizon: performance_summary(horizon, is_backtest=True)
        for horizon in ("1d", "3d", "5d")
    }
    if force_update:
        evaluate_due_predictions(price)
        save_prediction(world_model)

    market_shock_context = _safe_market_shock_context(
        futures_snapshot,
        intermarket_context,
    )
    basecalc_status_rows = status_display_rows(basecalc_status, world_model)
    decision = build_basecalc_decision_context(
        world_model,
        market_shock_context,
        basecalc_status_rows,
        backtest_performance_by_horizon.get("1d"),
    )

    return {
        "data": data,
        "decision": decision,
        "world_model": world_model,
        "market_shock": market_shock_context,
        "intermarket_technicals": world_model.get("intermarket_technicals") or {},
        "basecalc_status": basecalc_status,
        "basecalc_status_rows": basecalc_status_rows,
        "performance": performance,
        "performance_by_horizon": performance_by_horizon,
        "backtest_performance_by_horizon": backtest_performance_by_horizon,
        "detail_mode": request.GET.get("detail") == "1",
        "updated": force_update,
        "price_param": format_price_param(price),
        "can_update_basecalc_data": can_update_basecalc_data,
        "refresh_workflow_state": get_refresh_workflow_state()
        if can_update_basecalc_data
        else None,
    }


def _safe_market_shock_context(futures_snapshot=None, intermarket_context=None):
    try:
        return build_market_shock_context(
            base_snapshot=futures_snapshot,
            intermarket_context=intermarket_context,
        )
    except Exception:
        return {
            "has_data": False,
            "summary": "市場ショック判定データなし",
            "tone": "unknown",
            "rows": [],
        }


def hydrate_saved_snapshot_context(context):
    world_model = context.get("world_model") or {}
    if not isinstance(world_model, dict):
        return context
    intermarket = world_model.get("us_index_confirmation") or {}
    components = intermarket.get("components") if isinstance(intermarket, dict) else {}
    if not components:
        fallback_context = build_us_index_technical_context(_price_action_fallback_assets())
        if fallback_context.get("components"):
            world_model["us_index_confirmation"] = fallback_context
            world_model["intermarket_technicals"] = fallback_context
            context["intermarket_technicals"] = fallback_context
    market_shock = context.get("market_shock") or {}
    if not market_shock.get("has_data"):
        context["market_shock"] = _safe_market_shock_context(
            _snapshot_from_world_model(world_model),
            world_model.get("us_index_confirmation") or world_model.get("intermarket_technicals"),
        )
    return context


def _snapshot_from_world_model(world_model):
    features = world_model.get("features") or {}
    return {
        "symbol": features.get("symbol") or world_model.get("source_symbol") or "NIY=F",
        "price": world_model.get("price"),
        "change_pct": features.get("change_1d_pct")
        or features.get("daily_change_pct")
        or world_model.get("change_pct"),
    }


def _status_with_current_values(
    base_status,
    *,
    futures_snapshot,
    world_model,
    intermarket_context,
):
    status = dict(base_status or {})
    status.update(
        {
            "price_data": price_status_entry(
                futures_snapshot,
                world_model.get("readiness_level"),
            ),
            "intermarket": intermarket_status_entry(intermarket_context),
        }
    )
    return status


def update_market_caches(
    price,
    update_price_from_futures=False,
):
    futures_snapshot = get_cached_futures_snapshot()
    snapshot_price = price_from_futures_snapshot(futures_snapshot)
    if isinstance(futures_snapshot, dict):
        cache.set(CACHE_KEY_FUTURES, futures_snapshot, timeout=CACHE_TTL_PRICE)
        cache.set(CACHE_KEY_FUTURES_LAST_GOOD, futures_snapshot, timeout=None)
    if update_price_from_futures and snapshot_price is not None:
        price = snapshot_price
        cache.set(CACHE_KEY_PRICE, price, timeout=CACHE_TTL_PRICE)
    return futures_snapshot, price


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
    snapshot = get_stale_futures_snapshot()
    if isinstance(snapshot, dict):
        cache.set(CACHE_KEY_FUTURES, snapshot, timeout=CACHE_TTL_PRICE)
        cache.set(CACHE_KEY_FUTURES_LAST_GOOD, snapshot, timeout=None)
    return snapshot


def get_cached_intermarket_technical_context():
    context = cache.get(CACHE_KEY_INTERMARKET_CONTEXT)
    if isinstance(context, dict):
        return context
    latest_prediction = (
        WorldModelPrediction.objects.exclude(context={}).order_by("-created_at").first()
    )
    if latest_prediction and isinstance(latest_prediction.context, dict):
        saved = latest_prediction.context
        if "confirmation_score" in saved:
            return saved
    return get_intermarket_technical_snapshot()


def update_intermarket_technical_cache():
    context = get_intermarket_technical_snapshot()
    cache.set(CACHE_KEY_INTERMARKET_CONTEXT, context, timeout=CACHE_TTL_JGB)
    cache.set(CACHE_KEY_INTERMARKET_FETCHED_AT, timezone.now(), timeout=None)
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
        cache.set(CACHE_KEY_PER_LATEST_VALUES, values, timeout=None)
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


def build_technical_data(price):
    return {
        "price": round(price, 0) if price else 0,
        "price_display": format_price(price, decimals=0),
    }


def get_stale_futures_snapshot():
    snapshot = cache.get(CACHE_KEY_FUTURES_LAST_GOOD)
    if isinstance(snapshot, dict):
        snapshot = dict(snapshot)
        snapshot["is_stale"] = True
        snapshot["fallback_reason"] = "last_good_cache"
        return snapshot
    latest_snapshot = (
        MarketSnapshot.objects.filter(source="225navi")
        .order_by("-fetched_at", "-created_at")
        .first()
    )
    if latest_snapshot is None:
        latest_snapshot = MarketSnapshot.objects.order_by("-fetched_at", "-created_at").first()
    if latest_snapshot is None:
        return None
    fetched_at = latest_snapshot.fetched_at or latest_snapshot.created_at
    snapshot = {
        "source": latest_snapshot.source or "saved_snapshot",
        "fetched_at": fetched_at,
    }
    is_stale = is_snapshot_stale(snapshot)
    return attach_saved_daily_bars({
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
    })


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
        "target_1_probability_display": _target_probability_display(
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
        "expected_return": _expected_return_value((prediction.expected_returns or {}).get(horizon)),
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


def _target_probability_display(targets, index):
    if not targets or len(targets) <= index:
        return "N/A"
    target = targets[index]
    if not isinstance(target, dict):
        return "N/A"
    if target.get("probability_display"):
        return target["probability_display"]
    probability = target.get("probability")
    if probability is None:
        return "表示停止"
    return f"旧形式 {float(probability):.2f}"


def _expected_return_value(value):
    if isinstance(value, dict):
        return value.get("value")
    return value


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


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_bool(value, default=None):
    if value in (None, ""):
        return default
    return str(value).lower() in {"1", "true", "yes", "on"}
