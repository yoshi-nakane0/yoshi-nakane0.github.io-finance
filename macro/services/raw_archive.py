"""表示用DBから削る前のマクロ履歴を gzip CSV に退避する。"""

import csv
import gzip
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from django.conf import settings
from django.db.models import QuerySet
from django.utils import timezone


ARCHIVE_RELATIVE_DIR = Path('static') / 'macro' / 'raw_archive'
ARCHIVE_DIR_ENV = 'MACRO_RAW_ARCHIVE_DIR'
ARCHIVE_BACKEND_ENV = 'MACRO_RAW_ARCHIVE_STORAGE_BACKEND'
ARCHIVE_PATH_PREFIX_ENV = 'MACRO_RAW_ARCHIVE_PATH_PREFIX'

FIELDNAMES = [
    'archived_at',
    'reason',
    'table',
    'series_id',
    'source',
    'frequency',
    'date',
    'value',
    'prev_value',
    'yoy_change',
    'deviation_from_long_term',
    'expanding_z_score',
    'rolling_10y_z_score',
    'rolling_5y_z_score',
    'ticker',
    'close_price',
    'regime_label',
    'inflation_flag',
    'rule_strength',
    'data_quality',
    'payload_json',
]


def archive_dir() -> Path:
    configured = os.getenv(ARCHIVE_DIR_ENV)
    if configured:
        return Path(configured).expanduser()
    return Path(settings.BASE_DIR) / ARCHIVE_RELATIVE_DIR


def _storage_backend(path: Path) -> str:
    configured_backend = os.getenv(ARCHIVE_BACKEND_ENV)
    if configured_backend:
        return configured_backend
    configured = os.getenv(ARCHIVE_DIR_ENV)
    if configured:
        return 'configured_local'
    try:
        path.relative_to(Path(settings.BASE_DIR))
    except ValueError:
        return 'external_local'
    return 'local'


