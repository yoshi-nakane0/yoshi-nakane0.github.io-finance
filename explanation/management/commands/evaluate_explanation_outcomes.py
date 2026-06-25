from django.core.management.base import BaseCommand, CommandError

from explanation.services.static_snapshot import write_static_trade_outcomes
from explanation.services.validation_engine import HORIZON_DAYS, evaluate_due_trade_outcomes


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
            '--no-export-json',
            action='store_true',
            help='DB保存のみ行い、検証結果JSONを出力しない',
        )

    def handle(self, *args, **options):
        horizon = options.get('horizon')
        try:
            counts = evaluate_due_trade_outcomes(horizon=horizon)
        except Exception as exc:
            raise CommandError(f'Explanation outcome evaluation failed: {exc}') from exc
        if not options['no_export_json']:
            write_static_trade_outcomes(options['output'])
        summary = ', '.join(f'{key}: {value}' for key, value in sorted(counts.items()))
        self.stdout.write(self.style.SUCCESS(f'evaluated ExplanationTradeOutcome {summary}'))
