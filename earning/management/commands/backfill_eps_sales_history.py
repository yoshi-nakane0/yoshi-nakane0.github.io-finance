import csv
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from earning.models import EarningsEvent


QUARTER_COUNT = 8


def _fmt(value):
    if value is None:
        return ''
    return str(value).strip()


def _parse_date(value):
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, '%Y-%m-%d').date()
    except ValueError:
        return None


def _has_value(event):
    return bool(
        (event.eps_forecast or '').strip()
        or (event.sales_forecast or '').strip()
        or (event.surp_eps_current or '').strip()
        or (event.surp_current or '').strip()
    )


class Command(BaseCommand):
    help = 'Backfill historical EPS/Sales (q0〜q7) from eps_sales.csv into matching EarningsEvent rows. Empty rows only by default.'

    def add_arguments(self, parser):
        parser.add_argument('csv_path', type=str, help='Path to eps_sales.csv')
        parser.add_argument(
            '--force',
            action='store_true',
            help='Overwrite even if the target row already has EPS/Sales values',
        )

    def handle(self, *args, **options):
        path = Path(options['csv_path'])
        if not path.exists():
            raise CommandError(f'CSV not found: {path}')
        force = options['force']

        written = preserved = no_db_slot = 0

        with path.open('r', encoding='utf-8', newline='') as f:
            reader = csv.DictReader(f)
            with transaction.atomic():
                for row in reader:
                    symbol = (row.get('symbol') or '').strip()
                    if not symbol:
                        continue
                    q0_date = _parse_date(row.get('date'))
                    if q0_date is None:
                        continue

                    db_events = list(
                        EarningsEvent.objects
                        .filter(stock__symbol=symbol, event_date__lte=q0_date)
                        .order_by('-event_date')[:QUARTER_COUNT]
                    )

                    for q in range(QUARTER_COUNT):
                        prefix = f'q{q}_'
                        eps_forecast = _fmt(row.get(f'{prefix}eps_forecast'))
                        eps_surprise = _fmt(row.get(f'{prefix}eps_surprise'))
                        sales_forecast = _fmt(row.get(f'{prefix}sales_forecast'))
                        sales_surprise = _fmt(row.get(f'{prefix}sales_surprise'))

                        if not any([eps_forecast, eps_surprise,
                                    sales_forecast, sales_surprise]):
                            continue

                        if q >= len(db_events):
                            no_db_slot += 1
                            continue

                        event = db_events[q]

                        if not force and _has_value(event):
                            preserved += 1
                            continue

                        event.eps_forecast = eps_forecast
                        event.surp_eps_current = eps_surprise
                        event.sales_forecast = sales_forecast
                        event.surp_current = sales_surprise
                        event.save(update_fields=[
                            'eps_forecast', 'surp_eps_current',
                            'sales_forecast', 'surp_current',
                            'updated_at',
                        ])
                        written += 1

        self.stdout.write(self.style.SUCCESS(
            f'Written={written} Preserved={preserved} NoDBSlot={no_db_slot}'
        ))
