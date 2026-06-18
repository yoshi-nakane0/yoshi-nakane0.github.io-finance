from django.core.management.base import BaseCommand

from macro.services.dashboard_cache import write_static_macro_payload
from macro.services.vintage_quality import build_vintage_quality_report


class Command(BaseCommand):
    help = '改定前データのカバー率を static JSON として出力する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output',
            default='static/macro/vintage_quality_report.json',
            help='出力先JSONパス',
        )

    def handle(self, *args, **options):
        payload = {'vintage_quality_report': build_vintage_quality_report()}
        write_static_macro_payload(payload, options['output'])
        self.stdout.write(
            self.style.SUCCESS(f"exported vintage quality report: {options['output']}")
        )
