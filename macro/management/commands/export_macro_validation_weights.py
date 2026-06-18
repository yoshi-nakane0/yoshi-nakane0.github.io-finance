from django.core.management.base import BaseCommand

from macro.services.dashboard_cache import write_static_macro_payload
from macro.services.validation_weights import build_validation_weight_report


class Command(BaseCommand):
    help = '検証結果に基づくモデル重みを static JSON として出力する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output',
            default='static/macro/validation_weights.json',
            help='出力先JSONパス',
        )

    def handle(self, *args, **options):
        payload = {'validation_weight_report': build_validation_weight_report()}
        write_static_macro_payload(payload, options['output'])
        self.stdout.write(
            self.style.SUCCESS(f"exported validation weights: {options['output']}")
        )
