from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from earning.services.eps_sales_sync import sync_eps_sales_csv_to_db


class Command(BaseCommand):
    help = 'Sync EPS/Sales values from eps_sales.csv into existing EarningsEvent rows (idempotent).'

    def add_arguments(self, parser):
        parser.add_argument('csv_path', type=str, help='Path to eps_sales.csv')

    def handle(self, *args, **options):
        path = Path(options['csv_path'])
        if not path.exists():
            raise CommandError(f'CSV not found: {path}')
        q0, q1, skipped = sync_eps_sales_csv_to_db(str(path))
        self.stdout.write(self.style.SUCCESS(
            f'Updated q0={q0} q1={q1} skipped={skipped}'
        ))
