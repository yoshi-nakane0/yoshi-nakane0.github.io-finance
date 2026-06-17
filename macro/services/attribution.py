"""判断理由を良い材料・悪い材料へ分ける。"""

from __future__ import annotations

from typing import Dict, List


def split_drivers(evidence: List[Dict]) -> dict:
    positive = []
    negative = []
    for item in evidence:
        contribution = item.get('contribution') or 0
        label = item.get('name') or item.get('series_id')
        if contribution >= 0:
            positive.append(label)
        else:
            negative.append(label)
    return {
        'positive': positive[:5],
        'negative': negative[:5],
    }
