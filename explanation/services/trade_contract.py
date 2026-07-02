from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TradeDecision:
    selected_side: str
    decision_type: str
    horizon: str
    current_price: Optional[float]
    entry_price: Optional[float]
    entry_zone_low: Optional[float]
    entry_zone_high: Optional[float]
    target_1: Optional[Dict[str, Any]]
    target_2: Optional[Dict[str, Any]]
    stop_price: Optional[float]
    invalidation_price: Optional[float]
    reward_risk: Optional[float]
    expected_return_pct: Optional[float]
    probability: Optional[float]
    expected_value: Optional[float]
    confidence_score: int
    confidence_grade: str
    long_score: int
    short_score: int
    no_trade_score: int
    trend_follow_score: int
    reversal_score: int
    counter_scenario: Dict[str, Any] = field(default_factory=dict)
    reversal_watch: Dict[str, Any] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    blocked_reasons: List[str] = field(default_factory=list)
    model_version: str = 'explanation_v2'
    price_source: str = 'market_data'
    decision_status: str = 'wait'
    entry_permission: str = 'no_entry'
    validation_level: str = 'none'
    hard_block_reasons: List[str] = field(default_factory=list)
    soft_warning_reasons: List[str] = field(default_factory=list)
    confidence_components: Dict[str, Any] = field(default_factory=dict)
    position_size_pct: Optional[int] = 0
    position_size_cap: str = 'none'

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def no_trade_decision(
    *,
    decision_type: str,
    current_price: Optional[float],
    confidence_score: int,
    confidence_grade: str,
    long_score: int = 0,
    short_score: int = 0,
    no_trade_score: int = 100,
    trend_follow_score: int = 0,
    reversal_score: int = 0,
    reasons: Optional[List[str]] = None,
    warnings: Optional[List[str]] = None,
    blocked_reasons: Optional[List[str]] = None,
    counter_scenario: Optional[Dict[str, Any]] = None,
    reversal_watch: Optional[Dict[str, Any]] = None,
    price_source: str = 'market_data',
    entry_price: Optional[float] = None,
    entry_zone_low: Optional[float] = None,
    entry_zone_high: Optional[float] = None,
    target_1: Optional[Dict[str, Any]] = None,
    target_2: Optional[Dict[str, Any]] = None,
    stop_price: Optional[float] = None,
    invalidation_price: Optional[float] = None,
    reward_risk: Optional[float] = None,
    expected_return_pct: Optional[float] = None,
    probability: Optional[float] = None,
    expected_value: Optional[float] = None,
    decision_status: str = 'wait',
    entry_permission: str = 'no_entry',
    validation_level: str = 'none',
    hard_block_reasons: Optional[List[str]] = None,
    soft_warning_reasons: Optional[List[str]] = None,
    confidence_components: Optional[Dict[str, Any]] = None,
    position_size_pct: Optional[int] = 0,
    position_size_cap: str = 'none',
) -> TradeDecision:
    hard_blocks = hard_block_reasons if hard_block_reasons is not None else blocked_reasons
    return TradeDecision(
        selected_side='no_trade',
        decision_type=decision_type,
        horizon='3d',
        current_price=current_price,
        entry_price=entry_price,
        entry_zone_low=entry_zone_low,
        entry_zone_high=entry_zone_high,
        target_1=target_1,
        target_2=target_2,
        stop_price=stop_price,
        invalidation_price=invalidation_price,
        reward_risk=reward_risk,
        expected_return_pct=expected_return_pct,
        probability=probability,
        expected_value=expected_value,
        confidence_score=confidence_score,
        confidence_grade=confidence_grade,
        long_score=long_score,
        short_score=short_score,
        no_trade_score=no_trade_score,
        trend_follow_score=trend_follow_score,
        reversal_score=reversal_score,
        counter_scenario=counter_scenario or {},
        reversal_watch=reversal_watch or {},
        reasons=reasons or [],
        warnings=warnings or [],
        blocked_reasons=blocked_reasons or [],
        price_source=price_source,
        decision_status=decision_status,
        entry_permission=entry_permission,
        validation_level=validation_level,
        hard_block_reasons=hard_blocks or [],
        soft_warning_reasons=soft_warning_reasons or [],
        confidence_components=confidence_components or {},
        position_size_pct=position_size_pct,
        position_size_cap=position_size_cap,
    )
