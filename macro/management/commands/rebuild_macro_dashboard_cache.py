"""macro表示キャッシュを再構築する。"""

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'precompute_dashboard の読みやすい別名'

    def handle(self, *args, **options):
        call_command('precompute_dashboard')
