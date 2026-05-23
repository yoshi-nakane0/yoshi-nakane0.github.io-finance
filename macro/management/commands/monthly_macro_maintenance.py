"""ローカル月次メンテナンスをまとめて実行する管理コマンド。"""

import os

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


SERVERLESS_ENV_NAMES = (
    'VERCEL',
    'AWS_LAMBDA_FUNCTION_NAME',
    'LAMBDA_TASK_ROOT',
)


def _is_serverless_runtime():
    return any(os.getenv(name) for name in SERVERLESS_ENV_NAMES)


class Command(BaseCommand):
    help = 'ローカルで月次のモデル更新・DB整理・表示キャッシュ更新をまとめて実行する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--skip-refresh',
            action='store_true',
            help='指標取得を省略する。直前に日次更新済みの場合に使う。',
        )
        parser.add_argument(
            '--skip-lightgbm',
            action='store_true',
            help='LightGBM 学習を省略する。requirements-train 未導入時に使う。',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='実行内容だけ表示し、実処理は行わない。',
        )
        parser.add_argument('--target', default='GSPC')
        parser.add_argument('--horizon-days', type=int, default=63)
        parser.add_argument('--drawdown-threshold', type=float, default=-10.0)
        parser.add_argument('--validation-months', type=int, default=120)

    def handle(self, *args, **options):
        if _is_serverless_runtime():
            raise CommandError('月次メンテナンスはローカル環境で実行してください。')

        steps = self._build_steps(options)
        if options['dry_run']:
            for label, _, _, _ in steps:
                self.stdout.write(f'[dry-run] {label}')
            return

        for label, command_name, command_args, command_kwargs in steps:
            self.stdout.write(f'開始: {label}')
            call_command(command_name, *command_args, **command_kwargs)
            self.stdout.write(self.style.SUCCESS(f'完了: {label}'))

        self.stdout.write(self.style.SUCCESS('月次メンテナンスが完了しました'))

    def _build_steps(self, options):
        target = options['target']
        horizon_days = options['horizon_days']
        drawdown_threshold = options['drawdown_threshold']
        validation_months = options['validation_months']

        steps = []
        if not options['skip_refresh']:
            steps.append(('最新データ取得・景気判定', 'refresh_macro_data', (), {}))

        steps.extend([
            ('古いデータ削除', 'purge_old_data', (), {}),
            (
                '急落警戒スコアの月次検証',
                'backtest_crash_alert',
                (),
                {
                    'target': target,
                    'horizon_days': horizon_days,
                    'drawdown_threshold': drawdown_threshold,
                    'output': 'static/macro/crash_alert_backtest.json',
                    'csv_output': 'static/macro/crash_alert_backtest.csv',
                },
            ),
            (
                '急落確率モデル更新',
                'train_crash_probability_model',
                (),
                {
                    'target': target,
                    'horizon_days': horizon_days,
                    'drawdown_threshold': drawdown_threshold,
                    'validation_months': validation_months,
                },
            ),
        ])

        if not options['skip_lightgbm']:
            steps.append(('LightGBM 参考予測更新', 'train_crash_model', (), {}))

        steps.append(('Macro 表示キャッシュ更新', 'precompute_dashboard', (), {}))
        return steps
