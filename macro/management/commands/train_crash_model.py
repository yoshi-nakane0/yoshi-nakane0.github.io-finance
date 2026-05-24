"""互換用コマンド。train_return_model を実行する。"""

from django.core.management import call_command
from django.core.management.base import BaseCommand

from macro.services import forecast_models


class Command(BaseCommand):
    help = '互換用: train_return_model を実行する。将来削除予定。'

    def add_arguments(self, parser):
        parser.add_argument(
            '--target',
            choices=forecast_models.RETURN_TARGETS,
            default='GSPC',
        )
        parser.add_argument(
            '--horizon',
            choices=forecast_models.HORIZONS,
        )
        parser.add_argument('--all', action='store_true')
        parser.add_argument('--output', default='static/macro/return_forecast_model.json')

    def handle(self, *args, **options):
        self.stdout.write(
            'train_crash_model is deprecated. Use train_return_model instead.'
        )
        call_command(
            'train_return_model',
            target=options['target'],
            horizon=options.get('horizon'),
            all=options['all'],
            output=options['output'],
        )
