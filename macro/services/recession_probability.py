"""3〜6か月の景気後退リスクを返す。"""

from __future__ import annotations

from datetime import date
from typing import Optional

from . import regime


def estimate_recession_probability(*, as_of: Optional[date] = None) -> float:
    assessment = regime.build_current_regime_assessment(as_of=as_of)
    risks = assessment.get('risk_probabilities') or {}
    return float(risks.get('recession', 0.0))
