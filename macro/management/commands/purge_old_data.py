"""保持期限を過ぎた古いデータを削除する管理コマンド。

各テーブル別に保持期間を設定し、それより古い行を削除する。
Observation は指標の頻度（日次/週次/月次/四半期）ごとに保持年数を変える。

使い方:
    python manage.py purge_old_data            # 実削除
    python manage.py purge_old_data --dry-run  # 件数のみ表示
"""

from calendar import monthrange
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from macro.models import (
    DashboardCache,
    FeatureSnapshot,
    ForecastSnapshot,
    Indicator,
    ModelValidationReport,
    Observation,
    PriceObservation,
    RegimeSnapshot,
    VintageObservation,
    WorldStateSnapshot,
)
from macro.services.raw_archive import archive_macro_rows

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
REGIME_RETENTION_YEARS = 15
WORLD_STATE_RETENTION_YEARS = 25
FEATURE_SNAPSHOT_RETENTION_YEARS = 25
FORECAST_SNAPSHOT_RETENTION_YEARS = 25
MODEL_VALIDATION_REPORT_RETENTION_YEARS = 10
DASHBOARD_CACHE_RETENTION_DAYS = 7
LOW_IMPORTANCE_VINTAGE_RETENTION_DAYS = 365


def _years_ago(years: int) -> date:
    today = date.today()
    try:
        return today.replace(year=today.year - years)
    except ValueError:
        return today.replace(year=today.year - years, day=28)


