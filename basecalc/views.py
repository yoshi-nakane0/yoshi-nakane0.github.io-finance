import json
import logging
import os
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.db import OperationalError, ProgrammingError
from django.http import HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from myproject.settings import is_serverless_runtime

from .intermarket_technicals import get_intermarket_technical_snapshot
from .intermarket_technicals import build_us_index_technical_context
from .market_bars import attach_saved_daily_bars
from .market_shock import build_market_shock_context
from .market_context import _price_action_fallback_assets
from .nikkei_bias import get_jgb10y_yield_percent, get_nikkei_per_values
from .models import MarketBar, MarketSnapshot, PredictionOutcome, WorldModelPrediction
from .outcomes import (
    evaluate_due_predictions,
    performance_summary,
    save_prediction,
)
from .persistence import import_basecalc_history
from .serializers import serialize_snapshot
from .services.decision_context import (
    build_basecalc_decision_context,
    build_basecalc_top_context,
    enrich_basecalc_context,
    ensure_plain_summary_card_display,
)
from .snapshot import load_basecalc_snapshot
from .status import load_basecalc_status, status_display_rows
from .status import (
    intermarket_status_entry,
    price_status_entry,
)
from .validation_report import load_validation_report
from .world_model import build_features, build_world_model, _normalize_ohlcv
from .data_quality import is_snapshot_stale
from .github_actions import dispatch_refresh_workflow, get_refresh_workflow_state
from .output_contract import apply_output_contract
from .signal_contract import build_basecalc_signal_contract
from .targets import build_targets

logger = logging.getLogger(__name__)

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
CACHE_KEY_RUNTIME_HISTORY_IMPORT = "basecalc_runtime_history_import"
CACHE_TTL_PRICE = 300
CACHE_TTL_JGB = 3600
PER_FETCH_MIN_INTERVAL_SEC = 21600
JGB_FETCH_MIN_INTERVAL_SEC = 3600
FUTURES_FETCH_MIN_INTERVAL_SEC = 60
BASECALC_HISTORY_PATH = Path(__file__).resolve().parent / "data" / "basecalc_history.json"
TRUSTED_FUTURES_SOURCES = ("cme_daily_bulletin", "225navi", "matsui")


def index(request):
    can_update_basecalc_data = request.user.is_authenticated and request.user.is_staff
    if request.method == "POST":
        if request.POST.get("action") != "update":
            return HttpResponseBadRequest("Invalid action")
        if not can_update_basecalc_data:
            return HttpResponseForbidden("Forbidden")
        ensure_runtime_basecalc_history()

    force_update = request.method == "POST"
    manual_price = normalize_price(parse_float_param(request.GET.get("price"))) if not force_update else None
    if not force_update and manual_price is not None:
        try:
            context = build_context(
                request,
                force_update=False,
                persist_price_override=False,
            )
        except Exception as exc:
            context = {
                "error": str(exc),
                "can_update_basecalc_data": can_update_basecalc_data,
            }
        if can_update_basecalc_data:
            context["refresh_workflow_state"] = get_refresh_workflow_state()
        return render(request, "basecalc/index.html", context)

    if not force_update:
        snapshot = load_basecalc_snapshot()
        if snapshot:
            basecalc_status = load_basecalc_status()
            if _saved_snapshot_should_use_runtime_context(snapshot, basecalc_status):
                try:
                    context = build_context(request, force_update=False)
                except Exception as exc:
                    context = {
                        "error": str(exc),
                        "can_update_basecalc_data": can_update_basecalc_data,
                    }
                if can_update_basecalc_data:
                    context["refresh_workflow_state"] = get_refresh_workflow_state()
                return _render_basecalc_index(request, context)
            context = dict(snapshot)
            context["can_update_basecalc_data"] = can_update_basecalc_data
            if can_update_basecalc_data:
                context["refresh_workflow_state"] = get_refresh_workflow_state()
            context["detail_mode"] = request.GET.get("detail") == "1"
            if _should_hydrate_saved_snapshot_context(request):
                hydrate_saved_snapshot_context(context)
            apply_saved_snapshot_status_context(context, basecalc_status)
            enrich_basecalc_context(context)
            return _render_basecalc_index(request, context)
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
    return _render_basecalc_index(request, context)


def _should_hydrate_saved_snapshot_context(request):
    return request.GET.get("detail") == "1" or request.GET.get("hydrate") == "1"


