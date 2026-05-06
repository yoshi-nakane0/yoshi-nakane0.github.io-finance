"""指標値を5段階評価に変換するロジック。

Indicator.judgment_rule の構造:
    {
        "metric":   "yoy" | "level" | "mom" | "diff_50",
        "economic": {"direction": "...", "thresholds": [t1, t2, t3, t4]},
        "market":   {"direction": "...", "thresholds": [t1, t2, t3, t4]},
    }

direction:
    "lower_better"  値が小さいほど良い
    "higher_better" 値が大きいほど良い
    "target_band"   中央が良い（U字評価）

戻り値: 1〜5 の整数（1=最良, 5=最悪）。判定不能なら None。
"""

from typing import Optional, Tuple


VALID_DIRECTIONS = ('lower_better', 'higher_better', 'target_band')
VALID_METRICS = ('yoy', 'level', 'mom', 'diff_50')


def _extract_metric_value(observation, metric: str) -> Optional[float]:
    """Observation から判定対象の値を取り出す。"""
    if metric == 'yoy':
        return observation.yoy_change
    if metric == 'level':
        return observation.value
    if metric == 'mom':
        if observation.prev_value is None or observation.value is None:
            return None
        return observation.value - observation.prev_value
    if metric == 'diff_50':
        if observation.value is None:
            return None
        return observation.value - 50.0
    return None


def _stage_lower_better(value: float, thresholds) -> int:
    t1, t2, t3, t4 = thresholds
    if value <= t1:
        return 1
    if value <= t2:
        return 2
    if value <= t3:
        return 3
    if value <= t4:
        return 4
    return 5


def _stage_higher_better(value: float, thresholds) -> int:
    t1, t2, t3, t4 = thresholds
    if value <= t1:
        return 5
    if value <= t2:
        return 4
    if value <= t3:
        return 3
    if value <= t4:
        return 2
    return 1


def _stage_target_band(value: float, thresholds) -> int:
    t1, t2, t3, t4 = thresholds
    if value <= t1:
        return 5
    if value <= t2:
        return 3
    if value <= t3:
        return 1
    if value <= t4:
        return 3
    return 5


def _stage_for(value: Optional[float], view_rule: dict) -> Optional[int]:
    if value is None or view_rule is None:
        return None
    direction = view_rule.get('direction')
    thresholds = view_rule.get('thresholds')
    if direction not in VALID_DIRECTIONS or not thresholds or len(thresholds) != 4:
        return None
    if direction == 'lower_better':
        return _stage_lower_better(value, thresholds)
    if direction == 'higher_better':
        return _stage_higher_better(value, thresholds)
    return _stage_target_band(value, thresholds)


def evaluate(observation, rule: Optional[dict]) -> Tuple[Optional[int], Optional[int]]:
    """observation と judgment_rule を渡して (経済段階, 市場段階) を返す。

    判定できない場合は対応する位置に None を返す。
    """
    if observation is None or not rule:
        return (None, None)
    metric = rule.get('metric')
    if metric not in VALID_METRICS:
        return (None, None)
    value = _extract_metric_value(observation, metric)
    economic_stage = _stage_for(value, rule.get('economic'))
    market_stage = _stage_for(value, rule.get('market'))
    return (economic_stage, market_stage)
