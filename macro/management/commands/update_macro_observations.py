"""macro観測値を更新する。"""

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'refresh_macro_data の読みやすい別名'

    def handle(self, *args, **options):
        call_command('refresh_macro_data')
