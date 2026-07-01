from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


REQUIRED_DISPLAY_FILES = (
    'static/macro/latest_dashboard.json',
    'basecalc/data/latest_snapshot.json',
    'basecalc/data/basecalc_status.json',
    'basecalc/data/basecalc_history.json',
    'explanation/data/latest_snapshot.json',
    'explanation/data/snapshot_history.json',
    'explanation/data/trade_outcomes.json',
    'static/finance_data_manifest.json',
)


class Command(BaseCommand):
    help = 'finance表示用データの共通後処理を実行する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--evaluate-outcomes',
            action='store_true',
            help='Explanationの1d/3d/5d検証結果JSONも更新する',
        )

    def handle(self, *args, **options):
        call_command('precompute_explanation')
        if options['evaluate_outcomes']:
            call_command('evaluate_explanation_outcomes')
        call_command('export_finance_data_manifest')
        self._check_required_files()
        self.stdout.write(self.style.SUCCESS('finance display data finalized'))

    def _check_required_files(self):
        missing = [
            relative_path
            for relative_path in REQUIRED_DISPLAY_FILES
            if not (Path(settings.BASE_DIR) / relative_path).is_file()
        ]
        if missing:
            raise CommandError(
                '表示用データが不足しています: ' + ', '.join(missing)
            )
