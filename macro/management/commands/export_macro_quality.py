from django.core.management.base import BaseCommand

from macro.services.dashboard_cache import write_static_macro_payload
from macro.services.data_quality import build_data_quality_report


class Command(BaseCommand):
    help = 'マクロ判断用のデータ品質レポートを static JSON として出力する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output',
            default='static/macro/data_quality_report.json',
            help='出力先JSONパス',
        )

    def handle(self, *args, **options):
        payload = build_data_quality_report()
        write_static_macro_payload(payload, options['output'])
        self.stdout.write(
            self.style.SUCCESS(f"exported data quality report: {options['output']}")
        )
