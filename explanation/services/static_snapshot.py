import json
from datetime import date, datetime
from pathlib import Path

from django.conf import settings
from django.db import OperationalError, ProgrammingError
from django.utils import timezone

from ..models import ExplanationSnapshot, ExplanationTradeOutcome


DEFAULT_EXPLANATION_SNAPSHOT_PATH = Path('explanation/data/latest_snapshot.json')
DEFAULT_EXPLANATION_SNAPSHOT_HISTORY_PATH = Path('explanation/data/snapshot_history.json')
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


def load_static_snapshot_history(path=None):
    payload_path = _path(path, DEFAULT_EXPLANATION_SNAPSHOT_HISTORY_PATH)
    payload = _read_json(payload_path)
    if not isinstance(payload, dict):
        return []
    rows = payload.get('snapshots') or []
    return rows if isinstance(rows, list) else []


def append_static_explanation_history(snapshot, path=None, max_rows=500):
    payload_path = _path(path, DEFAULT_EXPLANATION_SNAPSHOT_HISTORY_PATH)
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    rows = load_static_snapshot_history(payload_path)
    snapshot_payload = json.loads(json.dumps(explanation_snapshot_payload(snapshot), default=_json_default))
    key = _snapshot_history_key(snapshot_payload)
    existing_keys = {_snapshot_history_key(row) for row in rows if isinstance(row, dict)}
    added = key not in existing_keys
    if added:
        rows.append(snapshot_payload)
    rows = rows[-max_rows:]
    payload = {
        'schema': 'explanation_snapshot_history_v1',
        'generated_at': timezone.now().isoformat(),
        'max_rows': max_rows,
        'snapshots': rows,
    }
    payload_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    return {'added': added, 'count': len(rows), 'path': str(payload_path)}


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


def import_static_snapshot_history(path=None):
    imported = 0
    for payload in load_static_snapshot_history(path):
        if not isinstance(payload, dict):
            continue
        snapshot = snapshot_from_payload(payload)
        existing = ExplanationSnapshot.objects.filter(
            as_of=snapshot.as_of,
            version=snapshot.version,
        ).first()
        if existing:
            continue
        snapshot.save()
        imported += 1
    return imported


def trade_outcomes_payload(outcomes=None, static_rows=None):
    if outcomes is not None:
        rows = list(outcomes)
    else:
        try:
            rows = list(ExplanationTradeOutcome.objects.select_related('explanation').all())
        except (OperationalError, ProgrammingError):
            rows = []
    merged = {}
    for row in static_rows or []:
        if isinstance(row, dict):
            merged[_trade_outcome_key(row)] = row
    for row in rows:
        payload = _trade_outcome_payload(row)
        merged[_trade_outcome_key(payload)] = payload
    return {
        'schema': 'explanation_trade_outcomes_v1',
        'generated_at': timezone.now().isoformat(),
        'outcomes': sorted(
            merged.values(),
            key=lambda row: (row.get('evaluated_at') or '', row.get('explanation_as_of') or '', row.get('horizon') or ''),
            reverse=True,
        ),
    }


def write_static_trade_outcomes(path=None, outcomes=None, static_rows=None):
    payload_path = _path(path, DEFAULT_EXPLANATION_TRADE_OUTCOMES_PATH)
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.loads(json.dumps(trade_outcomes_payload(outcomes, static_rows=static_rows), default=_json_default))
    payload_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    return payload


def load_static_trade_outcomes(path=None):
    payload_path = _path(path, DEFAULT_EXPLANATION_TRADE_OUTCOMES_PATH)
    payload = _read_json(payload_path)
    if not isinstance(payload, dict):
        return []
    rows = payload.get('outcomes') or []
    return rows if isinstance(rows, list) else []


def import_static_trade_outcomes(path=None):
    imported = 0
    for row in load_static_trade_outcomes(path):
        if not isinstance(row, dict):
            continue
        as_of = _parse_datetime(row.get('explanation_as_of'))
        if as_of is None:
            continue
        snapshot = ExplanationSnapshot.objects.filter(
            as_of=as_of,
        ).order_by('-created_at').first()
        if snapshot is None:
            continue
        horizon = row.get('horizon')
        if not horizon:
            continue
        ExplanationTradeOutcome.objects.update_or_create(
            explanation=snapshot,
            horizon=horizon,
            defaults={
                'evaluated_at': _parse_datetime(row.get('evaluated_at')) or timezone.now(),
                'selected_side': row.get('selected_side') or 'no_trade',
                'decision_type': row.get('decision_type') or '',
                'trend_or_reversal': row.get('trend_or_reversal') or '',
                'entry_price': _number(row.get('entry_price')),
                'target_1_price': _number(row.get('target_1_price')),
                'target_1_hit': bool(row.get('target_1_hit')),
                'target_2_price': _number(row.get('target_2_price')),
                'target_2_hit': bool(row.get('target_2_hit')),
                'stop_price': _number(row.get('stop_price')),
                'stop_hit': bool(row.get('stop_hit')),
                'max_favorable_excursion': _number(row.get('max_favorable_excursion')),
                'max_adverse_excursion': _number(row.get('max_adverse_excursion')),
                'exit_price': _number(row.get('exit_price')),
                'exit_reason': row.get('exit_reason') or '',
                'realized_rr': _number(row.get('realized_rr')),
                'expected_rr': _number(row.get('expected_rr')),
                'direction_hit': _direction_hit_value(row),
                'is_actionable': (row.get('selected_side') or '') in {'long', 'short'},
                'outcome_kind': row.get('outcome_kind') or _outcome_kind(row),
                'missed_opportunity': bool(row.get('missed_opportunity')),
                'horizon_return_pct': _number(row.get('horizon_return_pct')),
                'macro_regime': row.get('macro_regime') or '',
                'technical_regime': row.get('technical_regime') or '',
                'confidence_bucket': row.get('confidence_bucket') or '',
                'sample_count_at_decision': _int_or_none(row.get('sample_count_at_decision')),
            },
        )
        imported += 1
    return imported


def _trade_outcome_payload(outcome):
    if isinstance(outcome, dict):
        return dict(outcome)
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
        'is_actionable': outcome.is_actionable,
        'outcome_kind': outcome.outcome_kind,
        'missed_opportunity': outcome.missed_opportunity,
        'horizon_return_pct': outcome.horizon_return_pct,
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


def _read_json(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None


def _snapshot_history_key(payload):
    final = payload.get('final') or {}
    basecalc = payload.get('basecalc') or {}
    macro = payload.get('macro') or {}
    decision = payload.get('trade_decision') or {}
    return '|'.join(
        str(value or '')
        for value in (
            payload.get('as_of'),
            payload.get('version'),
            final.get('stance'),
            basecalc.get('bias'),
            macro.get('bias'),
            decision.get('selected_side'),
            decision.get('decision_type'),
        )
    )


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _direction_hit_value(row):
    if (row.get('selected_side') or '') not in {'long', 'short'}:
        return None
    value = row.get('direction_hit')
    return bool(value) if value is not None else None


def _outcome_kind(row):
    return 'actionable_observed' if (row.get('selected_side') or '') in {'long', 'short'} else 'wait_observed'


def _trade_outcome_key(row):
    return '|'.join(
        str(row.get(item) or '')
        for item in ('explanation_as_of', 'horizon', 'selected_side', 'decision_type')
    )
