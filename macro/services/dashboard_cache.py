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

DASHBOARD_CACHE_KEY = 'macro_index_v1'


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
    except Exception:
        logger.exception('failed to read DashboardCache')
        return None
    if cache_obj is None:
        return None
    return cache_obj.payload


def precompute_dashboard_payload() -> dict:
    """ビューで使う重い計算結果をまとめて返す。"""
    from .dashboard import (
        build_crash_alert_context,
        build_historical_crash_similarity,
        build_indicator_cards,
        build_linkages,
        build_similar_periods,
        build_upcoming_events,
    )
    from .data_sync import get_latest_observation_date

    latest_obs_date = get_latest_observation_date()

    return {
        'last_updated': latest_obs_date.isoformat() if latest_obs_date else '—',
        'similar_periods': build_similar_periods(force=True),
        'linkages': build_linkages(force=True),
        'indicator_cards': build_indicator_cards(),
        'crash_alert': build_crash_alert_context(),
        'historical_crash_similarity': build_historical_crash_similarity(),
        'upcoming_events': build_upcoming_events(),
    }
