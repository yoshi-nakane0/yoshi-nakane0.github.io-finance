"""モデル検証レポートを作成する。"""

from django.core.management.base import BaseCommand

from macro.services import forecast_models, model_validation


class Command(BaseCommand):
    help = 'モデル別・対象別の walk-forward 検証結果を保存する'

    def add_arguments(self, parser):
        parser.add_argument('--model', default=forecast_models.RETURN_MODEL_VERSION)
        parser.add_argument('--target', default='GSPC')
        parser.add_argument('--horizon', default='3m')
        parser.add_argument('--all', action='store_true')

    def handle(self, *args, **options):
        if options['all']:
            reports = model_validation.run_all_model_validations()
        else:
            reports = [
                model_validation.validate_model(
                    model_version=options['model'],
                    target=options['target'],
                    horizon=options['horizon'],
                )
            ]
        self.stdout.write(f'検証レポート {len(reports)} 件を保存しました。')
        for report in reports[:20]:
            self.stdout.write(
                f'  {report.model_version} {report.target} {report.horizon}: '
                f'{report.sample_count}件'
            )
