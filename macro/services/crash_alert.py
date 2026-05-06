"""クラッシュ警戒度の計算。

複数のリスク指標から 0〜100 の総合スコアを算出する。
スコアが高いほど市場ストレスが大きく、クラッシュリスクが高いと判定。
"""

from typing import Dict, List, Optional

from ..models import Indicator, Observation


# 各指標のサブスコア定義。
# direction "lower":  値が小さいほど警戒度が高い（イールドスプレッドなど）
# direction "higher": 値が大きいほど警戒度が高い（VIX など）
# bands は (上限値, スコア) のリスト（昇順）。値がその上限以下に収まる最初のバンドのスコアを採用。
COMPONENT_SPECS = [
    {
        'series_id': 'T10Y2Y', 'label': '2-10年スプレッド', 'direction': 'lower',
        'bands': [(-0.5, 100), (0.0, 75), (0.5, 50), (1.5, 25), (float('inf'), 0)],
    },
    {
        'series_id': 'T10Y3M', 'label': '3M-10年スプレッド', 'direction': 'lower',
        'bands': [(-0.5, 100), (0.0, 75), (0.5, 50), (1.5, 25), (float('inf'), 0)],
    },
    {
        'series_id': 'VIXCLS', 'label': 'VIX', 'direction': 'higher',
        'bands': [(15, 0), (20, 25), (25, 50), (30, 75), (40, 90), (float('inf'), 100)],
    },
    {
        'series_id': 'BAMLH0A0HYM2', 'label': 'HYスプレッド', 'direction': 'higher',
        'bands': [(3, 0), (4, 25), (5, 50), (7, 75), (10, 90), (float('inf'), 100)],
    },
    {
        'series_id': 'BAMLC0A0CM', 'label': 'IGスプレッド', 'direction': 'higher',
        'bands': [(1.0, 0), (1.3, 25), (1.7, 50), (2.2, 75), (3.0, 90), (float('inf'), 100)],
    },
    {
        'series_id': 'NFCI', 'label': '金融状況', 'direction': 'higher',
        'bands': [(-0.5, 0), (0.0, 25), (0.3, 50), (0.7, 75), (1.0, 90), (float('inf'), 100)],
    },
    {
        'series_id': 'STLFSI4', 'label': '金融ストレス', 'direction': 'higher',
        'bands': [(-1.0, 0), (0.0, 25), (0.5, 50), (1.5, 75), (2.5, 90), (float('inf'), 100)],
    },
    # Phase 4 で追加（外部データ）
    {
        'series_id': 'CBOE_SKEW', 'label': 'SKEW（テール警戒）', 'direction': 'higher',
        'bands': [(120, 0), (130, 25), (140, 50), (150, 75), (160, 90), (float('inf'), 100)],
    },
    {
        'series_id': 'NAAIM_EXPOSURE', 'label': 'NAAIMエクスポージャー', 'direction': 'higher',
        # プロが極端に強気（>90）な時はクラッシュ前兆とされる
        'bands': [(50, 0), (70, 25), (85, 50), (95, 75), (105, 90), (float('inf'), 100)],
    },
    {
        'series_id': 'AAII_BULLISH', 'label': 'AAII強気%', 'direction': 'higher',
        # 個人が極端に楽観（>50%）の時は反転リスク
        'bands': [(30, 0), (40, 25), (50, 50), (55, 75), (60, 90), (float('inf'), 100)],
    },
]


def _band_score(value: float, bands) -> int:
    for upper, score in bands:
        if value <= upper:
            return score
    return bands[-1][1]


def _latest_value(series_id: str) -> Optional[float]:
    obs = (
        Observation.objects
        .filter(indicator__fred_series_id=series_id)
        .order_by('-observation_date')
        .values_list('value', flat=True)
        .first()
    )
    return obs


def compute_crash_alert() -> Dict:
    """クラッシュ警戒度を計算する。

    戻り値:
      {
        'total_score': int (0〜100, データなしは None),
        'level': 'low' | 'medium' | 'high' | 'extreme' | 'unknown',
        'level_label': str,
        'components': [
          {'series_id', 'label', 'value', 'score'},
          ...
        ],
      }
    """
    components: List[Dict] = []
    for spec in COMPONENT_SPECS:
        value = _latest_value(spec['series_id'])
        if value is None:
            continue
        score = _band_score(value, spec['bands'])
        components.append({
            'series_id': spec['series_id'],
            'label': spec['label'],
            'value': value,
            'score': score,
        })

    if not components:
        return {
            'total_score': None,
            'level': 'unknown',
            'level_label': '判定不能',
            'components': [],
        }

    total = round(sum(c['score'] for c in components) / len(components))
    level, level_label = _classify(total)
    return {
        'total_score': total,
        'level': level,
        'level_label': level_label,
        'components': components,
    }


def _classify(score: int):
    if score < 30:
        return 'low', '低（落ち着いている）'
    if score < 60:
        return 'medium', '中（注意）'
    if score < 80:
        return 'high', '高（警戒）'
    return 'extreme', '極高（緊急警戒）'
