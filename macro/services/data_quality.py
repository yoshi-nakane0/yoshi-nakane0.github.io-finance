"""データ鮮度・欠損・検証状況をまとめる境界。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Optional

from django.utils import timezone

from ..models import Indicator, Observation
from . import regime
from .crash_alert import FRESHNESS_LIMIT_DAYS


REQUIRED_DECISION_SERIES = (
    ('CPIAUCSL', 'CPI'),
    ('CPILFESL', 'Core CPI'),
    ('PCEPI', 'PCE'),
    ('PCEPILFE', 'Core PCE'),
    ('UNRATE', '失業率'),
    ('DGS10', '米10年金利'),
    ('VIXCLS', 'VIX'),
    ('BAMLH0A0HYM2', '信用スプレッド'),
)


@dataclass
class DataQualityGateResult:
    usable_for_decision: bool
    confidence_cap: str
    freshness_score: float
    missing_required_count: int
    blocking_issues: list[str]
    warnings: list[str]
    as_of: str
    display_allowed: bool
    stale_required_count: int
    required_count: int


def assess_macro_data_quality(*, as_of: Optional[date] = None) -> dict:
    assessment = regime.build_current_regime_assessment(as_of=as_of)
    return {
        'score': assessment.get('data_quality', 0),
        'warnings': assessment.get('warnings') or [],
        'model_version': assessment.get('model_version'),
    }


def _latest_required_observation(series_id: str, as_of: date) -> Optional[Observation]:
    return (
        Observation.objects
        .filter(indicator__fred_series_id=series_id, observation_date__lte=as_of)
        .select_related('indicator')
        .order_by('-observation_date')
        .first()
    )


def _is_stale(indicator: Indicator, observation_date: date, as_of: date) -> bool:
    limit_days = FRESHNESS_LIMIT_DAYS.get(indicator.frequency)
    if limit_days is None:
        return False
    return max((as_of - observation_date).days, 0) > limit_days


def build_data_quality_gate(*, as_of: Optional[date] = None) -> DataQualityGateResult:
    """トップ判断に使ってよいデータ状態かを一か所で判定する。"""
    as_of = as_of or timezone.localdate()
    missing = []
    stale = []
    warnings = []

    for series_id, name in REQUIRED_DECISION_SERIES:
        indicator = Indicator.objects.filter(
            fred_series_id=series_id,
            is_active=True,
        ).first()
        if indicator is None:
            missing.append(f'{name}（{series_id}）')
            continue

        latest = _latest_required_observation(series_id, as_of)
        if latest is None:
            missing.append(f'{indicator.name_ja}（{series_id}）')
            continue
        if _is_stale(indicator, latest.observation_date, as_of):
            stale.append(
                f'{indicator.name_ja}（{latest.observation_date.isoformat()}）'
            )

    required_count = len(REQUIRED_DECISION_SERIES)
    fresh_count = max(required_count - len(missing) - len(stale), 0)
    freshness_score = round(fresh_count / required_count * 100, 1)

    blocking_issues = []
    if missing:
        blocking_issues.append(
            f'主要指標が{len(missing)}件未取得です: {", ".join(missing[:4])}'
        )
    if freshness_score < 50:
        blocking_issues.append('主要データの鮮度が50%未満です。')
    if stale:
        warnings.append(
            f'主要指標が{len(stale)}件古くなっています: {", ".join(stale[:4])}'
        )
    if freshness_score < 50:
        warnings.append('トップの総合判断は参考扱いです。')

    if missing:
        confidence_cap = 'C'
    elif freshness_score < 50:
        confidence_cap = 'C'
    elif stale:
        confidence_cap = 'B'
    else:
        confidence_cap = 'A'

    usable_for_decision = not blocking_issues
    return DataQualityGateResult(
        usable_for_decision=usable_for_decision,
        confidence_cap=confidence_cap,
        freshness_score=freshness_score,
        missing_required_count=len(missing),
        blocking_issues=blocking_issues,
        warnings=warnings,
        as_of=as_of.isoformat(),
        display_allowed=usable_for_decision,
        stale_required_count=len(stale),
        required_count=required_count,
    )


def build_data_quality_report(*, as_of: Optional[date] = None) -> dict:
    return asdict(build_data_quality_gate(as_of=as_of))
