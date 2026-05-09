from django.core.management.base import BaseCommand, CommandError

from earning.models import EarningsEvent
from earning.services.predict import load_model, predict_event


class Command(BaseCommand):
    help = 'Predict reaction_close for each EarningsEvent using the saved baseline model.'

    def add_arguments(self, parser):
        parser.add_argument('--symbol', type=str, default=None,
                            help='Restrict to a single ticker (debugging).')

    def handle(self, *args, **options):
        symbol = options['symbol']

        try:
            model = load_model()
        except FileNotFoundError as exc:
            raise CommandError(str(exc))

        queryset = EarningsEvent.objects.select_related('stock').all()
        if symbol:
            queryset = queryset.filter(stock__symbol=symbol)
        events = list(queryset.order_by('event_date'))

        total = len(events)
        wrote = skipped = failed = 0
        for i, event in enumerate(events, start=1):
            label = f'[{i}/{total}] {event.stock.symbol} {event.fiscal_period}'
            try:
                y_hat = predict_event(event, model)
                if y_hat is None:
                    skipped += 1
                    self.stdout.write(f'{label}: skipped (no features)')
                else:
                    wrote += 1
                    self.stdout.write(f'{label}: y_hat={y_hat:.3f}')
            except Exception as exc:
                failed += 1
                self.stdout.write(self.style.WARNING(f'{label}: failed ({exc})'))

        self.stdout.write(self.style.SUCCESS(
            f'Wrote {wrote} predictions, {skipped} skipped, {failed} failed'
        ))
