import json
from datetime import date, datetime

from django.utils import timezone

from ..models import ExplanationSnapshot
from .audit_engine import evaluate_audit
from .basecalc_adapter import load_basecalc_signal
from .fusion_engine import build_final_decision, build_trade_decision_v2
from .macro_adapter import load_macro_signal
from .scenario_builder import build_scenarios


def build_explanation_snapshot(*, save=True, basecalc_price_override=None):
    macro = load_macro_signal()
    basecalc = load_basecalc_signal(price_override=basecalc_price_override)
    audit = evaluate_audit(macro, basecalc)
    fusion = build_final_decision(macro, basecalc, audit)
    trade_decision = build_trade_decision_v2(macro, basecalc, audit)
    scenario = build_scenarios(macro, basecalc)
    as_of = max(
        [value for value in [macro.as_of, basecalc.as_of] if value is not None],
        default=timezone.now(),
    )
    snapshot = ExplanationSnapshot(
        as_of=as_of,
        final_label=fusion.final_label,
        final_stance=fusion.final_stance,
        action_posture=fusion.action_posture,
        confidence_score=fusion.confidence_score,
        confidence_grade=fusion.confidence_grade,
        macro_bias=macro.bias,
        basecalc_bias=basecalc.bias,
        alignment_status=audit.alignment_status,
        data_quality_score=audit.data_quality_score,
        audit_level=audit.level,
        audit_items=_json_safe(audit.items),
        scenario=_json_safe(scenario),
        trade_decision=_json_safe(trade_decision.to_dict()),
        evidence=_json_safe(fusion.evidence),
        source_snapshots=_json_safe({
            'macro': {
                'bias': macro.bias,
                'summary': macro.summary,
                'as_of': macro.as_of.isoformat() if macro.as_of else None,
                'model_version': (macro.source or {}).get('model_version') or (macro.source or {}).get('schema') or '',
                'confidence_score': macro.confidence_score,
                'confidence_grade': macro.confidence_grade,
                'data_quality_score': macro.data_quality_score,
                'warnings': macro.warnings,
                'factor_vector': macro.factor_vector,
                'raw': macro.source,
            },
            'basecalc': {
                'bias': basecalc.bias,
                'summary': basecalc.summary,
                'as_of': basecalc.as_of.isoformat() if basecalc.as_of else None,
                'model_version': ((basecalc.source or {}).get('world_model') or {}).get('model_version') or '',
                'confidence_score': basecalc.confidence_score,
                'confidence_grade': basecalc.confidence_grade,
                'data_quality_score': basecalc.data_quality_score,
                'readiness_level': basecalc.readiness_level,
                'can_show_prediction': basecalc.can_show_prediction,
                'current_price': basecalc.current_price,
                'price_source': basecalc.price_source,
                'primary_direction': basecalc.primary_direction,
                'primary_setup': basecalc.primary_setup,
                'counter_bias': basecalc.counter_bias,
                'scenario_probabilities': basecalc.scenario_probabilities,
                'horizons': basecalc.horizons,
                'expected_return_1d': basecalc.expected_return_1d,
                'expected_return_3d': basecalc.expected_return_3d,
                'expected_return_5d': basecalc.expected_return_5d,
                'bullish_invalidation': basecalc.bullish_invalidation,
                'bearish_invalidation': basecalc.bearish_invalidation,
                'reversal_risk_score': basecalc.reversal_risk_score,
                'rebound_improvement_score': basecalc.rebound_improvement_score,
                'continuation_score': basecalc.continuation_score,
                'shock_score': basecalc.shock_score,
                'contract_status': basecalc.contract_status,
                'allowed_direction': basecalc.allowed_direction,
                'allowed_horizons': basecalc.allowed_horizons,
                'validated_targets': basecalc.validated_targets,
                'invalidated_targets': basecalc.invalidated_targets,
                'stop_reasons': basecalc.stop_reasons,
                'confidence_calibrated': basecalc.confidence_calibrated,
                'validation_gate_status': basecalc.validation_gate_status,
                'warnings': basecalc.warnings,
                'raw': basecalc.source,
            },
        }),
        score_breakdown=_json_safe(fusion.score_breakdown),
        version='explanation_v2',
    )
    if save:
        snapshot.save()
    return snapshot


def _json_safe(value):
    return json.loads(json.dumps(value, default=_json_default))


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f'Object of type {type(value).__name__} is not JSON serializable')
