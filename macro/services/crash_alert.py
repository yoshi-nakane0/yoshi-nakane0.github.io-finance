"""クラッシュ警戒度の計算。

3 カテゴリ（市場 / 信用 / マクロ）の重み付き集約で 0〜100 の総合スコアを算出する。
- 市場（指数・センチメント・価格アクション）: 65%
- 信用・レバレッジ（クレジットスプレッド・金融状況）: 25%
- 景気・雇用マクロ（イールドカーブ等）: 10%

各カテゴリ内は所属コンポーネントの単純平均で評価する。
データが揃わないカテゴリがあれば、残りカテゴリ間で重みを比例配分し直す。

スコアが高いほど市場ストレスが大きい。
表示は 5 段階（平常 / 注意 / 警戒 / 高警戒 / 危険）を主軸に、100 点満点の数値を併記する。
"""

from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from ..models import Observation


# 各サブスコア定義。
# direction はドキュメント目的。bands の並びが昇順で、低い値ほどリスクの大きいケースは
# (上限値, スコア) の組で score を降順に書く（lower=危険）。逆に高い値ほどリスクの大きい
# 場合は score を昇順に書く（higher=危険）。
COMPONENT_SPECS = [
    # --- 市場 ---
    {
        'series_id': 'VIXCLS', 'label': 'VIX', 'category': 'market', 'direction': 'higher',
        'bands': [(15, 0), (20, 25), (25, 50), (30, 75), (40, 90), (float('inf'), 100)],
    },
    {
        'series_id': 'CBOE_SKEW', 'label': 'SKEW（テール警戒）', 'category': 'market', 'direction': 'higher',
        'bands': [(120, 0), (130, 25), (140, 50), (150, 75), (160, 90), (float('inf'), 100)],
    },
    {
        'series_id': 'NAAIM_EXPOSURE', 'label': 'NAAIMエクスポージャー', 'category': 'market', 'direction': 'higher',
        'bands': [(50, 0), (70, 25), (85, 50), (95, 75), (105, 90), (float('inf'), 100)],
    },
    {
        'series_id': 'AAII_BULLISH', 'label': 'AAII強気%', 'category': 'market', 'direction': 'higher',
        'bands': [(30, 0), (40, 25), (50, 50), (55, 75), (60, 90), (float('inf'), 100)],
    },
    {
        'series_id': 'MOVE_INDEX', 'label': 'MOVE指数', 'category': 'market', 'direction': 'higher',
        'bands': [(80, 0), (100, 25), (130, 50), (160, 75), (200, 90), (float('inf'), 100)],
    },
    {
        'series_id': 'VIX_VIX3M_RATIO', 'label': 'VIX/VIX3M比', 'category': 'market', 'direction': 'higher',
        'bands': [(0.9, 0), (0.95, 25), (1.0, 50), (1.1, 75), (1.2, 90), (float('inf'), 100)],
    },
    # --- 市場（価格アクション: 4指数 × 3指標）---
    *[
        {
            'series_id': f'PA_{sym}_DD200', 'label': f'{label} 200日線', 'category': 'market', 'direction': 'lower',
            'bands': [(-15, 100), (-10, 85), (-5, 65), (0, 40), (5, 20), (float('inf'), 0)],
        }
        for sym, label in (('GSPC', 'S&P500'), ('N225', '日経225'), ('DJI', 'NYダウ'), ('IXIC', 'NASDAQ'))
    ],
    *[
        {
            'series_id': f'PA_{sym}_DD52W', 'label': f'{label} 52週高値', 'category': 'market', 'direction': 'lower',
            'bands': [(-25, 100), (-15, 85), (-10, 65), (-5, 40), (-1, 15), (float('inf'), 0)],
        }
        for sym, label in (('GSPC', 'S&P500'), ('N225', '日経225'), ('DJI', 'NYダウ'), ('IXIC', 'NASDAQ'))
    ],
    *[
        {
            'series_id': f'PA_{sym}_MOM20', 'label': f'{label} 20日', 'category': 'market', 'direction': 'lower',
            'bands': [(-15, 100), (-10, 85), (-5, 65), (-2, 40), (0, 20), (float('inf'), 0)],
        }
        for sym, label in (('GSPC', 'S&P500'), ('N225', '日経225'), ('DJI', 'NYダウ'), ('IXIC', 'NASDAQ'))
    ],
    # --- 信用・レバレッジ ---
    {
        'series_id': 'BAMLH0A0HYM2', 'label': 'HYスプレッド', 'category': 'credit', 'direction': 'higher',
        'bands': [(3, 0), (4, 25), (5, 50), (7, 75), (10, 90), (float('inf'), 100)],
    },
    {
        'series_id': 'BAMLC0A0CM', 'label': 'IGスプレッド', 'category': 'credit', 'direction': 'higher',
        'bands': [(1.0, 0), (1.3, 25), (1.7, 50), (2.2, 75), (3.0, 90), (float('inf'), 100)],
    },
    {
        'series_id': 'NFCI', 'label': '金融状況', 'category': 'credit', 'direction': 'higher',
        'bands': [(-0.5, 0), (0.0, 25), (0.3, 50), (0.7, 75), (1.0, 90), (float('inf'), 100)],
    },
    {
        'series_id': 'STLFSI4', 'label': '金融ストレス', 'category': 'credit', 'direction': 'higher',
        'bands': [(-1.0, 0), (0.0, 25), (0.5, 50), (1.5, 75), (2.5, 90), (float('inf'), 100)],
    },
    # --- 景気・雇用マクロ ---
    {
        'series_id': 'T10Y2Y', 'label': '2-10年スプレッド', 'category': 'macro', 'direction': 'lower',
        'bands': [(-0.5, 100), (0.0, 75), (0.5, 50), (1.5, 25), (float('inf'), 0)],
    },
    {
        'series_id': 'T10Y3M', 'label': '3M-10年スプレッド', 'category': 'macro', 'direction': 'lower',
        'bands': [(-0.5, 100), (0.0, 75), (0.5, 50), (1.5, 25), (float('inf'), 0)],
    },
]


