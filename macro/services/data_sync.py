"""FRED 観測値の取得と DB 保存。

手動更新ボタン押下時に呼ばれる。差分取得方式: 既存データがあれば
最新日付の少し手前から今日までだけ FRED に問い合わせ、既存値とマージする。
"""

import logging
import math
from bisect import bisect_left
from datetime import date, timedelta
from typing import Dict, List, Tuple

from django.db import transaction
from django.utils import timezone

from ..models import Indicator, Observation
from . import (
    aaii_client,
    cboe_client,
    external_yfinance_client,
    finra_client,
    naaim_client,
)
from .fred_client import FredApiError, fetch_observations as fetch_fred_observations

logger = logging.getLogger(__name__)

# 初回取得時に遡る期間（年）
HISTORY_YEARS = 15
# 既存データありの差分取得時に直近何日分を取り直すか（FRED の改定値を拾うバッファ）
REFRESH_BUFFER_DAYS = 45
# 長期統計を計算する際の最小サンプル数
MIN_SAMPLES_FOR_STATS = 24


def _compute_long_term_stats(values: List[float]) -> Tuple[float, float]:
    """長期平均と標準偏差を返す。サンプル不足時は (0.0, 0.0)。"""
    if len(values) < MIN_SAMPLES_FOR_STATS:
        return 0.0, 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    std = math.sqrt(variance)
    return mean, std


def _find_yoy_old_value(
    sorted_dates: List[date],
    sorted_values: List[float],
    target_date: date,
) -> float:
    """target_date 以下で最も新しい観測値を返す。見つからなければ NaN 相当の None。"""
    idx = bisect_left(sorted_dates, target_date)
    # bisect_left は target_date 以上の最初の位置を返す。
    # 直近の過去値が欲しいので、その1つ手前を採用。
    if idx >= len(sorted_dates):
        idx = len(sorted_dates) - 1
    elif sorted_dates[idx] > target_date and idx > 0:
        idx -= 1
    if idx < 0:
        return None
    return sorted_values[idx]


def _build_observation_rows(
    indicator: Indicator,
    raw_observations: List[Tuple[date, float]],
) -> List[Observation]:
    """生の観測列から Observation 行を組み立てる。"""
    if not raw_observations:
        return []

    sorted_obs = sorted(raw_observations, key=lambda x: x[0])
    sorted_dates = [d for d, _ in sorted_obs]
    sorted_values = [v for _, v in sorted_obs]

    long_term_mean, long_term_std = _compute_long_term_stats(sorted_values)

    rows: List[Observation] = []
    for i, (obs_date, value) in enumerate(sorted_obs):
        prev_value = sorted_values[i - 1] if i > 0 else None

        target_yoy_date = obs_date - timedelta(days=365)
        yoy_change = None
        if i > 0:
            yoy_base = _find_yoy_old_value(
                sorted_dates[:i],
                sorted_values[:i],
                target_yoy_date,
            )
            if yoy_base not in (None, 0):
                yoy_change = (value - yoy_base) / abs(yoy_base) * 100.0

        deviation = None
        if long_term_std > 0:
            deviation = (value - long_term_mean) / long_term_std

        rows.append(
            Observation(
                indicator=indicator,
                observation_date=obs_date,
                value=value,
                prev_value=prev_value,
                yoy_change=yoy_change,
                deviation_from_long_term=deviation,
            )
        )
    return rows


def _load_existing_values(indicator: Indicator) -> Dict[date, float]:
    """この指標について既にDBに保存されている (日付, 値) のマップを返す。"""
    qs = (
        Observation.objects
        .filter(indicator=indicator)
        .values_list('observation_date', 'value')
    )
    return {d: v for d, v in qs}


def _resolve_fetch_start(
    indicator: Indicator,
    today: date,
    history_years: int,
) -> Tuple[date, bool]:
    """FRED 取得開始日を決める。
    既存データがあれば最新日付から REFRESH_BUFFER_DAYS だけ遡る。
    なければ history_years 年前まで遡る（初回取得）。
    返り値: (start_date, is_initial_load)
    """
    latest = (
        Observation.objects
        .filter(indicator=indicator)
        .order_by('-observation_date')
        .values_list('observation_date', flat=True)
        .first()
    )
    if latest is None:
        start = today.replace(year=today.year - history_years)
        return start, True
    start = latest - timedelta(days=REFRESH_BUFFER_DAYS)
    return start, False


