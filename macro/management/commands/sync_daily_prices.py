"""主要指数の日次終値を同期する。"""

from django.core.management.base import BaseCommand

from macro.services.yfinance_client import TICKER_TO_SYMBOL, sync_all_daily_price_histories


class Command(BaseCommand):
    help = '主要指数の日次価格を Yahoo Finance から取得する'

    def add_arguments(self, parser):
        parser.add_argument('--years', type=int, default=25)
        parser.add_argument('--days', type=int)
        parser.add_argument(
            '--tickers',
            nargs='+',
            choices=sorted(TICKER_TO_SYMBOL),
            default=None,
        )

    def handle(self, *args, **options):
        result = sync_all_daily_price_histories(
            tickers=options.get('tickers'),
            years=options['years'],
            days=options.get('days'),
        )
        self.stdout.write(
            f"日次価格 成功 {len(result['success'])} / "
            f"失敗 {len(result['failed'])}"
        )
        for item in result['success']:
            self.stdout.write(
                f"  {item['ticker']}: fetched={item['fetched']} "
                f"created={item['created']} updated={item['updated']}"
            )
        for item in result['failed']:
            self.stdout.write(
                self.style.WARNING(f"  {item['ticker']}: {item['error']}")
            )
