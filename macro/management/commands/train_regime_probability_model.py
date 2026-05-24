"""景気確率モデルをUSRECで検証してJSONに保存する。"""

from django.core.management.base import BaseCommand

from macro.services.regime_probability import (
    save_validation_payload,
    validate_regime_probability_model,
)


def _fmt(value):
    if value is None:
        return '—'
    return f'{value:.3f}'


class Command(BaseCommand):
    help = '景気後退確率モデルを履歴検証し static/macro に保存する'

    def add_arguments(self, parser):
        parser.add_argument('--years', type=int, default=20)
        parser.add_argument('--horizon-months', type=int, default=3)
        parser.add_argument(
            '--output',
            default='static/macro/regime_probability_model.json',
        )

    def handle(self, *args, **options):
        payload = validate_regime_probability_model(
            years=options['years'],
            horizon_months=options['horizon_months'],
        )
        path = save_validation_payload(payload, output=options['output'])
        metrics = payload['metrics']
        self.stdout.write(
            f"景気確率モデル検証: {payload['sample_count']}件 / "
            f"イベント {payload['event_count']}件 / "
            f"ROC-AUC {_fmt(metrics['roc_auc'])} / "
            f"Brier {_fmt(metrics['brier_score'])}"
        )
        self.stdout.write(f'出力: {path}')
