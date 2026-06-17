"""指標値の正規化ユーティリティ。"""

from __future__ import annotations

from statistics import mean, pstdev
from typing import Iterable, Optional


def z_score(value: Optional[float], history: Iterable[float]) -> Optional[float]:
    if value is None:
        return None
    values = [float(item) for item in history if item is not None]
    if len(values) < 2:
        return None
    sigma = pstdev(values)
    if sigma == 0:
        return 0.0
    return (float(value) - mean(values)) / sigma
