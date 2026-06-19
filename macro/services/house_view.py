"""トップに出す公式見解を一か所で組み立てる。"""

from __future__ import annotations

from typing import Optional

from dateutil.relativedelta import relativedelta

from django.utils import timezone

from ..models import (
    MacroForecastRun,
    ModelValidationReport,
    Observation,
    RegimeSnapshot,
    WorldStateSnapshot,
)
from .data_quality import build_data_quality_report
from .model_validation import model_display_grade


GRADE_ORDER = {'A': 4, 'B': 3, 'C': 2, 'D': 1}


def _latest_world_state() -> Optional[WorldStateSnapshot]:
    return WorldStateSnapshot.objects.order_by('-as_of_date').first()


def _latest_regime() -> Optional[RegimeSnapshot]:
    return RegimeSnapshot.objects.order_by('-snapshot_date').first()


def _latest_macro_run() -> Optional[MacroForecastRun]:
    return MacroForecastRun.objects.order_by('-as_of').first()


def _grade_from_score(score: float) -> str:
    if score >= 85:
        return 'A'
    if score >= 70:
        return 'B'
    if score >= 50:
        return 'C'
    return 'D'


def _apply_grade_cap(grade: str, cap: str) -> str:
    if GRADE_ORDER.get(grade, 1) <= GRADE_ORDER.get(cap, 1):
        return grade
    return cap


def _probabilities(regime: Optional[RegimeSnapshot], world: Optional[WorldStateSnapshot]) -> dict:
    regime_probs = (regime.regime_probabilities if regime else {}) or {}
    risk_probs = (regime.risk_probabilities if regime else {}) or {}

    def risk_value(key: str, field: str) -> float:
        if risk_probs.get(key) is not None:
            return risk_probs[key]
        if world is None:
            return 0.0
        value = getattr(world, field, None)
        return round(value / 100, 4) if value is not None else 0.0

    return {
        'expansion': regime_probs.get('expansion', 0.0),
        'slowdown': regime_probs.get('slowdown', 0.0),
        'contraction': regime_probs.get('contraction', 0.0),
        'recovery': regime_probs.get('recovery', 0.0),
        'inflation_reacceleration': risk_value(
            'inflation_reacceleration',
            'inflation_reacceleration_score',
        ),
        'financial_stress': risk_value('financial_stress', 'financial_stress_score'),
    }


def _regime_label(
    regime: Optional[RegimeSnapshot],
    world: Optional[WorldStateSnapshot],
    probabilities: dict,
) -> str:
    if probabilities.get('inflation_reacceleration', 0) >= 0.7:
        if regime and regime.regime_label == RegimeSnapshot.Label.EXPANSION:
            return 'expansion_with_inflation_risk'
        return 'inflation_risk'
    if regime:
        return regime.regime_label
    if world and (world.growth_score or 0) >= 60:
        return 'expansion'
    return 'unknown'


def _house_view_text(
    regime_label: str,
    world: Optional[WorldStateSnapshot],
    probabilities: dict,
) -> str:
    inflation_risk = probabilities.get('inflation_reacceleration', 0)
    growth = world.growth_score if world else None
    stress = world.market_stress_score if world else None

    if regime_label == 'expansion_with_inflation_risk':
        return '景気は拡大寄りだが、物価再加速リスクが高く、株価には金利次第で逆風'
    if inflation_risk >= 0.7:
        return '景気判断は中立だが、物価再加速リスクが高く金利上昇に注意'
    if stress is not None and stress >= 60:
        return '市場ストレスが高く、リスク資産は慎重に見る局面'
    if growth is not None and growth >= 60:
        return '景気は拡大寄りで、物価と金利の確認が次の焦点'
    if growth is not None and growth <= 40:
        return '景気は弱含みで、雇用と信用環境の悪化に注意'
    return '主要データ不足のため、公式見解は参考扱い'


def _driver_list(world: Optional[WorldStateSnapshot], probabilities: dict) -> list[str]:
    if world is None:
        return []
    explanation = world.explanation or {}
    drivers = list(explanation.get('positive_drivers') or [])
    if (world.labor_score or 0) >= 60 and '雇用はまだ強い' not in drivers:
        drivers.append('雇用はまだ強い')
    if probabilities.get('inflation_reacceleration', 0) >= 0.7:
        drivers.append('インフレ再加速リスクが高い')
    if (world.market_trend_score or 0) >= 55:
        drivers.append('市場トレンドは底堅い')
    return drivers[:5]


def _model_risks() -> list[str]:
    risks = []
    for report in ModelValidationReport.objects.order_by('-evaluated_at')[:6]:
        grade, reason = model_display_grade(report)
        if grade != 'show':
            risks.append(f'{report.target} {report.horizon}: {reason}')
    return risks[:3]


def _recent_observations(series_id: str, limit: int) -> list[Observation]:
    return list(
        Observation.objects
        .filter(indicator__fred_series_id=series_id)
        .order_by('-observation_date')[:limit]
    )


def _signed_points(value: float) -> str:
    return f'{value:+.2f}pt'


