"""ダッシュボードの重い計算結果を JSON で永続キャッシュする。

Vercel のサーバーレス環境では、ビューでの計算がコールドスタート時に
タイムアウト（10秒）してしまうため、事前計算した結果を DB の
DashboardCache テーブルから読み出す形にする。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

DASHBOARD_CACHE_KEY = 'macro_index_v2'
LEGACY_DASHBOARD_CACHE_KEYS = ('macro_index_v1',)
INDICATOR_DETAIL_CACHE_PREFIX = 'macro_indicator_detail_v1:'
SIMILAR_DETAIL_CACHE_PREFIX = 'macro_similar_detail_v1:'


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


def precompute_dashboard_payload() -> dict:
    """ビューで使う重い計算結果をまとめて返す。"""
    from .dashboard import (
        build_crash_alert_context,
        build_historical_crash_similarity,
        build_indicator_cards,
        build_linkages,
        build_similar_periods,
    )
    from .data_sync import get_latest_observation_date

    latest_obs_date = get_latest_observation_date()

    return {
        'has_observations': latest_obs_date is not None,
        'last_updated': latest_obs_date.isoformat() if latest_obs_date else '—',
        'similar_periods': build_similar_periods(),
        'linkages': build_linkages(),
        'indicator_cards': build_indicator_cards(),
        'crash_alert': build_crash_alert_context(),
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
