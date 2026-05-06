"""過去類似局面の検索。

重要度A指標の標準化済みベクトルを使い、現在のベクトルと過去各月のベクトルの距離で
最も近い月を上位N件返す。
"""

import logging
import math
from bisect import bisect_left
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from dateutil.relativedelta import relativedelta
from django.utils import timezone

from ..models import Indicator, Observation, PriceObservation
from .yfinance_client import get_next_month_return

logger = logging.getLogger(__name__)

# 過去類似検索の対象期間（年）
SEARCH_HISTORY_YEARS = 15
# 直近からの除外期間（月）— 直近3ヶ月は「現在」と被るため除外
RECENT_EXCLUDE_MONTHS = 3
# トップN
DEFAULT_TOP_N = 5
# ベクトルに含まれる必要のある最小指標数（不足月をスキップする閾値）
MIN_VECTOR_SIZE_TOLERANCE = 2  # 11指標のうち2つまで欠損許容
# 「本当に類似」と見なす距離の上限。これを超える月は採用しない。
DISTANCE_THRESHOLD = 1.5


def get_importance_a_indicators() -> List[Indicator]:
    return list(
        Indicator.objects.filter(is_active=True, importance='A').order_by('display_order')
    )


def _build_observation_lookup(
    series_ids: List[str],
    cutoff_date: Optional[date] = None,
):
    """series_id -> (sorted_dates, sorted_deviations) のマップを作る。

    cutoff_date 以降のみロードしてメモリ消費を抑える。Observation モデルでは
    なく values_list でタプルだけ取り出して大量行のオブジェクト生成を回避。
    """
    qs = Observation.objects.filter(
        indicator__fred_series_id__in=series_ids,
        deviation_from_long_term__isnull=False,
    )
    if cutoff_date is not None:
        qs = qs.filter(observation_date__gte=cutoff_date)
    rows = qs.order_by('indicator', 'observation_date').values_list(
        'indicator__fred_series_id', 'observation_date', 'deviation_from_long_term',
    )
    lookup: Dict[str, Tuple[List[date], List[float]]] = {}
    for sid, obs_date, dev in rows:
        bucket = lookup.get(sid)
        if bucket is None:
            bucket = ([], [])
            lookup[sid] = bucket
        bucket[0].append(obs_date)
        bucket[1].append(dev)
    return lookup


def _value_at_or_before(
    series_data: Tuple[List[date], List[float]],
    target_date: date,
) -> Optional[float]:
    """target_date 以下で最も新しい値を返す。"""
    if not series_data or not series_data[0]:
        return None
    dates, values = series_data
    idx = bisect_left(dates, target_date)
    # bisect_left は target_date 以上の最初の位置を返す
    if idx == len(dates):
        idx -= 1
    elif idx < len(dates) and dates[idx] > target_date:
        if idx == 0:
            return None
        idx -= 1
    return values[idx]


def _month_end(month_start: date) -> date:
    next_month = month_start.replace(day=1) + relativedelta(months=1)
    return next_month - timedelta(days=1)


def build_vector_at(
    target_date: date,
    lookup,
    series_ids: List[str],
) -> Dict[str, float]:
    """target_date 時点での標準化指標ベクトルを構築する。"""
    vector: Dict[str, float] = {}
    for sid in series_ids:
        series_data = lookup.get(sid)
        if not series_data:
            continue
        val = _value_at_or_before(series_data, target_date)
        if val is not None:
            vector[sid] = val
    return vector


def vector_distance(v1: Dict[str, float], v2: Dict[str, float]) -> float:
    """2つの指標ベクトル間の距離（共通次元数で正規化）"""
    common = set(v1.keys()) & set(v2.keys())
    if not common:
        return float('inf')
    sq_sum = sum((v1[k] - v2[k]) ** 2 for k in common)
    return math.sqrt(sq_sum / len(common))


def find_similar_months(
    current_vector: Optional[Dict[str, float]] = None,
    top_n: int = DEFAULT_TOP_N,
    history_years: int = SEARCH_HISTORY_YEARS,
    distance_threshold: Optional[float] = DISTANCE_THRESHOLD,
) -> List[Dict]:
    """現在ベクトルに最も近い過去月を返す。

    各要素: { 'month_start', 'distance', 'vector', 'main3' }
    main3 は表示用の主要3指標（Core PCE / INDPRO / 2-10スプレッド）の各月時点の値。
    distance_threshold が指定された場合、距離がそれより大きい月は除外する。
    """
    indicators = get_importance_a_indicators()
    if not indicators:
        return []
    series_ids = [i.fred_series_id for i in indicators]

    # 探索範囲＋少し余裕を持たせた cutoff（現在ベクトル算出のため余裕分）
    today = timezone.localdate()
    cutoff_date = today.replace(year=today.year - history_years - 1).replace(day=1)

    lookup = _build_observation_lookup(series_ids, cutoff_date=cutoff_date)
    if not lookup:
        return []

    if current_vector is None:
        current_vector = build_vector_at(today, lookup, series_ids)
    if not current_vector:
        return []

    # 主要3指標も別途ロード（Core PCE / INDPRO / T10Y2Y）
    main3_ids = ['PCEPILFE', 'INDPRO', 'T10Y2Y']
    main3_lookup = _build_observation_value_lookup(main3_ids, cutoff_date=cutoff_date)

    # 検索範囲
    earliest = today.replace(year=today.year - history_years).replace(day=1)
    cutoff_month = today.replace(day=1) - relativedelta(months=RECENT_EXCLUDE_MONTHS)

    required_size = len(series_ids) - MIN_VECTOR_SIZE_TOLERANCE

    candidates: List[Dict] = []
    cur = earliest
    while cur <= cutoff_month:
        month_end = _month_end(cur)
        vector = build_vector_at(month_end, lookup, series_ids)
        if len(vector) >= required_size:
            distance = vector_distance(current_vector, vector)
            main3 = {}
            for sid in main3_ids:
                v = _value_at_or_before(main3_lookup.get(sid), month_end)
                if v is not None:
                    main3[sid] = v

            nikkei_return = get_next_month_return(
                PriceObservation.Ticker.NIKKEI, cur
            )
            spx_return = get_next_month_return(
                PriceObservation.Ticker.SP500, cur
            )

            candidates.append({
                'month_start': cur,
                'month_end': month_end,
                'distance': distance,
                'vector': vector,
                'main3': main3,
                'nikkei_next_return': nikkei_return,
                'spx_next_return': spx_return,
            })
        cur = cur + relativedelta(months=1)

    candidates.sort(key=lambda x: x['distance'])
    if distance_threshold is not None:
        candidates = [c for c in candidates if c['distance'] <= distance_threshold]
    return candidates[:top_n]


def _build_observation_value_lookup(
    series_ids: List[str],
    cutoff_date: Optional[date] = None,
):
    """主要3指標の生値（標準化前）を取得するためのルックアップ。"""
    qs = Observation.objects.filter(indicator__fred_series_id__in=series_ids)
    if cutoff_date is not None:
        qs = qs.filter(observation_date__gte=cutoff_date)
    rows = qs.order_by('indicator', 'observation_date').values_list(
        'indicator__fred_series_id', 'observation_date', 'value',
    )
    lookup: Dict[str, Tuple[List[date], List[float]]] = {}
    for sid, obs_date, value in rows:
        bucket = lookup.get(sid)
        if bucket is None:
            bucket = ([], [])
            lookup[sid] = bucket
        bucket[0].append(obs_date)
        bucket[1].append(value)
    return lookup
