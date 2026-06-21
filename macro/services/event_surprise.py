"""経済指標の市場予想との差を標準形にする。"""

from __future__ import annotations

from typing import Optional


def _round_gap(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(value, 4)


def _direction(surprise: Optional[float]) -> str:
    if surprise is None:
        return 'unknown'
    if surprise > 0:
        return 'above_consensus'
    if surprise < 0:
        return 'below_consensus'
    return 'in_line'


def _market_impact(category: str, direction: str) -> str:
    if direction == 'unknown':
        return '市場予想との差を判定できません。'
    if category == 'inflation':
        if direction == 'above_consensus':
            return 'Fed利下げ期待後退、金利上昇、株式には逆風。'
        if direction == 'below_consensus':
            return 'Fed利下げ期待が戻りやすく、金利低下、株式には追い風。'
    if category == 'labor':
        if direction == 'above_consensus':
            return '雇用の底堅さで景気見通しは上向く一方、利下げは遅れやすい。'
        if direction == 'below_consensus':
            return '雇用鈍化で景気後退警戒が強まり、株式には逆風。'
    if category == 'growth':
        if direction == 'above_consensus':
            return '成長見通しは上向くが、金利上昇との綱引き。'
        if direction == 'below_consensus':
            return '成長見通しを下げ、リスク資産には慎重材料。'
    return '市場反応を金利、為替、株価で確認。'


def _forecast_impact(category: str, direction: str) -> str:
    if direction == 'unknown':
        return '次回予測への影響は未判定。'
    if category == 'inflation':
        return (
            'インフレ見通しを上方修正。'
            if direction == 'above_consensus'
            else 'インフレ見通しを下方修正。'
        )
    if category == 'labor':
        return (
            '雇用と消費の見通しを上方確認。'
            if direction == 'above_consensus'
            else '雇用悪化リスクを上方確認。'
        )
    if category == 'growth':
        return (
            '成長率見通しを上方確認。'
            if direction == 'above_consensus'
            else '成長率見通しを下方確認。'
        )
    return 'House Viewへの反映は次回更新で確認。'


def build_event_surprise(
    *,
    event_name: str,
    actual: Optional[float],
    consensus: Optional[float],
    previous: Optional[float],
    unit: str = '',
    category: str = 'macro',
) -> dict:
    surprise = (
        _round_gap(actual - consensus)
        if actual is not None and consensus is not None
        else None
    )
    revision = (
        _round_gap(actual - previous)
        if actual is not None and previous is not None
        else None
    )
    direction = _direction(surprise)
    return {
        'event_name': event_name,
        'actual': actual,
        'consensus': consensus,
        'previous': previous,
        'surprise': surprise,
        'revision': revision,
        'unit': unit,
        'category': category,
        'direction': direction,
        'market_impact': _market_impact(category, direction),
        'next_forecast_impact': _forecast_impact(category, direction),
    }


def save_event_surprise(*, event_date, source: str = 'manual_consensus', **kwargs):
    from ..models import MacroEventSurprise

    payload = build_event_surprise(**kwargs)
    obj, _ = MacroEventSurprise.objects.update_or_create(
        event_date=event_date,
        event_name=payload['event_name'],
        source=source,
        defaults={
            'category': payload['category'],
            'actual': payload['actual'],
            'consensus': payload['consensus'],
            'previous': payload['previous'],
            'surprise': payload['surprise'],
            'revision': payload['revision'],
            'unit': payload['unit'],
            'direction': payload['direction'],
            'market_impact': payload['market_impact'],
            'next_forecast_impact': payload['next_forecast_impact'],
            'payload': payload,
        },
    )
    return obj
