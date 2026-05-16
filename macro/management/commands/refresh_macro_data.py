"""全マクロ指標を取得元から再取得し DB を更新する管理コマンド。

GitHub Actions の定期ジョブから呼ばれる前提。手動実行も可。

使い方:
    python manage.py refresh_macro_data
"""

from django.core.management.base import BaseCommand, CommandError

from macro.services.data_sync import sync_all_indicators
from macro.services.fred_client import get_api_key
from macro.services.regime import compute_current_regime
from macro.services.yfinance_client import sync_all_price_histories


class Command(BaseCommand):
    help = '全 macro 指標を取得元から再取得し DB に保存する'

    def handle(self, *args, **options):
        if not get_api_key():
            raise CommandError('FRED_API_KEY が未設定です')

        result = sync_all_indicators()
        ok = len(result['success'])
        ng = len(result['failed'])
        self.stdout.write(f'成功 {ok} 件 / 失敗 {ng} 件')
        for failure in result['failed']:
            self.stdout.write(
                f"  失敗: {failure['series_id']}: {failure['error']}"
            )
        if ok == 0:
            raise CommandError('1 件も取得できなかったため失敗扱い')

        compute_current_regime()

        price_result = sync_all_price_histories()
        price_ok = len(price_result['success'])
        price_ng = len(price_result['failed'])
        self.stdout.write(f'価格データ 成功 {price_ok} 件 / 失敗 {price_ng} 件')
        for failure in price_result['failed']:
            self.stdout.write(
                f"  価格失敗: {failure['ticker']}: {failure['error']}"
            )
