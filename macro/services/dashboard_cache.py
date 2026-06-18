"""ダッシュボードの重い計算結果を JSON で永続キャッシュする。

Vercel のサーバーレス環境では、ビューでの計算がコールドスタート時に
タイムアウト（10秒）してしまうため、事前計算した結果を DB の
DashboardCache テーブルから読み出す形にする。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

DASHBOARD_CACHE_KEY = 'macro_index_v7'
LEGACY_DASHBOARD_CACHE_KEYS = (
    'macro_index_v6',
    'macro_index_v5',
    'macro_index_v4',
    'macro_index_v3',
    'macro_index_v2',
    'macro_index_v1',
)
INDICATOR_DETAIL_CACHE_PREFIX = 'macro_indicator_detail_v1:'
SIMILAR_DETAIL_CACHE_PREFIX = 'macro_similar_detail_v1:'
UPDATE_STATUS_CACHE_KEY = 'macro_update_status_v1'
STATIC_MACRO_PAYLOAD_PATH = Path('static/macro/latest_dashboard.json')


def indicator_detail_cache_key(series_id: str) -> str:
    return f'{INDICATOR_DETAIL_CACHE_PREFIX}{series_id}'


def similar_detail_cache_key(month_iso: str) -> str:
    return f'{SIMILAR_DETAIL_CACHE_PREFIX}{month_iso}'


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    # Django モデルインスタンスはキャッシュ目的では捨てる（テンプレート未使用）
    from django.db.models import Model
    if isinstance(value, Model):
        return None
    raise TypeError(f'Object of type {type(value).__name__} is not JSON serializable')


def save_dashboard_payload(payload: dict) -> None:
    from ..models import DashboardCache
    serialized = json.loads(json.dumps(payload, default=_json_default))
    DashboardCache.objects.update_or_create(
        cache_key=DASHBOARD_CACHE_KEY,
        defaults={'payload': serialized},
    )


def load_dashboard_payload() -> Optional[dict]:
    from ..models import DashboardCache
    try:
        cache_obj = DashboardCache.objects.filter(cache_key=DASHBOARD_CACHE_KEY).first()
        if cache_obj is None and LEGACY_DASHBOARD_CACHE_KEYS:
            cache_obj = (
                DashboardCache.objects
                .filter(cache_key__in=LEGACY_DASHBOARD_CACHE_KEYS)
                .order_by('-computed_at')
                .first()
            )
    except Exception:
        logger.exception('failed to read DashboardCache')
        return None
    if cache_obj is None:
        return None
    return cache_obj.payload


def load_static_macro_payload(path: str | Path | None = None) -> Optional[dict]:
    payload_path = Path(path) if path else settings.BASE_DIR / STATIC_MACRO_PAYLOAD_PATH
    if not payload_path.exists():
        return None
    try:
        with payload_path.open(encoding='utf-8') as fp:
            payload = json.load(fp)
    except (OSError, json.JSONDecodeError):
        logger.exception('failed to read static macro payload: %s', payload_path)
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def write_static_macro_payload(payload: dict, path: str | Path | None = None) -> None:
    payload_path = Path(path) if path else settings.BASE_DIR / STATIC_MACRO_PAYLOAD_PATH
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.loads(json.dumps(payload, default=_json_default))
    payload_path.write_text(
        json.dumps(serialized, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )


def load_dashboard_cache_meta() -> dict:
    from ..models import DashboardCache
    try:
        cache_obj = DashboardCache.objects.filter(cache_key=DASHBOARD_CACHE_KEY).first()
        if cache_obj is None and LEGACY_DASHBOARD_CACHE_KEYS:
            cache_obj = (
                DashboardCache.objects
                .filter(cache_key__in=LEGACY_DASHBOARD_CACHE_KEYS)
                .order_by('-computed_at')
                .first()
            )
    except Exception:
        logger.exception('failed to read DashboardCache meta')
        return {}
    if cache_obj is None:
        return {}
    return {
        'cache_key': cache_obj.cache_key,
        'computed_at': cache_obj.computed_at.isoformat(),
    }


def invalidate_dashboard_cache() -> None:
    from ..models import DashboardCache
    DashboardCache.objects.filter(
        cache_key__in=(DASHBOARD_CACHE_KEY, *LEGACY_DASHBOARD_CACHE_KEYS),
    ).delete()


def save_indicator_detail_payload(series_id: str, payload: dict) -> None:
    from ..models import DashboardCache
    serialized = json.loads(json.dumps(payload, default=_json_default))
    DashboardCache.objects.update_or_create(
        cache_key=indicator_detail_cache_key(series_id),
        defaults={'payload': serialized},
    )


def load_indicator_detail_payload(series_id: str) -> Optional[dict]:
    from ..models import DashboardCache
    try:
        cache_obj = DashboardCache.objects.filter(
            cache_key=indicator_detail_cache_key(series_id),
        ).first()
    except Exception:
        logger.exception('failed to read indicator detail cache')
        return None
    if cache_obj is None:
        return None
    return cache_obj.payload


def invalidate_indicator_detail_caches() -> None:
    """全指標詳細キャッシュを削除。指標更新時の使用を想定。"""
    from ..models import DashboardCache
    DashboardCache.objects.filter(
        cache_key__startswith=INDICATOR_DETAIL_CACHE_PREFIX,
    ).delete()


def save_similar_detail_payload(month_iso: str, payload: dict) -> None:
    from ..models import DashboardCache
    serialized = json.loads(json.dumps(payload, default=_json_default))
    DashboardCache.objects.update_or_create(
        cache_key=similar_detail_cache_key(month_iso),
        defaults={'payload': serialized},
    )


def load_similar_detail_payload(month_iso: str) -> Optional[dict]:
    from ..models import DashboardCache
    try:
        cache_obj = DashboardCache.objects.filter(
            cache_key=similar_detail_cache_key(month_iso),
        ).first()
    except Exception:
        logger.exception('failed to read similar detail cache')
        return None
    if cache_obj is None:
        return None
    return cache_obj.payload


def invalidate_similar_detail_caches() -> None:
    from ..models import DashboardCache
    DashboardCache.objects.filter(
        cache_key__startswith=SIMILAR_DETAIL_CACHE_PREFIX,
    ).delete()


def save_macro_update_status(status: dict) -> None:
    from ..models import DashboardCache
    payload = {
        **status,
        'recorded_at': status.get('recorded_at') or timezone.now().isoformat(),
    }
    serialized = json.loads(json.dumps(payload, default=_json_default))
    DashboardCache.objects.update_or_create(
        cache_key=UPDATE_STATUS_CACHE_KEY,
        defaults={'payload': serialized},
    )


def load_macro_update_status() -> Optional[dict]:
    from ..models import DashboardCache
    try:
        cache_obj = DashboardCache.objects.filter(
            cache_key=UPDATE_STATUS_CACHE_KEY,
        ).first()
    except Exception:
        logger.exception('failed to read macro update status')
        return None
    if cache_obj is None:
        return None
    return {
        **cache_obj.payload,
        'status_cache_computed_at': cache_obj.computed_at.isoformat(),
    }


def precompute_dashboard_payload() -> dict:
    """ビューで使う重い計算結果をまとめて返す。"""
    from .dashboard import (
        build_crash_alert_context,
        build_forecast_monitor_context,
        build_historical_crash_similarity,
        build_indicator_cards,
        build_linkages,
        build_macro_decision_context,
        build_macro_forecast_report_context,
        build_macro_outcome_validation_context,
        build_forecast_model_context,
        build_model_validation_context,
        build_monthly_model_status,
        build_raw_archive_context,
        build_vintage_status_context,
        build_world_state_context,
        build_world_model_operations_context,
        build_similar_periods,
        TOP_MACRO_SERIES,
        load_regime_probability_model,
    )
    from .data_sync import get_latest_observation_date
    from .data_quality import build_data_quality_report
    from .house_view import build_house_view_context
    from .scenario import build_auto_scenarios
    from .policy_expectation import (
        build_policy_expectation_context,
        build_policy_expectation_snapshot,
    )

    latest_obs_date = get_latest_observation_date()
    from ..models import RegimeSnapshot
    latest_snapshot = RegimeSnapshot.objects.order_by('-snapshot_date').first()
    all_indicator_cards = build_indicator_cards()
    top_indicator_cards = [
        card for card in all_indicator_cards
        if card.get('series_id') in TOP_MACRO_SERIES
    ]

    try:
        build_policy_expectation_snapshot()
    except Exception:
        logger.exception('policy expectation precompute failed')

    return {
        'has_observations': latest_obs_date is not None,
        'last_updated': latest_obs_date.isoformat() if latest_obs_date else '—',
        'data_quality_report': build_data_quality_report(),
        'house_view': build_house_view_context(),
        'macro_decision': build_macro_decision_context(latest_snapshot),
        'macro_forecast_report': build_macro_forecast_report_context(),
        'macro_outcome_validation': build_macro_outcome_validation_context(),
        'similar_periods': build_similar_periods(),
        'linkages': build_linkages(),
        'indicator_cards': top_indicator_cards,
        'audit_indicator_cards': all_indicator_cards,
        'crash_alert': build_crash_alert_context(),
        'monthly_model_status': build_monthly_model_status(),
        'forecast_monitor': build_forecast_monitor_context(),
        'world_state': build_world_state_context(),
        'forecast_models': build_forecast_model_context(),
        'model_validation': build_model_validation_context(),
        'world_model_operations': build_world_model_operations_context(),
        'raw_archive_status': build_raw_archive_context(),
        'vintage_status': build_vintage_status_context(),
        'regime_probability_model': load_regime_probability_model(),
        'policy_expectation': build_policy_expectation_context(),
        'scenario_analysis': build_auto_scenarios(),
        'historical_crash_similarity': build_historical_crash_similarity(),
    }


def precompute_all_indicator_details() -> int:
    """全アクティブ指標の詳細ページ用ペイロードを事前計算して保存する。"""
    from ..models import Indicator
    from .detail import build_indicator_detail_context

    count = 0
    for indicator in Indicator.objects.filter(is_active=True):
        try:
            payload = build_indicator_detail_context(indicator)
        except Exception:
            logger.exception(
                'precompute indicator detail failed: %s',
                indicator.fred_series_id,
            )
            continue
        # indicator モデルはキャッシュ不要（ビュー側で再取得）
        payload.pop('indicator', None)
        save_indicator_detail_payload(indicator.fred_series_id, payload)
        count += 1
    return count


def precompute_top_similar_details(payload: Optional[dict] = None) -> int:
    """トップページの類似期間上位件分だけ詳細ページペイロードを事前計算する。"""
    from datetime import date as _date
    from .detail import build_similar_detail_context

    if payload is None:
        payload = load_dashboard_payload() or {}
    similar_periods = payload.get('similar_periods', []) or []

    # 古い類似期間のキャッシュは事前にクリア
    invalidate_similar_detail_caches()

    count = 0
    for period in similar_periods:
        month_iso = period.get('month_start')
        if not month_iso:
            continue
        try:
            month_start = _date.fromisoformat(month_iso)
        except (TypeError, ValueError):
            continue
        try:
            detail_payload = build_similar_detail_context(month_start)
        except Exception:
            logger.exception('precompute similar detail failed: %s', month_iso)
            continue
        save_similar_detail_payload(month_iso, detail_payload)
        count += 1
    return count
