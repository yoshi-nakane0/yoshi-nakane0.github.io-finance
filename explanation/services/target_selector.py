from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .contracts import BasecalcSignal


MIN_TARGET_DISTANCE_PCT = 0.15
MAX_RISK_DISTANCE_PCT = 5.0


@dataclass
class TradeTargetPlan:
    side: str
    current_price: Optional[float]
    target_1: Optional[Dict[str, Any]]
    target_2: Optional[Dict[str, Any]]
    stop_price: Optional[float]
    invalidation_price: Optional[float]
    reward_risk: Optional[float]
    probability: Optional[float]
    expected_value: Optional[float] = None
    blocked_reasons: List[str] = field(default_factory=list)

    @property
    def tradable(self) -> bool:
        return not self.blocked_reasons


def select_trade_targets(side: str, price: Optional[float], basecalc: BasecalcSignal) -> TradeTargetPlan:
    current_price = _number(price if price is not None else basecalc.current_price)
    if current_price is None:
        return _blocked(side, current_price, ['現在値がありません'])

    targets = _targets_for_side(side, basecalc)
    valid_targets = _valid_targets(side, current_price, targets)
    if not valid_targets:
        return _blocked(side, current_price, ['target不足'])

    stop_price = _stop_for_side(side, current_price, basecalc)
    if stop_price is None:
        return _blocked(
            side,
            current_price,
            ['stop不足'],
            target_1=valid_targets[0],
            target_2=valid_targets[1] if len(valid_targets) > 1 else None,
        )

    ranked_targets = _rank_targets_by_expected_value(side, current_price, stop_price, valid_targets)
    target_1 = ranked_targets[0]
    target_2 = ranked_targets[1] if len(ranked_targets) > 1 else None
    target_price = _number(target_1.get('price'))
    reward = _reward(side, current_price, target_price)
    risk = _risk(side, current_price, stop_price)
    expected_value = target_1.get('expected_value')
    blocked = []
    if reward is None or reward <= 0:
        blocked.append('targetが現在値に近すぎる')
    elif reward / current_price * 100 < MIN_TARGET_DISTANCE_PCT:
        blocked.append('targetが現在値に近すぎる')
    if risk is None or risk <= 0:
        blocked.append('stop位置が不正')
    elif risk / current_price * 100 > MAX_RISK_DISTANCE_PCT:
        blocked.append('stopが遠すぎる')
    if expected_value is not None and expected_value <= 0:
        blocked.append('期待値不足')

    reward_risk = None
    if reward is not None and risk is not None and risk > 0:
        reward_risk = round(reward / risk, 2)

    return TradeTargetPlan(
        side=side,
        current_price=current_price,
        target_1=target_1,
        target_2=target_2,
        stop_price=stop_price,
        invalidation_price=_invalidation_for_side(side, current_price, basecalc),
        reward_risk=reward_risk,
        probability=_probability(target_1),
        expected_value=expected_value,
        blocked_reasons=blocked,
    )


def _targets_for_side(side, basecalc):
    key = 'upside' if side == 'long' else 'downside'
    validated = basecalc.validated_targets or {}
    if isinstance(validated, dict) and validated.get(key):
        return validated.get(key) or []
    raw_key = 'resistance' if side == 'long' else 'support'
    raw_price = getattr(basecalc, raw_key, None)
    return [{'label': 'T1', 'price': raw_price}] if raw_price is not None else []


def _valid_targets(side, current_price, targets):
    result = []
    for target in targets or []:
        if not isinstance(target, dict):
            continue
        price = _number(target.get('price'))
        if price is None:
            continue
        if side == 'long' and price > current_price:
            result.append({**target, 'price': price})
        if side == 'short' and price < current_price:
            result.append({**target, 'price': price})
    reverse = side == 'short'
    return sorted(result, key=lambda item: item['price'], reverse=reverse)


def _rank_targets_by_expected_value(side, current_price, stop_price, targets):
    ranked = []
    risk = _risk(side, current_price, stop_price)
    for index, target in enumerate(targets):
        target_price = _number(target.get('price'))
        reward = _reward(side, current_price, target_price)
        probability = _probability(target)
        expected_value = _expected_value(reward, risk, probability)
        reward_risk = round(reward / risk, 2) if reward is not None and risk and risk > 0 else None
        ranked.append({
            **target,
            'expected_value': expected_value,
            'reward_risk': reward_risk,
            '_distance_rank': index,
        })
    ranked.sort(
        key=lambda item: (
            item.get('expected_value') if item.get('expected_value') is not None else float('-inf'),
            item.get('reward_risk') if item.get('reward_risk') is not None else float('-inf'),
            -item.get('_distance_rank', 0),
        ),
        reverse=True,
    )
    return [{key: value for key, value in item.items() if key != '_distance_rank'} for item in ranked]


def _stop_for_side(side, current_price, basecalc):
    if side == 'long':
        for value in (basecalc.bullish_invalidation, basecalc.invalidation, basecalc.support):
            value = _number(value)
            if value is not None and value < current_price:
                return value
    else:
        for value in (basecalc.bearish_invalidation, basecalc.resistance, basecalc.invalidation):
            value = _number(value)
            if value is not None and value > current_price:
                return value
    return None


def _invalidation_for_side(side, current_price, basecalc):
    value = basecalc.bullish_invalidation if side == 'long' else basecalc.bearish_invalidation
    value = _number(value if value is not None else basecalc.invalidation)
    if value is None:
        return None
    if side == 'long' and value < current_price:
        return value
    if side == 'short' and value > current_price:
        return value
    return None


def _reward(side, current_price, target_price):
    if target_price is None:
        return None
    return target_price - current_price if side == 'long' else current_price - target_price


def _risk(side, current_price, stop_price):
    return current_price - stop_price if side == 'long' else stop_price - current_price


def _probability(target):
    if not target:
        return None
    value = target.get('probability')
    if value is None:
        display = target.get('probability_display')
        if display is not None:
            value = str(display).strip().rstrip('%')
            parsed = _number(value)
            return round(parsed / 100, 4) if parsed is not None and parsed > 1 else parsed
        return None
    parsed = _number(value)
    if parsed is None:
        return None
    return round(parsed / 100, 4) if parsed > 1 else parsed


def _expected_value(reward, risk, probability):
    if reward is None or risk is None or risk <= 0:
        return None
    probability = 0.5 if probability is None else probability
    probability = max(0, min(1, probability))
    return round(probability * reward - (1 - probability) * risk, 2)


def _blocked(side, current_price, reasons, target_1=None, target_2=None):
    return TradeTargetPlan(
        side=side,
        current_price=current_price,
        target_1=target_1,
        target_2=target_2,
        stop_price=None,
        invalidation_price=None,
        reward_risk=None,
        probability=None,
        expected_value=None,
        blocked_reasons=reasons,
    )


def _number(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.replace(',', '').strip()
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
