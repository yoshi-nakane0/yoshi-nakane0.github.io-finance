"""ローカルデータ更新の入口をまとめる管理コマンド。"""

import subprocess
import sys
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


BASE_DIR = Path(settings.BASE_DIR)


class Command(BaseCommand):
    help = 'ローカルのデータ更新処理を1つの入口から実行する'

    def add_arguments(self, parser):
        parser.add_argument('--list', action='store_true', help='実行できる更新処理を表示する')
        parser.add_argument('--all', action='store_true', help='主要な更新処理をまとめて実行する')
        parser.add_argument('--nikkei-per', action='store_true', help='日経PER・配当利回りJSONを更新する')
        parser.add_argument('--events', action='store_true', help='経済イベントCSVを更新する')
        parser.add_argument('--earnings', action='store_true', help='決算CSVを更新する')
        parser.add_argument('--macro', action='store_true', help='マクロ指標と価格データをDBへ更新する')
        parser.add_argument('--gdelt', action='store_true', help='ニュース感情データをDBへ更新する')
        parser.add_argument(
            '--events-months',
            default='',
            help='経済イベントの対象月。例: 5 または 5,6。未指定なら今月。',
        )
        parser.add_argument(
            '--earnings-count',
            type=int,
            default=None,
            help='決算CSV更新でスクレイピングする銘柄数。--earnings 実行時は必須。',
        )
        parser.add_argument(
            '--macro-full-history',
            action='store_true',
            help='マクロ更新で既存データがあっても指定年数ぶん再取得する',
        )
        parser.add_argument(
            '--macro-history-years',
            type=int,
            default=25,
            help='マクロ更新で遡る年数。初期値: 25',
        )
        parser.add_argument(
            '--gdelt-force',
            action='store_true',
            help='GDELT更新で直近実行済みでも強制再取得する',
        )

    def handle(self, *args, **options):
        if options['list'] or not self._has_task(options):
            self._write_task_list()
            return

        self._validate_options(options)
        tasks = self._build_tasks(options)
        for label, runner in tasks:
            self.stdout.write(f'開始: {label}')
            runner()
            self.stdout.write(self.style.SUCCESS(f'完了: {label}'))

        self.stdout.write(self.style.SUCCESS('ローカルデータ更新が完了しました'))

    def _has_task(self, options):
        return any(
            options[name]
            for name in ('all', 'nikkei_per', 'events', 'earnings', 'macro', 'gdelt')
        )

    def _validate_options(self, options):
        if (options['all'] or options['earnings']) and (
            options['earnings_count'] is None or options['earnings_count'] <= 0
        ):
            raise CommandError('--earnings 実行時は --earnings-count に1以上の数を指定してください')

    def _build_tasks(self, options):
        run_all = options['all']
        tasks = []
        if run_all or options['nikkei_per']:
            tasks.append(('日経PER・配当利回り', self._run_nikkei_per))
        if run_all or options['events']:
            tasks.append((
                '経済イベントCSV',
                lambda: self._run_events(options['events_months']),
            ))
        if run_all or options['earnings']:
            tasks.append((
                '決算CSV',
                lambda: self._run_earnings(options['earnings_count']),
            ))
        if run_all or options['macro']:
            tasks.append((
                'マクロ指標・価格データ',
                lambda: self._run_macro(
                    history_years=options['macro_history_years'],
                    full_history=options['macro_full_history'],
                ),
            ))
        if run_all or options['gdelt']:
            tasks.append((
                'ニュース感情データ',
                lambda: self._run_gdelt(force=options['gdelt_force']),
            ))
        return tasks

    def _write_task_list(self):
        self.stdout.write('実行できる更新処理:')
        self.stdout.write('  python manage.py update_local_data --nikkei-per')
        self.stdout.write('  python manage.py update_local_data --events --events-months 5,6')
        self.stdout.write('  python manage.py update_local_data --earnings --earnings-count 10')
        self.stdout.write('  python manage.py update_local_data --macro')
        self.stdout.write('  python manage.py update_local_data --gdelt')
        self.stdout.write('  python manage.py update_local_data --all --earnings-count 10')

    def _run_script(self, script_path, input_text=None):
        command = [sys.executable, str(script_path)]
        result = subprocess.run(
            command,
            cwd=BASE_DIR,
            input=input_text,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise CommandError(f'{script_path.name} が失敗しました')

    def _run_nikkei_per(self):
        self._run_script(BASE_DIR / 'scripts' / 'update_nikkei_per_data.py')

    def _run_events(self, months):
        selected_months = (months or str(date.today().month)).strip()
        self._run_script(
            BASE_DIR / 'scripts' / 'schedule.py',
            input_text=f'{selected_months}\n',
        )

    def _run_earnings(self, count):
        self._run_script(
            BASE_DIR / 'scripts' / 'earning.py',
            input_text=f'{count}\n',
        )

    def _run_macro(self, *, history_years, full_history):
        kwargs = {'history_years': history_years}
        if full_history:
            kwargs['full_history'] = True
        call_command('refresh_macro_data', **kwargs)
        call_command('precompute_dashboard')

    def _run_gdelt(self, *, force):
        call_command('refresh_gdelt', force=force)
