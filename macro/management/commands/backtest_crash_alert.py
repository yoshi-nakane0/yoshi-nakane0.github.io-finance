"""指定した過去日付でクラッシュ警戒度がどう判定されるかを検証する。

直近 2〜3 年の代表的な下落局面（SVB、Yen carry unwind、関税ショック等）で
新しいスコアリング（カテゴリ重み付け + 価格アクション）が適切に上がるかを確認する。

使い方:
  python manage.py backtest_crash_alert
  python manage.py backtest_crash_alert --dates 2024-08-05,2025-04-07
  python manage.py backtest_crash_alert --details      # 全コンポーネント値を表示
"""

from datetime import date, datetime

from django.core.management.base import BaseCommand, CommandError

from macro.models import Observation
from macro.services.crash_alert import compute_crash_alert


# 直近 3 年（Yahoo 日次データの取得可能範囲）の代表的なストレスイベント。
# (target_date, 局面ラベル)
DEFAULT_TARGETS = [
    ('2023-03-13', 'SVB 銀行危機'),
    ('2023-10-27', '長期金利急上昇局面'),
    ('2024-04-19', '中東情勢悪化の下落'),
    ('2024-08-05', '円キャリー巻き戻し（VIX 65）'),
    ('2025-04-07', '関税ショック（S&P -10%）'),
    ('2026-05-08', '現在（参照: 落ち着いた相場）'),
]


def _make_lookup_for_date(target_date: date):
    """target_date 時点で利用可能だった各 series の最新値を返すコールバック。"""
    cache = {}

    def lookup(series_id: str):
        if series_id in cache:
            return cache[series_id]
        value = (
            Observation.objects
            .filter(
                indicator__fred_series_id=series_id,
                observation_date__lte=target_date,
            )
            .order_by('-observation_date')
            .values_list('value', flat=True)
            .first()
        )
        cache[series_id] = value
        return value

    return lookup


def _parse_dates(spec: str):
    out = []
    for s in spec.split(','):
        s = s.strip()
        if not s:
            continue
        try:
            d = datetime.strptime(s, '%Y-%m-%d').date()
        except ValueError as exc:
            raise CommandError(f"不正な日付指定: {s} ({exc})")
        out.append((s, ''))
    return out


class Command(BaseCommand):
    help = "過去日付でクラッシュ警戒度を再計算して検証する"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dates',
            default='',
            help='カンマ区切りの YYYY-MM-DD 日付。未指定なら代表局面を使う。',
        )
        parser.add_argument(
            '--details',
            action='store_true',
            help='各コンポーネントのスコアと値を表示する。',
        )

    def handle(self, *args, **options):
        if options['dates']:
            targets = _parse_dates(options['dates'])
        else:
            targets = DEFAULT_TARGETS

        self.stdout.write('=' * 70)
        self.stdout.write('クラッシュ警戒度 バックテスト')
        self.stdout.write('=' * 70)
        self.stdout.write(
            f"{'日付':<12} {'レベル':<8} {'点':>4}  {'市場':>5} {'信用':>5} {'マクロ':>5}  局面"
        )
        self.stdout.write('-' * 70)

        for date_str, label in targets:
            target = datetime.strptime(date_str, '%Y-%m-%d').date()
            lookup = _make_lookup_for_date(target)
            result = compute_crash_alert(value_lookup=lookup)

            cat_scores = {c['category']: c['avg_score'] for c in result.get('category_summary', [])}
            cat_n = {c['category']: c['count'] for c in result.get('category_summary', [])}

            market = cat_scores.get('market', '—')
            credit = cat_scores.get('credit', '—')
            macro = cat_scores.get('macro', '—')

            score = result['total_score']
            score_str = f"{score:>4}" if score is not None else '   —'
            level = result['level_label']

            self.stdout.write(
                f"{date_str:<12} {level:<8} {score_str}"
                f"  {str(market):>5} {str(credit):>5} {str(macro):>5}  {label}"
            )

            if options['details']:
                self.stdout.write(
                    f"   コンポーネント数: market={cat_n.get('market', 0)} "
                    f"credit={cat_n.get('credit', 0)} macro={cat_n.get('macro', 0)}"
                )
                from collections import defaultdict
                grouped = defaultdict(list)
                for c in result['components']:
                    grouped[c['category']].append(c)
                for cat in ('market', 'credit', 'macro'):
                    items = grouped.get(cat, [])
                    if not items:
                        continue
                    self.stdout.write(f"   [{cat}]")
                    for c in sorted(items, key=lambda x: -x['score']):
                        self.stdout.write(
                            f"     {c['label']:<22} val={c['value']:>9.2f}  score={c['score']:>3}"
                        )
                self.stdout.write('')

        self.stdout.write('=' * 70)
        self.stdout.write('凡例: 平常 0-20 / 注意 21-40 / 警戒 41-60 / 高警戒 61-80 / 危険 81-100')
