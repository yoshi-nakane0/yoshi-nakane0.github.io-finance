import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from macro.services.dashboard_cache import (
    precompute_dashboard_payload,
    write_static_macro_payload,
)
from macro.services.regime import MODEL_VERSION as REGIME_MODEL_VERSION


def _payload_data_quality(payload):
    quality_report = payload.get('data_quality_report') or {}
    if quality_report.get('freshness_score') is not None:
        return quality_report.get('freshness_score')
    crash_alert = payload.get('crash_alert') or {}
    if crash_alert.get('data_quality_pct') is not None:
        return crash_alert.get('data_quality_pct')
    if payload.get('data_quality_pct') is not None:
        return payload.get('data_quality_pct')
    return 0.0


def _payload_model_version(payload):
    return (
        payload.get('regime_model_version')
        or payload.get('model_version')
        or REGIME_MODEL_VERSION
    )


class Command(BaseCommand):
    help = 'macro トップページ用の生成済みJSONを static/macro/latest_dashboard.json に出力する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output',
            default='static/macro/latest_dashboard.json',
            help='出力先JSONパス',
        )
        parser.add_argument(
            '--source',
            default='github_actions',
            help='生成元ラベル',
        )
        parser.add_argument(
            '--stale',
            action='store_true',
            help='最後の正常データを古いデータとして出力する場合に指定',
        )

    def handle(self, *args, **options):
        started = time.monotonic()
        payload = precompute_dashboard_payload()
        duration = round(time.monotonic() - started, 3)
        warnings = payload.get('warnings') or []
        if not isinstance(warnings, list):
            warnings = [str(warnings)]
        payload = {
            **payload,
            'generated_at': timezone.localtime().isoformat(),
            'source': options['source'],
            'data_quality': _payload_data_quality(payload),
            'stale': bool(options['stale']),
            'model_version': _payload_model_version(payload),
            'job_duration_sec': duration,
            'warnings': warnings,
        }
        write_static_macro_payload(payload, options['output'])
        self.stdout.write(
            self.style.SUCCESS(
                f"exported macro payload: {options['output']} "
                f"({duration:.3f}s)"
            )
        )
