"""macro モジュールのビュー。"""

import logging
import os
from datetime import datetime

from django.contrib import messages
from django.core.management import call_command
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from myproject.auth import is_creator_user

from .models import Indicator, RegimeSnapshot
from .services.commentary import (
    build_linkage_explanation,
    build_overview_commentary,
    build_similar_explanation,
)
from .services.dashboard import (
    build_crash_alert_context,
    build_forecast_monitor_context,
    build_historical_crash_similarity,
    build_indicator_cards,
    build_linkages,
    build_monthly_model_status,
    build_raw_archive_context,
    build_reliability_context,
    build_regime_context,
    build_forecast_model_context,
    build_model_validation_context,
    build_similar_periods,
    build_world_state_context,
    build_world_model_operations_context,
    load_crash_probability_model,
    load_lightgbm_prediction,
)
from .services.scenario import build_scenario_analysis, scenario_overrides_from_query
from .services.dashboard_cache import (
    invalidate_dashboard_cache,
    invalidate_indicator_detail_caches,
    invalidate_similar_detail_caches,
    load_dashboard_cache_meta,
    load_dashboard_payload,
    load_macro_update_status,
    load_indicator_detail_payload,
    load_similar_detail_payload,
    precompute_dashboard_payload,
    save_dashboard_payload,
    save_macro_update_status,
)
from .services.data_sync import (
    get_latest_observation_date,
    sync_all_indicators,
)
from .services.detail import (
    DEFAULT_RANGE_PARAM,
    RANGE_OPTIONS,
    build_indicator_detail_context,
    build_similar_detail_context,
    normalize_range_param,
)
from .services.fred_client import get_api_key
from .services.regime import compute_current_regime
from .services.world_state import compute_current_world_state
from .services.yfinance_client import sync_all_price_histories

logger = logging.getLogger(__name__)

SERVERLESS_REFRESH_SERIES_IDS = (
    'VIXCLS',
    'BAMLH0A0HYM2',
    'CBOE_SKEW',
    'MOVE_INDEX',
    'VIX_VIX3M_RATIO',
)


def _is_serverless_runtime():
    return any(
        os.getenv(name)
        for name in ('VERCEL', 'AWS_LAMBDA_FUNCTION_NAME', 'LAMBDA_TASK_ROOT')
    )


def _can_refresh_macro_data(user):
    return is_creator_user(user)


def _can_run_macro_model_jobs(user):
    return is_creator_user(user) and not _is_serverless_runtime()


def _record_macro_update_status(
    *,
    source: str,
    result=None,
    status=None,
    message: str = '',
    extra_failed=None,
):
    result = result or {}
    failed = list(result.get('failed') or [])
    failed.extend(extra_failed or [])
    success = list(result.get('success') or [])
    if status is None:
        if failed and not success:
            status = 'failed'
        elif failed:
            status = 'partial'
        else:
            status = 'success'
    payload = {
        'source': source,
        'status': status,
        'message': message,
        'success_count': len(success),
        'failed_count': len(failed),
        'failed': failed,
        'started_at': result.get('started_at'),
        'finished_at': result.get('finished_at') or timezone.now().isoformat(),
    }
    try:
        save_macro_update_status(payload)
    except Exception:
        logger.exception("Failed to save macro update status")


def _attach_reliability_context(
    context: dict,
    latest_snapshot,
    *,
    dashboard_cache_meta=None,
):
    context.update(build_regime_context(latest_snapshot))
    context['macro_reliability'] = build_reliability_context(
        last_updated=context.get('last_updated'),
        dashboard_cache_meta=dashboard_cache_meta,
        update_status=load_macro_update_status(),
        regime_model_version=context.get('regime_model_version'),
    )
    return context


