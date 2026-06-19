"""macro モジュールのビュー。"""

import logging
import os
from datetime import datetime

import requests
from django.contrib import messages
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
    build_macro_decision_context,
    build_reliability_context,
    build_regime_context,
    build_static_reliability_context,
    build_top_decision_context,
    load_crash_probability_model,
    load_lightgbm_prediction,
    load_regime_probability_model,
)
from .services.scenario import build_scenario_analysis, scenario_overrides_from_query
from .services.dashboard_cache import (
    invalidate_dashboard_cache,
    invalidate_indicator_detail_caches,
    invalidate_similar_detail_caches,
    load_macro_update_status,
    load_indicator_detail_payload,
    load_similar_detail_payload,
    load_static_macro_operations_status,
    load_static_macro_payload,
    precompute_dashboard_payload,
    save_dashboard_payload,
    save_macro_update_status,
)
from .services.data_sync import sync_all_indicators
from .services.data_quality import build_data_quality_report
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
from .services.house_view import build_house_view_context

logger = logging.getLogger(__name__)

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
    static_payload=None,
    static_operations_status=None,
):
    context.update(build_regime_context(latest_snapshot))
    if 'macro_decision' not in context:
        context['macro_decision'] = build_macro_decision_context(latest_snapshot)
    static_reliability = None
    if static_payload is not None:
        static_reliability = build_static_reliability_context(
            static_payload,
            operations_status=static_operations_status,
            regime_model_version=context.get('regime_model_version'),
        )
    context['macro_reliability'] = static_reliability or build_reliability_context(
        last_updated=context.get('last_updated'),
        dashboard_cache_meta=dashboard_cache_meta,
        update_status=load_macro_update_status(),
        regime_model_version=context.get('regime_model_version'),
    )
    return context


def _refresh_serverless_macro_data(request):
    """本番では直接計算せず、外部の更新ジョブだけを起動する。"""
    webhook_url = os.getenv('MACRO_UPDATE_WEBHOOK_URL')
    if not webhook_url:
        _record_macro_update_status(
            source='manual_job_request',
            status='skipped',
            message='MACRO_UPDATE_WEBHOOK_URL が未設定です。',
        )
        messages.warning(
            request,
            "本番では画面から直接計算しません。GitHub Actionsの更新ジョブを実行してください。",
        )
        return redirect(reverse('macro:index'))

    try:
        response = requests.post(webhook_url, timeout=10)
        response.raise_for_status()
    except Exception as exc:
        logger.exception("Serverless macro job trigger failed")
        _record_macro_update_status(
            source='manual_job_request',
            status='failed',
            message='更新ジョブの起動に失敗しました。',
            extra_failed=[{'phase': 'trigger_update_job', 'error': str(exc)}],
        )
        messages.error(request, f"更新ジョブの起動に失敗しました: {exc}")
        return redirect(reverse('macro:index'))

    _record_macro_update_status(
        source='manual_job_request',
        status='success',
        message='更新ジョブを起動しました。',
    )
    messages.success(request, "更新ジョブを起動しました。完了後に画面へ反映されます。")
    return redirect(reverse('macro:index'))