def _render_basecalc_index(request, context):
    response = render(request, "basecalc/index.html", context)
    if _can_cache_basecalc_index(request, context):
        response.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=300"
    return response


def _saved_snapshot_should_use_runtime_context(snapshot, basecalc_status):
    if not isinstance(snapshot, dict):
        return False
    world_model = snapshot.get("world_model") or {}
    if not isinstance(world_model, dict):
        return False
    if not _saved_snapshot_price_status_is_newer(snapshot, world_model, basecalc_status):
        return False
    latest_snapshot = get_stale_futures_snapshot()
    if not isinstance(latest_snapshot, dict):
        return False
    return _market_snapshot_is_current_for_saved_snapshot(
        latest_snapshot,
        snapshot,
        world_model,
    )


def _can_cache_basecalc_index(request, context):
    return (
        request.method == "GET"
        and not request.user.is_authenticated
        and not context.get("detail_mode")
        and not context.get("error")
    )


def dispatch_basecalc_refresh_workflow(request):
    if request.method != "POST":
        return HttpResponseBadRequest("Invalid method")
    if not (request.user.is_authenticated and request.user.is_staff):
        return HttpResponseForbidden("Forbidden")
    dispatch_refresh_workflow()
    return redirect("basecalc:index")


def ensure_runtime_basecalc_history(history_path=BASECALC_HISTORY_PATH):
    if not _should_import_runtime_basecalc_history():
        logger.info(
            "basecalc runtime history hydration skipped: serverless=%s debug=%s sqlite_env=%s",
            is_serverless_runtime(),
            settings.DEBUG,
            os.getenv("SQLITE_DB_PATH") or "",
        )
        return {"skipped": True, "reason": "not_serverless"}
    if cache.get(CACHE_KEY_RUNTIME_HISTORY_IMPORT):
        return {"skipped": True, "reason": "cached"}
    try:
        existing_outcomes = PredictionOutcome.objects.count()
        bundled_outcomes = _bundled_history_outcome_count(history_path)
        if existing_outcomes and existing_outcomes >= bundled_outcomes:
            cache.set(CACHE_KEY_RUNTIME_HISTORY_IMPORT, True, timeout=3600)
            logger.info(
                "basecalc runtime history hydration skipped: existing_outcomes=%s bundled_outcomes=%s",
                existing_outcomes,
                bundled_outcomes,
            )
            return {"skipped": True, "reason": "history_exists"}
    except (OperationalError, ProgrammingError):
        return {"skipped": True, "reason": "db_unavailable"}

    result = import_basecalc_history(str(history_path))
    logger.info("basecalc runtime history hydration result: %s", result)
    cache.set(
        CACHE_KEY_RUNTIME_HISTORY_IMPORT,
        True,
        timeout=3600 if not result.get("skipped") else 300,
    )
    return result


