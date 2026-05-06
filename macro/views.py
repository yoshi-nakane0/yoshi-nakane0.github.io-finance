"""macro モジュールのビュー。"""

import logging
from datetime import datetime

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .models import Indicator, RegimeSnapshot
from .services.commentary import (
    build_linkage_explanation,
    build_overview_commentary,
    build_similar_explanation,
)
from .services.dashboard import (
    build_crash_alert_context,
    build_historical_crash_similarity,
    build_indicator_cards,
    build_linkages,
    build_regime_context,
    build_similar_periods,
    build_upcoming_events,
    invalidate_caches,
    load_lightgbm_prediction,
)
from .services.data_sync import (
    get_latest_observation_date,
    sync_all_indicators,
)
from .services.detail import (
    build_indicator_detail_context,
    build_similar_detail_context,
)
from .services.fred_client import get_api_key
from .services.regime import compute_current_regime
from .services.yfinance_client import sync_all_price_histories

logger = logging.getLogger(__name__)


def index(request):
    """macro モジュールのトップ画面"""
    import time
    def _t(label, t0):
        print(f"[macro.index] {label}: {time.perf_counter()-t0:.2f}s", flush=True)

    t0 = time.perf_counter()
    latest_obs_date = get_latest_observation_date()
    _t('latest_obs_date', t0)
    t0 = time.perf_counter()
    latest_snapshot = RegimeSnapshot.objects.order_by('-snapshot_date').first()
    _t('latest_snapshot', t0)

    fred_key_present = bool(get_api_key())
    has_observations = latest_obs_date is not None

    t0 = time.perf_counter()
    similar_periods = build_similar_periods() if has_observations else []
    _t('similar_periods', t0)
    t0 = time.perf_counter()
    linkages = build_linkages() if has_observations else []
    _t('linkages', t0)
    t0 = time.perf_counter()
    indicator_cards = build_indicator_cards() if has_observations else []
    _t('indicator_cards', t0)
    t0 = time.perf_counter()
    crash_alert = build_crash_alert_context() if has_observations else None
    _t('crash_alert', t0)
    t0 = time.perf_counter()
    historical_crash_similarity = (
        build_historical_crash_similarity() if has_observations else []
    )
    _t('historical_crash_similarity', t0)
    t0 = time.perf_counter()
    lightgbm_prediction = load_lightgbm_prediction()
    _t('lightgbm_prediction', t0)
    t0 = time.perf_counter()
    upcoming_events = build_upcoming_events()
    _t('upcoming_events', t0)

    context = {
        'has_observations': has_observations,
        'last_updated': (
            latest_obs_date.isoformat() if latest_obs_date else '—'
        ),
        'fred_key_present': fred_key_present,
        'indicator_cards': indicator_cards,
        'crash_alert': crash_alert,
        'historical_crash_similarity': historical_crash_similarity,
        'lightgbm_prediction': lightgbm_prediction,
        'similar_periods': similar_periods,
        'linkages': linkages,
        'upcoming_events': upcoming_events,
        'overview_commentary': (
            build_overview_commentary(latest_snapshot, similar_periods)
            if has_observations else None
        ),
        'similar_commentary': build_similar_explanation(similar_periods),
        'linkage_commentary': build_linkage_explanation(linkages),
    }
    t0 = time.perf_counter()
    context.update(build_regime_context(latest_snapshot))
    _t('regime_context', t0)
    t0 = time.perf_counter()
    resp = render(request, 'macro/index.html', context)
    _t('render', t0)
    return resp


@require_POST
def refresh(request):
    """全指標を FRED から再取得し、レジームを再計算する。"""
    if not get_api_key():
        messages.error(
            request,
            "FRED_API_KEY が未設定のため取得できません。.env に設定してください。",
        )
        return redirect(reverse('macro:index'))

    try:
        result = sync_all_indicators()
    except Exception as exc:
        logger.exception("FRED sync failed")
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

    if ok_count > 0:
        try:
            compute_current_regime()
        except Exception:
            logger.exception("Regime recomputation failed")
            messages.warning(
                request,
                "指標は更新したがレジーム判定でエラーが発生しました（ログを確認）",
            )

    # 価格データも併せて更新
    try:
        price_result = sync_all_price_histories()
        price_ok = len(price_result['success'])
        price_ng = len(price_result['failed'])
        if price_ok > 0:
            messages.info(request, f"価格データ {price_ok} 銘柄を更新")
        if price_ng > 0:
            messages.warning(request, f"価格データ {price_ng} 銘柄の取得に失敗")
    except Exception:
        logger.exception("Price sync failed")
        messages.warning(request, "価格データ更新でエラー（ログを確認）")

    # キャッシュ無効化（次のページ表示で再計算）
    invalidate_caches()

    return redirect(reverse('macro:index'))


def indicator_detail(request, series_id):
    """指標詳細ページ"""
    indicator = get_object_or_404(
        Indicator, fred_series_id=series_id, is_active=True
    )
    context = build_indicator_detail_context(indicator)
    return render(request, 'macro/indicator_detail.html', context)


def similar_period_detail(request, month):
    """類似局面詳細ページ。month は YYYY-MM-DD 形式（月初日推奨）。"""
    try:
        target = datetime.strptime(month, '%Y-%m-%d').date()
    except ValueError:
        raise Http404("Invalid month format")
    target = target.replace(day=1)
    context = build_similar_detail_context(target)
    return render(request, 'macro/similar_detail.html', context)
