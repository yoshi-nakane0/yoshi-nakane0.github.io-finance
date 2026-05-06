"""歴史的クラッシュ月との類似度計算。

過去の代表的なクラッシュ月の指標ベクトルと、現在の指標ベクトルとの
距離を比較し、最も似ている月を返す。既存 similarity.py のロジックを
再利用する。
"""

from datetime import date
from typing import Dict, List, Optional

from django.utils import timezone

from .similarity import (
    _build_observation_lookup,
    _month_end,
    build_vector_at,
    get_importance_a_indicators,
    vector_distance,
)


# 歴史的クラッシュ月の定義。月単位で記録。
# month_start, label, period のセット
HISTORICAL_CRASH_MONTHS = [
    # ITバブル崩壊
    (date(2000, 3, 1), 'ITバブル崩壊（始まり）', 'dotcom'),
    (date(2000, 9, 1), 'ITバブル崩壊（後半）', 'dotcom'),
    (date(2001, 9, 1), '9.11テロ後', 'dotcom'),
    (date(2002, 7, 1), 'ITバブル崩壊（底）', 'dotcom'),
    # リーマンショック
    (date(2008, 1, 1), 'リーマン直前', 'gfc'),
    (date(2008, 9, 1), 'リーマンショック', 'gfc'),
    (date(2008, 11, 1), '金融危機ピーク', 'gfc'),
    (date(2009, 3, 1), 'リーマン後の底', 'gfc'),
    # 欧州債務危機
    (date(2011, 8, 1), '欧州債務危機', 'eu_debt'),
    # チャイナショック
    (date(2015, 8, 1), 'チャイナショック', 'china'),
    # コロナショック
    (date(2020, 2, 1), 'コロナショック前夜', 'covid'),
    (date(2020, 3, 1), 'コロナショック', 'covid'),
    # 2022年急落
    (date(2022, 6, 1), '2022年米株急落', 'inflation_2022'),
    (date(2022, 10, 1), '2022年底', 'inflation_2022'),
]


def find_similar_crash_months(top_n: int = 3) -> List[Dict]:
    """現在ベクトルに最も近い歴史的クラッシュ月を上位 top_n 件返す。

    返り値の各要素:
      {
        'month_start': date,
        'label': str,
        'period': str,
        'distance': float,
        'distance_display': str,
      }
    """
    indicators = get_importance_a_indicators()
    if not indicators:
        return []
    series_ids = [i.fred_series_id for i in indicators]
    lookup = _build_observation_lookup(series_ids)
    if not lookup:
        return []

    today = timezone.localdate()
    current_vector = build_vector_at(today, lookup, series_ids)
    if not current_vector:
        return []

    candidates: List[Dict] = []
    for month_start, label, period in HISTORICAL_CRASH_MONTHS:
        month_end = _month_end(month_start)
        vector = build_vector_at(month_end, lookup, series_ids)
        if not vector:
            continue
        distance = vector_distance(current_vector, vector)
        if distance == float('inf'):
            continue
        candidates.append({
            'month_start': month_start,
            'label': label,
            'period': period,
            'distance': distance,
        })

    if not candidates:
        return []

    candidates.sort(key=lambda x: x['distance'])
    top = candidates[:top_n]
    for item in top:
        item['distance_display'] = f"{item['distance']:.2f}"
        item['month_label'] = item['month_start'].strftime('%Y年%m月')
    return top
