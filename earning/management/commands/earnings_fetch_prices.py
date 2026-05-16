import time
from datetime import date, timedelta

from django.core.management.base import BaseCommand

from earning.models import EarningsEvent
from earning.services.reactions import update_price_reactions
from earning.services.yfinance import fetch_price_window


class Command(BaseCommand):
    help = 'Fetch daily OHLCV around each recent earnings event into EarningsPriceWindow.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=90,
                            help='Consider events whose event_date is within the last N calendar days (default: 90).')
        parser.add_argument('--symbol', type=str, default=None,
                            help='Restrict to a single ticker (debugging).')
        parser.add_argument('--force', action='store_true',
                            help='Re-fetch even if rows already exist (currently informational; update_or_create is always idempotent).')
        parser.add_argument('--sleep', type=float, default=0.3,
                            help='Seconds to sleep between events (default: 0.3).')

    def handle(self, *args, **options):
        days = options['days']
        symbol = options['symbol']
        sleep_seconds = options['sleep']

        cutoff = date.today() - timedelta(days=days)
        queryset = EarningsEvent.objects.filter(event_date__gte=cutoff).select_related('stock')
        if symbol:
            queryset = queryset.filter(stock__symbol=symbol)
        events = list(queryset.order_by('event_date'))

        total = len(events)
        written_total = reaction_total = failed = 0
        for i, event in enumerate(events, start=1):
            label = f'[{i}/{total}] {event.stock.symbol} {event.fiscal_period}'
            try:
                n = fetch_price_window(event)
                written_total += n
                rc, rn = update_price_reactions(event)
                if rc is not None or rn is not None:
                    reaction_total += 1
                self.stdout.write(f'{label}: {n} rows')
            except Exception as exc:
                failed += 1
                self.stdout.write(self.style.WARNING(f'{label}: failed ({exc})'))

            if i < total and sleep_seconds > 0:
                time.sleep(sleep_seconds)

        self.stdout.write(self.style.SUCCESS(
            f'Processed: {total} events, {written_total} rows written, '
            f'{reaction_total} reactions updated, {failed} failed'
        ))