def _refresh_serverless_macro_data(request):
    """本番向けの軽量更新。即時性の高い市場ストレス指標だけ取得する。"""
    try:
        result = sync_all_indicators(series_ids=SERVERLESS_REFRESH_SERIES_IDS)
    except Exception as exc:
        logger.exception("Serverless lightweight macro sync failed")
        _record_macro_update_status(
            source='manual_lightweight',
            status='failed',
            message='即時指標の取得に失敗しました。',
            extra_failed=[{'phase': 'sync_all_indicators', 'error': str(exc)}],
        )
        messages.error(request, f"更新中にエラー: {exc}")
        return redirect(reverse('macro:index'))

    ok_count = len(result['success'])
    ng_count = len(result['failed'])

    if ok_count == 0:
        _record_macro_update_status(
            source='manual_lightweight',
            result=result,
            status='failed',
            message='即時指標の取得に失敗しました。',
        )
        messages.error(request, f"即時指標 {ng_count} 件の取得に失敗しました")
        return redirect(reverse('macro:index'))

    extra_failed = []
    try:
        compute_current_regime()
    except Exception as exc:
        logger.exception("Serverless regime recomputation failed")
        extra_failed.append({'phase': 'regime', 'error': str(exc)})
        messages.warning(request, "即時指標は更新しましたが、判定更新でエラーが発生しました")
    try:
        compute_current_world_state()
    except Exception as exc:
        logger.exception("Serverless world state recomputation failed")
        extra_failed.append({'phase': 'world_state', 'error': str(exc)})

    try:
        payload = load_dashboard_payload() or {}
        latest_obs_date = get_latest_observation_date()
        payload.update({
            'has_observations': latest_obs_date is not None,
            'last_updated': latest_obs_date.isoformat() if latest_obs_date else '—',
            'indicator_cards': build_indicator_cards(),
            'crash_alert': build_crash_alert_context(),
            'world_state': build_world_state_context(),
        })
        save_dashboard_payload(payload)
        invalidate_indicator_detail_caches()
    except Exception as exc:
        logger.exception("Serverless dashboard cache patch failed")
        extra_failed.append({'phase': 'dashboard_cache', 'error': str(exc)})
        messages.warning(request, "即時指標は更新しましたが、画面キャッシュ更新でエラーが発生しました")

    _record_macro_update_status(
        source='manual_lightweight',
        result=result,
        message='即時指標を更新しました。',
        extra_failed=extra_failed,
    )
    if ng_count == 0:
        messages.success(request, f"即時指標 {ok_count} 件を更新しました")
    else:
        messages.warning(request, f"即時指標 {ok_count} 件成功、{ng_count} 件失敗")
    return redirect(reverse('macro:index'))


