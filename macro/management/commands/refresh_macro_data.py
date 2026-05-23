"""全マクロ指標を取得元から再取得し DB を更新する管理コマンド。

GitHub Actions の定期ジョブから呼ばれる前提。手動実行も可。

使い方:
    python manage.py refresh_macro_data
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from macro.services.dashboard_cache import save_macro_update_status
from macro.services.data_sync import sync_all_indicators
from macro.services.fred_client import get_api_key
from macro.services.regime import compute_current_regime
from macro.services.yfinance_client import sync_all_price_histories


def _save_status(
    *,
    result=None,
    status=None,
    message='',
    extra_failed=None,
):
    result = result or {}
    failed = list(result.get('failed') or [])
    failed.extend(extra_failed or [])
    success = list(result.get('success') or [])
    if status is None:
        if failed and not success:
            status = 'failed'
        elif failed:
            status = 'partial'
        else:
            status = 'success'
    save_macro_update_status({
        'source': 'refresh_macro_data',
        'status': status,
        'message': message,
        'success_count': len(success),
        'failed_count': len(failed),
        'failed': failed,
        'started_at': result.get('started_at'),
        'finished_at': result.get('finished_at') or timezone.now().isoformat(),
    })


class Command(BaseCommand):
    help = '全 macro 指標を取得元から再取得し DB に保存する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--history-years',
            type=int,
            default=25,
            help='初回取得または --full-history 時に遡る年数。',
        )
        parser.add_argument(
            '--full-history',
            action='store_true',
            help='既存データがあっても指定年数ぶんを再取得して埋め直す。',
        )

    def handle(self, *args, **options):
        if not get_api_key():
            _save_status(
                status='failed',
                message='FRED_API_KEY が未設定のため更新できません。',
                extra_failed=[{
                    'phase': 'FRED_API_KEY',
                    'error': 'FRED_API_KEY が未設定です',
                }],
            )
            raise CommandError('FRED_API_KEY が未設定です')

        try:
            result = sync_all_indicators(
                history_years=options['history_years'],
                force_full_history=options['full_history'],
            )
        except Exception as exc:
            _save_status(
                status='failed',
                message='指標取得処理が途中で失敗しました。',
                extra_failed=[{
                    'phase': 'sync_all_indicators',
                    'error': str(exc),
                }],
            )
            raise
        ok = len(result['success'])
        ng = len(result['failed'])
        self.stdout.write(f'成功 {ok} 件 / 失敗 {ng} 件')
        for failure in result['failed']:
            self.stdout.write(
                f"  失敗: {failure['series_id']}: {failure['error']}"
            )
        if ok == 0:
            _save_status(
                result=result,
                status='failed',
                message='1 件も取得できなかったため失敗しました。',
            )
            raise CommandError('1 件も取得できなかったため失敗扱い')

        extra_failed = []
        try:
            compute_current_regime()
        except Exception as exc:
            extra_failed.append({'phase': 'regime', 'error': str(exc)})
            _save_status(
                result=result,
                status='partial' if ok else 'failed',
                message='指標は取得しましたが、景気判定に失敗しました。',
                extra_failed=extra_failed,
            )
            raise CommandError(f'景気判定に失敗しました: {exc}')

        try:
            price_result = sync_all_price_histories(years=options['history_years'])
        except Exception as exc:
            extra_failed.append({'phase': 'price_sync', 'error': str(exc)})
            _save_status(
                result=result,
                status='partial',
                message='指標は取得しましたが、価格データ更新に失敗しました。',
                extra_failed=extra_failed,
            )
            raise CommandError(f'価格データ更新に失敗しました: {exc}')
        price_ok = len(price_result['success'])
        price_ng = len(price_result['failed'])
        self.stdout.write(f'価格データ 成功 {price_ok} 件 / 失敗 {price_ng} 件')
        for failure in price_result['failed']:
            extra_failed.append({
                'ticker': failure['ticker'],
                'error': failure['error'],
            })
            self.stdout.write(
                f"  価格失敗: {failure['ticker']}: {failure['error']}"
            )
        _save_status(
            result=result,
            message='日次更新を実行しました。',
            extra_failed=extra_failed,
        )