def _bundled_history_outcome_count(history_path):
    try:
        payload = json.loads(Path(history_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    return len(payload.get("outcomes") or [])


def _should_import_runtime_basecalc_history():
    default_database = settings.DATABASES.get("default", {})
    is_sqlite = default_database.get("ENGINE") == "django.db.backends.sqlite3"
    runtime_sqlite_path = os.getenv("SQLITE_DB_PATH") or ""
    uses_tmp_sqlite = Path(runtime_sqlite_path).as_posix().startswith("/tmp/")
    return is_sqlite and (is_serverless_runtime() or (not settings.DEBUG and uses_tmp_sqlite))


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


def build_context(request, force_update=False, persist_price_override=None):
    params = request.POST if force_update else request.GET
    if persist_price_override is None:
        persist_price_override = force_update
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
        if persist_price_override:
            cache.set(CACHE_KEY_PRICE, price, timeout=CACHE_TTL_PRICE)
    elif force_update and price is not None:
        cache.set(CACHE_KEY_PRICE, price, timeout=CACHE_TTL_PRICE)

    if price is None:
        price = 0.0

    futures_snapshot = attach_saved_daily_bars(futures_snapshot)
    status_snapshot = futures_snapshot
    manual_price_override = _manual_price_override_context(price_override)
    model_snapshot = _snapshot_with_manual_price_override(
        futures_snapshot,
        price_override,
    )
    world_model = build_world_model(price, model_snapshot, intermarket_context)
    ensure_plain_summary_card_display(world_model)
    basecalc_status = _status_with_current_values(
        load_basecalc_status(),
        futures_snapshot=status_snapshot,
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
    apply_output_contract(
        world_model,
        display_price=price,
        validation_report=load_validation_report(),
        performance_by_horizon=backtest_performance_by_horizon,
    )
    world_model["basecalc_signal"] = build_basecalc_signal_contract(world_model)
    if force_update:
        evaluate_due_predictions(price)
        save_prediction(world_model)

    market_shock_context = _safe_market_shock_context(
        futures_snapshot,
        intermarket_context,
    )
    basecalc_status_rows = status_display_rows(basecalc_status, world_model)
    if manual_price_override["active"]:
        basecalc_status_rows.append(_manual_price_status_row(manual_price_override))
    decision = build_basecalc_decision_context(
        world_model,
        market_shock_context,
        basecalc_status_rows,
        backtest_performance_by_horizon.get("1d"),
    )
    basecalc_top = build_basecalc_top_context(
        world_model,
        decision,
        basecalc_status_rows,
        backtest_performance_by_horizon.get("1d"),
    )

    return {
        "data": data,
        "decision": decision,
        "basecalc_top": basecalc_top,
        "world_model": world_model,
        "market_shock": market_shock_context,
        "intermarket_technicals": world_model.get("intermarket_technicals") or {},
        "basecalc_status": basecalc_status,
        "basecalc_status_rows": basecalc_status_rows,
        "manual_price_override": manual_price_override,
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


def _manual_price_override_context(price):
    if price is None:
        return {"active": False}
    return {
        "active": True,
        "price": int(price),
        "price_display": format_price(price, decimals=0),
        "label": "手入力価格を判定に使用中",
        "decision_label": "一時判定",
    }


def _manual_price_status_row(manual_price):
    return {
        "key": "manual_price",
        "label": "手入力価格",
        "age_display": "適用中",
        "source": manual_price["price_display"],
        "fallback_display": "対象外",
        "decision_label": manual_price["decision_label"],
        "decision_level": "limited",
        "last_success_at": "",
        "last_failed_at": "",
    }


def _snapshot_with_manual_price_override(snapshot, price):
    if price is None or not isinstance(snapshot, dict):
        return snapshot
    root = dict(snapshot)
    _apply_manual_price_to_snapshot_frame(root, price)
    timeframes = {}
    for key, frame in (snapshot.get("timeframes") or {}).items():
        if isinstance(frame, dict):
            next_frame = dict(frame)
            _apply_manual_price_to_snapshot_frame(next_frame, price)
            timeframes[key] = next_frame
        else:
            timeframes[key] = frame
    if timeframes:
        root["timeframes"] = timeframes
    root["manual_price_override"] = True
    root["manual_price"] = int(price)
    return root


def _apply_manual_price_to_snapshot_frame(frame, price):
    frame["price"] = price
    frame["close"] = price
    for key in ("closes", "highs", "lows"):
        values = list(frame.get(key) or [])
        if not values:
            continue
        if key == "highs":
            values[-1] = max(_float_or_none(values[-1]) or price, price)
        elif key == "lows":
            values[-1] = min(_float_or_none(values[-1]) or price, price)
        else:
            values[-1] = price
        frame[key] = values
    previous_close = _manual_previous_close(frame)
    if previous_close:
        frame["change_pct"] = round(((price - previous_close) / previous_close) * 100, 4)


def _manual_previous_close(frame):
    previous_close = _float_or_none(frame.get("previous_close"))
    if previous_close:
        return previous_close
    closes = frame.get("closes") or []
    if len(closes) >= 2:
        return _float_or_none(closes[-2])
    return None


def _float_or_none(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
    hydrate_saved_snapshot_intermarket_context(context, world_model)
    world_model = hydrate_saved_snapshot_world_model(context, world_model)
    hydrate_saved_snapshot_current_price(context, world_model)
    return context


def apply_saved_snapshot_status_context(context, basecalc_status=None):
    if not isinstance(context, dict):
        return context
    world_model = context.get("world_model") or {}
    if not isinstance(world_model, dict):
        return context
    basecalc_status = basecalc_status or load_basecalc_status()
    if not isinstance(basecalc_status, dict) or not basecalc_status.get("updated_at"):
        return context

    context["basecalc_status"] = basecalc_status
    context["basecalc_status_rows"] = status_display_rows(basecalc_status, world_model)
    if _saved_snapshot_price_status_is_newer(context, world_model, basecalc_status):
        _limit_saved_snapshot_contract(
            world_model,
            "価格データ更新後にbasecalc判断が再作成されていません",
        )
    return context


def _saved_snapshot_price_status_is_newer(context, world_model, basecalc_status):
    price_status = basecalc_status.get("price_data") or {}
    price_timestamp = _parse_status_timestamp(price_status.get("last_success_at"))
    snapshot_timestamp = _parse_status_timestamp(
        context.get("generated_at")
        or world_model.get("generated_at")
        or world_model.get("as_of")
        or world_model.get("last_updated_display")
    )
    return bool(price_timestamp and snapshot_timestamp and price_timestamp > snapshot_timestamp)


def _limit_saved_snapshot_contract(world_model, reason):
    reasons = list(world_model.get("stop_reasons") or [])
    if reason not in reasons:
        reasons.insert(0, reason)
    world_model["stop_reasons"] = reasons
    contract = dict(world_model.get("output_contract") or {})
    contract["contract_status"] = "limited"
    contract["directional_allowed"] = False
    contract["target_display_allowed"] = False
    contract["probability_display_allowed"] = False
    contract["allowed_direction"] = "stopped"
    contract["available_display"] = "支持抵抗・ATRレンジのみ"
    contract_reasons = list(contract.get("stop_reasons") or [])
    if reason not in contract_reasons:
        contract_reasons.insert(0, reason)
    contract["stop_reasons"] = contract_reasons
    world_model["output_contract"] = contract
    world_model["contract_status"] = "limited"


def _parse_status_timestamp(value):
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith(" JST"):
            text = text[:-4].strip() + "+09:00"
        elif " " in text and "T" not in text:
            text = text.replace(" ", "T", 1)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    return parsed


def hydrate_saved_snapshot_world_model(context, world_model):
    if not _saved_world_model_needs_rebuild(world_model):
        return world_model
    model_price = normalize_price(world_model.get("price"))
    latest_snapshot = get_stale_futures_snapshot()
    latest_price = price_from_futures_snapshot(latest_snapshot)
    use_latest_snapshot = (
        latest_price is not None
        and _market_snapshot_is_current_for_saved_snapshot(
            latest_snapshot,
            context,
            world_model,
        )
    )
    rebuild_price = latest_price if use_latest_snapshot else model_price
    if rebuild_price is None:
        return world_model
    if use_latest_snapshot and isinstance(latest_snapshot, dict):
        snapshot = dict(latest_snapshot)
    else:
        snapshot = _saved_snapshot_rebuild_frame(context, world_model, rebuild_price)
    snapshot = attach_saved_daily_bars(snapshot)
    if len(snapshot.get("closes") or []) < 100:
        return world_model
    _apply_rebuild_price_to_snapshot(snapshot, rebuild_price)
    intermarket_context = (
        world_model.get("us_index_confirmation")
        or world_model.get("intermarket_technicals")
        or {}
    )
    rebuilt = build_world_model(rebuild_price, snapshot, intermarket_context)
    if int(rebuilt.get("confidence_score") or 0) <= int(world_model.get("confidence_score") or 0):
        return world_model
    context["world_model"] = rebuilt
    data = context.setdefault("data", {})
    data["price_display"] = format_price(rebuild_price, decimals=0)
    data["world_model"] = rebuilt
    data.update(rebuilt)
    context["price_param"] = format_price_param(rebuild_price)
    return rebuilt


def _apply_rebuild_price_to_snapshot(snapshot, price):
    _apply_manual_price_to_snapshot_frame(snapshot, price)
    timeframes = snapshot.get("timeframes")
    if not isinstance(timeframes, dict):
        return
    for frame in timeframes.values():
        if isinstance(frame, dict):
            _apply_manual_price_to_snapshot_frame(frame, price)


def _saved_world_model_needs_rebuild(world_model):
    confidence_score = int(world_model.get("confidence_score") or 0)
    similar_summary = world_model.get("similar_summary") or {}
    intermarket = world_model.get("us_index_confirmation") or {}
    components = intermarket.get("components") if isinstance(intermarket, dict) else {}
    return (
        confidence_score < 70
        and (
            int(similar_summary.get("case_count") or 0) < 30
            or int(similar_summary.get("searched_case_count") or 0) < 100
            or not components
        )
    )


def _saved_snapshot_rebuild_frame(context, world_model, model_price):
    data_quality = world_model.get("data_quality") or {}
    source_status = world_model.get("source_status") or {}
    features = world_model.get("features") or {}
    timestamp = (
        ((world_model.get("output_contract") or {}).get("generated_at"))
        or context.get("generated_at")
        or world_model.get("last_updated_display")
    )
    return {
        "symbol": source_status.get("symbol") or data_quality.get("symbol") or features.get("symbol") or "NIY=F",
        "source": source_status.get("source") or data_quality.get("source") or features.get("source") or "225navi",
        "instrument_key": source_status.get("instrument_key") or features.get("instrument_key") or "cme_nikkei_futures",
        "instrument_type": source_status.get("instrument_type") or data_quality.get("instrument_type") or features.get("instrument_type") or "futures",
        "price": model_price,
        "close": model_price,
        "fetched_at": timestamp,
        "quality": data_quality,
    }


def hydrate_saved_snapshot_intermarket_context(context, world_model):
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


def hydrate_saved_snapshot_current_price(context, world_model):
    latest_snapshot = get_stale_futures_snapshot()
    latest_price = price_from_futures_snapshot(latest_snapshot)
    model_price = normalize_price(world_model.get("price"))
    if latest_price is None:
        apply_output_contract(
            world_model,
            display_price=model_price,
            validation_report=_validation_report_for_saved_snapshot(context, world_model),
            performance_by_horizon=context.get("backtest_performance_by_horizon") or {},
        )
        world_model["basecalc_signal"] = build_basecalc_signal_contract(world_model)
        _refresh_saved_snapshot_status_rows(context, world_model)
        return context

    if not _market_snapshot_is_current_for_saved_snapshot(latest_snapshot, context, world_model):
        context["latest_price"] = model_price
        context["latest_price_display"] = format_price(model_price, decimals=0)
        context["model_price_display"] = format_price(model_price, decimals=0)
        apply_output_contract(
            world_model,
            display_price=model_price,
            validation_report=_validation_report_for_saved_snapshot(context, world_model),
            performance_by_horizon=context.get("backtest_performance_by_horizon") or {},
        )
        world_model["basecalc_signal"] = build_basecalc_signal_contract(world_model)
        _refresh_saved_snapshot_status_rows(context, world_model)
        return context

    context["latest_price"] = latest_price
    context["latest_price_display"] = format_price(latest_price, decimals=0)
    context["model_price_display"] = format_price(model_price, decimals=0)
    data = context.setdefault("data", {})
    if model_price is not None:
        data["price_display"] = format_price(model_price, decimals=0)
    data_world_model = data.get("world_model")
    if isinstance(data_world_model, dict):
        data_world_model["price"] = world_model.get("price")
    context["price_param"] = format_price_param(model_price)

    _refresh_saved_snapshot_status_rows(context, world_model, latest_snapshot)
    apply_output_contract(
        world_model,
        display_price=latest_price,
        latest_price=latest_price,
        validation_report=_validation_report_for_saved_snapshot(context, world_model),
        performance_by_horizon=context.get("backtest_performance_by_horizon") or {},
    )
    if not _practical_lines_match_latest_price(world_model, latest_price):
        _attach_practical_lines_from_latest_snapshot(world_model, latest_snapshot, latest_price)
    world_model["basecalc_signal"] = build_basecalc_signal_contract(world_model)
    return context


def _practical_lines_match_latest_price(world_model, latest_price):
    if not isinstance(world_model, dict):
        return False
    practical_lines = world_model.get("practical_lines")
    if not isinstance(practical_lines, dict):
        return False
    current_price = normalize_price(practical_lines.get("current_price"))
    latest_price = normalize_price(latest_price)
    if current_price is None or latest_price is None or current_price != latest_price:
        return False
    if practical_lines.get("target_model_version") != "targets_v2":
        return False
    required_keys = (
        "upside_resistance",
        "downside_support",
    )
    return all(normalize_price(practical_lines.get(key)) is not None for key in required_keys)


def _attach_practical_lines_from_latest_snapshot(world_model, latest_snapshot, latest_price):
    if not isinstance(world_model, dict) or not isinstance(latest_snapshot, dict):
        return world_model
    latest_price = normalize_price(latest_price)
    if latest_price is None:
        return world_model
    snapshot = attach_saved_daily_bars(dict(latest_snapshot))
    _apply_rebuild_price_to_snapshot(snapshot, latest_price)
    daily_snapshot = (snapshot.get("timeframes") or {}).get("1d") or snapshot
    ohlcv = _normalize_ohlcv(latest_price, daily_snapshot, allow_synthetic=False)
    if len(ohlcv.get("closes") or []) < 20:
        return world_model
    features = build_features(latest_price, daily_snapshot, ohlcv)
    similar_summary = world_model.get("similar_summary") if isinstance(world_model.get("similar_summary"), dict) else {}
    targets = build_targets(features, similar_summary)
    near_levels = targets.get("near_levels") or {}
    upside_row = _first_structural_target(targets.get("upside"))
    downside_row = _first_structural_target(targets.get("downside"))
    near_upside_row = _first_level_row(near_levels.get("upside"))
    near_downside_row = _first_level_row(near_levels.get("downside"))
    practical_lines = {
        "target_model_version": "targets_v2",
        "current_price": latest_price,
        "upside_resistance": _line_price(upside_row),
        "downside_support": _line_price(downside_row),
        "near_upside": _line_price(near_upside_row),
        "near_downside": _line_price(near_downside_row),
        "upside_resistance_detail": _line_payload(upside_row),
        "downside_support_detail": _line_payload(downside_row),
        "near_upside_detail": _line_payload(near_upside_row),
        "near_downside_detail": _line_payload(near_downside_row),
        "source": latest_snapshot.get("source"),
        "source_timestamp": latest_snapshot.get("fetched_at"),
    }
    world_model["practical_lines"] = practical_lines
    world_model["near_levels"] = near_levels
    world_model["upside_targets"] = targets.get("upside") or world_model.get("upside_targets") or []
    world_model["downside_targets"] = targets.get("downside") or world_model.get("downside_targets") or []
    world_model["target_ranges"] = targets.get("target_ranges") or world_model.get("target_ranges") or []
    if targets.get("invalidation"):
        world_model["invalidation"] = targets.get("invalidation")
    return world_model


def _first_target_price(rows):
    first = (rows or [None])[0]
    if isinstance(first, dict):
        return first.get("price")
    return None


def _first_structural_target(rows):
    usable = [row for row in (rows or []) if isinstance(row, dict) and row.get("price") is not None]
    if not usable:
        return None
    for row in usable:
        if row.get("line_role") in {"structural", "psychological"}:
            return row
    for row in usable:
        if row.get("line_role") != "atr_projection":
            return row
    return usable[0]


def _first_level_row(rows):
    first = (rows or [None])[0]
    return first if isinstance(first, dict) else None


def _line_price(row):
    return row.get("price") if isinstance(row, dict) else None


def _line_payload(row):
    if not isinstance(row, dict):
        return None
    return {
        "price": row.get("price"),
        "reason": row.get("reason") or "",
        "source": row.get("source") or "",
        "sources": row.get("sources") or ([row.get("source")] if row.get("source") else []),
        "line_role": row.get("line_role") or "",
        "confidence": row.get("confidence") or "",
        "distance_abs": row.get("distance_abs"),
        "distance_pct": row.get("distance_pct"),
        "distance_atr": row.get("distance_atr"),
        "rank_score": row.get("rank_score"),
        "confluence_count": row.get("confluence_count"),
    }


def _refresh_saved_snapshot_status_rows(context, world_model, latest_snapshot=None):
    basecalc_status = dict(context.get("basecalc_status") or {})
    if latest_snapshot is not None:
        basecalc_status["price_data"] = price_status_entry(
            latest_snapshot,
            world_model.get("readiness_level"),
        )
    intermarket_context = (
        world_model.get("us_index_confirmation")
        or world_model.get("intermarket_technicals")
    )
    if isinstance(intermarket_context, dict) and intermarket_context:
        basecalc_status["intermarket"] = intermarket_status_entry(intermarket_context)
    context["basecalc_status"] = basecalc_status
    context["basecalc_status_rows"] = status_display_rows(
        basecalc_status,
        _world_model_for_status_rows(world_model, basecalc_status, latest_snapshot),
    )
    return context


def _world_model_for_status_rows(world_model, basecalc_status, latest_snapshot=None):
    if not isinstance(world_model, dict) or not isinstance(latest_snapshot, dict):
        return world_model
    price_entry = (basecalc_status or {}).get("price_data") or {}
    source_status = dict(world_model.get("source_status") or {})
    for key in ("source", "symbol", "instrument_key", "instrument_type"):
        if latest_snapshot.get(key):
            source_status[key] = latest_snapshot.get(key)
    return {
        **world_model,
        "source_status": source_status,
        "stale_minutes": price_entry.get("age_minutes"),
    }


def _validation_report_for_saved_snapshot(context, world_model):
    report = load_validation_report()
    if not report:
        return None
    report_timestamp = _parse_saved_snapshot_timestamp(report.get("generated_at"))
    saved_timestamp = _saved_world_model_timestamp(context, world_model)
    if report_timestamp is not None and saved_timestamp is not None and report_timestamp < saved_timestamp:
        return None
    return report


def _market_snapshot_is_current_for_saved_snapshot(latest_snapshot, context, world_model):
    latest_timestamp = _parse_saved_snapshot_timestamp((latest_snapshot or {}).get("fetched_at"))
    saved_timestamp = _saved_world_model_timestamp(context, world_model)
    if latest_timestamp is None or saved_timestamp is None:
        return True
    return latest_timestamp >= saved_timestamp


def _saved_world_model_timestamp(context, world_model):
    output_contract = (world_model or {}).get("output_contract") or {}
    for value in (
        output_contract.get("source_timestamp"),
        (world_model or {}).get("last_updated_display"),
        (world_model or {}).get("as_of"),
        (context or {}).get("generated_at"),
        output_contract.get("generated_at"),
    ):
        timestamp = _parse_saved_snapshot_timestamp(value)
        if timestamp is not None:
            return timestamp
    return None


def _parse_saved_snapshot_timestamp(value):
    if not value:
        return None
    if isinstance(value, datetime):
        timestamp = value
    elif isinstance(value, str):
        normalized = value.strip()
        if normalized.endswith(" JST"):
            try:
                timestamp = datetime.strptime(normalized, "%Y-%m-%d %H:%M JST")
            except ValueError:
                return None
            timestamp = timestamp.replace(tzinfo=dt_timezone(timedelta(hours=9)))
        else:
            try:
                timestamp = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
            except ValueError:
                return None
    else:
        return None
    if timezone.is_naive(timestamp):
        timestamp = timezone.make_aware(timestamp, timezone=dt_timezone.utc)
    return timestamp


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
    latest_bar = (
        MarketBar.objects.filter(
            symbol="NIY=F",
            timeframe="1d",
            source__in=TRUSTED_FUTURES_SOURCES,
        )
        .order_by("-timestamp", "-created_at")
        .first()
    )
    if latest_bar is not None:
        return _snapshot_from_market_bar(latest_bar)
    latest_snapshot = (
        MarketSnapshot.objects.filter(source__in=TRUSTED_FUTURES_SOURCES)
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


def _snapshot_from_market_bar(latest_bar):
    previous_bar = (
        MarketBar.objects.filter(
            symbol=latest_bar.symbol,
            timeframe=latest_bar.timeframe,
            timestamp__lt=latest_bar.timestamp,
        )
        .order_by("-timestamp", "-created_at")
        .first()
    )
    fetched_at = latest_bar.timestamp
    close = latest_bar.close
    snapshot = {
        "source": latest_bar.source or "saved_market_bar",
        "fetched_at": fetched_at,
    }
    return attach_saved_daily_bars({
        "symbol": latest_bar.symbol,
        "name": latest_bar.symbol,
        "source": latest_bar.source or "saved_market_bar",
        "instrument_key": latest_bar.instrument_key,
        "instrument_type": latest_bar.instrument_type,
        "price": close,
        "previous_close": previous_bar.close if previous_bar else latest_bar.open or close,
        "change_pct": None,
        "opens": [latest_bar.open or close],
        "highs": [latest_bar.high or close],
        "lows": [latest_bar.low or close],
        "closes": [close],
        "volumes": [latest_bar.volume or 0],
        "timestamps": [int(latest_bar.timestamp.timestamp())],
        "fetched_at": fetched_at,
        "is_stale": is_snapshot_stale(snapshot),
        "fallback_reason": "saved_market_bar",
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
