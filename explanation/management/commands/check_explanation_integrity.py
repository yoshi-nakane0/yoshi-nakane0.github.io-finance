import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from explanation.models import ExplanationSnapshot
from explanation.services.serializer import snapshot_to_view
from explanation.services.static_snapshot import snapshot_from_payload
from explanation.services.validation_engine import build_static_trade_validation_summary


class Command(BaseCommand):
    help = 'Explanation の静的JSONと表示整合性を検査する'

    def add_arguments(self, parser):
        parser.add_argument('--latest', default='explanation/data/latest_snapshot.json')
        parser.add_argument('--history', default='explanation/data/snapshot_history.json')
        parser.add_argument('--outcomes', default='explanation/data/trade_outcomes.json')

    def handle(self, *args, **options):
        latest = _read_json(options['latest'])
        history = _read_json(options['history'])
        outcomes = _read_json(options['outcomes'])

        if history.get('schema') != 'explanation_snapshot_history_v1':
            raise CommandError('snapshot_history.json schema must be explanation_snapshot_history_v1')
        if outcomes.get('schema') != 'explanation_trade_outcomes_v1':
            raise CommandError('trade_outcomes.json schema must be explanation_trade_outcomes_v1')

        decision = latest.get('trade_decision') or {}
        if decision.get('decision_type') == 'no_trade_direction_stopped':
            for key in ('target_1', 'target_2', 'stop_price', 'reward_risk'):
                if decision.get(key) is not None:
                    raise CommandError(f'direction stopped decision must not include {key}')

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
            key = '|'.join(str(row.get(item) or '') for item in ('explanation_as_of', 'horizon', 'selected_side', 'decision_type'))
            if key in seen:
                raise CommandError(f'duplicate trade outcome key: {key}')
            seen.add(key)

        summary = build_static_trade_validation_summary()
        for row in summary.get('side_rows') or []:
            if row.get('label') == 'no_trade' and row.get('direction_hit_rate') != 'N/A':
                raise CommandError('no_trade must not be included in direction hit denominator')

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
