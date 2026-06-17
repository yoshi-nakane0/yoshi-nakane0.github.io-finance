"""データ鮮度・欠損・検証状況をまとめる境界。"""

from __future__ import annotations

from datetime import date
from typing import Optional

from . import regime


def assess_macro_data_quality(*, as_of: Optional[date] = None) -> dict:
    assessment = regime.build_current_regime_assessment(as_of=as_of)
    return {
        'score': assessment.get('data_quality', 0),
        'warnings': assessment.get('warnings') or [],
        'model_version': assessment.get('model_version'),
    }