def _consecutive_value_increases(series_id: str, target_months: int) -> dict:
    rows = _recent_observations(series_id, target_months + 1)
    if len(rows) < target_months + 1:
        return {
            'streak': 0,
            'latest': None,
            'delta': None,
            'has_enough_data': False,
        }

    streak = 0
    for index in range(target_months):
        if rows[index].value > rows[index + 1].value:
            streak += 1
        else:
            break

    return {
        'streak': streak,
        'latest': rows[0],
        'delta': rows[0].value - rows[1].value,
        'has_enough_data': True,
    }


def _yoy_change(row: Observation) -> Optional[float]:
    if row.yoy_change is not None:
        return row.yoy_change
    previous = (
        Observation.objects
        .filter(
            indicator=row.indicator,
            observation_date__lte=row.observation_date - relativedelta(months=12),
        )
        .order_by('-observation_date')
        .first()
    )
    if previous is None or previous.value in (None, 0):
        return None
    return (row.value - previous.value) / abs(previous.value) * 100


def _consecutive_yoy_reaccelerations(series_id: str, target_months: int) -> dict:
    rows = _recent_observations(series_id, target_months + 1)
    yoy_values = [_yoy_change(row) for row in rows]
    if len(rows) < target_months + 1 or any(value is None for value in yoy_values):
        return {
            'streak': 0,
            'latest': None,
            'latest_yoy': None,
            'delta': None,
            'has_enough_data': False,
        }

    streak = 0
    for index in range(target_months):
        if yoy_values[index] > yoy_values[index + 1]:
            streak += 1
        else:
            break

    return {
        'streak': streak,
        'latest': rows[0],
        'latest_yoy': yoy_values[0],
        'delta': yoy_values[0] - yoy_values[1],
        'has_enough_data': True,
    }


def _invalidation_status_notes() -> list[dict]:
    unrate = _consecutive_value_increases('UNRATE', 3)
    core_pce = _consecutive_yoy_reaccelerations('PCEPILFE', 2)
    notes = []

    if unrate['has_enough_data']:
        latest = unrate['latest']
        notes.append({
            'label': '失業率',
            'detail': (
                f"直近{unrate['streak']}/3か月連続で上昇"
                f"（{latest.observation_date.isoformat()}: {latest.value:.2f}%、"
                f"前月比 {_signed_points(unrate['delta'])}）"
            ),
        })
    else:
        notes.append({
            'label': '失業率',
            'detail': '直近3か月の連続上昇を判定できるデータが不足しています。',
        })

    if core_pce['has_enough_data']:
        latest = core_pce['latest']
        notes.append({
            'label': 'Core PCE',
            'detail': (
                f"直近{core_pce['streak']}/2か月連続で再加速"
                f"（{latest.observation_date.isoformat()}: {core_pce['latest_yoy']:.2f}%、"
                f"前月比 {_signed_points(core_pce['delta'])}）"
            ),
        })
    else:
        notes.append({
            'label': 'Core PCE',
            'detail': '直近2か月の再加速を判定できる前年比データが不足しています。',
        })

    return notes


def build_house_view_context(*, as_of=None) -> dict:
    world = _latest_world_state()
    regime = _latest_regime()
    run = _latest_macro_run()
    quality = build_data_quality_report(as_of=as_of)
    probabilities = _probabilities(regime, world)
    regime_label = _regime_label(regime, world, probabilities)

    score_inputs = [
        value for value in [
            getattr(world, 'data_quality', None),
            getattr(regime, 'data_quality', None),
            getattr(run, 'confidence', None),
            quality.get('freshness_score'),
        ]
        if value is not None
    ]
    confidence_score = round(sum(score_inputs) / len(score_inputs)) if score_inputs else 0
    confidence_grade = _apply_grade_cap(
        _grade_from_score(confidence_score),
        quality.get('confidence_cap') or 'D',
    )
    if confidence_grade == 'C':
        confidence_score = min(confidence_score, 69)
    elif confidence_grade == 'D':
        confidence_score = min(confidence_score, 49)

    main_risks = []
    main_risks.extend(quality.get('blocking_issues') or [])
    main_risks.extend(quality.get('warnings') or [])
    main_risks.extend(_model_risks())
    if probabilities.get('inflation_reacceleration', 0) >= 0.7:
        main_risks.append('金利再上昇時に株価先物へ逆風')

    as_of_value = (
        getattr(world, 'as_of_date', None)
        or getattr(regime, 'snapshot_date', None)
        or getattr(run, 'as_of', None)
        or timezone.localdate()
    )
    return {
        'as_of': as_of_value.isoformat(),
        'house_view': _house_view_text(regime_label, world, probabilities),
        'regime_label': regime_label,
        'confidence_grade': confidence_grade,
        'confidence_score': confidence_score,
        'probabilities': probabilities,
        'key_drivers': _driver_list(world, probabilities),
        'main_risks': main_risks[:6],
        'invalidation_triggers': [
            '失業率が3か月連続で上昇',
            'Core PCEが2か月連続で再加速',
            '米10年金利が急上昇',
            '信用スプレッドが急拡大',
        ],
        'invalidation_status_notes': _invalidation_status_notes(),
        'display_allowed': bool(quality.get('display_allowed')),
        'blocking_issues': quality.get('blocking_issues') or [],
        'data_quality_report': quality,
    }
