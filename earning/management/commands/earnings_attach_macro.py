from datetime import date, timedelta

from django.core.management.base import BaseCommand

from earning.models import EarningsEvent
from earning.services.macro import attach_macro_snapshot


class Command(BaseCommand):
    help = 'Attach macro snapshot (5 daily indicators as of event_date) to recent EarningsEvents.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=365,
                            help='Consider events whose event_date is within the last N calendar days (default: 365).')
        parser.add_argument('--symbol', type=str, default=None,
                            help='Restrict to a single ticker (debugging).')

    def handle(self, *args, **options):
        days = options['days']
        symbol = options['symbol']

        cutoff = date.today() - timedelta(days=days)
        queryset = EarningsEvent.objects.filter(event_date__gte=cutoff).select_related('stock')
        if symbol:
            queryset = queryset.filter(stock__symbol=symbol)
        events = list(queryset.order_by('event_date'))

        total = len(events)
        filled_total = 0
        for i, event in enumerate(events, start=1):
            label = f'[{i}/{total}] {event.stock.symbol} {event.fiscal_period}'
            try:
                k = attach_macro_snapshot(event)
                filled_total += k
                self.stdout.write(f'{label}: {k}/5 columns filled')
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f'{label}: failed ({exc})'))

        self.stdout.write(self.style.SUCCESS(
            f'Processed: {total} events, {filled_total} columns filled total'
        ))