def index(request):
    """macro モジュールのトップ画面。重い計算は事前計算キャッシュから取得。"""
    custom_scenario = scenario_overrides_from_query(request.GET)
    cache_payload = load_dashboard_payload()

    if cache_payload is not None:
        dashboard_cache_meta = load_dashboard_cache_meta()
        latest_snapshot = RegimeSnapshot.objects.order_by('-snapshot_date').first()
        context = dict(cache_payload)
        context['has_observations'] = context.get('has_observations', True)
        context['dashboard_cache_missing'] = not context['has_observations']
        if (
            context['has_observations']
            and (
                not context.get('crash_alert')
                or 'data_quality_pct' not in context['crash_alert']
            )
        ):
            context['crash_alert'] = build_crash_alert_context()
        context['fred_key_present'] = bool(get_api_key())
        context['can_refresh_macro_data'] = _can_refresh_macro_data(request.user)
        context['can_run_macro_model_jobs'] = _can_run_macro_model_jobs(request.user)
        context['lightgbm_prediction'] = load_lightgbm_prediction()
        context['crash_probability_model'] = load_crash_probability_model()
        context['monthly_model_status'] = build_monthly_model_status()
        context['forecast_monitor'] = (
            context.get('forecast_monitor') or build_forecast_monitor_context()
        )
        context['world_state'] = (
            context.get('world_state') or build_world_state_context()
        )
        context['forecast_models'] = (
            context.get('forecast_models') or build_forecast_model_context()
        )
        context['model_validation'] = (
            context.get('model_validation') or build_model_validation_context()
        )
        context['world_model_operations'] = (
            context.get('world_model_operations')
            or build_world_model_operations_context()
        )
        context['raw_archive_status'] = (
            context.get('raw_archive_status') or build_raw_archive_context()
        )
        context['scenario_analysis'] = build_scenario_analysis(custom_scenario) if custom_scenario else (
            context.get('scenario_analysis') or build_scenario_analysis()
        )
        similar_periods = context.get('similar_periods', [])
        linkages = context.get('linkages', [])
        context['overview_commentary'] = build_overview_commentary(
            latest_snapshot, similar_periods
        )
        context['similar_commentary'] = build_similar_explanation(similar_periods)
        context['linkage_commentary'] = build_linkage_explanation(linkages)
        _attach_reliability_context(
            context,
            latest_snapshot,
            dashboard_cache_meta=dashboard_cache_meta,
        )
        return render(request, 'macro/index.html', context)

    latest_obs_date = get_latest_observation_date()
    latest_snapshot = RegimeSnapshot.objects.order_by('-snapshot_date').first()
    fred_key_present = bool(get_api_key())
    has_observations = latest_obs_date is not None

    if _is_serverless_runtime():
        context = {
            'has_observations': has_observations,
            'last_updated': (
                latest_obs_date.isoformat() if latest_obs_date else '—'
            ),
            'fred_key_present': fred_key_present,
            'can_refresh_macro_data': _can_refresh_macro_data(request.user),
            'can_run_macro_model_jobs': _can_run_macro_model_jobs(request.user),
            'indicator_cards': build_indicator_cards() if has_observations else [],
            'crash_alert': None,
            'historical_crash_similarity': [],
            'lightgbm_prediction': load_lightgbm_prediction(),
            'crash_probability_model': load_crash_probability_model(),
            'monthly_model_status': build_monthly_model_status(),
            'forecast_monitor': build_forecast_monitor_context(),
            'world_state': build_world_state_context(),
            'forecast_models': build_forecast_model_context(),
            'model_validation': build_model_validation_context(),
            'world_model_operations': build_world_model_operations_context(),
            'raw_archive_status': build_raw_archive_context(),
            'scenario_analysis': build_scenario_analysis(custom_scenario),
            'similar_periods': [],
            'linkages': [],
            'overview_commentary': None,
            'similar_commentary': build_similar_explanation([]),
            'linkage_commentary': build_linkage_explanation([]),
            'dashboard_cache_missing': True,
        }
        _attach_reliability_context(context, latest_snapshot)
        return render(request, 'macro/index.html', context)

    similar_periods = build_similar_periods() if has_observations else []
    linkages = build_linkages() if has_observations else []

    context = {
        'has_observations': has_observations,
        'last_updated': (
            latest_obs_date.isoformat() if latest_obs_date else '—'
        ),
        'fred_key_present': fred_key_present,
        'can_refresh_macro_data': _can_refresh_macro_data(request.user),
        'can_run_macro_model_jobs': _can_run_macro_model_jobs(request.user),
        'indicator_cards': build_indicator_cards() if has_observations else [],
        'crash_alert': build_crash_alert_context() if has_observations else None,
        'historical_crash_similarity': (
            build_historical_crash_similarity() if has_observations else []
        ),
        'lightgbm_prediction': load_lightgbm_prediction(),
        'crash_probability_model': load_crash_probability_model(),
        'monthly_model_status': build_monthly_model_status(),
        'forecast_monitor': build_forecast_monitor_context(),
        'world_state': build_world_state_context(),
        'forecast_models': build_forecast_model_context(),
        'model_validation': build_model_validation_context(),
        'world_model_operations': build_world_model_operations_context(),
        'raw_archive_status': build_raw_archive_context(),
        'scenario_analysis': build_scenario_analysis(custom_scenario),
        'similar_periods': similar_periods,
        'linkages': linkages,
        'overview_commentary': (
            build_overview_commentary(latest_snapshot, similar_periods)
            if has_observations else None
        ),
        'similar_commentary': build_similar_explanation(similar_periods),
        'linkage_commentary': build_linkage_explanation(linkages),
    }
    _attach_reliability_context(context, latest_snapshot)
    return render(request, 'macro/index.html', context)


