from django.core.management.base import BaseCommand, CommandError

from explanation.services.validation_engine import HORIZON_DAYS, evaluate_due_trade_outcomes


class Command(BaseCommand):
    help = 'Explanation trade_decision の 1d/3d/5d 結果を評価して保存する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--horizon',
            choices=sorted(HORIZON_DAYS),
            help='評価する期間。未指定なら 1d/3d/5d をすべて評価する。',
        )

    def handle(self, *args, **options):
        horizon = options.get('horizon')
        try:
            counts = evaluate_due_trade_outcomes(horizon=horizon)
        except Exception as exc:
            raise CommandError(f'Explanation outcome evaluation failed: {exc}') from exc
        summary = ', '.join(f'{key}: {value}' for key, value in sorted(counts.items()))
        self.stdout.write(self.style.SUCCESS(f'evaluated ExplanationTradeOutcome {summary}'))
