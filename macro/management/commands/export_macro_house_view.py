from django.core.management.base import BaseCommand

from macro.services.dashboard_cache import write_static_macro_payload
from macro.services.house_view import build_house_view_context


class Command(BaseCommand):
    help = 'House View を static JSON として出力する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output',
            default='static/macro/house_view.json',
            help='出力先JSONパス',
        )

    def handle(self, *args, **options):
        payload = build_house_view_context()
        write_static_macro_payload(payload, options['output'])
        self.stdout.write(self.style.SUCCESS(f"exported house view: {options['output']}"))
