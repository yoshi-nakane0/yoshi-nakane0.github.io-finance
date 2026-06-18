from django.core.management.base import BaseCommand

from macro.models import ModelValidationReport
from macro.services.dashboard_cache import write_static_macro_payload
from macro.services.model_validation import model_display_grade


def build_model_validation_report(limit=100):
    rows = []
    for report in ModelValidationReport.objects.order_by('-evaluated_at')[:limit]:
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
    return {'model_validation_report': rows}


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
