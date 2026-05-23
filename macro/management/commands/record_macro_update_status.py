"""macro 更新状態を DashboardCache に記録する補助コマンド。"""

from django.core.management.base import BaseCommand

from macro.services.dashboard_cache import save_macro_update_status


class Command(BaseCommand):
    help = 'macro 更新状態を DashboardCache に記録する'

    def add_arguments(self, parser):
        parser.add_argument('--source', default='build_files')
        parser.add_argument('--status', default='skipped')
        parser.add_argument('--message', default='')
        parser.add_argument('--phase', default='build_files')

    def handle(self, *args, **options):
        message = options['message']
        save_macro_update_status({
            'source': options['source'],
            'status': options['status'],
            'message': message,
            'success_count': 0,
            'failed_count': 0 if options['status'] == 'skipped' else 1,
            'failed': [] if options['status'] == 'skipped' else [{
                'phase': options['phase'],
                'error': message,
            }],
        })
        self.stdout.write('macro update status recorded')
