"""指標間の連動関係（相関・リードラグ）分析。

各ペアの月次・時点別標準化値で相関を計算し、ラグを±k月でずらして最大相関を探す。
最も連動の強いペア上位N件を返す。
"""

import logging
import math
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from dateutil.relativedelta import relativedelta

from ..models import Indicator, Observation

logger = logging.getLogger(__name__)

# ラグ探索の候補（月）
LAG_CANDIDATES = [-6, -3, -2, -1, 0, 1, 2, 3, 6]
# 相関計算に必要な最小サンプル数
MIN_PAIRS_SAMPLES = 36
# 過去何年分のデータを使うか
LINKAGE_HISTORY_YEARS = 10
# 出力上位件数
DEFAULT_TOP_N = 10


def _pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    """ピアソン相関係数。NaN/不足時は None。"""
    n = len(xs)
    if n != len(ys) or n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    sq_x = sum((xs[i] - mean_x) ** 2 for i in range(n))
    sq_y = sum((ys[i] - mean_y) ** 2 for i in range(n))
    if sq_x <= 0 or sq_y <= 0:
        return None
    return num / math.sqrt(sq_x * sq_y)


def _month_key(d: date) -> date:
    return d.replace(day=1)


def _aggregate_to_monthly(observations) -> Dict[date, float]:
    """observation の time-series を月次（月末値）に集約する。"""
    # observations: list of (date, deviation) tuples sorted by date
    monthly: Dict[date, float] = {}
    for obs_date, value in observations:
        key = _month_key(obs_date)
        monthly[key] = value  # 同月は最新値で上書き
    return monthly


def _load_monthly_data(history_years: int) -> Dict[str, Dict[date, float]]:
    """連動分析対象の指標を月次標準化値で読み込む。"""
    # 重要度A・Bを対象（C は参考扱いで除外）
    indicators = list(
        Indicator.objects
        .filter(is_active=True, importance__in=['A', 'B'])
        .order_by('display_order')
    )
    series_ids = [i.fred_series_id for i in indicators]
    if not series_ids:
        return {}

    from django.utils import timezone
    cutoff = timezone.localdate().replace(
        year=timezone.localdate().year - history_years
    )

    obs_rows = (
        Observation.objects
        .filter(
            indicator__fred_series_id__in=series_ids,
            observation_date__gte=cutoff,
            expanding_z_score__isnull=False,
        )
        .order_by('indicator', 'observation_date')
        .values_list(
            'indicator__fred_series_id',
            'observation_date',
            'expanding_z_score',
        )
    )

    # 観測値の月内最終値だけを直接保持（中間 list を持たずメモリ節約）
    monthly_data: Dict[str, Dict[date, float]] = {sid: {} for sid in series_ids}
    for sid, obs_date, dev in obs_rows:
        monthly_data[sid][_month_key(obs_date)] = dev
    return monthly_data


def _shifted_series(
    data1: Dict[date, float],
    data2: Dict[date, float],
    lag_months: int,
):
    """data1 と data2 をラグずらした上で共通月のペア配列を返す。

    lag_months > 0: data1 が先行（data2 を lag_months 月遅らせて並べる）
    lag_months < 0: data2 が先行
    """
    pairs: List[Tuple[date, float, float]] = []
    for month, v1 in data1.items():
        if lag_months >= 0:
            target = month + relativedelta(months=lag_months)
            if target in data2:
                pairs.append((month, v1, data2[target]))
        else:
            target = month + relativedelta(months=-lag_months)
            if target in data1 and month in data2:
                pairs.append((month, data1[target], data2[month]))
    return pairs


def compute_pair_relationships(
    history_years: int = LINKAGE_HISTORY_YEARS,
    top_n: int = DEFAULT_TOP_N,
) -> List[Dict]:
    """指標ペアごとの最強連動関係を計算。

    返り値の各要素:
      - leader: 先行する系列ID
      - follower: 追随する系列ID
      - correlation: 相関係数
      - lag_months: 先行月数
    """
    monthly = _load_monthly_data(history_years)
    if not monthly:
        return []

    series_ids = [sid for sid, m in monthly.items() if len(m) >= MIN_PAIRS_SAMPLES]
    series_ids.sort()

    pairs: List[Dict] = []
    for i, sid1 in enumerate(series_ids):
        for sid2 in series_ids[i + 1:]:
            data1 = monthly[sid1]
            data2 = monthly[sid2]
            best = None
            for lag in LAG_CANDIDATES:
                shifted = _shifted_series(data1, data2, lag)
                if len(shifted) < MIN_PAIRS_SAMPLES:
                    continue
                xs = [p[1] for p in shifted]
                ys = [p[2] for p in shifted]
                corr = _pearson(xs, ys)
                if corr is None:
                    continue
                if best is None or abs(corr) > abs(best['correlation']):
                    best = {
                        'correlation': corr,
                        'lag_months': lag,
                    }

            if best is None:
                continue

            lag_months = best['lag_months']
            corr = best['correlation']
            if lag_months > 0:
                leader, follower, abs_lag = sid1, sid2, lag_months
            elif lag_months < 0:
                leader, follower, abs_lag = sid2, sid1, -lag_months
            else:
                leader, follower, abs_lag = sid1, sid2, 0

            pairs.append({
                'leader': leader,
                'follower': follower,
                'correlation': corr,
                'lag_months': abs_lag,
                'pair_key': tuple(sorted([sid1, sid2])),
            })

    pairs.sort(key=lambda x: abs(x['correlation']), reverse=True)
    return pairs[:top_n]
