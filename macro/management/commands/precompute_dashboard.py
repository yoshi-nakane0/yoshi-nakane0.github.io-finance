"""ダッシュボードの重い計算結果を事前計算し DashboardCache に保存する。

Vercel コールドスタートでビューが10秒タイムアウトを超えるのを避けるため、
GitHub Actions やビルド時に本コマンドを実行し、一時 DB に結果を保存する。

メインダッシュボードペイロードの計算に失敗した場合は、古いキャッシュ全体を
削除して stale data 配信を防ぐ（最低限フォールバック計算が走る方を選ぶ）。
詳細ページの精算は best-effort で、個別失敗時にも全体は中断しない。
"""

import logging

from django.core.management.base import BaseCommand, CommandError

from macro.models import DashboardCache
from macro.services.dashboard_cache import (
    precompute_all_indicator_details,
    precompute_dashboard_payload,
    precompute_top_similar_details,
    save_dashboard_payload,
    save_macro_update_status,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'macro ダッシュボードの重い計算結果を DashboardCache に保存する'

    def handle(self, *args, **options):
        # 1) メインのダッシュボードペイロード（最重要）
        try:
            payload = precompute_dashboard_payload()
            save_dashboard_payload(payload)
        except Exception as exc:
            logger.exception('dashboard payload precompute failed')
            # 古いキャッシュを残すと「最新だと誤認させる」リスクがあるため全消し。
            # 次回のビュー表示はその場で同期計算（フォールバック）にする。
            DashboardCache.objects.all().delete()
            save_macro_update_status({
                'source': 'precompute_dashboard',
                'status': 'failed',
                'message': '画面キャッシュ作成に失敗しました。',
                'success_count': 0,
                'failed_count': 1,
                'failed': [{
                    'phase': 'precompute_dashboard',
                    'error': str(exc),
                }],
            })
            self.stdout.write(
                'dashboard payload precompute failed; cleared all caches'
            )
            raise CommandError(f'dashboard payload precompute failed: {exc}')

        keys = ', '.join(payload.keys())
        self.stdout.write(f'precomputed dashboard payload saved (keys: {keys})')

        # 2) 指標詳細ページ（best-effort）
        try:
            detail_count = precompute_all_indicator_details()
            self.stdout.write(
                f'precomputed indicator detail payloads saved: {detail_count} 件'
            )
        except Exception:
            logger.exception('indicator detail precompute failed')
            self.stdout.write(
                'indicator detail precompute failed (継続)'
            )

        # 3) 類似期間詳細ページ（best-effort）
        try:
            similar_count = precompute_top_similar_details(payload=payload)
            self.stdout.write(
                f'precomputed similar detail payloads saved: {similar_count} 件'
            )
        except Exception:
            logger.exception('similar detail precompute failed')
            self.stdout.write(
                'similar detail precompute failed (継続)'
            )
