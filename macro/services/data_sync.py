"""FRED 観測値の取得と DB 保存。

手動更新ボタン押下時に呼ばれる。差分取得方式: 既存データがあれば
最新日付の少し手前から今日までだけ FRED に問い合わせ、既存値とマージする。

DB 更新は「既存行の UPDATE」と「新規行の INSERT」に分離し、全削除→再挿入は行わない。
"""

import logging
import math
from bisect import bisect_left
from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional, Tuple

from django.db import transaction
from django.utils import timezone

from ..models import Indicator, Observation
from . import (
    aaii_client,
    cboe_client,
    external_yfinance_client,
    finra_client,
    naaim_client,
    price_action_client,
)
from .fred_client import FredApiError, fetch_observations as fetch_fred_observations

logger = logging.getLogger(__name__)

# 初回取得時に遡る期間（年）
HISTORY_YEARS = 25
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


def _load_existing_observations(indicator: Indicator) -> Dict[date, Observation]:
    """既存の Observation を date -> Observation の辞書で返す（UPDATE対象の特定用）。"""
    return {
        o.observation_date: o
        for o in Observation.objects.filter(indicator=indicator)
    }


def _is_value_in_range(indicator: Indicator, value: Optional[float]) -> bool:
    """指標値が想定範囲内かを判定する。NaN/inf や指標固有の min/max 外は False。"""
    if value is None:
        return False
    try:
        fv = float(value)
    except (TypeError, ValueError):
        return False
    if math.isnan(fv) or math.isinf(fv):
        return False
    if indicator.value_min is not None and fv < indicator.value_min:
        return False
    if indicator.value_max is not None and fv > indicator.value_max:
        return False
    return True


def _filter_valid_observations(
    indicator: Indicator,
    raw: List[Tuple[date, float]],
) -> Tuple[List[Tuple[date, float]], int]:
    """異常値・無効値を除外する。除外した件数も返す。"""
    valid: List[Tuple[date, float]] = []
    skipped = 0
    for d, v in raw:
        if _is_value_in_range(indicator, v):
            valid.append((d, v))
        else:
            skipped += 1
            logger.warning(
                "%s skipped invalid value: date=%s value=%r (range=[%s, %s])",
                indicator.fred_series_id, d, v,
                indicator.value_min, indicator.value_max,
            )
    return valid, skipped


def _resolve_fetch_start(
    indicator: Indicator,
    today: date,
    history_years: int,
    force_full_history: bool = False,
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
    if latest is None or force_full_history:
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
    if source == 'yfinance_daily':
        return price_action_client.fetch_observations(
            sid, observation_start=start_date, observation_end=end_date,
        )
    raise FredApiError(f"未対応の source: {source}")


def sync_indicator(
    indicator: Indicator,
    *,
    history_years: int = HISTORY_YEARS,
    force_full_history: bool = False,
) -> dict:
    """1指標を取得元から差分取得し、既存値とマージしてDBを更新する。

    取得元は indicator.source に応じて切替。
    DB 反映は既存日付の UPDATE と新規日付の INSERT に分離し、delete は行わない
    （途中失敗時の一時的なデータ消失を防ぐため）。
    """
    today = timezone.localdate()
    start_date, is_initial = _resolve_fetch_start(
        indicator,
        today,
        history_years,
        force_full_history=force_full_history,
    )

    raw_new = _fetch_for_source(indicator, start_date, today)
    raw_new_valid, skipped_new = _filter_valid_observations(indicator, raw_new)

    existing = _load_existing_observations(indicator)
    merged: Dict[date, float] = {d: o.value for d, o in existing.items()}
    for d, v in raw_new_valid:
        merged[d] = v

    merged_list: List[Tuple[date, float]] = sorted(merged.items())
    new_rows = _build_observation_rows(indicator, merged_list)

    updates: List[Observation] = []
    creates: List[Observation] = []
    for new_obs in new_rows:
        existing_obs = existing.get(new_obs.observation_date)
        if existing_obs is None:
            creates.append(new_obs)
            continue
        # 値や派生値が変わったときだけ UPDATE する
        if (
            existing_obs.value != new_obs.value
            or existing_obs.prev_value != new_obs.prev_value
            or existing_obs.yoy_change != new_obs.yoy_change
            or existing_obs.deviation_from_long_term != new_obs.deviation_from_long_term
        ):
            existing_obs.value = new_obs.value
            existing_obs.prev_value = new_obs.prev_value
            existing_obs.yoy_change = new_obs.yoy_change
            existing_obs.deviation_from_long_term = new_obs.deviation_from_long_term
            updates.append(existing_obs)

    with transaction.atomic():
        if updates:
            Observation.objects.bulk_update(
                updates,
                fields=[
                    'value',
                    'prev_value',
                    'yoy_change',
                    'deviation_from_long_term',
                ],
                batch_size=500,
            )
        if creates:
            Observation.objects.bulk_create(creates, batch_size=500)

    return {
        'series_id': indicator.fred_series_id,
        'fetched': len(raw_new),
        'fetched_valid': len(raw_new_valid),
        'skipped_invalid': skipped_new,
        'updated': len(updates),
        'created': len(creates),
        'stored': len(new_rows),
        'latest_date': new_rows[-1].observation_date if new_rows else None,
        'mode': 'initial' if is_initial else 'incremental',
    }


def sync_all_indicators(
    *,
    history_years: int = HISTORY_YEARS,
    series_ids: Optional[Iterable[str]] = None,
    force_full_history: bool = False,
) -> dict:
    """全アクティブ指標を FRED から取得・更新する。

    1指標ずつ独立に処理し、失敗があっても他は続行する。
    """
    results = {
        'success': [],
        'failed': [],
        'started_at': timezone.now().isoformat(),
    }

    indicators = Indicator.objects.filter(is_active=True).order_by('display_order')
    if series_ids is not None:
        indicators = indicators.filter(fred_series_id__in=tuple(series_ids))
    expected_errors = (
        FredApiError,
        cboe_client.CboeError,
        finra_client.FinraError,
        aaii_client.AaiiError,
        naaim_client.NaaimError,
        external_yfinance_client.ExternalYfinanceError,
        price_action_client.PriceActionError,
    )
    for indicator in indicators:
        try:
            summary = sync_indicator(
                indicator,
                history_years=history_years,
                force_full_history=force_full_history,
            )
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
