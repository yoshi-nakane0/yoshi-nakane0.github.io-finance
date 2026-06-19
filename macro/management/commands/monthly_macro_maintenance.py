"""ローカル月次メンテナンスをまとめて実行する管理コマンド。"""

import os

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from macro.models import WorldModelRun
from macro.services.operations import finish_run, start_run


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
            help='リターン予測学習を省略する。requirements-train 未導入時に使う。',
        )
        parser.add_argument(
            '--skip-macro-forecast',
            action='store_true',
            help='マクロ予測モデル学習を省略する。',
        )
        parser.add_argument(
            '--skip-model-validation',
            action='store_true',
            help='モデル検証レポート作成を省略する。',
        )
        parser.add_argument(
            '--skip-archive',
            action='store_true',
            help='月次の全履歴アーカイブ作成を省略する。',
        )
        parser.add_argument(
            '--skip-purge',
            action='store_true',
            help='古いデータ削除とDB再圧縮を省略する。使い捨てCI DBでは有効。',
        )
        parser.add_argument(
            '--skip-regime-probability',
            action='store_true',
            help='景気確率モデルの履歴検証を省略する。',
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
        parser.add_argument('--price-history-years', type=int, default=25)
        parser.add_argument('--world-state-years', type=int, default=3)

    def handle(self, *args, **options):
        if _is_serverless_runtime():
            raise CommandError('月次メンテナンスはローカル環境で実行してください。')

        steps = self._build_steps(options)
        if options['dry_run']:
            for label, _, _, _ in steps:
                self.stdout.write(f'[dry-run] {label}')
            return

        run = start_run(
            cadence=WorldModelRun.Cadence.MONTHLY,
            name='monthly_macro_maintenance',
            steps=[{'label': label, 'command': command_name} for label, command_name, _, _ in steps],
        )
        completed = []
        try:
            for label, command_name, command_args, command_kwargs in steps:
                self.stdout.write(f'開始: {label}')
                call_command(command_name, *command_args, **command_kwargs)
                completed.append(label)
                self.stdout.write(self.style.SUCCESS(f'完了: {label}'))
        except Exception as exc:
            finish_run(
                run,
                status=WorldModelRun.Status.FAILED,
                summary={
                    'message': '月次メンテナンスが途中で失敗しました。',
                    'completed_steps': completed,
                },
                error=str(exc),
            )
            raise
        finish_run(
            run,
            status=WorldModelRun.Status.SUCCESS,
            summary={
                'message': '月次メンテナンスが完了しました。',
                'completed_steps': completed,
            },
        )

        self.stdout.write(self.style.SUCCESS('月次メンテナンスが完了しました'))

    def _build_steps(self, options):
        target = options['target']
        horizon_days = options['horizon_days']
        drawdown_threshold = options['drawdown_threshold']
        validation_months = options['validation_months']
        price_history_years = options['price_history_years']
        world_state_years = options['world_state_years']

        steps = []
        if not options['skip_archive']:
            steps.append((
                '月次履歴アーカイブ作成',
                'archive_macro_data',
                (),
                {'reason': 'monthly_snapshot'},
            ))
        if not options['skip_refresh']:
            steps.append(('最新データ取得・景気判定', 'refresh_macro_data', (), {}))

        steps.append((
            '日次価格履歴同期',
            'sync_daily_prices',
            (),
            {'years': price_history_years},
        ))
        if not options['skip_purge']:
            steps.append(('古いデータ削除', 'purge_old_data', (), {}))

        steps.extend([
            ('期限到来予測の実績反映', 'settle_forecast_snapshots', (), {}),
            (
                'World State 月次バックフィル',
                'backfill_world_state',
                (),
                {'years': world_state_years},
            ),
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

        if not options['skip_regime_probability']:
            steps.append((
                '景気確率モデルの履歴検証',
                'train_regime_probability_model',
                (),
                {'years': 20, 'horizon_months': 3},
            ))

        if not options['skip_lightgbm']:
            steps.append(('リターン参考予測更新', 'train_return_model', (), {'all': True}))

        if not options['skip_macro_forecast']:
            steps.append((
                'マクロ予測モデル更新',
                'train_macro_forecast_model',
                (),
                {'all': True},
            ))

        if not options['skip_model_validation']:
            steps.append((
                'モデル walk-forward 検証',
                'run_model_validation',
                (),
                {'all': True},
            ))

        steps.append(('Macro 表示キャッシュ更新', 'precompute_dashboard', (), {}))
        return steps