def index(request):
    """macro モジュールのトップ画面。生成済みJSONだけを表示に使う。"""
    custom_scenario = scenario_overrides_from_query(request.GET)
    cache_payload = load_static_macro_payload()

    if cache_payload is None:
        latest_snapshot = RegimeSnapshot.objects.order_by('-snapshot_date').first()
        context = {
            'dashboard_cache_missing': True,
            'error_message': '事前計算データがありません。更新ジョブを確認してください。',
            'has_observations': False,
            'last_updated': '—',
            'fred_key_present': bool(get_api_key()),
            'can_refresh_macro_data': _can_refresh_macro_data(request.user),
            'can_run_macro_model_jobs': _can_run_macro_model_jobs(request.user),
            'indicator_cards': [],
            'crash_alert': None,
            'data_quality_report': build_data_quality_report(),
            'house_view': build_house_view_context(),
            'historical_crash_similarity': [],
            'lightgbm_prediction': load_lightgbm_prediction(),
            'crash_probability_model': load_crash_probability_model(),
            'regime_probability_model': load_regime_probability_model(),
            'monthly_model_status': {},
            'forecast_monitor': {},
            'house_view_validation': {},
            'macro_forecast_report': {},
            'macro_outcome_validation': {},
            'world_state': {},
            'forecast_models': {},
            'model_validation': {},
            'world_model_operations': {},
            'raw_archive_status': {},
            'vintage_status': {},
            'scenario_analysis': {},
            'similar_periods': [],
            'linkages': [],
            'overview_commentary': None,
            'similar_commentary': build_similar_explanation([]),
            'linkage_commentary': build_linkage_explanation([]),
            'audit_indicator_cards': [],
        }
        _attach_reliability_context(context, latest_snapshot)
        context['top_decision'] = build_top_decision_context(context)
        return render(request, 'macro/index.html', context)

    latest_snapshot = RegimeSnapshot.objects.order_by('-snapshot_date').first()
    context = dict(cache_payload)
    context['has_observations'] = context.get('has_observations', True)
    context['dashboard_cache_missing'] = False
    context['generated_payload_meta'] = {
        'generated_at': context.get('generated_at'),
        'source': context.get('source'),
        'data_quality': context.get('data_quality'),
        'stale': context.get('stale'),
        'model_version': context.get('model_version'),
        'job_duration_sec': context.get('job_duration_sec'),
        'warnings': context.get('warnings') or [],
    }
    context.setdefault('indicator_cards', [])
    context.setdefault('crash_alert', None)
    context.setdefault('data_quality_report', {})
    context.setdefault('house_view', {})
    context.setdefault('historical_crash_similarity', [])
    context.setdefault('monthly_model_status', {})
    context.setdefault('forecast_monitor', {})
    context.setdefault('house_view_validation', {})
    context.setdefault('macro_forecast_report', {})
    context.setdefault('macro_outcome_validation', {})
    context.setdefault('world_state', {})
    context.setdefault('forecast_models', {})
    context.setdefault('model_validation', {})
    context.setdefault('world_model_operations', {})
    context.setdefault('raw_archive_status', {})
    context.setdefault('vintage_status', {})
    context.setdefault('policy_expectation', {})
    context.setdefault('audit_indicator_cards', context.get('indicator_cards', []))
    if 'macro_decision' not in context:
        context['macro_decision'] = build_macro_decision_context(latest_snapshot)
    context.setdefault('scenario_analysis', {})
    context.setdefault('similar_periods', [])
    context.setdefault('linkages', [])
    if custom_scenario and not _is_serverless_runtime():
        context['scenario_analysis'] = build_scenario_analysis(custom_scenario)
    context['fred_key_present'] = bool(get_api_key())
    context['can_refresh_macro_data'] = _can_refresh_macro_data(request.user)
    context['can_run_macro_model_jobs'] = _can_run_macro_model_jobs(request.user)
    context['lightgbm_prediction'] = load_lightgbm_prediction()
    context['crash_probability_model'] = load_crash_probability_model()
    context['regime_probability_model'] = load_regime_probability_model()
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
        static_payload=cache_payload,
        static_operations_status=load_static_macro_operations_status(),
    )
    context['top_decision'] = build_top_decision_context(context)
    return render(request, 'macro/index.html', context)


def audit(request):
    """macro の運用・検証・詳細データを確認する監査用ページ。"""
    cache_payload = load_static_macro_payload() or {}
    latest_snapshot = RegimeSnapshot.objects.order_by('-snapshot_date').first()
    context = dict(cache_payload)
    context['dashboard_cache_missing'] = not bool(cache_payload)
    context.setdefault('has_observations', context.get('has_observations', False))
    context.setdefault('last_updated', context.get('last_updated', '—'))
    context.setdefault('indicator_cards', [])
    context.setdefault(
        'audit_indicator_cards',
        context.get('audit_indicator_cards') or context.get('indicator_cards') or [],
    )
    context.setdefault('crash_alert', None)
    context.setdefault('data_quality_report', {})
    context.setdefault('house_view', {})
    context.setdefault('historical_crash_similarity', [])
    context.setdefault('monthly_model_status', {})
    context.setdefault('forecast_monitor', {})
    context.setdefault('house_view_validation', {})
    context.setdefault('macro_forecast_report', {})
    context.setdefault('macro_outcome_validation', {})
    context.setdefault('world_state', {})
    context.setdefault('forecast_models', {})
    context.setdefault('model_validation', {})
    context.setdefault('world_model_operations', {})
    context.setdefault('raw_archive_status', {})
    context.setdefault('vintage_status', {})
    context.setdefault('policy_expectation', {})
    context.setdefault('scenario_analysis', {})
    context.setdefault('similar_periods', [])
    context.setdefault('linkages', [])
    context['fred_key_present'] = bool(get_api_key())
    context['can_refresh_macro_data'] = _can_refresh_macro_data(request.user)
    context['can_run_macro_model_jobs'] = _can_run_macro_model_jobs(request.user)
    context['lightgbm_prediction'] = load_lightgbm_prediction()
    context['crash_probability_model'] = load_crash_probability_model()
    context['regime_probability_model'] = load_regime_probability_model()
    context['similar_commentary'] = build_similar_explanation(
        context.get('similar_periods', [])
    )
    context['linkage_commentary'] = build_linkage_explanation(
        context.get('linkages', [])
    )
    _attach_reliability_context(
        context,
        latest_snapshot,
        static_payload=cache_payload or None,
        static_operations_status=load_static_macro_operations_status(),
    )
    return render(request, 'macro/audit.html', context)


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
    """重い月次検証は画面から実行せず、ローカル実行コマンドを案内する。"""
    if not is_creator_user(request.user):
        return HttpResponseForbidden("権限がありません。")
    messages.warning(
        request,
        "重い月次メンテナンスは画面から実行しません。"
        "ローカルで `python manage.py monthly_macro_maintenance` を実行してください。",
    )
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
