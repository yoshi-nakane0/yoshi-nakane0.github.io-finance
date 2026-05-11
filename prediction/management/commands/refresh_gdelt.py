"""GDELT から全アクティブトピックの集計を再取得し DB に保存する。

使い方:
    python manage.py refresh_gdelt          # 5分以内はスキップ
    python manage.py refresh_gdelt --force  # キャッシュ無視で強制実行
"""

from django.core.management.base import BaseCommand

from prediction.services.refresh import refresh_all_topics


class Command(BaseCommand):
    help = 'GDELT からセンチメント情報を再取得し DB に保存する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='直近5分以内に取得済みでも強制的に再取得する',
        )

    def handle(self, *args, **options):
        force = bool(options.get('force'))
        result = refresh_all_topics(force=force)

        if result.get('skipped'):
            self.stdout.write(
                f"スキップ: 直近実行から {result['remaining_sec']} 秒経過待ち"
            )
            return

        ok = len(result['success'])
        ng = len(result['failed'])
        self.stdout.write(f'成功 {ok} トピック / 失敗 {ng} トピック')
        for item in result['success']:
            self.stdout.write(
                f"  OK {item['topic']}: {item['articles_count']} 件 / "
                f"トーン {item['tone_avg']}"
            )
        for failure in result['failed']:
            self.stdout.write(
                f"  失敗 {failure['topic']}: {failure['error']}"
            )
