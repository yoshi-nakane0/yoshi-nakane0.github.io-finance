from django.core.management.base import BaseCommand
from django.utils import timezone

from macro.models import ModelValidationReport
from macro.services.dashboard_cache import write_static_macro_payload
from macro.services.model_validation import latest_validation_reports, model_display_grade


STALE_MODEL_VALIDATION_DAYS = 30


def _freshness():
    latest = ModelValidationReport.objects.order_by('-evaluated_at').first()
    if latest is None:
        return {
            'latest_evaluated_at': None,
            'age_days': None,
            'stale_after_days': STALE_MODEL_VALIDATION_DAYS,
            'is_stale': True,
        }
    age = timezone.now() - latest.evaluated_at
    age_days = max(0, age.days)
    return {
        'latest_evaluated_at': latest.evaluated_at.isoformat(),
        'age_days': age_days,
        'stale_after_days': STALE_MODEL_VALIDATION_DAYS,
        'is_stale': age_days > STALE_MODEL_VALIDATION_DAYS,
    }


def build_model_validation_report(limit=100):
    rows = []
    for report in latest_validation_reports(limit=limit):
        display_grade, display_reason = model_display_grade(report)
        rows.append({
            'model_version': report.model_version,
            'target': report.target,
            'horizon': report.horizon,
            'validation_method': report.validation_method,
            'sample_count': report.sample_count,
            'event_count': report.event_count,
            'metrics': report.metrics or {},
            'warnings': report.warnings or [],
            'display_grade': display_grade,
            'display_reason': display_reason,
            'evaluated_at': report.evaluated_at.isoformat(),
        })
    freshness = _freshness()
    warnings = []
    if freshness['is_stale']:
        warnings.append('model_validation_report の更新日が古いです。再検証してください。')
    return {
        'model_validation_report': rows,
        'freshness': freshness,
        'warnings': warnings,
    }


class Command(BaseCommand):
    help = 'モデル検証レポートを static JSON として出力する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output',
            default='static/macro/model_validation_report.json',
            help='出力先JSONパス',
        )
        parser.add_argument('--limit', type=int, default=100)

    def handle(self, *args, **options):
        payload = build_model_validation_report(limit=options['limit'])
        write_static_macro_payload(payload, options['output'])
        self.stdout.write(
            self.style.SUCCESS(f"exported model validation report: {options['output']}")
        )
