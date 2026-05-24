"""WorldStateSnapshot を作成・更新する。"""

from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from macro.models import WorldModelRun, WorldStateSnapshot
from macro.services.operations import finish_run, start_run
from macro.services.world_state import compute_current_world_state


class Command(BaseCommand):
    help = 'World Model の現在状態ベクトルを作成・更新する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--cadence',
            choices=[choice[0] for choice in WorldStateSnapshot.Cadence.choices],
            default=WorldStateSnapshot.Cadence.DAILY,
        )
        parser.add_argument('--as-of', dest='as_of', help='YYYY-MM-DD')

    def handle(self, *args, **options):
        as_of = None
        if options.get('as_of'):
            try:
                as_of = datetime.strptime(options['as_of'], '%Y-%m-%d').date()
            except ValueError as exc:
                raise CommandError('--as-of は YYYY-MM-DD で指定してください。') from exc

        run_cadence = (
            WorldModelRun.Cadence.MANUAL
            if as_of is not None else options['cadence']
        )
        run = start_run(
            cadence=run_cadence,
            name='compute_world_state',
            steps=[{'label': 'World State 計算', 'command': 'compute_world_state'}],
        )
        try:
            snapshot = compute_current_world_state(
                cadence=options['cadence'],
                as_of=as_of,
            )
        except Exception as exc:
            finish_run(
                run,
                status=WorldModelRun.Status.FAILED,
                summary={'message': 'World State 計算に失敗しました。'},
                error=str(exc),
            )
            raise

        finish_run(
            run,
            status=WorldModelRun.Status.SUCCESS,
            summary={
                'message': 'World State を更新しました。',
                'as_of_date': snapshot.as_of_date.isoformat(),
                'data_quality': snapshot.data_quality,
            },
        )
        self.stdout.write(
            self.style.SUCCESS(
                f'WorldStateSnapshot: {snapshot.as_of_date} '
                f'quality={snapshot.data_quality:.1f}'
            )
        )