@require_POST
def refresh(request):
    """全指標を FRED から再取得し、レジームを再計算する。"""
    if not is_creator_user(request.user):
        return HttpResponseForbidden("権限がありません。")

    if _is_serverless_runtime():
        return _refresh_serverless_macro_data(request)

    if not get_api_key():
        _record_macro_update_status(
            source='manual_full',
            status='failed',
            message='FRED_API_KEY が未設定のため更新できません。',
            extra_failed=[{
                'phase': 'FRED_API_KEY',
                'error': 'FRED_API_KEY が未設定です',
            }],
        )
        messages.error(
            request,
            "FRED_API_KEY が未設定のため取得できません。.env に設定してください。",
        )
        return redirect(reverse('macro:index'))

    try:
        result = sync_all_indicators()
    except Exception as exc:
        logger.exception("FRED sync failed")
        _record_macro_update_status(
            source='manual_full',
            status='failed',
            message='指標取得に失敗しました。',
            extra_failed=[{'phase': 'sync_all_indicators', 'error': str(exc)}],
        )
        messages.error(request, f"更新中にエラー: {exc}")
        return redirect(reverse('macro:index'))

    ok_count = len(result['success'])
    ng_count = len(result['failed'])
    if ng_count == 0:
        messages.success(request, f"指標 {ok_count} 件を更新しました")
    elif ok_count == 0:
        messages.error(request, f"全 {ng_count} 件の取得に失敗しました")
    else:
        messages.warning(
            request,
            f"指標 {ok_count} 件成功、{ng_count} 件失敗",
        )

    extra_failed = []
    regime_snapshot = None
    if ok_count > 0:
        try:
            regime_snapshot = compute_current_regime()
        except Exception as exc:
            logger.exception("Regime recomputation failed")
            extra_failed.append({'phase': 'regime', 'error': str(exc)})
            messages.warning(
                request,
                "指標は更新したがレジーム判定でエラーが発生しました（ログを確認）",
            )
        try:
            compute_current_world_state()
        except Exception as exc:
            logger.exception("World State recomputation failed")
            extra_failed.append({'phase': 'world_state', 'error': str(exc)})
            messages.warning(request, "World State 更新でエラーが発生しました")

    # 価格データも併せて更新
    try:
        price_result = sync_all_price_histories()
        price_ng = len(price_result['failed'])
        if price_ng > 0:
            extra_failed.extend(
                {
                    'ticker': item.get('ticker'),
                    'error': item.get('error'),
                }
                for item in price_result['failed']
            )
            messages.warning(request, f"価格データ {price_ng} 銘柄の取得に失敗")
    except Exception as exc:
        logger.exception("Price sync failed")
        extra_failed.append({'phase': 'price_sync', 'error': str(exc)})
        messages.warning(request, "価格データ更新でエラー（ログを確認）")

    # キャッシュ無効化後、トップ画面用キャッシュまで作り直す。
    # これにより「指標取得 → 判定 → 画面反映」をボタン1回で完了させる。
    invalidate_dashboard_cache()
    invalidate_indicator_detail_caches()
    invalidate_similar_detail_caches()

    if ok_count > 0 and regime_snapshot is not None:
        try:
            payload = precompute_dashboard_payload()
            save_dashboard_payload(payload)
            messages.success(request, "最新データで景気局面を再判定しました")
        except Exception as exc:
            logger.exception("Dashboard cache refresh failed")
            extra_failed.append({'phase': 'dashboard_cache', 'error': str(exc)})
            messages.warning(
                request,
                "判定は更新しましたが、重い分析の画面反映は次回表示時に再計算します",
            )

    _record_macro_update_status(
        source='manual_full',
        result=result,
        message='指標取得・判定・画面反映を実行しました。',
        extra_failed=extra_failed,
    )
    return redirect(reverse('macro:index'))


@require_POST
def recompute_crash_backtest(request):
    """月次検証と急落確率モデル再学習をまとめて実行する。"""
    if not is_creator_user(request.user):
        return HttpResponseForbidden("権限がありません。")
    if _is_serverless_runtime():
        messages.warning(request, "重い月次メンテナンスはローカル環境で実行してください。")
        return redirect(reverse('macro:index'))

    try:
        call_command(
            'backtest_crash_alert',
            target='GSPC',
            horizon_days=63,
            drawdown_threshold=-10.0,
            output='static/macro/crash_alert_backtest.json',
            csv_output='static/macro/crash_alert_backtest.csv',
        )
        call_command(
            'train_crash_probability_model',
            target='GSPC',
            horizon_days=63,
            drawdown_threshold=-10.0,
            validation_months=120,
        )
        payload = precompute_dashboard_payload()
        save_dashboard_payload(payload)
    except Exception as exc:
        logger.exception("Monthly crash maintenance failed")
        messages.error(request, f"月次メンテナンスに失敗しました: {exc}")
    else:
        messages.success(request, "月次検証と急落確率モデルを更新しました")
    return redirect(reverse('macro:index'))


def indicator_detail(request, series_id):
    """指標詳細ページ。?range= で表示期間を切り替えられる。"""
    indicator = get_object_or_404(
        Indicator, fred_series_id=series_id, is_active=True
    )
    range_param = normalize_range_param(request.GET.get('range'))

    # デフォルト期間のときだけキャッシュを使う
    context = None
    if range_param == DEFAULT_RANGE_PARAM:
        cached = load_indicator_detail_payload(series_id)
        if cached is not None:
            context = dict(cached)
            context['indicator'] = indicator

    if context is None:
        context = build_indicator_detail_context(indicator, range_param=range_param)

    context['range_param'] = range_param
    context['range_options'] = RANGE_OPTIONS
    return render(request, 'macro/indicator_detail.html', context)


def similar_period_detail(request, month):
    """類似局面詳細ページ。month は YYYY-MM-DD 形式（月初日推奨）。"""
    try:
        target = datetime.strptime(month, '%Y-%m-%d').date()
    except ValueError:
        raise Http404("Invalid month format")
    target = target.replace(day=1)
    month_iso = target.isoformat()

    cached = load_similar_detail_payload(month_iso)
    if cached is not None:
        context = dict(cached)
    else:
        context = build_similar_detail_context(target)

    return render(request, 'macro/similar_detail.html', context)
