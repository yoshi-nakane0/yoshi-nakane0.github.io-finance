"""保存済みmacro予測を簡易評価する。"""

from datetime import date

from django.core.management.base import BaseCommand

from macro.models import ForecastSnapshot
from macro.services.validation import evaluate_forecast_snapshot


class Command(BaseCommand):
    help = 'ForecastSnapshotのmacro予測を実績値で評価する'

    def add_arguments(self, parser):
        parser.add_argument('--forecast-id', type=int, required=True)
        parser.add_argument('--target-date', required=True, help='YYYY-MM-DD')
        parser.add_argument('--target-name', default='expansion')
        parser.add_argument('--actual-value', type=float, required=True)

    def handle(self, *args, **options):
        forecast = ForecastSnapshot.objects.get(pk=options['forecast_id'])
        outcome = evaluate_forecast_snapshot(
            forecast,
            target_date=date.fromisoformat(options['target_date']),
            target_name=options['target_name'],
            actual_value=options['actual_value'],
        )
        self.stdout.write(
            self.style.SUCCESS(
                f'evaluated outcome={outcome.id} '
                f'brier={outcome.brier_score} hit={outcome.direction_hit}'
            )
        )
