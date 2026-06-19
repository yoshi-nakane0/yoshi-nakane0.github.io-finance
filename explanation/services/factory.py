from django.utils import timezone

from ..models import ExplanationSnapshot
from .audit_engine import evaluate_audit
from .basecalc_adapter import load_basecalc_signal
from .fusion_engine import build_final_decision
from .macro_adapter import load_macro_signal
from .scenario_builder import build_scenarios


def build_explanation_snapshot(*, save=True, basecalc_price_override=None):
    macro = load_macro_signal()
    basecalc = load_basecalc_signal(price_override=basecalc_price_override)
    audit = evaluate_audit(macro, basecalc)
    fusion = build_final_decision(macro, basecalc, audit)
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
        audit_items=audit.items,
        scenario=scenario,
        evidence=fusion.evidence,
        source_snapshots={
            'macro': {
                'bias': macro.bias,
                'summary': macro.summary,
                'confidence_score': macro.confidence_score,
                'confidence_grade': macro.confidence_grade,
                'data_quality_score': macro.data_quality_score,
                'warnings': macro.warnings,
                'raw': macro.source,
            },
            'basecalc': {
                'bias': basecalc.bias,
                'summary': basecalc.summary,
                'confidence_score': basecalc.confidence_score,
                'confidence_grade': basecalc.confidence_grade,
                'data_quality_score': basecalc.data_quality_score,
                'readiness_level': basecalc.readiness_level,
                'can_show_prediction': basecalc.can_show_prediction,
                'warnings': basecalc.warnings,
                'raw': basecalc.source,
            },
        },
        score_breakdown=fusion.score_breakdown,
    )
    if save:
        snapshot.save()
    return snapshot
