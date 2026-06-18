from django.core.management.base import BaseCommand

from macro.models import WorldModelRun
from macro.services.dashboard_cache import load_macro_update_status, write_static_macro_payload


def build_operations_status(limit=50):
    rows = []
    for run in WorldModelRun.objects.order_by('-started_at')[:limit]:
        rows.append({
            'cadence': run.cadence,
            'name': run.name,
            'status': run.status,
            'started_at': run.started_at.isoformat(),
            'finished_at': run.finished_at.isoformat() if run.finished_at else None,
            'steps': run.steps or [],
            'summary': run.summary or {},
            'error': run.error,
        })
    return {
        'operations_status': rows,
        'latest_update_status': load_macro_update_status() or {},
    }


class Command(BaseCommand):
    help = 'マクロ運用ステータスを static JSON として出力する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output',
            default='static/macro/operations_status.json',
            help='出力先JSONパス',
        )
        parser.add_argument('--limit', type=int, default=50)

    def handle(self, *args, **options):
        payload = build_operations_status(limit=options['limit'])
        write_static_macro_payload(payload, options['output'])
        self.stdout.write(
            self.style.SUCCESS(f"exported operations status: {options['output']}")
        )
