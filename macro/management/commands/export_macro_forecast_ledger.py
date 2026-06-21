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


def _forecast_id(snapshot):
    return (
        f'{snapshot.as_of_date}:'
        f'{snapshot.model_version}:'
        f'{snapshot.target}:'
        f'{snapshot.horizon}'
    )


def _fallback_prediction_interval(snapshot, confidence):
    value = snapshot.prediction_value
    return {
        'type': 'legacy_missing_interval',
        'lower': value,
        'upper': value,
        'confidence': confidence,
    }


def _confidence_from_snapshot(snapshot, metadata):
    if metadata.get('confidence') is not None:
        return float(metadata.get('confidence')), False
    interval = snapshot.prediction_interval or {}
    if interval.get('confidence') is not None:
        return float(interval.get('confidence')), False
    if 'wilson_95' in str(interval.get('type') or ''):
        return 0.95, False
    mae = interval.get('mae_pct')
    if mae is None:
        mae = interval.get('mae')
    if mae is not None:
        return round(max(0.0, min(1.0, 1 - float(mae) / 100)), 4), False
    return 0.0, True


def _source_dates(metadata):
    return (
        metadata.get('source_dates')
        or (metadata.get('feature_payload') or {}).get('source_freshness')
        or {}
    )


def build_forecast_ledger(limit=200):
    rows = []
    for snapshot in ForecastSnapshot.objects.order_by('-as_of_date', '-created_at')[:limit]:
        metadata = snapshot.metadata or {}
        confidence, missing_confidence = _confidence_from_snapshot(snapshot, metadata)
        features_hash = snapshot.features_hash or f'legacy-missing-{snapshot.id}'
        prediction_interval = (
            snapshot.prediction_interval
            or _fallback_prediction_interval(snapshot, confidence)
        )
        audit_warnings = []
        if missing_confidence:
            audit_warnings.append('confidence missing')
        if not snapshot.features_hash:
            audit_warnings.append('features_hash missing')
        if not snapshot.prediction_interval:
            audit_warnings.append('prediction_interval missing')
        rows.append({
            'forecast_id': _forecast_id(snapshot),
            'as_of': snapshot.as_of_date.isoformat(),
            'created_at': snapshot.created_at.isoformat(),
            'model_version': snapshot.model_version,
            'target': snapshot.target,
            'horizon': snapshot.horizon,
            'prediction': snapshot.prediction_value,
            'prediction_interval': prediction_interval,
            'direction': metadata.get('direction') or _direction(snapshot.prediction_value),
            'confidence': confidence,
            'features_hash': features_hash,
            'source_dates': _source_dates(metadata),
            'data_vintage': metadata.get('data_vintage') or 'unknown',
            'primary_regime': metadata.get('primary_regime'),
            'previous_regime': metadata.get('previous_regime'),
            'scenario_id': metadata.get('scenario_id'),
            'status': 'settled' if snapshot.realized_value is not None else 'open',
            'realized_value': snapshot.realized_value,
            'error': snapshot.error,
            'realized_at': snapshot.realized_at.isoformat() if snapshot.realized_at else None,
            'settled_at': snapshot.realized_at.isoformat() if snapshot.realized_at else None,
            'audit_warnings': audit_warnings,
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