CATEGORY_WEIGHTS = {
    'market': 0.65,
    'credit': 0.25,
    'macro': 0.10,
}

CATEGORY_LABELS = {
    'market': '株式面',
    'credit': '金融面',
    'macro': '景気面',
}


# 表示用の 5 段階分類。閾値は「これ未満ならそのレベル」。
LEVEL_BANDS = [
    (21, 'calm', '平常'),
    (41, 'caution', '注意'),
    (61, 'alert', '警戒'),
    (81, 'high', '高警戒'),
    (10 ** 9, 'danger', '危険'),
]


def _band_score(value: float, bands) -> int:
    for upper, score in bands:
        if value <= upper:
            return score
    return bands[-1][1]


def _latest_value(series_id: str) -> Optional[float]:
    return (
        Observation.objects
        .filter(indicator__fred_series_id=series_id)
        .order_by('-observation_date')
        .values_list('value', flat=True)
        .first()
    )


def _classify(score: int) -> Tuple[str, str]:
    for upper, level, label in LEVEL_BANDS:
        if score < upper:
            return level, label
    return LEVEL_BANDS[-1][1], LEVEL_BANDS[-1][2]


def compute_crash_alert(
    value_lookup=None,
) -> Dict:
    """クラッシュ警戒度を計算する。

    value_lookup: 任意の (series_id) → float コールバック。指定するとそれを使い、未指定なら
                  Observation テーブルから最新値を取得する。バックテスト用途で利用する。

    戻り値:
      {
        'total_score': int | None,
        'level': 'calm' | 'caution' | 'alert' | 'high' | 'danger' | 'unknown',
        'level_label': str,
        'components': [{'series_id', 'label', 'category', 'value', 'score'}, ...],
        'category_summary': [
            {'category', 'category_label', 'avg_score', 'weight_pct', 'count'},
            ...
        ],
      }
    """
    lookup = value_lookup if value_lookup is not None else _latest_value

    components: List[Dict] = []
    for spec in COMPONENT_SPECS:
        value = lookup(spec['series_id'])
        if value is None:
            continue
        score = _band_score(value, spec['bands'])
        components.append({
            'series_id': spec['series_id'],
            'label': spec['label'],
            'category': spec['category'],
            'value': value,
            'score': score,
        })

    if not components:
        return {
            'total_score': None,
            'level': 'unknown',
            'level_label': '判定不能',
            'components': [],
            'category_summary': [],
        }

    by_cat: Dict[str, List[int]] = defaultdict(list)
    for c in components:
        by_cat[c['category']].append(c['score'])

    cat_avgs: Dict[str, float] = {
        cat: sum(scores) / len(scores)
        for cat, scores in by_cat.items()
    }

    # データが揃わないカテゴリがある場合は、残りカテゴリ間で重みを比例配分し直す。
    available_weight = sum(CATEGORY_WEIGHTS.get(cat, 0.0) for cat in cat_avgs)
    if available_weight <= 0:
        total = sum(cat_avgs.values()) / len(cat_avgs)
        normalized_weights = {cat: 1.0 / len(cat_avgs) for cat in cat_avgs}
    else:
        normalized_weights = {
            cat: CATEGORY_WEIGHTS.get(cat, 0.0) / available_weight
            for cat in cat_avgs
        }
        total = sum(cat_avgs[cat] * normalized_weights[cat] for cat in cat_avgs)

    total_int = round(total)
    level, level_label = _classify(total_int)

    category_summary = [
        {
            'category': cat,
            'category_label': CATEGORY_LABELS.get(cat, cat),
            'avg_score': round(cat_avgs[cat]),
            'weight_pct': round(normalized_weights[cat] * 100),
            'count': len(by_cat[cat]),
        }
        for cat in sorted(cat_avgs, key=lambda c: -CATEGORY_WEIGHTS.get(c, 0))
    ]

    return {
        'total_score': total_int,
        'level': level,
        'level_label': level_label,
        'components': components,
        'category_summary': category_summary,
    }
