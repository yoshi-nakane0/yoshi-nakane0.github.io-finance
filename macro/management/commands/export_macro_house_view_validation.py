from django.core.management.base import BaseCommand

from macro.services.dashboard_cache import (
    load_static_macro_payload,
    write_static_macro_payload,
)
from macro.services.house_view_validation import build_house_view_validation_report


class Command(BaseCommand):
    help = 'House View の過去的中率検証を static JSON として出力する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output',
            default='static/macro/house_view_validation.json',
            help='出力先JSONパス',
        )
        parser.add_argument(
            '--source-payload',
            default=None,
            help='latest_dashboard.json から house_view_validation を再利用する場合の入力JSONパス',
        )

    def handle(self, *args, **options):
        house_view_validation = None
        if options.get('source_payload'):
            source_payload = load_static_macro_payload(options['source_payload'])
            if source_payload:
                house_view_validation = source_payload.get('house_view_validation')
        if not isinstance(house_view_validation, dict):
            house_view_validation = build_house_view_validation_report()

        payload = {'house_view_validation': house_view_validation}
        write_static_macro_payload(payload, options['output'])
        self.stdout.write(
            self.style.SUCCESS(f"exported house view validation: {options['output']}")
        )
