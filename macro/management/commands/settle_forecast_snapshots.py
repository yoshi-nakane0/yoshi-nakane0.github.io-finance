"""過去に保存した予測へ実績値を入れる。"""

from django.core.management.base import BaseCommand

from macro.services.forecast_tracking import settle_due_forecasts


class Command(BaseCommand):
    help = 'ForecastSnapshot の期限到来分に realized_value と error を保存する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='確認する未決済予測の最大件数。',
        )

    def handle(self, *args, **options):
        summary = settle_due_forecasts(limit=options['limit'])
        self.stdout.write(
            f"予測検証: {summary['checked_count']} 件確認 / "
            f"{summary['settled_count']} 件確定"
        )
