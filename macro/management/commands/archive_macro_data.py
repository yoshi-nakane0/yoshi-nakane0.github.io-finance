"""マクロDBの履歴を gzip CSV に退避する。"""

from pathlib import Path

from django.core.management.base import BaseCommand

from macro.models import WorldModelRun
from macro.services.operations import finish_run, start_run
from macro.services.raw_archive import archive_macro_rows


class Command(BaseCommand):
    help = '表示用DBとは別にマクロ履歴アーカイブを作成する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reason',
            default='manual',
            help='アーカイブ名に含める理由。',
        )
        parser.add_argument(
            '--output-dir',
            default=None,
            help='出力先ディレクトリ。未指定なら static/macro/raw_archive。',
        )

    def handle(self, *args, **options):
        output_dir = Path(options['output_dir']) if options['output_dir'] else None
        run = start_run(
            cadence=WorldModelRun.Cadence.ARCHIVE,
            name='archive_macro_data',
            steps=[{'label': '履歴アーカイブ作成', 'command': 'archive_macro_data'}],
        )
        try:
            summary = archive_macro_rows(
                reason=options['reason'],
                output_dir=output_dir,
            )
        except Exception as exc:
            finish_run(
                run,
                status=WorldModelRun.Status.FAILED,
                summary={'message': 'アーカイブ作成に失敗しました。'},
                error=str(exc),
            )
            raise
        if not summary['created']:
            finish_run(
                run,
                status=WorldModelRun.Status.SUCCESS,
                summary={'message': 'アーカイブ対象の行はありません。'},
            )
            self.stdout.write('アーカイブ対象の行はありません。')
            return
        finish_run(
            run,
            status=WorldModelRun.Status.SUCCESS,
            summary={
                'message': 'アーカイブを作成しました。',
                **summary,
            },
        )
        self.stdout.write(
            f"アーカイブ作成: {summary['path']} "
            f"({summary['row_count']} 行 / {summary['size_bytes']} bytes)"
        )
