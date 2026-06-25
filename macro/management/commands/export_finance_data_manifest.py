from django.core.management.base import BaseCommand

from macro.services.finance_manifest import (
    build_finance_data_manifest,
    write_finance_data_manifest,
)


class Command(BaseCommand):
    help = 'Macro / basecalc / Explanation の保存済みJSON状態を manifest に出力する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output',
            default='static/finance_data_manifest.json',
            help='出力先JSONパス',
        )

    def handle(self, *args, **options):
        manifest = build_finance_data_manifest()
        write_finance_data_manifest(manifest, options['output'])
        self.stdout.write(
            self.style.SUCCESS(
                f"exported finance data manifest: {options['output']}"
            )
        )
