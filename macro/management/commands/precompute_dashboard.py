"""ダッシュボードの重い計算結果を事前計算し DashboardCache に保存する。

Vercel コールドスタートでビューが10秒タイムアウトを超えるのを避けるため、
GitHub Actions の日次ジョブで本コマンドを実行し、結果を db.sqlite3 に焼き込む。
"""

from django.core.management.base import BaseCommand

from macro.services.dashboard_cache import (
    precompute_dashboard_payload,
    save_dashboard_payload,
)


class Command(BaseCommand):
    help = 'macro ダッシュボードの重い計算結果を DashboardCache に保存する'

    def handle(self, *args, **options):
        payload = precompute_dashboard_payload()
        save_dashboard_payload(payload)
        keys = ', '.join(payload.keys())
        self.stdout.write(f'precomputed dashboard payload saved (keys: {keys})')
