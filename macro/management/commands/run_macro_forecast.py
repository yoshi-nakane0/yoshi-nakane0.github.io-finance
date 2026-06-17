"""macro予測を保存する。"""

from datetime import date

from django.core.management.base import BaseCommand

from macro.services.forecast_runner import run_macro_forecast


class Command(BaseCommand):
    help = '経済状態ベクトル、レジーム確率、3シナリオをForecastSnapshotへ保存する'

    def add_arguments(self, parser):
        parser.add_argument('--as-of', dest='as_of', help='YYYY-MM-DD')

    def handle(self, *args, **options):
        as_of = (
            date.fromisoformat(options['as_of'])
            if options.get('as_of') else None
        )
        result = run_macro_forecast(as_of=as_of)
        self.stdout.write(
            self.style.SUCCESS(
                f'saved macro forecast {result.snapshot.as_of_date} '
                f'{result.run.primary_regime} scenarios={len(result.scenarios)}'
            )
        )