def _fetch_for_source(
    indicator: Indicator,
    start_date: date,
    end_date: date,
) -> List[Tuple[date, float]]:
    """Indicator.source に応じて適切なクライアントから観測値を取得する。"""
    source = getattr(indicator, 'source', 'fred') or 'fred'
    sid = indicator.fred_series_id
    if source == 'fred':
        return fetch_fred_observations(
            sid, observation_start=start_date, observation_end=end_date,
        )
    if source == 'cboe':
        return cboe_client.fetch_observations(
            sid, observation_start=start_date, observation_end=end_date,
        )
    if source == 'finra':
        return finra_client.fetch_observations(
            sid, observation_start=start_date, observation_end=end_date,
        )
    if source == 'aaii':
        return aaii_client.fetch_observations(
            sid, observation_start=start_date, observation_end=end_date,
        )
    if source == 'naaim':
        return naaim_client.fetch_observations(
            sid, observation_start=start_date, observation_end=end_date,
        )
    if source == 'yfinance':
        return external_yfinance_client.fetch_monthly_index(
            sid, observation_start=start_date, observation_end=end_date,
        )
    raise FredApiError(f"未対応の source: {source}")


def sync_indicator(indicator: Indicator, *, history_years: int = HISTORY_YEARS) -> dict:
    """1指標を取得元から差分取得し、既存値とマージしてDBを更新する。

    取得元は indicator.source（'fred' / 'cboe' / 'finra' / 'aaii' / 'naaim' / 'yfinance'）に応じて切替。
    """
    today = timezone.localdate()
    start_date, is_initial = _resolve_fetch_start(indicator, today, history_years)

    raw_new = _fetch_for_source(indicator, start_date, today)

    existing = _load_existing_values(indicator)
    merged: Dict[date, float] = dict(existing)
    for d, v in raw_new:
        merged[d] = v

    merged_list: List[Tuple[date, float]] = sorted(merged.items())
    rows = _build_observation_rows(indicator, merged_list)

    with transaction.atomic():
        Observation.objects.filter(indicator=indicator).delete()
        Observation.objects.bulk_create(rows, batch_size=500)

    return {
        'series_id': indicator.fred_series_id,
        'fetched': len(raw_new),
        'stored': len(rows),
        'latest_date': rows[-1].observation_date if rows else None,
        'mode': 'initial' if is_initial else 'incremental',
    }


def sync_all_indicators(*, history_years: int = HISTORY_YEARS) -> dict:
    """全アクティブ指標を FRED から取得・更新する。

    1指標ずつ独立に処理し、失敗があっても他は続行する。
    """
    results = {
        'success': [],
        'failed': [],
        'started_at': timezone.now().isoformat(),
    }

    indicators = Indicator.objects.filter(is_active=True).order_by('display_order')
    expected_errors = (
        FredApiError,
        cboe_client.CboeError,
        finra_client.FinraError,
        aaii_client.AaiiError,
        naaim_client.NaaimError,
        external_yfinance_client.ExternalYfinanceError,
    )
    for indicator in indicators:
        try:
            summary = sync_indicator(indicator, history_years=history_years)
            results['success'].append(summary)
        except expected_errors as exc:
            logger.warning(
                "%s sync failed for %s: %s",
                indicator.source, indicator.fred_series_id, exc,
            )
            results['failed'].append({
                'series_id': indicator.fred_series_id,
                'error': str(exc),
            })
        except Exception as exc:
            logger.exception("Unexpected sync error for %s", indicator.fred_series_id)
            results['failed'].append({
                'series_id': indicator.fred_series_id,
                'error': str(exc),
            })

    results['finished_at'] = timezone.now().isoformat()
    return results


def get_latest_observation_date() -> date:
    """全指標で最も新しい観測日を返す。データがなければ None。"""
    latest = (
        Observation.objects
        .order_by('-observation_date')
        .values_list('observation_date', flat=True)
        .first()
    )
    return latest
