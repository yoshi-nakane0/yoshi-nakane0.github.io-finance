"""WorldStateSnapshot を月次でバックフィルする。"""

from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from macro.models import WorldStateSnapshot
from macro.services.world_state import backfill_world_states


class Command(BaseCommand):
    help = 'World State を月末ごとに作成・更新する'

    def add_arguments(self, parser):
        parser.add_argument('--years', type=int, default=20)
        parser.add_argument('--start', help='YYYY-MM-DD')
        parser.add_argument('--end', help='YYYY-MM-DD')
        parser.add_argument(
            '--cadence',
            choices=[choice[0] for choice in WorldStateSnapshot.Cadence.choices],
            default=WorldStateSnapshot.Cadence.MONTHLY,
        )

    def _parse_date(self, value):
        if not value:
            return None
        try:
            return datetime.strptime(value, '%Y-%m-%d').date()
        except ValueError as exc:
            raise CommandError('日付は YYYY-MM-DD で指定してください。') from exc

    def handle(self, *args, **options):
        summary = backfill_world_states(
            years=options['years'],
            cadence=options['cadence'],
            start=self._parse_date(options.get('start')),
            end=self._parse_date(options.get('end')),
        )
        self.stdout.write(
            'World State backfill: '
            f"処理 {summary['processed_count']} / "
            f"成功 {summary['success_count']} / "
            f"失敗 {summary['failed_count']}"
        )
        for failure in summary['failures'][:10]:
            self.stdout.write(
                self.style.WARNING(
                    f"{failure['as_of_date']}: {failure['error']}"
                )
            )
