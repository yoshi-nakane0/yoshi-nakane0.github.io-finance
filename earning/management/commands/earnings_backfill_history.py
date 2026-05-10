import time
from datetime import date

from django.core.management.base import BaseCommand

from earning.models import EarningsEvent, Stock
from earning.services.macro import attach_macro_snapshot
from earning.services.yfinance import build_yahoo_symbol, fetch_price_window


def _compute_reaction_close(event):
    rows = list(event.price_window.filter(offset_days__in=[-1, 0]).values_list('offset_days', 'close'))
    closes = {off: c for off, c in rows if c is not None}
    if 0 not in closes or -1 not in closes:
        return None
    return (closes[0] / closes[-1] - 1) * 100


class Command(BaseCommand):
    help = 'Backfill historical EarningsEvent rows from yfinance.Ticker.earnings_dates.'

    def add_arguments(self, parser):
        parser.add_argument('--symbol', type=str, default=None,
                            help='Restrict to a single ticker (debugging).')
        parser.add_argument('--sleep', type=float, default=0.5,
                            help='Seconds to sleep between stocks.')

    def handle(self, *args, **options):
        import yfinance as yf

        today = date.today()
        symbol = options['symbol']
        sleep_s = options['sleep']

        stocks = Stock.objects.all().order_by('symbol')
        if symbol:
            stocks = stocks.filter(symbol=symbol)
        stocks = list(stocks)

        total_stocks = len(stocks)
        new_events = price_filled = reaction_filled = macro_filled = 0
        skipped_stocks = 0

        for i, stock in enumerate(stocks, start=1):
            yh = build_yahoo_symbol(stock.market, stock.symbol)
            label = f'[{i}/{total_stocks}] {stock.market}-{stock.symbol}'
            if yh is None:
                self.stdout.write(f'{label}: skip (unsupported market)')
                skipped_stocks += 1
                continue

            try:
                df = yf.Ticker(yh).earnings_dates
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f'{label}: yfinance err ({exc})'))
                skipped_stocks += 1
                continue

            if df is None or len(df) == 0:
                self.stdout.write(f'{label}: no earnings_dates')
                skipped_stocks += 1
                continue

            existing_dates = set(
                EarningsEvent.objects.filter(stock=stock)
                .exclude(event_date__isnull=True)
                .values_list('event_date', flat=True)
            )

            count = 0
            for ts in df.index:
                ev_date = ts.date()
                if ev_date >= today:
                    continue
                if ev_date in existing_dates:
                    continue

                fiscal = f'BF-{ev_date.isoformat()}'
                event, created = EarningsEvent.objects.get_or_create(
                    stock=stock, fiscal_period=fiscal,
                    defaults={'event_date': ev_date},
                )
                if created:
                    new_events += 1
                    existing_dates.add(ev_date)

                if not event.price_window.exists():
                    try:
                        n = fetch_price_window(event)
                        if n:
                            price_filled += 1
                    except Exception as exc:
                        self.stdout.write(self.style.WARNING(f'  {ev_date} price err: {exc}'))
                        continue

                if event.reaction_close is None:
                    rc = _compute_reaction_close(event)
                    if rc is not None:
                        event.reaction_close = rc
                        event.save(update_fields=['reaction_close'])
                        reaction_filled += 1

                if event.vix_at_event is None:
                    try:
                        n = attach_macro_snapshot(event)
                        if n:
                            macro_filled += 1
                    except Exception:
                        pass

                count += 1

            self.stdout.write(f'{label}: processed {count} past events')
            if i < total_stocks and sleep_s > 0:
                time.sleep(sleep_s)

        self.stdout.write(self.style.SUCCESS(
            f'Done: stocks={total_stocks} skipped={skipped_stocks} '
            f'new_events={new_events} price_filled={price_filled} '
            f'reaction_filled={reaction_filled} macro_filled={macro_filled}'
        ))
