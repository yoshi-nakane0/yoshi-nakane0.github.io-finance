"""保持期限を過ぎた古いデータを削除する管理コマンド。

各テーブル別に保持期間を設定し、それより古い行を削除する。
Observation は指標の頻度（日次/週次/月次/四半期）ごとに保持年数を変える。

使い方:
    python manage.py purge_old_data            # 実削除
    python manage.py purge_old_data --dry-run  # 件数のみ表示
"""

from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from macro.models import (
    DashboardCache,
    Indicator,
    Observation,
    PriceObservation,
    RegimeSnapshot,
)

# 観測値の頻度別保持年数。
# 日次は類似度検索（過去15年遡る）と整合させて15年。
# 月次・四半期は容量負荷が小さいので長めに保持。
OBSERVATION_RETENTION_BY_FREQUENCY = {
    Indicator.Frequency.DAILY: 15,
    Indicator.Frequency.WEEKLY: 20,
    Indicator.Frequency.MONTHLY: 30,
    Indicator.Frequency.QUARTERLY: 30,
}
PRICE_RETENTION_YEARS = 25
REGIME_RETENTION_YEARS = 5
DASHBOARD_CACHE_RETENTION_DAYS = 7


def _years_ago(years: int) -> date:
    today = date.today()
    try:
        return today.replace(year=today.year - years)
    except ValueError:
        return today.replace(year=today.year - years, day=28)


class Command(BaseCommand):
    help = '保持期限を過ぎた古いマクロデータを削除する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='削除を実行せず対象件数のみ表示する',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        prefix = '[dry-run] ' if dry_run else ''

        total = 0

        # Observation: 頻度別の保持期間で削除
        for frequency, years in OBSERVATION_RETENTION_BY_FREQUENCY.items():
            cutoff = _years_ago(years)
            qs = Observation.objects.filter(
                indicator__frequency=frequency,
                observation_date__lt=cutoff,
            )
            count = qs.count()
            total += count
            label = f'Observation({frequency})'
            condition = f'{years}年より古い'
            if dry_run:
                self.stdout.write(f'[dry-run] {label}: {count} 件 ({condition})')
            else:
                qs.delete()
                self.stdout.write(f'{label}: {count} 件削除 ({condition})')

        # PriceObservation
        price_cutoff = _years_ago(PRICE_RETENTION_YEARS)
        qs = PriceObservation.objects.filter(observation_month__lt=price_cutoff)
        count = qs.count()
        total += count
        condition = f'{PRICE_RETENTION_YEARS}年より古い'
        if dry_run:
            self.stdout.write(f'[dry-run] PriceObservation: {count} 件 ({condition})')
        else:
            qs.delete()
            self.stdout.write(f'PriceObservation: {count} 件削除 ({condition})')

        # RegimeSnapshot
        regime_cutoff = _years_ago(REGIME_RETENTION_YEARS)
        qs = RegimeSnapshot.objects.filter(snapshot_date__lt=regime_cutoff)
        count = qs.count()
        total += count
        condition = f'{REGIME_RETENTION_YEARS}年より古い'
        if dry_run:
            self.stdout.write(f'[dry-run] RegimeSnapshot: {count} 件 ({condition})')
        else:
            qs.delete()
            self.stdout.write(f'RegimeSnapshot: {count} 件削除 ({condition})')

        # DashboardCache
        cache_cutoff = timezone.now() - timedelta(
            days=DASHBOARD_CACHE_RETENTION_DAYS,
        )
        qs = DashboardCache.objects.filter(computed_at__lt=cache_cutoff)
        count = qs.count()
        total += count
        condition = f'{DASHBOARD_CACHE_RETENTION_DAYS}日より古い'
        if dry_run:
            self.stdout.write(f'[dry-run] DashboardCache: {count} 件 ({condition})')
        else:
            qs.delete()
            self.stdout.write(f'DashboardCache: {count} 件削除 ({condition})')

        # SQLite ファイルを実際に縮める
        if not dry_run and total > 0:
            from django.db import connection
            with connection.cursor() as cur:
                cur.execute('VACUUM')
            self.stdout.write('VACUUM 実行（DB ファイルサイズを再圧縮）')

        self.stdout.write(f'{prefix}合計: {total} 件')
