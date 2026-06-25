import json
from datetime import date, datetime
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from ..models import ExplanationSnapshot, ExplanationTradeOutcome


DEFAULT_EXPLANATION_SNAPSHOT_PATH = Path('explanation/data/latest_snapshot.json')
DEFAULT_EXPLANATION_TRADE_OUTCOMES_PATH = Path('explanation/data/trade_outcomes.json')


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f'Object of type {type(value).__name__} is not JSON serializable')


def _path(path, default):
    return Path(path) if path else settings.BASE_DIR / default


def explanation_snapshot_payload(snapshot):
    return {
        'schema': 'explanation_snapshot_v1',
        'generated_at': timezone.now().isoformat(),
        'as_of': snapshot.as_of.isoformat(),
        'version': snapshot.version,
        'final': {
            'label': snapshot.final_label,
            'stance': snapshot.final_stance,
            'action_posture': snapshot.action_posture,
            'confidence_score': snapshot.confidence_score,
            'confidence_grade': snapshot.confidence_grade,
            'status': _status_from_audit_level(snapshot.audit_level),
        },
        'macro': {
            'bias': snapshot.macro_bias,
        },
        'basecalc': {
            'bias': snapshot.basecalc_bias,
        },
        'alignment_status': snapshot.alignment_status,
        'data_quality_score': snapshot.data_quality_score,
        'audit': {
            'level': snapshot.audit_level,
            'items': snapshot.audit_items or [],
        },
        'scenario': snapshot.scenario or {},
        'trade_decision': snapshot.trade_decision or {},
        'evidence': snapshot.evidence or [],
        'source_snapshots': snapshot.source_snapshots or {},
        'score_breakdown': snapshot.score_breakdown or {},
    }


def write_static_explanation_snapshot(snapshot, path=None):
    payload_path = _path(path, DEFAULT_EXPLANATION_SNAPSHOT_PATH)
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.loads(json.dumps(explanation_snapshot_payload(snapshot), default=_json_default))
    payload_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    return payload


def load_static_explanation_snapshot(path=None):
    payload_path = _path(path, DEFAULT_EXPLANATION_SNAPSHOT_PATH)
    if not payload_path.exists():
        return None
    try:
        payload = json.loads(payload_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return snapshot_from_payload(payload)


def snapshot_from_payload(payload):
    final = payload.get('final') or {}
    macro = payload.get('macro') or {}
    basecalc = payload.get('basecalc') or {}
    audit = payload.get('audit') or {}
    return ExplanationSnapshot(
        as_of=_parse_datetime(payload.get('as_of')) or timezone.now(),
        final_label=final.get('label') or '',
        final_stance=final.get('stance') or '',
        action_posture=final.get('action_posture') or '',
        confidence_score=int(final.get('confidence_score') or 0),
        confidence_grade=final.get('confidence_grade') or '',
        macro_bias=macro.get('bias') or '',
        basecalc_bias=basecalc.get('bias') or '',
        alignment_status=payload.get('alignment_status') or '',
        data_quality_score=int(payload.get('data_quality_score') or 0),
        audit_level=audit.get('level') or 'blocked',
        audit_items=audit.get('items') or [],
        scenario=payload.get('scenario') or {},
        trade_decision=payload.get('trade_decision') or {},
        evidence=payload.get('evidence') or [],
        source_snapshots=payload.get('source_snapshots') or {},
        score_breakdown=payload.get('score_breakdown') or {},
        version=payload.get('version') or 'explanation_v2',
    )


def import_static_explanation_snapshot(path=None):
    snapshot = load_static_explanation_snapshot(path)
    if snapshot is None:
        return None, False
    existing = ExplanationSnapshot.objects.filter(
        as_of=snapshot.as_of,
        version=snapshot.version,
    ).first()
    if existing:
        return existing, False
    snapshot.save()
    return snapshot, True


def trade_outcomes_payload(outcomes=None):
    rows = outcomes if outcomes is not None else ExplanationTradeOutcome.objects.select_related('explanation').all()
    return {
        'schema': 'explanation_trade_outcomes_v1',
        'generated_at': timezone.now().isoformat(),
        'outcomes': [_trade_outcome_payload(outcome) for outcome in rows],
    }


def write_static_trade_outcomes(path=None, outcomes=None):
    payload_path = _path(path, DEFAULT_EXPLANATION_TRADE_OUTCOMES_PATH)
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.loads(json.dumps(trade_outcomes_payload(outcomes), default=_json_default))
    payload_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    return payload


def _trade_outcome_payload(outcome):
    return {
        'explanation_as_of': outcome.explanation.as_of.isoformat(),
        'horizon': outcome.horizon,
        'evaluated_at': outcome.evaluated_at.isoformat(),
        'selected_side': outcome.selected_side,
        'decision_type': outcome.decision_type,
        'trend_or_reversal': outcome.trend_or_reversal,
        'entry_price': outcome.entry_price,
        'target_1_price': outcome.target_1_price,
        'target_1_hit': outcome.target_1_hit,
        'target_2_price': outcome.target_2_price,
        'target_2_hit': outcome.target_2_hit,
        'stop_price': outcome.stop_price,
        'stop_hit': outcome.stop_hit,
        'max_favorable_excursion': outcome.max_favorable_excursion,
        'max_adverse_excursion': outcome.max_adverse_excursion,
        'exit_price': outcome.exit_price,
        'exit_reason': outcome.exit_reason,
        'realized_rr': outcome.realized_rr,
        'expected_rr': outcome.expected_rr,
        'direction_hit': outcome.direction_hit,
        'macro_regime': outcome.macro_regime,
        'technical_regime': outcome.technical_regime,
        'confidence_bucket': outcome.confidence_bucket,
        'sample_count_at_decision': outcome.sample_count_at_decision,
    }


def _status_from_audit_level(level):
    if level == 'blocked':
        return 'blocked'
    if level in {'warning', 'limited'}:
        return 'limited'
    return 'ok'


def _parse_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        except ValueError:
            return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone=timezone.get_current_timezone())
    return parsed
