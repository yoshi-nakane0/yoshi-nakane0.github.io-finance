from django.core.management.base import BaseCommand

from macro.services.dashboard_cache import write_static_macro_payload
from macro.services.house_view_validation import build_house_view_validation_report


class Command(BaseCommand):
    help = 'House View の過去的中率検証を static JSON として出力する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output',
            default='static/macro/house_view_validation.json',
            help='出力先JSONパス',
        )

    def handle(self, *args, **options):
        payload = {'house_view_validation': build_house_view_validation_report()}
        write_static_macro_payload(payload, options['output'])
        self.stdout.write(
            self.style.SUCCESS(f"exported house view validation: {options['output']}")
        )
