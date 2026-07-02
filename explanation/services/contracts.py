from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class MacroSignal:
    bias: str
    summary: str
    confidence_score: int
    confidence_grade: str
    data_quality_score: int
    display_status: str = 'reference'
    publish_status: str = 'reference'
    warnings: List[str] = field(default_factory=list)
    factor_vector: Dict[str, Any] = field(default_factory=dict)
    source: Dict[str, Any] = field(default_factory=dict)
    as_of: Optional[datetime] = None


@dataclass
class BasecalcSignal:
    bias: str
    summary: str
    confidence_score: int
    confidence_grade: str
    data_quality_score: int
    readiness_level: str
    can_show_prediction: bool
    support: Optional[float] = None
    resistance: Optional[float] = None
    invalidation: Optional[float] = None
    current_price: Optional[float] = None
    price_source: str = 'market_data'
    direction_1d: str = 'neutral'
    direction_3d: str = 'neutral'
    direction_5d: str = 'neutral'
    primary_direction: str = 'range'
    primary_setup: str = 'range_wait'
    counter_bias: Dict[str, Any] = field(default_factory=dict)
    scenario_probabilities: Dict[str, Any] = field(default_factory=dict)
    horizons: Dict[str, Any] = field(default_factory=dict)
    expected_return_1d: Optional[float] = None
    expected_return_3d: Optional[float] = None
    expected_return_5d: Optional[float] = None
    bullish_invalidation: Optional[float] = None
    bearish_invalidation: Optional[float] = None
    reversal_risk_score: int = 0
    rebound_improvement_score: int = 0
    continuation_score: int = 0
    shock_score: int = 0
    fallback_used: bool = False
    us_index_available: bool = True
    contract_status: str = 'unchecked'
    allowed_direction: str = 'stopped'
    allowed_horizons: Dict[str, Any] = field(default_factory=dict)
    validated_targets: Dict[str, Any] = field(default_factory=dict)
    invalidated_targets: Dict[str, Any] = field(default_factory=dict)
    stop_reasons: List[str] = field(default_factory=list)
    hard_block_reasons: List[str] = field(default_factory=list)
    soft_warning_reasons: List[str] = field(default_factory=list)
    validation_warnings: List[str] = field(default_factory=list)
    confidence_cap_reason: str = ''
    display_status: str = ''
    explanation_allowed: str = ''
    confidence_calibrated: bool = False
    validation_gate_status: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    source: Dict[str, Any] = field(default_factory=dict)
    as_of: Optional[datetime] = None


@dataclass
class AuditResult:
    level: str
    status: str
    alignment_status: str
    items: List[str]
    penalty: int
    confidence_cap: Optional[str]
    data_quality_score: int


@dataclass
class FusionResult:
    final_label: str
    final_stance: str
    action_posture: str
    confidence_score: int
    confidence_grade: str
    evidence: List[str]
    score_breakdown: Dict[str, Any]
