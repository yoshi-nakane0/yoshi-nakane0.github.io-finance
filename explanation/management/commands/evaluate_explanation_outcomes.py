from django.core.management.base import BaseCommand, CommandError

from explanation.services.static_snapshot import (
    import_static_snapshot_history,
    import_static_trade_outcomes,
    load_static_snapshot_history,
    load_static_trade_outcomes,
    write_static_trade_outcomes,
)
from explanation.services.validation_engine import HORIZON_DAYS, build_pending_trade_outcomes, evaluate_due_trade_outcomes


class Command(BaseCommand):
    help = 'Explanation trade_decision の 1d/3d/5d 結果を評価して保存する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--horizon',
            choices=sorted(HORIZON_DAYS),
            help='評価する期間。未指定なら 1d/3d/5d をすべて評価する。',
        )
        parser.add_argument(
            '--output',
            default='explanation/data/trade_outcomes.json',
            help='Explanation 検証結果JSONの出力先',
        )
        parser.add_argument(
            '--snapshot-history',
            default='explanation/data/snapshot_history.json',
            help='評価前に取り込む Explanation 判定履歴JSON',
        )
        parser.add_argument(
            '--input-outcomes',
            default='explanation/data/trade_outcomes.json',
            help='評価前に取り込む Explanation 検証結果JSON',
        )
        parser.add_argument(
            '--no-export-json',
            action='store_true',
            help='DB保存のみ行い、検証結果JSONを出力しない',
        )

    def handle(self, *args, **options):
        horizon = options.get('horizon')
        try:
            imported_snapshots = import_static_snapshot_history(options['snapshot_history'])
            imported_outcomes = import_static_trade_outcomes(options['input_outcomes'])
            counts = evaluate_due_trade_outcomes(horizon=horizon)
        except Exception as exc:
            raise CommandError(f'Explanation outcome evaluation failed: {exc}') from exc
        if not options['no_export_json']:
            static_rows = load_static_trade_outcomes(options['input_outcomes'])
            pending_rows = build_pending_trade_outcomes(
                load_static_snapshot_history(options['snapshot_history']),
                static_rows,
                horizon=horizon,
            )
            write_static_trade_outcomes(
                options['output'],
                static_rows=static_rows + pending_rows,
            )
        else:
            pending_rows = []
        summary = ', '.join(f'{key}: {value}' for key, value in sorted(counts.items()))
        self.stdout.write(
            self.style.SUCCESS(
                f'evaluated ExplanationTradeOutcome {summary}; '
                f'imported_snapshots={imported_snapshots}; imported_outcomes={imported_outcomes}; '
                f'pending={len(pending_rows)}'
            )
        )