def _is_month_end(value: date) -> bool:
    return value.day == monthrange(value.year, value.month)[1]


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
        archive_observation_querysets = []
        archive_price_queryset = None
        archive_regime_queryset = None
        archive_world_state_queryset = None
        archive_feature_queryset = None
        archive_forecast_queryset = None
        archive_validation_queryset = None
        archive_vintage_queryset = None
        delete_targets = []

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
            delete_targets.append((label, condition, qs))
            archive_observation_querysets.append(qs)
            if dry_run:
                self.stdout.write(f'[dry-run] {label}: {count} 件 ({condition})')

        # PriceObservation
        price_cutoff = _years_ago(PRICE_RETENTION_YEARS)
        qs = PriceObservation.objects.filter(observation_month__lt=price_cutoff)
        count = qs.count()
        total += count
        archive_price_queryset = qs
        condition = f'{PRICE_RETENTION_YEARS}年より古い'
        delete_targets.append(('PriceObservation', condition, qs))
        if dry_run:
            self.stdout.write(f'[dry-run] PriceObservation: {count} 件 ({condition})')

        # RegimeSnapshot
        regime_cutoff = _years_ago(REGIME_RETENTION_YEARS)
        qs = RegimeSnapshot.objects.filter(snapshot_date__lt=regime_cutoff)
        count = qs.count()
        total += count
        archive_regime_queryset = qs
        condition = f'{REGIME_RETENTION_YEARS}年より古い'
        delete_targets.append(('RegimeSnapshot', condition, qs))
        if dry_run:
            self.stdout.write(f'[dry-run] RegimeSnapshot: {count} 件 ({condition})')

        # WorldStateSnapshot
        world_state_cutoff = _years_ago(WORLD_STATE_RETENTION_YEARS)
        qs = WorldStateSnapshot.objects.filter(as_of_date__lt=world_state_cutoff)
        count = qs.count()
        total += count
        archive_world_state_queryset = qs
        condition = f'{WORLD_STATE_RETENTION_YEARS}年より古い'
        delete_targets.append(('WorldStateSnapshot', condition, qs))
        if dry_run:
            self.stdout.write(f'[dry-run] WorldStateSnapshot: {count} 件 ({condition})')

        # FeatureSnapshot
        feature_cutoff = _years_ago(FEATURE_SNAPSHOT_RETENTION_YEARS)
        qs = FeatureSnapshot.objects.filter(as_of_date__lt=feature_cutoff)
        count = qs.count()
        total += count
        archive_feature_queryset = qs
        condition = f'{FEATURE_SNAPSHOT_RETENTION_YEARS}年より古い'
        delete_targets.append(('FeatureSnapshot', condition, qs))
        if dry_run:
            self.stdout.write(f'[dry-run] FeatureSnapshot: {count} 件 ({condition})')

        # ForecastSnapshot
        forecast_cutoff = _years_ago(FORECAST_SNAPSHOT_RETENTION_YEARS)
        qs = ForecastSnapshot.objects.filter(as_of_date__lt=forecast_cutoff)
        count = qs.count()
        total += count
        archive_forecast_queryset = qs
        condition = f'{FORECAST_SNAPSHOT_RETENTION_YEARS}年より古い'
        delete_targets.append(('ForecastSnapshot', condition, qs))
        if dry_run:
            self.stdout.write(f'[dry-run] ForecastSnapshot: {count} 件 ({condition})')

        # ModelValidationReport
        validation_cutoff = _years_ago(MODEL_VALIDATION_REPORT_RETENTION_YEARS)
        qs = ModelValidationReport.objects.filter(evaluated_at__date__lt=validation_cutoff)
        count = qs.count()
        total += count
        archive_validation_queryset = qs
        condition = f'{MODEL_VALIDATION_REPORT_RETENTION_YEARS}年より古い'
        delete_targets.append(('ModelValidationReport', condition, qs))
        if dry_run:
            self.stdout.write(f'[dry-run] ModelValidationReport: {count} 件 ({condition})')

        # VintageObservation: 重要指標は全履歴を保持。参考指標だけ古い非月末ビンテージを圧縮退避。
        vintage_cutoff = timezone.now() - timedelta(
            days=LOW_IMPORTANCE_VINTAGE_RETENTION_DAYS,
        )
        vintage_candidates = (
            VintageObservation.objects
            .filter(
                indicator__importance=Indicator.Importance.C,
                collected_at__lt=vintage_cutoff,
            )
            .order_by('indicator_id', 'realtime_start', 'observation_date')
        )
        vintage_delete_ids = [
            row.id
            for row in vintage_candidates.only('id', 'realtime_start').iterator()
            if not _is_month_end(row.realtime_start)
        ]
        qs = VintageObservation.objects.filter(id__in=vintage_delete_ids)
        count = qs.count()
        total += count
        archive_vintage_queryset = qs
        condition = (
            f'{LOW_IMPORTANCE_VINTAGE_RETENTION_DAYS}日より古い'
            '参考指標の非月末ビンテージ'
        )
        delete_targets.append(('VintageObservation', condition, qs))
        if dry_run:
            self.stdout.write(f'[dry-run] VintageObservation: {count} 件 ({condition})')

        # DashboardCache
        cache_cutoff = timezone.now() - timedelta(
            days=DASHBOARD_CACHE_RETENTION_DAYS,
        )
        cache_qs = DashboardCache.objects.filter(computed_at__lt=cache_cutoff)
        cache_count = cache_qs.count()
        total += cache_count
        condition = f'{DASHBOARD_CACHE_RETENTION_DAYS}日より古い'
        if dry_run:
            self.stdout.write(f'[dry-run] DashboardCache: {cache_count} 件 ({condition})')
        else:
            if total > cache_count:
                summary = archive_macro_rows(
                    observation_querysets=archive_observation_querysets,
                    price_queryset=archive_price_queryset,
                    regime_queryset=archive_regime_queryset,
                    world_state_queryset=archive_world_state_queryset,
                    feature_queryset=archive_feature_queryset,
                    forecast_queryset=archive_forecast_queryset,
                    validation_queryset=archive_validation_queryset,
                    vintage_queryset=archive_vintage_queryset,
                    reason='purge_old_data',
                )
                if summary['created']:
                    self.stdout.write(
                        f"RawArchive: {summary['row_count']} 件退避 "
                        f"({summary['path']})"
                    )
            for label, condition, qs in delete_targets:
                count = qs.count()
                qs.delete()
                self.stdout.write(f'{label}: {count} 件削除 ({condition})')
            cache_qs.delete()
            self.stdout.write(f'DashboardCache: {cache_count} 件削除 ({condition})')

        # SQLite ファイルを実際に縮める
        if not dry_run and total > 0:
            from django.db import connection
            try:
                with connection.cursor() as cur:
                    cur.execute('VACUUM')
            except Exception as exc:
                self.stdout.write(
                    self.style.WARNING(f'VACUUM をスキップしました: {exc}')
                )
            else:
                self.stdout.write('VACUUM 実行（DB ファイルサイズを再圧縮）')

        self.stdout.write(f'{prefix}合計: {total} 件')