def _manifest_path(path: Path) -> str:
    prefix = os.getenv(ARCHIVE_PATH_PREFIX_ENV)
    if prefix:
        return f'{prefix.rstrip("/")}/{path.name}'
    return str(path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_path(created_at: datetime, reason: str, output_dir: Optional[Path]) -> Path:
    safe_reason = ''.join(ch if ch.isalnum() or ch == '_' else '_' for ch in reason)
    target_dir = Path(output_dir) if output_dir else archive_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = timezone.localtime(created_at).strftime('%Y%m%d%H%M%S')
    return target_dir / f'macro_raw_{safe_reason}_{stamp}.csv.gz'


def _json(value) -> str:
    if value in (None, '', [], {}):
        return ''
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _blank_row(archived_at: str, reason: str, table: str) -> dict:
    return {name: '' for name in FIELDNAMES} | {
        'archived_at': archived_at,
        'reason': reason,
        'table': table,
    }


def _write_observations(writer, querysets: Iterable[QuerySet], archived_at: str, reason: str) -> int:
    count = 0
    for qs in querysets:
        rows = (
            qs.select_related('indicator')
            .order_by('indicator__fred_series_id', 'observation_date')
            .iterator(chunk_size=1000)
        )
        for obs in rows:
            row = _blank_row(archived_at, reason, 'observation')
            row.update({
                'series_id': obs.indicator.fred_series_id,
                'source': obs.indicator.source,
                'frequency': obs.indicator.frequency,
                'date': obs.observation_date.isoformat(),
                'value': obs.value,
                'prev_value': obs.prev_value,
                'yoy_change': obs.yoy_change,
                'deviation_from_long_term': obs.deviation_from_long_term,
                'expanding_z_score': obs.expanding_z_score,
                'rolling_10y_z_score': obs.rolling_10y_z_score,
                'rolling_5y_z_score': obs.rolling_5y_z_score,
            })
            writer.writerow(row)
            count += 1
    return count


def _write_prices(writer, qs: Optional[QuerySet], archived_at: str, reason: str) -> int:
    if qs is None:
        return 0
    count = 0
    for price in qs.order_by('ticker', 'observation_month').iterator(chunk_size=1000):
        row = _blank_row(archived_at, reason, 'price_observation')
        row.update({
            'ticker': price.ticker,
            'date': price.observation_month.isoformat(),
            'close_price': price.close_price,
        })
        writer.writerow(row)
        count += 1
    return count


def _write_regimes(writer, qs: Optional[QuerySet], archived_at: str, reason: str) -> int:
    if qs is None:
        return 0
    count = 0
    for snapshot in qs.order_by('snapshot_date').iterator(chunk_size=500):
        row = _blank_row(archived_at, reason, 'regime_snapshot')
        row.update({
            'date': snapshot.snapshot_date.isoformat(),
            'regime_label': snapshot.regime_label,
            'inflation_flag': snapshot.inflation_flag,
            'rule_strength': snapshot.rule_strength,
            'data_quality': snapshot.data_quality,
            'payload_json': _json({
                'evidence': snapshot.evidence,
                'warnings': snapshot.warnings,
                'indicator_vector': snapshot.indicator_vector,
                'regime_probabilities': snapshot.regime_probabilities,
                'risk_probabilities': snapshot.risk_probabilities,
                'model_version': snapshot.model_version,
            }),
        })
        writer.writerow(row)
        count += 1
    return count


def _write_payload_rows(
    writer,
    qs: Optional[QuerySet],
    archived_at: str,
    reason: str,
    table: str,
    date_attr: str,
    payload_builder,
) -> int:
    if qs is None:
        return 0
    count = 0
    for obj in qs.iterator(chunk_size=500):
        row = _blank_row(archived_at, reason, table)
        value_date = getattr(obj, date_attr, None)
        row.update({
            'date': value_date.isoformat() if value_date else '',
            'series_id': getattr(obj, 'target', ''),
            'ticker': getattr(obj, 'ticker', ''),
            'data_quality': getattr(obj, 'data_quality', ''),
            'payload_json': _json(payload_builder(obj)),
        })
        writer.writerow(row)
        count += 1
    return count


def archive_macro_rows(
    *,
    observation_querysets: Optional[Iterable[QuerySet]] = None,
    price_queryset: Optional[QuerySet] = None,
    regime_queryset: Optional[QuerySet] = None,
    world_state_queryset: Optional[QuerySet] = None,
    feature_queryset: Optional[QuerySet] = None,
    forecast_queryset: Optional[QuerySet] = None,
    validation_queryset: Optional[QuerySet] = None,
    reason: str = 'manual',
    output_dir: Optional[Path] = None,
) -> dict:
    """指定された行を gzip CSV に保存する。行がなければファイルは作らない。"""
    from ..models import (
        FeatureSnapshot,
        ForecastSnapshot,
        ModelValidationReport,
        Observation,
        PriceObservation,
        RegimeSnapshot,
        WorldStateSnapshot,
    )

    observation_querysets = list(observation_querysets or [])
    if (
        not observation_querysets
        and price_queryset is None
        and regime_queryset is None
        and world_state_queryset is None
        and feature_queryset is None
        and forecast_queryset is None
        and validation_queryset is None
    ):
        observation_querysets = [Observation.objects.all()]
        price_queryset = PriceObservation.objects.all()
        regime_queryset = RegimeSnapshot.objects.all()
        world_state_queryset = WorldStateSnapshot.objects.all()
        feature_queryset = FeatureSnapshot.objects.all()
        forecast_queryset = ForecastSnapshot.objects.all()
        validation_queryset = ModelValidationReport.objects.all()

    total_candidates = sum(qs.count() for qs in observation_querysets)
    total_candidates += price_queryset.count() if price_queryset is not None else 0
    total_candidates += regime_queryset.count() if regime_queryset is not None else 0
    total_candidates += world_state_queryset.count() if world_state_queryset is not None else 0
    total_candidates += feature_queryset.count() if feature_queryset is not None else 0
    total_candidates += forecast_queryset.count() if forecast_queryset is not None else 0
    total_candidates += validation_queryset.count() if validation_queryset is not None else 0
    if total_candidates <= 0:
        return {'created': False, 'row_count': 0, 'path': None, 'size_bytes': 0}

    now = timezone.now()
    path = _archive_path(now, reason, output_dir)
    archived_at = timezone.localtime(now).isoformat()

    with gzip.open(path, 'wt', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        observation_count = _write_observations(
            writer,
            observation_querysets,
            archived_at,
            reason,
        )
        price_count = _write_prices(writer, price_queryset, archived_at, reason)
        regime_count = _write_regimes(writer, regime_queryset, archived_at, reason)
        world_state_count = _write_payload_rows(
            writer,
            world_state_queryset.order_by('as_of_date') if world_state_queryset is not None else None,
            archived_at,
            reason,
            'world_state_snapshot',
            'as_of_date',
            lambda obj: {
                'cadence': obj.cadence,
                'scores': {
                    'growth_score': obj.growth_score,
                    'labor_score': obj.labor_score,
                    'inflation_score': obj.inflation_score,
                    'market_stress_score': obj.market_stress_score,
                },
                'feature_vector': obj.feature_vector,
                'explanation': obj.explanation,
                'warnings': obj.warnings,
                'model_version': obj.model_version,
            },
        )
        feature_count = _write_payload_rows(
            writer,
            feature_queryset.order_by('as_of_date') if feature_queryset is not None else None,
            archived_at,
            reason,
            'feature_snapshot',
            'as_of_date',
            lambda obj: {
                'namespace': obj.namespace,
                'target': obj.target,
                'horizon': obj.horizon,
                'model_version': obj.model_version,
                'feature_hash': obj.feature_hash,
                'feature_vector': obj.feature_vector,
                'source_dates': obj.source_dates,
                'metadata': obj.metadata,
            },
        )
        forecast_count = _write_payload_rows(
            writer,
            forecast_queryset.order_by('as_of_date') if forecast_queryset is not None else None,
            archived_at,
            reason,
            'forecast_snapshot',
            'as_of_date',
            lambda obj: {
                'model_version': obj.model_version,
                'target': obj.target,
                'horizon': obj.horizon,
                'prediction_value': obj.prediction_value,
                'prediction_interval': obj.prediction_interval,
                'features_hash': obj.features_hash,
                'metadata': obj.metadata,
                'realized_value': obj.realized_value,
                'error': obj.error,
                'realized_at': obj.realized_at.isoformat() if obj.realized_at else None,
            },
        )
        validation_count = _write_payload_rows(
            writer,
            validation_queryset.order_by('evaluated_at') if validation_queryset is not None else None,
            archived_at,
            reason,
            'model_validation_report',
            'evaluated_at',
            lambda obj: {
                'model_version': obj.model_version,
                'target': obj.target,
                'horizon': obj.horizon,
                'validation_method': obj.validation_method,
                'sample_count': obj.sample_count,
                'event_count': obj.event_count,
                'metrics': obj.metrics,
                'rows': obj.rows,
                'warnings': obj.warnings,
            },
        )

    row_count = (
        observation_count
        + price_count
        + regime_count
        + world_state_count
        + feature_count
        + forecast_count
        + validation_count
    )
    checksum = _sha256_file(path)
    summary = {
        'created': True,
        'row_count': row_count,
        'path': str(path),
        'manifest_path': _manifest_path(path),
        'size_bytes': path.stat().st_size,
        'observation_count': observation_count,
        'price_count': price_count,
        'regime_count': regime_count,
        'world_state_count': world_state_count,
        'feature_count': feature_count,
        'forecast_count': forecast_count,
        'validation_count': validation_count,
        'checksum': checksum,
        'storage_backend': _storage_backend(path),
    }
    try:
        from ..models import RawArchiveManifest
        RawArchiveManifest.objects.create(
            reason=reason,
            storage_backend=summary['storage_backend'],
            path=summary['manifest_path'],
            row_count=row_count,
            observation_count=observation_count,
            price_count=price_count,
            regime_count=regime_count,
            size_bytes=summary['size_bytes'],
            checksum=checksum,
            metadata={
                'fieldnames': FIELDNAMES,
                'world_state_count': world_state_count,
                'feature_count': feature_count,
                'forecast_count': forecast_count,
                'validation_count': validation_count,
                'archive_dir_env': ARCHIVE_DIR_ENV if os.getenv(ARCHIVE_DIR_ENV) else '',
                'local_path': str(path),
            },
        )
    except Exception:
        # アーカイブ本体が作れていれば削除保護としては成立するため、台帳失敗だけでは落とさない。
        pass
    return summary


def _size_display(size: int) -> str:
    if size >= 1024 * 1024:
        return f'{size / (1024 * 1024):.1f} MB'
    return f'{size / 1024:.1f} KB'


def latest_archive_status(output_dir: Optional[Path] = None) -> dict:
    if output_dir is None:
        try:
            from ..models import RawArchiveManifest
            manifest = RawArchiveManifest.objects.order_by('-created_at').first()
        except Exception:
            manifest = None
        if manifest is not None:
            latest_path = Path(manifest.path)
            return {
                'has_archive': True,
                'latest_file': latest_path.name,
                'latest_created_at': timezone.localtime(
                    manifest.created_at
                ).strftime('%Y-%m-%d %H:%M'),
                'latest_size_display': _size_display(manifest.size_bytes),
                'archive_count': RawArchiveManifest.objects.count(),
                'storage_backend': manifest.storage_backend,
                'checksum_short': manifest.checksum[:12],
                'path': manifest.path,
                'file_exists': latest_path.exists(),
            }
    target_dir = Path(output_dir) if output_dir else archive_dir()
    files = sorted(target_dir.glob('macro_raw_*.csv.gz'))
    if not files:
        return {
            'has_archive': False,
            'latest_file': '—',
            'latest_created_at': '—',
            'latest_size_display': '—',
            'archive_count': 0,
            'storage_backend': _storage_backend(target_dir),
            'checksum_short': '—',
            'path': str(target_dir),
            'file_exists': False,
        }
    latest = files[-1]
    size = latest.stat().st_size
    return {
        'has_archive': True,
        'latest_file': latest.name,
        'latest_created_at': timezone.localtime(
            datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.get_current_timezone())
        ).strftime('%Y-%m-%d %H:%M'),
        'latest_size_display': _size_display(size),
        'archive_count': len(files),
        'storage_backend': _storage_backend(latest),
        'checksum_short': '—',
        'path': str(latest),
        'file_exists': latest.exists(),
    }
