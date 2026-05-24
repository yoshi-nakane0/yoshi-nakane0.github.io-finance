"""週次の軽量検証ジョブ。"""

from django.core.management import call_command
from django.core.management.base import BaseCommand

from macro.models import WorldModelRun, WorldStateSnapshot
from macro.services.operations import finish_run, start_run
from macro.services.world_state import compute_current_world_state


class Command(BaseCommand):
    help = '既存DBの最新データで週次検証と表示キャッシュ更新を行う'

    def handle(self, *args, **options):
        steps = [
            {'label': 'World State 計算', 'command': 'compute_world_state'},
            {'label': '予測実績反映', 'command': 'settle_forecast_snapshots'},
            {'label': '表示キャッシュ更新', 'command': 'precompute_dashboard'},
        ]
        run = start_run(
            cadence=WorldModelRun.Cadence.WEEKLY,
            name='weekly_macro_validation',
            steps=steps,
        )
        completed = []
        failures = []
        try:
            compute_current_world_state(cadence=WorldStateSnapshot.Cadence.WEEKLY)
            completed.append('compute_world_state')
        except Exception as exc:
            failures.append({'phase': 'compute_world_state', 'error': str(exc)})

        for command_name in ('settle_forecast_snapshots', 'precompute_dashboard'):
            try:
                call_command(command_name)
                completed.append(command_name)
            except Exception as exc:
                failures.append({'phase': command_name, 'error': str(exc)})

        if failures and not completed:
            status = WorldModelRun.Status.FAILED
            message = '週次検証が失敗しました。'
        elif failures:
            status = WorldModelRun.Status.PARTIAL
            message = '週次検証は一部失敗しました。'
        else:
            status = WorldModelRun.Status.SUCCESS
            message = '週次検証が完了しました。'

        finish_run(
            run,
            status=status,
            summary={
                'message': message,
                'completed_steps': completed,
                'failures': failures,
            },
            error='; '.join(f"{f['phase']}: {f['error']}" for f in failures),
        )
        if status == WorldModelRun.Status.FAILED:
            self.stderr.write(self.style.ERROR(message))
        elif status == WorldModelRun.Status.PARTIAL:
            self.stdout.write(self.style.WARNING(message))
        else:
            self.stdout.write(self.style.SUCCESS(message))
