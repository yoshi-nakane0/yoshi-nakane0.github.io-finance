from django.core.management.base import BaseCommand

from macro.services.dashboard_cache import write_static_macro_payload
from macro.services.goldman_outlook import build_goldman_outlook_comparison


class Command(BaseCommand):
    help = 'Goldman Sachs 公開見通しとの比較を static JSON として出力する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output',
            default='static/macro/goldman_outlook_comparison.json',
            help='出力先JSONパス',
        )

    def handle(self, *args, **options):
        payload = build_goldman_outlook_comparison()
        write_static_macro_payload(payload, options['output'])
        self.stdout.write(
            self.style.SUCCESS(f"exported Goldman outlook comparison: {options['output']}")
        )
