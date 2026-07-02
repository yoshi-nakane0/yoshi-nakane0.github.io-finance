import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from explanation.models import ExplanationSnapshot
from explanation.services.serializer import snapshot_to_view
from explanation.services.static_snapshot import snapshot_from_payload
from explanation.services.validation_engine import build_static_trade_validation_summary


ALLOWED_DECISION_STATUSES = {'blocked', 'wait', 'watch_only', 'candidate_limited', 'candidate_confirmed'}
ALLOWED_BASECALC_DISPLAY_STATUSES = {'blocked', 'watch_only', 'candidate_limited', 'candidate_confirmed'}


class Command(BaseCommand):
    help = 'Explanation の静的JSONと表示整合性を検査する'

    def add_arguments(self, parser):
        parser.add_argument('--latest', default='explanation/data/latest_snapshot.json')
        parser.add_argument('--history', default='explanation/data/snapshot_history.json')
        parser.add_argument('--outcomes', default='explanation/data/trade_outcomes.json')
        parser.add_argument('--manifest', default='static/finance_data_manifest.json')

    def handle(self, *args, **options):
        latest = _read_json(options['latest'])
        history = _read_json(options['history'])
        outcomes = _read_json(options['outcomes'])
        manifest = _read_json(options['manifest'])

        if history.get('schema') != 'explanation_snapshot_history_v1':
            raise CommandError('snapshot_history.json schema must be explanation_snapshot_history_v1')
        if outcomes.get('schema') != 'explanation_trade_outcomes_v1':
            raise CommandError('trade_outcomes.json schema must be explanation_trade_outcomes_v1')
        if manifest.get('schema') != 'finance_data_manifest_v1':
            raise CommandError('finance_data_manifest.json schema must be finance_data_manifest_v1')
        if not latest.get('snapshot_key'):
            raise CommandError('latest_snapshot.json must include snapshot_key')
        if not isinstance(outcomes.get('summary'), dict):
            raise CommandError('trade_outcomes.json must include summary')
        if manifest.get('explanation_as_of') != latest.get('as_of'):
            raise CommandError('finance_data_manifest.json explanation_as_of must match latest_snapshot.json')
        if manifest.get('explanation_generated_at') != latest.get('generated_at'):
            raise CommandError('finance_data_manifest.json explanation_generated_at must match latest_snapshot.json')
        for key in ('git_sha', 'workflow_run_id'):
            if key not in manifest:
                raise CommandError(f'finance_data_manifest.json must include {key}')
            if manifest.get(key) != (latest.get(key) or ''):
                raise CommandError(f'finance_data_manifest.json {key} must match latest_snapshot.json')

        decision = latest.get('trade_decision') or {}
        if _is_no_trade_decision(decision):
            for key in (
                'entry_price',
                'entry_zone_low',
                'entry_zone_high',
                'target_1',
                'target_2',
                'stop_price',
                'invalidation_price',
                'reward_risk',
                'probability',
                'expected_value',
                'expected_return_pct',
            ):
                if decision.get(key) is not None:
                    raise CommandError(f'no_trade decision must not include {key}')

        neutral_snapshot = ExplanationSnapshot(
            as_of=snapshot_from_payload(latest).as_of,
            final_label='中立',
            final_stance='neutral_wait',
            action_posture='待機',
            confidence_score=50,
            confidence_grade='C',
            macro_bias='neutral',
            basecalc_bias='range',
            alignment_status='aligned',
            data_quality_score=50,
            audit_level='valid',
            audit_items=[],
            source_snapshots={},
            score_breakdown={},
        )
        if snapshot_to_view(neutral_snapshot)['alignment_summary']['status'] == '同方向':
            raise CommandError('neutral + range must not be labeled 同方向')

        seen = set()
        for row in outcomes.get('outcomes') or []:
            if not row.get('snapshot_key'):
                raise CommandError('trade outcome must include snapshot_key')
            key = '|'.join(str(row.get(item) or '') for item in ('snapshot_key', 'horizon'))
            if key in seen:
                raise CommandError(f'duplicate trade outcome key: {key}')
            seen.add(key)
            if (row.get('selected_side') or '') == 'no_trade':
                for item in ('direction_hit', 'target_1_hit', 'target_2_hit', 'stop_hit', 'realized_rr', 'expected_rr'):
                    if row.get(item) is not None:
                        raise CommandError(f'no_trade outcome must not include {item}')

        summary = build_static_trade_validation_summary(options['outcomes'])
        for row in summary.get('side_rows') or []:
            if row.get('label') == 'no_trade' and row.get('direction_hit_rate') != 'N/A':
                raise CommandError('no_trade must not be included in direction hit denominator')

        view = snapshot_to_view(snapshot_from_payload(latest))
        if decision.get('selected_side') == 'no_trade' or (decision.get('decision_type') or '').startswith('no_'):
            for row in view.get('world_model_predictions') or []:
                if row.get('bias') != '停止 / 参考' or row.get('expected_return') != 'N/A' or row.get('expected_price') != 'N/A':
                    raise CommandError('world model predictions must be stopped when trade decision is no_trade')
        _assert_score_bundle_contract(latest, 'latest_snapshot.json')
        _assert_status_names(latest, 'latest_snapshot.json')
        for index, snapshot_row in enumerate(history.get('snapshots') or []):
            if not isinstance(snapshot_row, dict):
                raise CommandError('snapshot_history.json snapshots must contain JSON objects')
            snapshot_key = snapshot_row.get('snapshot_key') or f'row {index + 1}'
            _assert_score_bundle_contract(snapshot_row, f'snapshot_history.json {snapshot_key}')
            _assert_status_names(snapshot_row, f'snapshot_history.json {snapshot_key}')

        rendered = json.dumps(latest, ensure_ascii=False)
        if '。のため' in rendered or 'ます。のため' in rendered:
            raise CommandError('latest_snapshot.json contains unnatural reason text')

        self.stdout.write(self.style.SUCCESS('Explanation integrity OK'))


