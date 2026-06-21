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
    direction_1d: str = 'neutral'
    direction_3d: str = 'neutral'
    direction_5d: str = 'neutral'
    fallback_used: bool = False
    us_index_available: bool = True
    contract_status: str = 'unchecked'
    allowed_direction: str = 'stopped'
    allowed_horizons: Dict[str, Any] = field(default_factory=dict)
    validated_targets: Dict[str, Any] = field(default_factory=dict)
    invalidated_targets: Dict[str, Any] = field(default_factory=dict)
    stop_reasons: List[str] = field(default_factory=list)
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
