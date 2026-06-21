from django.core.management.base import BaseCommand

from macro.models import ForecastSnapshot
from macro.services.dashboard_cache import write_static_macro_payload


def _direction(value):
    if value is None:
        return None
    if value > 0:
        return 'up'
    if value < 0:
        return 'down'
    return 'flat'


def build_forecast_ledger(limit=200):
    rows = []
    for snapshot in ForecastSnapshot.objects.order_by('-as_of_date', '-created_at')[:limit]:
        metadata = snapshot.metadata or {}
        rows.append({
            'as_of': snapshot.as_of_date.isoformat(),
            'model_version': snapshot.model_version,
            'target': snapshot.target,
            'horizon': snapshot.horizon,
            'prediction': snapshot.prediction_value,
            'prediction_interval': snapshot.prediction_interval,
            'direction': metadata.get('direction') or _direction(snapshot.prediction_value),
            'confidence': metadata.get('confidence'),
            'features_hash': snapshot.features_hash,
            'primary_regime': metadata.get('primary_regime'),
            'previous_regime': metadata.get('previous_regime'),
            'scenario_id': metadata.get('scenario_id'),
            'status': 'settled' if snapshot.realized_value is not None else 'open',
            'realized_value': snapshot.realized_value,
            'error': snapshot.error,
        })
    return {'forecast_ledger': rows}


class Command(BaseCommand):
    help = '予測台帳を static JSON として出力する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output',
            default='static/macro/forecast_ledger.json',
            help='出力先JSONパス',
        )
        parser.add_argument('--limit', type=int, default=200)

    def handle(self, *args, **options):
        payload = build_forecast_ledger(limit=options['limit'])
        write_static_macro_payload(payload, options['output'])
        self.stdout.write(
            self.style.SUCCESS(f"exported forecast ledger: {options['output']}")
        )