def _read_json(path):
    payload_path = Path(path)
    if not payload_path.exists():
        raise CommandError(f'{path} does not exist')
    try:
        payload = json.loads(payload_path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise CommandError(f'{path} is not valid JSON: {exc}') from exc
    if not isinstance(payload, dict):
        raise CommandError(f'{path} must be a JSON object')
    return payload


def _is_no_trade_decision(decision):
    decision_type = decision.get('decision_type') or ''
    return decision.get('selected_side') == 'no_trade' or decision_type.startswith('no_')


def _assert_score_bundle_contract(payload, source):
    score_bundle = payload.get('score_bundle') or {}
    if not score_bundle:
        return
    rows = score_bundle.get('system_quality_components') or []
    contract_row = next((row for row in rows if isinstance(row, dict) and row.get('label') == '判定契約'), None)
    if not contract_row:
        raise CommandError(f'{source} score_bundle must include 判定契約 row')
    if contract_row.get('status') != 'OK' or contract_row.get('value') != '20/20':
        raise CommandError(f'{source} score_bundle 判定契約 must be OK 20/20')


def _assert_status_names(payload, source):
    decision_status = ((payload.get('trade_decision') or {}).get('decision_status') or '')
    if decision_status and decision_status not in ALLOWED_DECISION_STATUSES:
        raise CommandError(f'{source} trade_decision decision_status is not allowed: {decision_status}')
    basecalc_raw = ((payload.get('source_snapshots') or {}).get('basecalc') or {}).get('raw') or {}
    world_model = basecalc_raw.get('world_model') or {}
    output_contract = world_model.get('output_contract') or {}
    display_status = output_contract.get('display_status') or world_model.get('display_status') or ''
    if display_status and display_status not in ALLOWED_BASECALC_DISPLAY_STATUSES:
        raise CommandError(f'{source} basecalc display_status is not allowed: {display_status}')
    if output_contract.get('contract_status') == 'error':
        if display_status and display_status != 'blocked':
            raise CommandError(f'{source} basecalc error contract display_status must be blocked')
        confidence_score = output_contract.get('confidence_score')
        if confidence_score not in (None, '', 0, 0.0):
            raise CommandError(f'{source} basecalc error contract confidence_score must be 0')
