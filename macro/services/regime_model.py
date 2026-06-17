"""景気4分類の確率分布を返す境界。"""

from __future__ import annotations

from datetime import date
from typing import Optional

from . import regime


def estimate_regime_distribution(*, as_of: Optional[date] = None) -> dict:
    assessment = regime.build_current_regime_assessment(as_of=as_of)
    return {
        'primary_regime': assessment.get('regime_label'),
        'probabilities': assessment.get('regime_probabilities') or {},
        'confidence': assessment.get('rule_strength', 0),
        'data_quality': assessment.get('data_quality', 0),
        'model_version': assessment.get('probability_model_version'),
    }
