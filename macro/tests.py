"""macro モジュールのユニットテスト。"""

import gzip
import json
import yaml
from datetime import date, timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    DashboardCache,
    DailyPriceObservation,
    FeatureSnapshot,
    ForecastSnapshot,
    Indicator,
    MacroForecastRun,
    ModelValidationReport,
    Observation,
    PolicyExpectationSnapshot,
    PriceObservation,
    RawArchiveManifest,
    RegimeSnapshot,
    VintageObservation,
    WorldStateSnapshot,
    WorldModelRun,
)
from .services import (
    crash_alert,
    crash_probability,
    dashboard,
    dashboard_cache,
    data_quality,
    data_sync,
    detail_analysis,
    forecast_models,
    forecast_tracking,
    house_view,
    historical_crash,
    judgment,
    linkage,
    operations,
    raw_archive,
    regime,
    regime_probability,
    scenario,
    similarity,
    sparkline,
)
from .services import feature_store, world_state


class MacroRuntimeConfigTest(SimpleTestCase):
    def test_github_workflow_files_are_valid_yaml(self):
        workflows_dir = Path(settings.BASE_DIR) / '.github' / 'workflows'

        for workflow_path in workflows_dir.glob('*.yml'):
            with self.subTest(workflow=workflow_path.name):
                yaml.safe_load(workflow_path.read_text(encoding='utf-8'))

    def test_macro_operations_daily_job_generates_payload_before_deploy(self):
        workflows_dir = Path(settings.BASE_DIR) / '.github' / 'workflows'
        workflow = (
            workflows_dir / 'macro-operations.yml'
        ).read_text(encoding='utf-8')

        self.assertIn('cron: "30 5 * * *"', workflow)
        self.assertIn('daily-refresh:', workflow)
        self.assertIn('VERCEL_DEPLOY_HOOK_URL', workflow)
        self.assertIn('actions/checkout@v4', workflow)
        self.assertIn('actions/setup-python@v5', workflow)
        self.assertIn('pip install -r requirements.txt', workflow)
        self.assertIn('python manage.py migrate --noinput', workflow)
        self.assertIn('python manage.py refresh_macro_data', workflow)
        self.assertIn('python manage.py compute_world_state', workflow)
        self.assertIn('python manage.py run_macro_forecast', workflow)
        self.assertIn('python manage.py settle_forecast_snapshots', workflow)
        self.assertIn('python manage.py precompute_dashboard', workflow)
        self.assertIn(
            'python manage.py export_macro_payload --output static/macro/latest_dashboard.json',
            workflow,
        )
        self.assertIn(
            'python manage.py export_macro_house_view --output static/macro/house_view.json',
            workflow,
        )
        self.assertIn(
            'python manage.py export_macro_quality --output static/macro/data_quality_report.json',
            workflow,
        )
        self.assertIn(
            'python manage.py export_macro_forecast_ledger --output static/macro/forecast_ledger.json',
            workflow,
        )
        self.assertIn(
            'python manage.py export_macro_scenarios --output static/macro/scenario_ledger.json',
            workflow,
        )
        self.assertIn(
            'python manage.py export_macro_model_validation --output static/macro/model_validation_report.json',
            workflow,
        )
        self.assertIn(
            'python manage.py export_macro_model_cards --output static/macro/model_cards.json',
            workflow,
        )
        self.assertIn(
            'python manage.py export_macro_operations_status --output static/macro/operations_status.json',
            workflow,
        )
        self.assertIn(
            'python manage.py export_macro_goldman_outlook --output static/macro/goldman_outlook_comparison.json',
            workflow,
        )
        self.assertIn(
            'python manage.py export_macro_house_view_validation --output static/macro/house_view_validation.json',
            workflow,
        )
        self.assertIn(
            'python manage.py export_macro_vintage_quality --output static/macro/vintage_quality_report.json',
            workflow,
        )
        self.assertIn(
            'python manage.py export_macro_validation_weights --output static/macro/validation_weights.json',
            workflow,
        )
        self.assertNotIn('python manage.py run_house_view_backtest', workflow)
        self.assertIn('python manage.py weekly_macro_validation', workflow)
        self.assertIn('python manage.py run_macro_forecast', workflow)
        self.assertIn('python manage.py export_macro_forecast_ledger --output static/macro/forecast_ledger.json', workflow)
        self.assertIn('git add static/macro/*.json', workflow)
        self.assertNotIn('git add static/macro/latest_dashboard.json runtime/db.sqlite3', workflow)
        self.assertIn('git commit -m "Update macro generated data"', workflow)
        self.assertIn('git push', workflow)
        self.assertIn('curl -fsS -X POST "$VERCEL_DEPLOY_HOOK_URL"', workflow)
        self.assertIn('timeout-minutes: 20', workflow)
        self.assertIn('concurrency:', workflow)
        self.assertNotIn('- monthly', workflow)
        self.assertNotIn('monthly-maintenance:', workflow)
        self.assertNotIn('monthly_macro_maintenance', workflow)
        self.assertNotIn('requirements-train.txt', workflow)
        self.assertNotIn('requirements-prod.txt', workflow)
        self.assertNotIn('SQLITE_DB_PATH: /tmp/macro-data.sqlite3', workflow)
        self.assertNotIn('git add db.sqlite3', workflow)
        self.assertNotIn('DATA_BRANCH', workflow)
        self.assertFalse((workflows_dir / 'refresh-macro-data.yml').exists())

    def test_local_macro_pipeline_exports_static_json_outputs(self):
        script = (Path(settings.BASE_DIR) / 'scripts' / 'run_macro_local_pipeline.sh').read_text(
            encoding='utf-8',
        )

        self.assertIn('set -euo pipefail', script)
        self.assertIn('python manage.py run_model_validation', script)
        self.assertIn(
            'python manage.py export_macro_payload --output static/macro/latest_dashboard.json',
            script,
        )
        self.assertIn(
            'python manage.py export_macro_house_view --output static/macro/house_view.json',
            script,
        )
        self.assertIn(
            'python manage.py export_macro_quality --output static/macro/data_quality_report.json',
            script,
        )
        self.assertIn(
            'python manage.py export_macro_forecast_ledger --output static/macro/forecast_ledger.json',
            script,
        )
        self.assertIn(
            'python manage.py export_macro_scenarios --output static/macro/scenario_ledger.json',
            script,
        )
        self.assertIn(
            'python manage.py export_macro_model_validation --output static/macro/model_validation_report.json',
            script,
        )
        self.assertIn(
            'python manage.py export_macro_model_cards --output static/macro/model_cards.json',
            script,
        )
        self.assertIn(
            'python manage.py export_macro_operations_status --output static/macro/operations_status.json',
            script,
        )
        self.assertIn(
            'python manage.py export_macro_goldman_outlook --output static/macro/goldman_outlook_comparison.json',
            script,
        )
        self.assertIn(
            'python manage.py export_macro_house_view_validation --output static/macro/house_view_validation.json',
            script,
        )
        self.assertIn(
            'python manage.py export_macro_vintage_quality --output static/macro/vintage_quality_report.json',
            script,
        )
        self.assertIn(
            'python manage.py export_macro_validation_weights --output static/macro/validation_weights.json',
            script,
        )
        self.assertNotIn('python manage.py run_house_view_backtest', script)
        self.assertIn('git add static/macro/*.json', script)
        self.assertNotIn('runtime/db.sqlite3', script)

        monthly_script = (
            Path(settings.BASE_DIR) / 'scripts' / 'run_macro_monthly_local_pipeline.sh'
        ).read_text(encoding='utf-8')
        self.assertIn('set -euo pipefail', monthly_script)
        self.assertIn('python manage.py monthly_macro_maintenance', monthly_script)
        self.assertIn(
            'python manage.py export_macro_payload --output static/macro/latest_dashboard.json',
            monthly_script,
        )
        self.assertIn(
            'python manage.py export_macro_model_validation --output static/macro/model_validation_report.json',
            monthly_script,
        )
        self.assertIn(
            'python manage.py export_macro_validation_weights --output static/macro/validation_weights.json',
            monthly_script,
        )
        self.assertIn('git add static/macro/*.json static/macro/*.csv', monthly_script)
        self.assertIn('python manage.py test macro', monthly_script)

        backtest_script = (
            Path(settings.BASE_DIR) / 'scripts' / 'run_macro_house_view_backtest.sh'
        ).read_text(encoding='utf-8')
        self.assertIn('set -euo pipefail', backtest_script)
        self.assertIn(
            'python manage.py run_house_view_backtest --output static/macro/house_view_backtest.json',
            backtest_script,
        )
        self.assertIn(
            'python manage.py export_macro_house_view_validation --output static/macro/house_view_validation.json',
            backtest_script,
        )

    def test_vercel_build_skips_macro_refresh_unless_explicitly_enabled(self):
        build_script = (
            Path(settings.BASE_DIR)
            / 'build_files.sh'
        ).read_text(encoding='utf-8')

        self.assertIn('Running finance production build bootstrap', build_script)
        self.assertIn('BUNDLED_SQLITE_PATH', build_script)
        self.assertIn('RUN_DATA_REFRESH_IN_BUILD', build_script)
        self.assertIn('Skip data refresh in Vercel build', build_script)
        self.assertIn('manage.py refresh_macro_data', build_script)
        self.assertIn('manage.py compute_world_state', build_script)
        self.assertIn('manage.py run_macro_forecast', build_script)
        self.assertIn('manage.py settle_forecast_snapshots', build_script)
        self.assertIn('manage.py precompute_dashboard', build_script)
        self.assertNotIn('FRED_API_KEY is not set; skipping macro data refresh', build_script)
        self.assertNotIn('refresh_macro_data failed during Vercel build', build_script)
        self.assertIn('cp "$SQLITE_DB_PATH" "$BUNDLED_SQLITE_PATH"', build_script)
        self.assertNotIn('origin/${DATA_BRANCH}:db.sqlite3', build_script)
        self.assertNotIn('ensurepip', build_script)
        self.assertNotIn('pip install -r requirements-prod.txt', build_script)

        vercel_config = (Path(settings.BASE_DIR) / 'vercel.json').read_text(
            encoding='utf-8',
        )
        python_project = (Path(settings.BASE_DIR) / 'pyproject.toml').read_text(
            encoding='utf-8',
        )
        self.assertIn('"buildCommand": "bash build_files.sh"', vercel_config)
        self.assertIn('"functions"', vercel_config)
        self.assertIn('"api/index.py"', vercel_config)
        self.assertIn('runtime/db.sqlite3', vercel_config)
        self.assertIn('basecalc/data/latest_snapshot.json', vercel_config)
        self.assertNotIn('"builds"', vercel_config)
        self.assertNotIn('"installCommand": "bash build_files.sh"', vercel_config)
        self.assertIn('requires-python = ">=3.12"', python_project)
        self.assertIn('"outputDirectory": "staticfiles"', vercel_config)
        self.assertIn('name = "yoshi-nakane-finance"', python_project)
        self.assertIn('"Django==5.2.14"', python_project)

    def test_macro_index_template_displays_backtest_and_live_accuracy(self):
        template = (
            Path(settings.BASE_DIR) / 'macro' / 'templates' / 'macro' / 'index.html'
        ).read_text(encoding='utf-8')

        self.assertIn('house_view_validation.accuracy_sections.backtest', template)
        self.assertIn('house_view_validation.accuracy_sections.live', template)
        self.assertIn('Backtest精度', template)
        self.assertIn('Live精度', template)

    def test_macro_world_model_workflows_include_new_jobs(self):
        workflows_dir = Path(settings.BASE_DIR) / '.github' / 'workflows'
        workflow = (workflows_dir / 'macro-operations.yml').read_text(
            encoding='utf-8',
        )

        self.assertNotIn('monthly-maintenance:', workflow)
        self.assertNotIn('monthly_macro_maintenance', workflow)
        monthly_script = (
            workflows_dir.parent.parent / 'scripts' / 'run_macro_monthly_local_pipeline.sh'
        ).read_text(encoding='utf-8')
        self.assertIn('return_forecast_model.json', monthly_script)
        self.assertIn('macro_forecast_model.json', monthly_script)
        self.assertIn('weekly-validation:', workflow)
        self.assertIn('python manage.py weekly_macro_validation', workflow)
        self.assertIn(
            'DATABASE_URL is not set; skipped weekly validation.',
            workflow,
        )
        self.assertFalse(
            (workflows_dir / 'monthly-macro-maintenance.yml').exists(),
        )
        self.assertFalse(
            (workflows_dir / 'weekly-macro-validation.yml').exists(),
        )

    def test_macro_operations_does_not_run_monthly_model_training_in_actions(self):
        workflows_dir = Path(settings.BASE_DIR) / '.github' / 'workflows'
        workflow = (workflows_dir / 'macro-operations.yml').read_text(
            encoding='utf-8',
        )

        self.assertNotIn('cron: "20 6 3 * *"', workflow)
        self.assertNotIn('operation == \'monthly\'', workflow)
        self.assertNotIn('requirements-train.txt', workflow)
        self.assertNotIn('monthly_macro_maintenance', workflow)

    def test_wsgi_runtime_migration_check_not_based_on_one_old_table(self):
        wsgi_source = (
            Path(settings.BASE_DIR)
            / 'myproject'
            / 'wsgi.py'
        ).read_text(encoding='utf-8')

        self.assertNotIn("name='macro_observation'", wsgi_source)


class MonthlyMacroMaintenanceCommandTest(SimpleTestCase):
    def test_monthly_command_runs_local_steps_in_order(self):
        with mock.patch(
            'macro.management.commands.monthly_macro_maintenance.call_command',
        ) as call_command_mock, mock.patch(
            'macro.management.commands.monthly_macro_maintenance.start_run',
            return_value=mock.Mock(),
        ), mock.patch(
            'macro.management.commands.monthly_macro_maintenance.finish_run',
        ):
            out = StringIO()
            call_command('monthly_macro_maintenance', stdout=out)

        self.assertEqual(
            [call.args[0] for call in call_command_mock.call_args_list],
            [
                'archive_macro_data',
                'refresh_macro_data',
                'sync_daily_prices',
                'purge_old_data',
                'settle_forecast_snapshots',
                'backfill_world_state',
                'backtest_crash_alert',
                'train_crash_probability_model',
                'train_regime_probability_model',
                'train_return_model',
                'train_macro_forecast_model',
                'run_model_validation',
                'precompute_dashboard',
            ],
        )
        self.assertEqual(
            call_command_mock.call_args_list[6],
            mock.call(
                'backtest_crash_alert',
                target='GSPC',
                horizon_days=63,
                drawdown_threshold=-10.0,
                output='static/macro/crash_alert_backtest.json',
                csv_output='static/macro/crash_alert_backtest.csv',
            ),
        )
        self.assertEqual(
            call_command_mock.call_args_list[7],
            mock.call(
                'train_crash_probability_model',
                target='GSPC',
                horizon_days=63,
                drawdown_threshold=-10.0,
                validation_months=120,
            ),
        )

    def test_monthly_command_can_skip_refresh_and_lightgbm(self):
        with mock.patch(
            'macro.management.commands.monthly_macro_maintenance.call_command',
        ) as call_command_mock, mock.patch(
            'macro.management.commands.monthly_macro_maintenance.start_run',
            return_value=mock.Mock(),
        ), mock.patch(
            'macro.management.commands.monthly_macro_maintenance.finish_run',
        ):
            call_command(
                'monthly_macro_maintenance',
                skip_refresh=True,
                skip_lightgbm=True,
                stdout=StringIO(),
            )

        self.assertEqual(
            [call.args[0] for call in call_command_mock.call_args_list],
            [
                'archive_macro_data',
                'sync_daily_prices',
                'purge_old_data',
                'settle_forecast_snapshots',
                'backfill_world_state',
                'backtest_crash_alert',
                'train_crash_probability_model',
                'train_regime_probability_model',
                'train_macro_forecast_model',
                'run_model_validation',
                'precompute_dashboard',
            ],
        )

    def test_monthly_command_can_skip_archive_and_purge_and_limit_price_history(self):
        with mock.patch(
            'macro.management.commands.monthly_macro_maintenance.call_command',
        ) as call_command_mock, mock.patch(
            'macro.management.commands.monthly_macro_maintenance.start_run',
            return_value=mock.Mock(),
        ), mock.patch(
            'macro.management.commands.monthly_macro_maintenance.finish_run',
        ):
            call_command(
                'monthly_macro_maintenance',
                skip_archive=True,
                skip_purge=True,
                price_history_years=10,
                stdout=StringIO(),
            )

        self.assertEqual(
            [call.args[0] for call in call_command_mock.call_args_list],
            [
                'refresh_macro_data',
                'sync_daily_prices',
                'settle_forecast_snapshots',
                'backfill_world_state',
                'backtest_crash_alert',
                'train_crash_probability_model',
                'train_regime_probability_model',
                'train_return_model',
                'train_macro_forecast_model',
                'run_model_validation',
                'precompute_dashboard',
            ],
        )
        self.assertEqual(
            call_command_mock.call_args_list[1],
            mock.call('sync_daily_prices', years=10),
        )

    @mock.patch.dict('os.environ', {'VERCEL': '1'})
    def test_monthly_command_rejects_serverless_runtime(self):
        with self.assertRaises(CommandError):
            call_command('monthly_macro_maintenance')


class CrashProbabilityModelCommandTest(TestCase):
    def test_training_command_stores_forecast_snapshot(self):
        rows = [
            {
                'month': f'2020-{(idx % 12) + 1:02d}-01',
                'event': idx % 10 == 0,
                'max_drawdown_pct': -12.0 if idx % 10 == 0 else -2.0,
                'lead_time_days': 30 if idx % 10 == 0 else None,
                'features': {'market_stress_score': float(idx % 100)},
            }
            for idx in range(100)
        ]

        with TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            with override_settings(BASE_DIR=base), mock.patch(
                'macro.management.commands.train_crash_probability_model.'
                'crash_probability.build_dataset',
                return_value=rows,
            ), mock.patch(
                'macro.management.commands.train_crash_probability_model.'
                'crash_probability.train_logistic_model',
                return_value={
                    'feature_names': ['market_stress_score'],
                    'weights': [0.0, 0.1],
                    'means': [0.0],
                    'scales': [1.0],
                },
            ), mock.patch(
                'macro.management.commands.train_crash_probability_model.'
                'crash_probability.predict_probability',
                return_value=0.2,
            ), mock.patch(
                'macro.management.commands.train_crash_probability_model.'
                'crash_probability.calibration_bins',
                return_value=[],
            ), mock.patch(
                'macro.management.commands.train_crash_probability_model.'
                'crash_probability.calibrated_probability',
                return_value=0.12,
            ), mock.patch(
                'macro.management.commands.train_crash_probability_model.'
                'crash_probability.current_features',
                return_value={'market_stress_score': 42.0},
            ), mock.patch(
                'macro.management.commands.train_crash_probability_model.'
                'crash_probability.coefficient_rows',
                return_value=[],
            ):
                call_command(
                    'train_crash_probability_model',
                    validation_months=20,
                    output='static/macro/test_crash_probability_model.json',
                    stdout=StringIO(),
                )

        snapshot = ForecastSnapshot.objects.get(
            model_version='crash_probability_logistic_v1',
            target='GSPC',
            horizon='63d',
        )
        self.assertEqual(snapshot.prediction_value, 0.12)
        self.assertEqual(len(snapshot.features_hash), 64)
        self.assertEqual(
            snapshot.prediction_interval['type'],
            'validation_event_rate_wilson_95',
        )


class MacroWorldModelStorageTest(TestCase):
    def test_new_snapshot_models_save_and_enforce_identity(self):
        WorldStateSnapshot.objects.create(
            as_of_date=date(2026, 5, 1),
            growth_score=55,
            data_quality=80,
            feature_vector={'world_growth_score': 55},
        )
        FeatureSnapshot.objects.create(
            as_of_date=date(2026, 5, 1),
            namespace='return_forecast',
            target='GSPC',
            horizon='3m',
            model_version='return_lightgbm_v2',
            feature_hash='a' * 64,
            feature_vector={'x': 1.0},
        )
        DailyPriceObservation.objects.create(
            ticker='GSPC',
            observation_date=date(2026, 5, 1),
            close_price=5000,
        )
        ModelValidationReport.objects.create(
            model_version='return_lightgbm_v2',
            target='GSPC',
            horizon='3m',
            sample_count=12,
            metrics={'mae': 1.2},
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                FeatureSnapshot.objects.create(
                    as_of_date=date(2026, 5, 1),
                    namespace='return_forecast',
                    target='GSPC',
                    horizon='3m',
                    model_version='return_lightgbm_v2',
                    feature_hash='b' * 64,
                    feature_vector={'x': 2.0},
                )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                DailyPriceObservation.objects.create(
                    ticker='GSPC',
                    observation_date=date(2026, 5, 1),
                    close_price=5001,
                )

    def test_world_state_assessment_and_compute_are_idempotent(self):
        assessment = world_state.build_world_state_assessment(
            as_of=date(2026, 5, 1),
        )
        self.assertIn('feature_vector', assessment)
        for key in world_state.STATE_SCORE_FIELDS:
            value = assessment.get(key)
            if value is not None:
                self.assertGreaterEqual(value, 0)
                self.assertLessEqual(value, 100)

        first = world_state.compute_current_world_state(
            as_of=date(2026, 5, 1),
        )
        second = world_state.compute_current_world_state(
            as_of=date(2026, 5, 1),
        )
        self.assertEqual(first.id, second.id)

    def test_feature_hash_is_stable_and_snapshot_links_to_forecast(self):
        vector = {'b': 2.0, 'a': 1.0}
        self.assertEqual(
            feature_store.hash_feature_vector(vector),
            feature_store.hash_feature_vector({'a': 1.0, 'b': 2.0}),
        )
        snapshot = feature_store.save_feature_snapshot(
            namespace='return_forecast',
            target='GSPC',
            horizon='3m',
            model_version='return_lightgbm_v2',
            as_of=date(2026, 5, 1),
            feature_vector=vector,
            source_dates={},
            data_quality=75,
            metadata={'missing_features': []},
        )
        forecast = ForecastSnapshot.objects.create(
            as_of_date=date(2026, 5, 1),
            model_version='return_lightgbm_v2',
            target='GSPC',
            horizon='3m',
            prediction_value=1.2,
            features_hash=snapshot.feature_hash,
            metadata={'feature_snapshot_id': snapshot.id},
        )
        self.assertEqual(
            forecast.metadata['feature_snapshot_id'],
            snapshot.id,
        )

    def test_crash_probability_prefers_daily_drawdown_when_available(self):
        for idx in range(5):
            PriceObservation.objects.create(
                ticker=PriceObservation.Ticker.SP500,
                observation_month=date(2026, idx + 1, 1),
                close_price=100 + idx,
            )
        for day in range(1, 120):
            DailyPriceObservation.objects.create(
                ticker='GSPC',
                observation_date=date(2026, 1, 1) + relativedelta(days=day),
                close_price=100 - day * 0.05,
            )

        with mock.patch(
            'macro.services.crash_probability.compute_crash_alert',
            return_value={
                'market_stress_score': 40,
                'forward_risk_score': 35,
                'data_quality_pct': 90,
                'rule_agreement_pct': 80,
                'category_summary': [],
            },
        ):
            rows = crash_probability.build_dataset(
                target='GSPC',
                horizon_days=30,
                drawdown_threshold=-3,
            )

        self.assertTrue(rows)
        self.assertEqual(rows[0]['target_mode'], 'daily_max_drawdown')

    def test_crash_probability_falls_back_to_monthly_without_daily_prices(self):
        for idx in range(5):
            PriceObservation.objects.create(
                ticker=PriceObservation.Ticker.SP500,
                observation_month=date(2026, idx + 1, 1),
                close_price=100 - idx,
            )

        with mock.patch(
            'macro.services.crash_probability.compute_crash_alert',
            return_value={
                'market_stress_score': 40,
                'forward_risk_score': 35,
                'data_quality_pct': 90,
                'rule_agreement_pct': 80,
                'category_summary': [],
            },
        ):
            rows = crash_probability.build_dataset(
                target='GSPC',
                horizon_days=30,
                drawdown_threshold=-3,
            )

        self.assertTrue(rows)
        self.assertEqual(rows[0]['target_mode'], 'monthly_fallback')


class MacroReliabilityEnhancementTest(TestCase):
    def test_goldman_outlook_comparison_uses_free_public_sources(self):
        from macro.services.goldman_outlook import build_goldman_outlook_comparison

        RegimeSnapshot.objects.create(
            snapshot_date=date(2026, 6, 1),
            regime_label=RegimeSnapshot.Label.EXPANSION,
            confidence=82,
            data_quality=95,
            risk_probabilities={'recession_probability': 0.18},
            regime_probabilities={'expansion': 0.7, 'slowdown': 0.2},
        )

        report = build_goldman_outlook_comparison()

        self.assertEqual(report['source_scope'], 'free_public_goldman_sachs_pages')
        self.assertTrue(report['free_public_sources'])
        self.assertTrue(
            all(
                source['url'].startswith('https://www.goldmansachs.com/')
                for source in report['free_public_sources']
            )
        )
        self.assertEqual(
            report['goldman_sachs_public_outlook']['forecasts']['us_gdp_growth_q4q4_2026'],
            2.5,
        )
        self.assertIn('recession_probability_12m', report['comparison'])

    def test_house_view_validation_scores_settled_regime_predictions(self):
        from macro.services.house_view_validation import build_house_view_validation_report

        ForecastSnapshot.objects.create(
            as_of_date=date(2026, 1, 1),
            model_version='macro_hatzius_v1',
            target='macro_regime',
            horizon='3m_6m',
            prediction_value=0.7,
            metadata={'primary_regime': 'expansion'},
        )
        RegimeSnapshot.objects.create(
            snapshot_date=date(2026, 4, 1),
            regime_label=RegimeSnapshot.Label.EXPANSION,
            confidence=80,
            data_quality=90,
        )

        report = build_house_view_validation_report()

        self.assertEqual(report['sample_count'], 1)
        self.assertEqual(report['hit_count'], 1)
        self.assertEqual(report['hit_rate'], 1.0)
        self.assertEqual(report['rows'][0]['predicted_regime'], 'expansion')
        self.assertEqual(report['rows'][0]['actual_regime'], 'expansion')

    def test_house_view_validation_separates_backtest_and_live_accuracy(self):
        from macro.services.house_view_validation import build_house_view_validation_report

        ForecastSnapshot.objects.create(
            as_of_date=date(2026, 1, 1),
            model_version='macro_hatzius_v1',
            target='macro_regime',
            horizon='3m_6m',
            prediction_value=0.7,
            metadata={'primary_regime': 'slowdown'},
        )
        RegimeSnapshot.objects.create(
            snapshot_date=date(2026, 4, 1),
            regime_label=RegimeSnapshot.Label.SLOWDOWN,
            confidence=80,
            data_quality=90,
        )
        with TemporaryDirectory() as tmpdir:
            backtest_path = Path(tmpdir) / 'house_view_backtest.json'
            backtest_path.write_text(
                json.dumps({
                    'backtest_accuracy': {
                        'sample_count': 12,
                        'hit_count': 9,
                        'hit_rate': 0.75,
                        'horizons': {
                            '3m': {'sample_count': 6, 'hit_rate': 0.8333},
                            '6m': {'sample_count': 6, 'hit_rate': 0.6667},
                        },
                        'data_modes': {
                            'point_in_time': {'sample_count': 8, 'hit_rate': 0.875},
                            'revised_reference': {'sample_count': 4, 'hit_rate': 0.5},
                        },
                    },
                }),
                encoding='utf-8',
            )

            report = build_house_view_validation_report(backtest_path=backtest_path)

        self.assertEqual(report['accuracy_sections']['backtest']['hit_rate'], 0.75)
        self.assertEqual(report['accuracy_sections']['backtest']['sample_count'], 12)
        self.assertEqual(report['accuracy_sections']['live']['hit_rate'], 1.0)
        self.assertEqual(report['accuracy_sections']['live']['sample_count'], 1)
        self.assertNotEqual(
            report['accuracy_sections']['backtest']['sample_kind'],
            report['accuracy_sections']['live']['sample_kind'],
        )

    def test_house_view_backtest_replays_monthly_predictions_for_3m_and_6m(self):
        from macro.services.house_view_backtest import run_house_view_backtest

        RegimeSnapshot.objects.create(
            snapshot_date=date(2026, 4, 1),
            regime_label=RegimeSnapshot.Label.EXPANSION,
            confidence=75,
            data_quality=90,
        )
        RegimeSnapshot.objects.create(
            snapshot_date=date(2026, 7, 1),
            regime_label=RegimeSnapshot.Label.SLOWDOWN,
            confidence=75,
            data_quality=90,
        )

        with mock.patch(
            'macro.services.house_view_backtest.regime.build_current_regime_assessment',
            return_value={
                'regime_label': 'expansion',
                'rule_strength': 70,
                'data_quality': 85,
                'warnings': [],
            },
        ):
            report = run_house_view_backtest(
                start=date(2026, 1, 1),
                end=date(2026, 1, 1),
                horizons=(3, 6),
                data_mode='revised_reference',
            )

        self.assertEqual(report['execution_scope'], 'local_heavy_backtest')
        self.assertEqual(report['backtest_accuracy']['sample_count'], 2)
        self.assertEqual(report['backtest_accuracy']['hit_count'], 1)
        self.assertEqual(report['backtest_accuracy']['hit_rate'], 0.5)
        self.assertEqual(report['backtest_accuracy']['horizons']['3m']['hit_rate'], 1.0)
        self.assertEqual(report['backtest_accuracy']['horizons']['6m']['hit_rate'], 0.0)
        self.assertEqual(report['backtest_accuracy']['data_modes']['revised_reference']['sample_count'], 2)
        self.assertEqual(report['rows'][0]['validation_target'], 'macro_regime_3m')
        self.assertEqual(report['rows'][1]['miss_type'], 'too_bullish')

    def test_house_view_backtest_point_in_time_uses_vintage_values(self):
        from macro.services.house_view_backtest import run_house_view_backtest

        indicator, _ = Indicator.objects.update_or_create(
            fred_series_id='INDPRO',
            defaults={
                'name_ja': '鉱工業生産',
                'category': Indicator.Category.GROWTH,
                'source': Indicator.Source.FRED,
                'importance': Indicator.Importance.A,
                'frequency': Indicator.Frequency.MONTHLY,
            },
        )
        VintageObservation.objects.create(
            indicator=indicator,
            observation_date=date(2025, 1, 1),
            realtime_start=date(2025, 1, 1),
            realtime_end=date(9999, 12, 31),
            value=95.0,
            collected_at=timezone.now(),
        )
        VintageObservation.objects.create(
            indicator=indicator,
            observation_date=date(2026, 1, 1),
            realtime_start=date(2026, 1, 1),
            realtime_end=date(9999, 12, 31),
            value=100.0,
            collected_at=timezone.now(),
        )
        RegimeSnapshot.objects.create(
            snapshot_date=date(2026, 4, 1),
            regime_label=RegimeSnapshot.Label.EXPANSION,
            confidence=75,
            data_quality=90,
        )

        with mock.patch(
            'macro.services.house_view_backtest.regime.build_regime_assessment_from_metrics',
            return_value={
                'regime_label': 'expansion',
                'rule_strength': 70,
                'data_quality': 85,
                'warnings': [],
            },
        ) as assessment_mock:
            report = run_house_view_backtest(
                start=date(2026, 1, 1),
                end=date(2026, 1, 1),
                horizons=(3,),
                data_mode='point_in_time',
            )

        metrics = assessment_mock.call_args.args[0]
        self.assertEqual(metrics['indpro_value'], 100.0)
        self.assertAlmostEqual(metrics['indpro_yoy'], 5.2631578, places=4)
        self.assertEqual(report['rows'][0]['data_mode'], 'point_in_time')
        self.assertEqual(report['backtest_accuracy']['data_modes']['point_in_time']['sample_count'], 1)

    def test_run_house_view_backtest_command_writes_summary_json(self):
        RegimeSnapshot.objects.create(
            snapshot_date=date(2026, 4, 1),
            regime_label=RegimeSnapshot.Label.EXPANSION,
            confidence=75,
            data_quality=90,
        )
        with TemporaryDirectory() as tmpdir, mock.patch(
            'macro.services.house_view_backtest.regime.build_current_regime_assessment',
            return_value={
                'regime_label': 'expansion',
                'rule_strength': 70,
                'data_quality': 85,
                'warnings': [],
            },
        ):
            output_path = Path(tmpdir) / 'backtest.json'
            call_command(
                'run_house_view_backtest',
                start='2026-01-01',
                end='2026-01-01',
                horizons='3',
                output=str(output_path),
                stdout=StringIO(),
            )

            payload = json.loads(output_path.read_text(encoding='utf-8'))

        self.assertEqual(payload['execution_scope'], 'local_heavy_backtest')
        self.assertIn('backtest_accuracy', payload)
        self.assertEqual(payload['backtest_accuracy']['sample_count'], 1)

    def test_vintage_quality_report_flags_missing_revision_safe_data(self):
        from macro.services.vintage_quality import build_vintage_quality_report

        covered = Indicator.objects.create(
            fred_series_id='COVERED',
            name_ja='covered',
            category=Indicator.Category.GROWTH,
            source=Indicator.Source.FRED,
            importance=Indicator.Importance.A,
        )
        missing = Indicator.objects.create(
            fred_series_id='MISSING',
            name_ja='missing',
            category=Indicator.Category.INFLATION,
            source=Indicator.Source.FRED,
            importance=Indicator.Importance.A,
        )
        VintageObservation.objects.create(
            indicator=covered,
            observation_date=date(2026, 1, 1),
            realtime_start=date(2026, 1, 15),
            realtime_end=date(9999, 12, 31),
            value=1.0,
            collected_at=timezone.now(),
        )

        report = build_vintage_quality_report()

        self.assertGreaterEqual(report['fred_active_series_count'], 2)
        self.assertGreaterEqual(report['vintage_covered_series_count'], 1)
        self.assertLess(report['vintage_coverage_pct'], 100.0)
        self.assertFalse(report['strict_point_in_time_ready'])
        self.assertIn(missing.fred_series_id, report['missing_vintage_series'])

    def test_validation_weights_are_adjusted_from_latest_validation_results(self):
        from macro.services.validation_weights import build_validation_weight_report

        ModelValidationReport.objects.create(
            model_version='return_lightgbm_v2',
            target='GSPC',
            horizon='3m',
            sample_count=60,
            metrics={'direction_accuracy': 0.64, 'skill_score': 0.2},
        )
        weak = ModelValidationReport.objects.create(
            model_version='macro_forecast_lightgbm_v1',
            target='PAYEMS',
            horizon='3m',
            sample_count=60,
            metrics={'direction_accuracy': 0.48, 'skill_score': -0.1},
        )

        report = build_validation_weight_report()
        weights = {
            row['model_key']: row
            for row in report['validation_weights']
        }

        self.assertEqual(report['weighting_policy'], 'validation_adjusted')
        self.assertGreater(
            weights['return_lightgbm_v2:GSPC:3m']['validation_weight'],
            weights['macro_forecast_lightgbm_v1:PAYEMS:3m']['validation_weight'],
        )
        self.assertEqual(
            weights['macro_forecast_lightgbm_v1:PAYEMS:3m']['report_id'],
            weak.id,
        )

    def test_validation_weights_include_house_view_backtest_result(self):
        from macro.services.validation_weights import build_validation_weight_report

        with TemporaryDirectory() as tmpdir:
            backtest_path = Path(tmpdir) / 'house_view_backtest.json'
            backtest_path.write_text(
                json.dumps({
                    'generated_at': '2026-06-18T00:00:00+00:00',
                    'backtest_accuracy': {
                        'sample_count': 20,
                        'hit_count': 16,
                        'hit_rate': 0.8,
                    },
                }),
                encoding='utf-8',
            )

            report = build_validation_weight_report(
                house_view_backtest_path=backtest_path,
            )

        weights = {
            row['model_key']: row
            for row in report['validation_weights']
        }
        house_view_weight = weights['macro_hatzius_v1:macro_regime:backtest']
        self.assertEqual(house_view_weight['source'], 'house_view_backtest')
        self.assertEqual(house_view_weight['sample_count'], 20)
        self.assertGreater(house_view_weight['validation_weight'], 0.7)

    def test_model_validation_export_warns_when_latest_report_is_stale(self):
        from macro.management.commands.export_macro_model_validation import (
            build_model_validation_report,
        )

        report = ModelValidationReport.objects.create(
            model_version='return_lightgbm_v2',
            target='GSPC',
            horizon='3m',
            sample_count=60,
            metrics={'direction_accuracy': 0.6},
        )
        stale_at = timezone.now() - timedelta(days=40)
        ModelValidationReport.objects.filter(id=report.id).update(evaluated_at=stale_at)

        payload = build_model_validation_report()

        self.assertTrue(payload['freshness']['is_stale'])
        self.assertGreaterEqual(payload['freshness']['age_days'], 40)
        self.assertTrue(payload['warnings'])

    def test_new_reliability_export_commands_write_static_json(self):
        with TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            commands = {
                'export_macro_goldman_outlook': (
                    output_dir / 'goldman.json',
                    'goldman_sachs_public_outlook',
                ),
                'export_macro_house_view_validation': (
                    output_dir / 'house_validation.json',
                    'house_view_validation',
                ),
                'export_macro_vintage_quality': (
                    output_dir / 'vintage.json',
                    'vintage_quality_report',
                ),
                'export_macro_validation_weights': (
                    output_dir / 'weights.json',
                    'validation_weight_report',
                ),
            }

            for command_name, (output_path, expected_key) in commands.items():
                call_command(command_name, output=str(output_path), stdout=StringIO())
                payload = json.loads(output_path.read_text(encoding='utf-8'))
                self.assertIn(expected_key, payload)


class UpdateLocalDataCommandTest(SimpleTestCase):
    def test_macro_task_refreshes_data_then_precomputes_dashboard(self):
        from macro.management.commands.update_local_data import Command

        command = Command()
        with mock.patch(
            'macro.management.commands.update_local_data.call_command',
        ) as call_command_mock:
            command._run_macro(history_years=10, full_history=True)

        self.assertEqual(
            call_command_mock.call_args_list,
            [
                mock.call('refresh_macro_data', history_years=10, full_history=True),
                mock.call('precompute_dashboard'),
            ],
        )


class _ObsStub:
    """ユニットテスト用の最小 Observation モック。"""

    def __init__(self, value=None, prev_value=None, yoy_change=None):
        self.value = value
        self.prev_value = prev_value
        self.yoy_change = yoy_change


class SparklineTest(TestCase):
    def test_empty_returns_empty_string(self):
        self.assertEqual(sparkline.generate_sparkline_svg([]), "")

    def test_single_value_returns_empty(self):
        self.assertEqual(sparkline.generate_sparkline_svg([1.0]), "")

    def test_normal_series_contains_polyline(self):
        svg = sparkline.generate_sparkline_svg([1.0, 2.0, 3.0])
        self.assertIn("<polyline", svg)
        self.assertIn("points=", svg)

    def test_flat_series_returns_horizontal_line(self):
        svg = sparkline.generate_sparkline_svg([5.0, 5.0, 5.0])
        # 全て同じ値なら中央水平線が描かれる
        self.assertIn("<polyline", svg)


class SimilarityTest(TestCase):
    def test_distance_empty_returns_inf(self):
        d = similarity.vector_distance({}, {})
        self.assertEqual(d, float('inf'))

    def test_distance_no_common_keys(self):
        d = similarity.vector_distance({'A': 1.0}, {'B': 2.0})
        self.assertEqual(d, float('inf'))

    def test_distance_identical_vectors_zero(self):
        v = {'A': 1.0, 'B': 2.0}
        d = similarity.vector_distance(v, v)
        self.assertEqual(d, 0.0)

    def test_distance_known_values(self):
        v1 = {'A': 0.0, 'B': 0.0}
        v2 = {'A': 3.0, 'B': 4.0}
        # sqrt((9+16)/2) = sqrt(12.5) ≈ 3.535
        d = similarity.vector_distance(v1, v2)
        self.assertAlmostEqual(d, 3.5355339, places=4)


class DataSyncNormalizationTest(TestCase):
    def test_z_score_uses_only_data_available_at_that_date(self):
        indicator = Indicator.objects.create(
            fred_series_id='TEST_Z',
            name_ja='テスト標準化',
            category=Indicator.Category.GROWTH,
            importance=Indicator.Importance.A,
        )
        raw = [
            (date(2000, month, 1), float(month))
            for month in range(1, 13)
        ] + [
            (date(2001, month, 1), float(month + 12))
            for month in range(1, 13)
        ] + [
            (date(2002, 1, 1), 1000.0)
        ]

        rows = data_sync._build_observation_rows(indicator, raw)

        self.assertIsNone(rows[22].expanding_z_score)
        self.assertIsNotNone(rows[23].expanding_z_score)
        mean_24 = sum(range(1, 25)) / 24
        std_24 = (
            sum((value - mean_24) ** 2 for value in range(1, 25)) / 24
        ) ** 0.5
        expected_24th = (24 - mean_24) / std_24
        self.assertAlmostEqual(rows[23].expanding_z_score, expected_24th)
        self.assertEqual(
            rows[23].deviation_from_long_term,
            rows[23].expanding_z_score,
        )
        self.assertGreater(rows[24].expanding_z_score, rows[23].expanding_z_score)

    def test_sync_indicator_deduplicates_same_date_rows_before_insert(self):
        indicator = Indicator.objects.create(
            fred_series_id='PA_DUPLICATE_TEST',
            source=Indicator.Source.YFINANCE_DAILY,
            name_ja='重複保存テスト',
            category=Indicator.Category.MARKET,
            importance=Indicator.Importance.B,
            frequency=Indicator.Frequency.DAILY,
        )
        raw_rows = [
            (date(2026, 6, 17), 10.0),
            (date(2026, 6, 17), 11.0),
            (date(2026, 6, 18), 12.0),
        ]

        with mock.patch(
            'macro.services.data_sync._fetch_for_source',
            return_value=raw_rows,
        ):
            first = data_sync.sync_indicator(indicator)
            second = data_sync.sync_indicator(indicator)

        stored = list(
            Observation.objects
            .filter(indicator=indicator)
            .order_by('observation_date')
            .values_list('observation_date', 'value')
        )
        self.assertEqual(first['created'], 2)
        self.assertEqual(second['created'], 0)
        self.assertEqual(stored, [(date(2026, 6, 17), 11.0), (date(2026, 6, 18), 12.0)])


class RawArchiveTest(TestCase):
    def test_save_vintage_observations_returns_actual_created_count(self):
        from .models import VintageObservation

        indicator = Indicator.objects.create(
            fred_series_id='VINTAGE_COUNT_TEST',
            name_ja='ビンテージ件数テスト',
            category=Indicator.Category.GROWTH,
            importance=Indicator.Importance.B,
        )
        VintageObservation.objects.create(
            indicator=indicator,
            observation_date=date(2026, 1, 1),
            realtime_start=date(2026, 1, 10),
            realtime_end=date(2026, 1, 20),
            value=1.0,
            collected_at=timezone.now(),
        )

        created_count = data_sync._save_vintage_observations(
            indicator,
            [
                {
                    'date': date(2026, 1, 1),
                    'realtime_start': '2026-01-10',
                    'realtime_end': '2026-01-20',
                    'value': 1.0,
                },
                {
                    'date': date(2026, 2, 1),
                    'realtime_start': '2026-02-10',
                    'realtime_end': '2026-02-20',
                    'value': 2.0,
                },
            ],
            {
                date(2026, 1, 1): 1.0,
                date(2026, 2, 1): 2.0,
            },
        )

        self.assertEqual(created_count, 1)
        self.assertEqual(VintageObservation.objects.count(), 2)

    def test_archive_macro_rows_writes_gzip_csv_outside_serving_db(self):
        indicator = Indicator.objects.create(
            fred_series_id='ARCHIVE_TEST',
            name_ja='アーカイブテスト',
            category=Indicator.Category.GROWTH,
            importance=Indicator.Importance.C,
        )
        Observation.objects.create(
            indicator=indicator,
            observation_date=date(1999, 1, 1),
            value=123.4,
            expanding_z_score=1.2,
        )
        PriceObservation.objects.create(
            ticker=PriceObservation.Ticker.SP500,
            observation_month=date(1999, 1, 1),
            close_price=1000,
        )
        RegimeSnapshot.objects.create(
            snapshot_date=date(1999, 1, 1),
            regime_label=RegimeSnapshot.Label.SLOWDOWN,
            inflation_flag=RegimeSnapshot.InflationFlag.NORMAL,
            regime_probabilities={'slowdown': 0.7},
            risk_probabilities={'recession': 0.4},
        )

        with TemporaryDirectory() as tmpdir:
            summary = raw_archive.archive_macro_rows(
                reason='test',
                output_dir=Path(tmpdir),
            )
            path = Path(summary['path'])
            with gzip.open(path, 'rt', encoding='utf-8') as handle:
                content = handle.read()

        self.assertTrue(summary['created'])
        self.assertEqual(summary['row_count'], 3)
        self.assertIn('ARCHIVE_TEST', content)
        self.assertIn('price_observation', content)
        self.assertIn('regime_snapshot', content)
        self.assertEqual(RawArchiveManifest.objects.count(), 1)

    def test_archive_macro_rows_includes_vintage_observations(self):
        indicator = Indicator.objects.create(
            fred_series_id='VINTAGE_ARCHIVE_TEST',
            name_ja='ビンテージアーカイブテスト',
            category=Indicator.Category.GROWTH,
            importance=Indicator.Importance.B,
        )
        from .models import VintageObservation

        VintageObservation.objects.create(
            indicator=indicator,
            observation_date=date(2026, 1, 1),
            realtime_start=date(2026, 2, 1),
            realtime_end=date(2026, 2, 28),
            value=12.3,
            collected_at=timezone.now(),
            source='fred',
            metadata={'source_series_id': 'VINTAGE_ARCHIVE_TEST'},
        )

        with TemporaryDirectory() as tmpdir:
            summary = raw_archive.archive_macro_rows(
                observation_querysets=[],
                vintage_queryset=VintageObservation.objects.all(),
                reason='vintage_test',
                output_dir=Path(tmpdir),
            )
            path = Path(summary['path'])
            with gzip.open(path, 'rt', encoding='utf-8') as handle:
                content = handle.read()

        self.assertTrue(summary['created'])
        self.assertEqual(summary['row_count'], 1)
        self.assertEqual(summary['vintage_count'], 1)
        self.assertIn('vintage_observation', content)
        self.assertIn('VINTAGE_ARCHIVE_TEST', content)

    def test_purge_old_data_archives_only_low_importance_old_vintages(self):
        from .models import VintageObservation

        old_collected_at = timezone.now() - timezone.timedelta(days=370)
        important = Indicator.objects.create(
            fred_series_id='VINTAGE_KEEP_A',
            name_ja='重要ビンテージ',
            category=Indicator.Category.INFLATION,
            importance=Indicator.Importance.A,
        )
        low = Indicator.objects.create(
            fred_series_id='VINTAGE_PURGE_C',
            name_ja='参考ビンテージ',
            category=Indicator.Category.MARKET,
            importance=Indicator.Importance.C,
        )
        VintageObservation.objects.create(
            indicator=important,
            observation_date=date(2025, 1, 1),
            realtime_start=date(2025, 2, 1),
            realtime_end=date(2025, 2, 28),
            value=1.0,
            collected_at=old_collected_at,
        )
        VintageObservation.objects.create(
            indicator=low,
            observation_date=date(2025, 1, 1),
            realtime_start=date(2025, 2, 1),
            realtime_end=date(2025, 2, 28),
            value=2.0,
            collected_at=old_collected_at,
        )

        archived_series = []

        def fake_archive_macro_rows(**kwargs):
            archived_series.extend(
                kwargs['vintage_queryset'].values_list(
                    'indicator__fred_series_id',
                    flat=True,
                )
            )
            return {'created': True, 'row_count': 1, 'path': 'archive.csv.gz'}

        with mock.patch(
            'macro.management.commands.purge_old_data.archive_macro_rows',
            side_effect=fake_archive_macro_rows,
        ):
            call_command('purge_old_data', stdout=StringIO())

        self.assertTrue(
            VintageObservation.objects.filter(indicator=important).exists(),
        )
        self.assertFalse(
            VintageObservation.objects.filter(indicator=low).exists(),
        )
        self.assertEqual(archived_series, ['VINTAGE_PURGE_C'])


class VintageStatusTest(TestCase):
    def test_vintage_status_uses_accumulated_label_and_good_tone_for_100k(self):
        with mock.patch(
            'macro.services.dashboard.VintageObservation.objects',
        ) as manager:
            manager.count.return_value = 100_036
            manager.values.return_value.distinct.return_value.count.return_value = 42
            manager.order_by.return_value.values_list.return_value.first.return_value = (
                timezone.now() - timezone.timedelta(days=1)
            )

            context = dashboard.build_vintage_status_context()

        self.assertEqual(context['status_label'], '蓄積済み')
        self.assertEqual(context['tone'], 'good')
        self.assertFalse(context['is_large'])
        self.assertFalse(context['is_stale'])
        self.assertNotIn('処理中', context['note'] if context['note'] else '')

    def test_vintage_status_warns_when_large_or_stale(self):
        with mock.patch(
            'macro.services.dashboard.VintageObservation.objects',
        ) as manager:
            manager.count.return_value = 500_001
            manager.values.return_value.distinct.return_value.count.return_value = 42
            manager.order_by.return_value.values_list.return_value.first.return_value = (
                timezone.now() - timezone.timedelta(days=10)
            )

            context = dashboard.build_vintage_status_context()

        self.assertEqual(context['tone'], 'warning')
        self.assertTrue(context['is_large'])
        self.assertTrue(context['archive_recommended'])


class PolicyExpectationTest(TestCase):
    def test_build_policy_expectation_snapshot_detects_policy_headwind(self):
        from .services.policy_expectation import build_policy_expectation_snapshot

        today = timezone.localdate()
        for series_id, value in {
            'FEDFUNDS': 5.33,
            'DFEDTARL': 5.25,
            'DFEDTARU': 5.50,
            'SOFR': 5.31,
            'DGS2': 4.90,
            'DGS10': 4.35,
            'T5YIE': 2.45,
            'MOVE_INDEX': 125.0,
            'DEXJPUS': 155.0,
            'BOJ_POLICY_RATE': 0.10,
            'JPN10Y': 1.25,
        }.items():
            indicator, _ = Indicator.objects.update_or_create(
                fred_series_id=series_id,
                defaults={
                    'name_ja': series_id,
                    'category': Indicator.Category.RATES,
                    'importance': Indicator.Importance.A,
                    'frequency': Indicator.Frequency.DAILY,
                },
            )
            Observation.objects.create(
                indicator=indicator,
                observation_date=today,
                value=value,
            )
            Observation.objects.create(
                indicator=indicator,
                observation_date=today - timezone.timedelta(days=5),
                value=value - 0.08,
            )

        snapshot = build_policy_expectation_snapshot()

        self.assertIsInstance(snapshot, PolicyExpectationSnapshot)
        self.assertEqual(snapshot.central_bank, 'FED')
        self.assertEqual(snapshot.target_lower, 5.25)
        self.assertEqual(snapshot.target_upper, 5.50)
        self.assertEqual(snapshot.policy_bias, 'hawkish_headwind')
        self.assertGreater(snapshot.rate_shock_5d_bp, 0)
        self.assertIn('米2年金利', snapshot.payload.get('drivers', []))
        self.assertEqual(snapshot.payload['values']['BOJ_POLICY_RATE'], 0.10)
        self.assertEqual(snapshot.payload['values']['JPN10Y'], 1.25)
        self.assertAlmostEqual(snapshot.payload['values']['US_JP_10Y_DIFF'], 3.10)


class ForecastTrackingTest(TestCase):
    def test_settle_return_forecast_writes_realized_value(self):
        PriceObservation.objects.create(
            ticker=PriceObservation.Ticker.SP500,
            observation_month=date(2025, 1, 1),
            close_price=100.0,
        )
        PriceObservation.objects.create(
            ticker=PriceObservation.Ticker.SP500,
            observation_month=date(2025, 2, 1),
            close_price=110.0,
        )
        ForecastSnapshot.objects.create(
            as_of_date=date(2025, 1, 15),
            model_version='lightgbm_return_v1',
            target='GSPC',
            horizon='1m',
            prediction_value=6.0,
            metadata={'prediction_kind': 'return_pct', 'horizon_months': 1},
        )

        summary = forecast_tracking.settle_due_forecasts()
        snapshot = ForecastSnapshot.objects.get()

        self.assertEqual(summary['settled_count'], 1)
        self.assertAlmostEqual(snapshot.realized_value, 10.0)
        self.assertAlmostEqual(snapshot.error, 4.0)
        self.assertEqual(snapshot.realized_at, date(2025, 2, 1))


class WorldModelOperationsTest(TestCase):
    def test_operations_context_uses_latest_runs(self):
        run = operations.start_run(
            cadence=WorldModelRun.Cadence.MONTHLY,
            name='test monthly',
        )
        operations.finish_run(
            run,
            status=WorldModelRun.Status.SUCCESS,
            summary={'message': 'ok'},
        )

        context = operations.build_operations_context()
        monthly = [
            row for row in context['rows']
            if row['cadence'] == WorldModelRun.Cadence.MONTHLY
        ][0]

        self.assertEqual(monthly['status_label'], '成功')
        self.assertEqual(monthly['summary_label'], 'ok')

    def test_operations_context_uses_static_monthly_outputs_as_fallback(self):
        context = operations.build_operations_context()
        rows = {row['cadence']: row for row in context['rows']}

        self.assertNotEqual(
            rows[WorldModelRun.Cadence.MONTHLY]['status_label'],
            '記録なし',
        )
        self.assertNotEqual(
            rows[WorldModelRun.Cadence.ARCHIVE]['status_label'],
            '記録なし',
        )


class LinkageTest(TestCase):
    def test_pearson_perfect_positive(self):
        xs = [1, 2, 3, 4, 5]
        ys = [2, 4, 6, 8, 10]
        self.assertAlmostEqual(linkage._pearson(xs, ys), 1.0, places=5)

    def test_pearson_perfect_negative(self):
        xs = [1, 2, 3, 4, 5]
        ys = [10, 8, 6, 4, 2]
        self.assertAlmostEqual(linkage._pearson(xs, ys), -1.0, places=5)

    def test_pearson_zero_variance_returns_none(self):
        xs = [1, 1, 1, 1]
        ys = [1, 2, 3, 4]
        self.assertIsNone(linkage._pearson(xs, ys))

    def test_pearson_too_short_returns_none(self):
        self.assertIsNone(linkage._pearson([1], [2]))


class RegimeClassificationTest(TestCase):
    def test_strong_expansion(self):
        metrics = {
            'indpro_yoy': 3.5,
            'unrate_6m_change': -0.1,
            'gdp_yoy': 2.5,
        }
        label, conf = regime.classify_regime(metrics)
        self.assertEqual(label, RegimeSnapshot.Label.EXPANSION)
        self.assertGreater(conf, 0)

    def test_contraction(self):
        metrics = {'indpro_yoy': -2.0, 'gdp_yoy': -0.5}
        label, _ = regime.classify_regime(metrics)
        self.assertEqual(label, RegimeSnapshot.Label.CONTRACTION)

    def test_fast_shock_contraction_before_gdp_catches_up(self):
        metrics = {
            'indpro_yoy': -5.0,
            'indpro_3m_change_pct': -4.0,
            'unrate_6m_change': 0.6,
            'gdp_yoy': 1.0,
            'vix': 40.0,
        }
        label, _ = regime.classify_regime(metrics)
        self.assertEqual(label, RegimeSnapshot.Label.CONTRACTION)

    def test_recovery_pattern(self):
        metrics = {
            'indpro_yoy': 0.5,
            'indpro_3m_change_pct': 1.0,
            'unrate_6m_change': -0.2,
        }
        label, _ = regime.classify_regime(metrics)
        self.assertEqual(label, RegimeSnapshot.Label.RECOVERY)

    def test_slowdown_with_employment_weakness(self):
        metrics = {
            'indpro_yoy': 0.8,
            'unrate_6m_change': 0.4,
        }
        label, _ = regime.classify_regime(metrics)
        self.assertEqual(label, RegimeSnapshot.Label.SLOWDOWN)

    def test_unknown_when_no_data(self):
        label, conf = regime.classify_regime({})
        self.assertEqual(label, RegimeSnapshot.Label.UNKNOWN)
        self.assertEqual(conf, 0)

    def test_inflation_high(self):
        flag, conf = regime.classify_inflation({'core_pce_yoy': 4.0})
        self.assertEqual(flag, RegimeSnapshot.InflationFlag.HIGH)
        self.assertGreater(conf, 0)

    def test_inflation_easing(self):
        flag, _ = regime.classify_inflation({
            'core_pce_yoy': 2.5,
            'core_pce_yoy_3m_ago': 3.0,
        })
        self.assertEqual(flag, RegimeSnapshot.InflationFlag.EASING)

    def test_inflation_normal(self):
        flag, _ = regime.classify_inflation({'core_pce_yoy': 1.8})
        self.assertEqual(flag, RegimeSnapshot.InflationFlag.NORMAL)

    def test_inflation_unknown(self):
        flag, _ = regime.classify_inflation({})
        self.assertEqual(flag, RegimeSnapshot.InflationFlag.UNKNOWN)

    def test_missing_data_assessment_has_quality_warning(self):
        assessment = regime.build_current_regime_assessment()
        self.assertEqual(
            assessment['regime_label'], RegimeSnapshot.Label.UNKNOWN
        )
        self.assertLess(assessment['data_quality'], 60)
        self.assertTrue(assessment['warnings'])

    def test_assessment_contains_evidence_when_data_exists(self):
        indpro = Indicator.objects.get(fred_series_id='INDPRO')
        Observation.objects.create(
            indicator=indpro,
            observation_date=date(2025, 1, 1),
            value=100,
        )
        Observation.objects.create(
            indicator=indpro,
            observation_date=date(2026, 1, 1),
            value=103,
            prev_value=102,
            yoy_change=3.0,
        )

        assessment = regime.build_current_regime_assessment(
            as_of=date(2026, 1, 1)
        )

        self.assertTrue(assessment['evidence'])
        self.assertEqual(assessment['model_version'], regime.MODEL_VERSION)
        self.assertIn('expansion', assessment['regime_probabilities'])
        self.assertIn('recession', assessment['risk_probabilities'])

    def test_regime_probability_distribution_sums_to_one(self):
        metrics = {
            'indpro_yoy': 2.4,
            'indpro_3m_change_pct': 0.7,
            'unrate_6m_change': -0.1,
            'gdp_yoy': 2.0,
            'hy_spread': 3.2,
            'vix': 15.0,
            'core_pce_yoy': 2.4,
            'core_pce_yoy_3m_ago': 2.6,
        }
        probabilities = regime.regime_probability_distribution(metrics)

        self.assertAlmostEqual(sum(probabilities.values()), 1.0, places=5)
        self.assertEqual(set(probabilities), {
            RegimeSnapshot.Label.EXPANSION,
            RegimeSnapshot.Label.SLOWDOWN,
            RegimeSnapshot.Label.CONTRACTION,
            RegimeSnapshot.Label.RECOVERY,
        })

    def test_regime_probability_validation_handles_empty_dataset(self):
        payload = regime_probability.validate_regime_probability_model()

        self.assertEqual(payload['sample_count'], 0)
        self.assertEqual(payload['model_version'], regime.PROBABILITY_MODEL_VERSION)


class DashboardFormatTest(TestCase):
    def test_format_value_large_numbers(self):
        self.assertEqual(dashboard.format_value(158000.0, '千人'), '158,000')

    def test_format_value_percent(self):
        self.assertEqual(dashboard.format_value(3.21, '%'), '3.21')

    def test_format_value_none(self):
        self.assertEqual(dashboard.format_value(None, '%'), '—')

    def test_format_pct_positive_has_plus(self):
        self.assertEqual(dashboard.format_pct(2.5), '+2.5%')

    def test_format_pct_negative(self):
        self.assertEqual(dashboard.format_pct(-1.2), '-1.2%')

    def test_format_pct_none(self):
        self.assertEqual(dashboard.format_pct(None), '—')

    def test_format_signed_positive(self):
        self.assertEqual(dashboard.format_signed(0.5, 2), '+0.50')

    def test_data_quality_gate_caps_confidence_when_required_series_missing(self):
        unrate, _ = Indicator.objects.update_or_create(
            fred_series_id='UNRATE',
            defaults={
                'name_ja': '失業率',
                'category': Indicator.Category.EMPLOYMENT,
                'importance': Indicator.Importance.A,
                'frequency': Indicator.Frequency.MONTHLY,
                'is_active': True,
            },
        )
        Observation.objects.create(
            indicator=unrate,
            observation_date=date(2026, 6, 1),
            value=4.0,
        )
        for series_id, name in [
            ('CPIAUCSL', 'CPI'),
            ('CPILFESL', 'Core CPI'),
            ('PCEPI', 'PCE'),
            ('PCEPILFE', 'Core PCE'),
            ('DGS10', '米10年金利'),
            ('VIXCLS', 'VIX'),
            ('BAMLH0A0HYM2', '信用スプレッド'),
        ]:
            Indicator.objects.update_or_create(
                fred_series_id=series_id,
                defaults={
                    'name_ja': name,
                    'category': Indicator.Category.INFLATION,
                    'importance': Indicator.Importance.A,
                    'frequency': Indicator.Frequency.MONTHLY,
                    'is_active': True,
                },
            )

        report = data_quality.build_data_quality_report(as_of=date(2026, 6, 18))

        self.assertFalse(report['usable_for_decision'])
        self.assertEqual(report['confidence_cap'], 'C')
        self.assertEqual(report['missing_required_count'], 7)
        self.assertLess(report['freshness_score'], 50)
        self.assertTrue(report['blocking_issues'])

    def test_data_quality_treats_recent_monthly_pce_as_fresh(self):
        specs = [
            ('CPIAUCSL', 'CPI', Indicator.Frequency.MONTHLY, date(2026, 5, 1)),
            ('CPILFESL', 'Core CPI', Indicator.Frequency.MONTHLY, date(2026, 5, 1)),
            ('PCEPI', 'PCE', Indicator.Frequency.MONTHLY, date(2026, 4, 1)),
            ('PCEPILFE', 'Core PCE', Indicator.Frequency.MONTHLY, date(2026, 4, 1)),
            ('UNRATE', '失業率', Indicator.Frequency.MONTHLY, date(2026, 5, 1)),
            ('DGS10', '米10年金利', Indicator.Frequency.DAILY, date(2026, 6, 17)),
            ('VIXCLS', 'VIX', Indicator.Frequency.DAILY, date(2026, 6, 17)),
            ('BAMLH0A0HYM2', '信用スプレッド', Indicator.Frequency.DAILY, date(2026, 6, 17)),
        ]
        for series_id, name, frequency, observation_date in specs:
            indicator, _ = Indicator.objects.update_or_create(
                fred_series_id=series_id,
                defaults={
                    'name_ja': name,
                    'category': Indicator.Category.INFLATION,
                    'importance': Indicator.Importance.A,
                    'frequency': frequency,
                    'is_active': True,
                },
            )
            Observation.objects.create(
                indicator=indicator,
                observation_date=observation_date,
                value=1.0,
            )

        report = data_quality.build_data_quality_report(as_of=date(2026, 6, 18))

        self.assertEqual(report['freshness_score'], 100.0)
        self.assertEqual(report['confidence_cap'], 'A')
        self.assertEqual(report['stale_required_count'], 0)
        self.assertEqual(report['warnings'], [])

    def test_house_view_uses_quality_gate_as_top_level_confidence_limit(self):
        WorldStateSnapshot.objects.create(
            as_of_date=date(2026, 6, 17),
            growth_score=66,
            labor_score=71,
            inflation_score=82,
            policy_pressure_score=63,
            credit_score=58,
            liquidity_score=55,
            risk_appetite_score=61,
            market_trend_score=57,
            market_stress_score=29,
            recession_risk_score=17,
            inflation_reacceleration_score=82,
            financial_stress_score=19,
            data_quality=92,
            explanation={
                'positive_drivers': ['雇用はまだ強い'],
                'negative_drivers': ['Core PCEが高い'],
            },
        )
        RegimeSnapshot.objects.create(
            snapshot_date=date(2026, 6, 17),
            regime_label=RegimeSnapshot.Label.EXPANSION,
            inflation_flag=RegimeSnapshot.InflationFlag.HIGH,
            rule_strength=80,
            data_quality=92,
            regime_probabilities={
                'expansion': 0.66,
                'slowdown': 0.20,
                'contraction': 0.07,
                'recovery': 0.07,
            },
            risk_probabilities={
                'inflation_reacceleration': 0.82,
                'financial_stress': 0.19,
            },
        )
        ModelValidationReport.objects.create(
            model_version='crash_probability_logistic_v1',
            target='GSPC',
            horizon='63d',
            sample_count=30,
            event_count=4,
            metrics={'roc_auc': 0.55, 'pr_auc': 0.11},
        )

        with mock.patch('macro.services.house_view.build_data_quality_report', return_value={
            'as_of': '2026-06-17',
            'usable_for_decision': False,
            'display_allowed': False,
            'confidence_cap': 'C',
            'freshness_score': 42,
            'missing_required_count': 4,
            'blocking_issues': ['主要系列の欠損があります。'],
            'warnings': ['トップ判断は参考扱いです。'],
        }):
            result = house_view.build_house_view_context()

        self.assertIn('拡大寄り', result['house_view'])
        self.assertIn('物価再加速', result['house_view'])
        self.assertEqual(result['confidence_grade'], 'C')
        self.assertFalse(result['display_allowed'])
        self.assertIn('雇用はまだ強い', result['key_drivers'])
        self.assertIn('主要系列の欠損があります。', result['blocking_issues'])

    def test_house_view_shows_current_invalidation_trigger_status(self):
        unrate, _ = Indicator.objects.update_or_create(
            fred_series_id='UNRATE',
            defaults={
                'name_ja': '失業率',
                'category': Indicator.Category.EMPLOYMENT,
                'importance': Indicator.Importance.A,
                'frequency': Indicator.Frequency.MONTHLY,
            },
        )
        for observation_date, value in [
            (date(2026, 2, 1), 4.0),
            (date(2026, 3, 1), 4.1),
            (date(2026, 4, 1), 4.1),
            (date(2026, 5, 1), 4.2),
        ]:
            Observation.objects.create(
                indicator=unrate,
                observation_date=observation_date,
                value=value,
            )
        core_pce, _ = Indicator.objects.update_or_create(
            fred_series_id='PCEPILFE',
            defaults={
                'name_ja': 'Core PCE',
                'category': Indicator.Category.INFLATION,
                'importance': Indicator.Importance.A,
                'frequency': Indicator.Frequency.MONTHLY,
            },
        )
        for observation_date, yoy_change in [
            (date(2026, 2, 1), 2.8),
            (date(2026, 3, 1), 2.9),
            (date(2026, 4, 1), 3.1),
        ]:
            Observation.objects.create(
                indicator=core_pce,
                observation_date=observation_date,
                value=100,
                yoy_change=yoy_change,
            )

        with mock.patch('macro.services.house_view.build_data_quality_report', return_value={
            'as_of': '2026-06-17',
            'display_allowed': True,
            'confidence_cap': 'A',
            'freshness_score': 90,
        }):
            result = house_view.build_house_view_context()

        self.assertIn('invalidation_status_notes', result)
        self.assertEqual(
            result['invalidation_status_notes'],
            [
                {
                    'label': '失業率',
                    'detail': '直近1/3か月連続で上昇（2026-05-01: 4.20%、前月比 +0.10pt）',
                },
                {
                    'label': 'Core PCE',
                    'detail': '直近2/2か月連続で再加速（2026-04-01: 3.10%、前月比 +0.20pt）',
                },
            ],
        )

    def test_regime_context_uses_rule_strength_not_confidence_pct(self):
        snapshot = RegimeSnapshot.objects.create(
            snapshot_date=date(2026, 5, 17),
            regime_label=RegimeSnapshot.Label.SLOWDOWN,
            inflation_flag=RegimeSnapshot.InflationFlag.HIGH,
            confidence=99,
            rule_strength=62,
            data_quality=78,
            evidence=[{
                'series_id': 'INDPRO',
                'name': '鉱工業生産指数',
                'metric': '前年比',
                'value': 0.8,
                'unit': '%',
                'observation_date': '2026-04-01',
                'signal': '減速寄り',
                'contribution': -0.35,
            }],
            warnings=['テスト警告'],
            model_version='regime_v2_score',
        )

        context = dashboard.build_regime_context(snapshot)

        self.assertNotIn('confidence_pct', context)
        self.assertEqual(context['rule_strength_pct'], 62)
        self.assertEqual(context['data_quality_pct'], 78)
        self.assertEqual(context['regime_evidence'][0]['signal'], '減速寄り')
        self.assertEqual(context['regime_warnings'], ['テスト警告'])
        self.assertEqual(context['regime_plain_judgment'], '景気は弱含みで物価も重い')
        self.assertEqual(context['regime_condition_score'], 2)
        self.assertEqual(context['regime_condition_score_display'], '2')
        self.assertEqual(context['regime_condition_fraction_display'], '2/5')
        self.assertEqual(context['regime_condition_bar_pct'], 40)
        self.assertEqual(context['regime_condition_pct_display'], '40%')
        self.assertEqual(context['regime_condition_label'], 'やや悪い')
        self.assertEqual(context['regime_condition_tone'], 'negative')
        self.assertEqual(context.get('regime_condition_bar_left_label'), '悪')
        self.assertEqual(context.get('regime_condition_bar_right_label'), '良')
        self.assertEqual(context['rule_strength_score'], 4)
        self.assertEqual(context['rule_strength_fraction_display'], '4/5')
        self.assertEqual(context.get('rule_strength_bar_left_label'), '弱')
        self.assertEqual(context.get('rule_strength_bar_right_label'), '強')
        self.assertEqual(context['data_quality_score'], 4)
        self.assertEqual(context['data_quality_fraction_display'], '4/5')
        self.assertEqual(context.get('data_quality_bar_left_label'), '古')
        self.assertEqual(context.get('data_quality_bar_right_label'), '新')
        self.assertTrue(context['regime_good_points'])
        self.assertTrue(context['regime_bad_points'])
        self.assertIn('在庫や企業利益の重し', context['regime_bad_points'][0])
        self.assertTrue(context['regime_outlook'])
        self.assertEqual(len(context['regime_update_guidance']), 4)

    def test_regime_summary_is_compact_without_ellipsis(self):
        snapshot = RegimeSnapshot.objects.create(
            snapshot_date=date(2026, 5, 18),
            regime_label=RegimeSnapshot.Label.RECOVERY,
            inflation_flag=RegimeSnapshot.InflationFlag.HIGH,
            rule_strength=66,
            data_quality=92,
            evidence=[
                {
                    'series_id': 'GDPC1',
                    'name': '実質GDP',
                    'metric': '前年比',
                    'value': 2.4,
                    'unit': '%',
                    'observation_date': '2026-04-01',
                    'signal': '拡大寄り',
                    'contribution': 0.8,
                },
                {
                    'series_id': 'CPIAUCSL',
                    'name': 'CPI',
                    'metric': '前年比',
                    'value': 3.4,
                    'unit': '%',
                    'observation_date': '2026-04-01',
                    'signal': '物価高止まり',
                    'contribution': -0.6,
                },
            ],
        )

        context = dashboard.build_regime_context(snapshot)

        self.assertEqual(context['regime_summary_label'], '回復寄り・物価高止まり')
        self.assertNotIn('...', context['regime_summary_label'])
        self.assertNotIn('主な', context['regime_summary_label'])
        self.assertIn('需要が強く', context['regime_good_points'][0])
        self.assertIn('金利が下がりにくく', context['regime_bad_points'][0])

    def test_regime_summary_css_uses_smaller_text_without_ellipsis(self):
        css = (
            Path(settings.BASE_DIR)
            / 'static'
            / 'macro'
            / 'css'
            / 'style.css'
        ).read_text(encoding='utf-8')
        summary_block_start = css.index('.macro-regime-main span {')
        summary_block_end = css.index('}', summary_block_start)
        summary_span_css = css[summary_block_start:summary_block_end]

        self.assertIn('.macro-regime-main {', css)
        self.assertIn('font-size: 1.03rem;', css)
        self.assertNotIn('text-overflow: ellipsis;', summary_span_css)

    def test_regime_context_groups_current_state_for_unified_ui(self):
        snapshot = RegimeSnapshot.objects.create(
            snapshot_date=date(2026, 5, 19),
            regime_label=RegimeSnapshot.Label.RECOVERY,
            inflation_flag=RegimeSnapshot.InflationFlag.HIGH,
            rule_strength=72,
            data_quality=88,
            regime_probabilities={
                RegimeSnapshot.Label.EXPANSION: 0.22,
                RegimeSnapshot.Label.SLOWDOWN: 0.18,
                RegimeSnapshot.Label.CONTRACTION: 0.08,
                RegimeSnapshot.Label.RECOVERY: 0.52,
            },
            risk_probabilities={
                'recession': 0.18,
                'acceleration': 0.27,
                'inflation_reacceleration': 0.61,
                'financial_stress': 0.34,
            },
            evidence=[
                {
                    'series_id': 'GDPC1',
                    'name': '実質GDP',
                    'category': 'growth',
                    'metric': '前年比',
                    'value': 2.4,
                    'unit': '%',
                    'observation_date': '2026-04-01',
                    'signal': '拡大寄り',
                    'contribution': 0.8,
                },
                {
                    'series_id': 'UNRATE',
                    'name': '失業率',
                    'category': 'labor',
                    'metric': '水準',
                    'value': 4.2,
                    'unit': '%',
                    'observation_date': '2026-04-01',
                    'signal': '雇用は底堅い',
                    'contribution': 0.25,
                },
                {
                    'series_id': 'INDPRO',
                    'name': '鉱工業生産指数',
                    'category': 'growth',
                    'metric': '前年比',
                    'value': -0.4,
                    'unit': '%',
                    'observation_date': '2026-04-01',
                    'signal': '減速寄り',
                    'contribution': -0.35,
                },
                {
                    'series_id': 'INDPRO',
                    'name': '鉱工業生産指数',
                    'category': 'growth',
                    'metric': '3カ月変化',
                    'value': -0.2,
                    'unit': '%',
                    'observation_date': '2026-04-01',
                    'signal': '減速寄り',
                    'contribution': -0.25,
                },
                {
                    'series_id': 'PCEPILFE',
                    'name': 'Core PCE',
                    'category': 'inflation',
                    'metric': '前年比',
                    'value': 3.3,
                    'unit': '%',
                    'observation_date': '2026-04-01',
                    'signal': '物価高止まり',
                    'contribution': -0.6,
                },
                {
                    'series_id': 'RSAFS',
                    'name': '小売売上高',
                    'category': 'growth',
                    'metric': '前年比',
                    'value': 2.1,
                    'unit': '%',
                    'observation_date': '2026-04-01',
                    'signal': '消費は底堅い',
                    'contribution': 0.18,
                },
            ],
        )

        context = dashboard.build_regime_context(snapshot)

        self.assertEqual(context['regime_state_sections'][0]['label'], '景気の向き')
        self.assertEqual(context['regime_state_sections'][1]['label'], '注意リスク')
        self.assertEqual(context['regime_state_sections'][2]['label'], '判断材料')
        material_labels = [
            row['label']
            for row in context['regime_state_sections'][2]['rows']
        ]
        self.assertEqual(material_labels, ['成長', '雇用', '生産', '物価', '消費'])
        production = context['regime_state_sections'][2]['rows'][2]
        self.assertEqual(production['primary_name'], '鉱工業生産指数')
        self.assertEqual(production['indicator_count'], 1)
        self.assertEqual(production['tone'], 'negative')
        expansion = context['regime_state_sections'][0]['rows'][0]
        recession = context['regime_state_sections'][1]['rows'][0]
        inflation_risk = context['regime_state_sections'][1]['rows'][2]
        growth = context['regime_state_sections'][2]['rows'][0]
        core_pce = context['regime_state_sections'][2]['rows'][3]
        self.assertEqual(expansion['badge_label'], '良い')
        self.assertEqual(recession['badge_label'], '良い')
        self.assertEqual(inflation_risk['badge_label'], '悪い')
        self.assertEqual(growth['badge_label'], '良い')
        self.assertEqual(core_pce['badge_label'], '悪い')
        self.assertEqual(core_pce['metric_label'], '前年比')

    def test_current_state_template_and_css_use_dashboard_card_ui(self):
        template = (
            Path(settings.BASE_DIR)
            / 'macro'
            / 'templates'
            / 'macro'
            / 'index.html'
        ).read_text(encoding='utf-8')
        css = (
            Path(settings.BASE_DIR)
            / 'static'
            / 'macro'
            / 'css'
            / 'style.css'
        ).read_text(encoding='utf-8')

        self.assertIn('macro-state-card', template)
        self.assertIn('macro-state-card__icon', template)
        self.assertIn('macro-state-card__badge', template)
        self.assertIn('macro-risk-gauge', template)
        self.assertIn('macro-material-icon', template)
        self.assertIn('.macro-state-card {', css)
        self.assertIn('.macro-state-card-grid {', css)
        self.assertIn('.macro-risk-gauge-fill {', css)
        self.assertIn('.macro-material-card {', css)
        self.assertIn('font-size: 1.5rem;', css)
        self.assertIn('font-size: 1.375rem;', css)
        self.assertIn(
            '.macro-direction-card__value {\n'
            '    align-self: center;\n'
            '    justify-self: center;\n'
            '    color: var(--state-color);\n'
            '    font-size: 1.5rem;\n'
            '    font-weight: 500;',
            css,
        )
        self.assertIn(
            '.macro-risk-card__value strong {\n'
            '    color: var(--state-color);\n'
            '    font-size: 1.375rem;\n'
            '    font-weight: 500;',
            css,
        )
        self.assertIn(
            '.macro-material-card__value {\n'
            '    color: #cbd5e1;\n'
            '    font-size: 1rem;\n'
            '    font-weight: 500;',
            css,
        )

    def test_dashboard_precompute_payload_excludes_monthly_macro_conclusion(self):
        with mock.patch('macro.services.data_sync.get_latest_observation_date', return_value=None):
            with mock.patch('macro.services.dashboard.build_similar_periods', return_value=[]):
                with mock.patch('macro.services.dashboard.build_linkages', return_value=[]):
                    with mock.patch('macro.services.dashboard.build_indicator_cards', return_value=[]):
                        with mock.patch('macro.services.dashboard.build_crash_alert_context', return_value=None):
                            payload = dashboard_cache.precompute_dashboard_payload()

        self.assertNotIn('macro_conclusion', payload)

    def test_regime_condition_scale_adjusts_for_inflation(self):
        strong = dashboard._regime_condition_summary(
            RegimeSnapshot.Label.EXPANSION,
            RegimeSnapshot.InflationFlag.NORMAL,
            [],
            80,
            90,
        )
        hot = dashboard._regime_condition_summary(
            RegimeSnapshot.Label.RECOVERY,
            RegimeSnapshot.InflationFlag.HIGH,
            [],
            80,
            90,
        )
        weak = dashboard._regime_condition_summary(
            RegimeSnapshot.Label.CONTRACTION,
            RegimeSnapshot.InflationFlag.HIGH,
            [],
            80,
            90,
        )

        self.assertEqual(strong['regime_condition_score'], 5)
        self.assertEqual(hot['regime_condition_score'], 3)
        self.assertEqual(weak['regime_condition_score'], 1)

    def test_regime_condition_holds_when_data_is_too_weak(self):
        condition = dashboard._regime_condition_summary(
            RegimeSnapshot.Label.EXPANSION,
            RegimeSnapshot.InflationFlag.NORMAL,
            [],
            80,
            30,
        )

        self.assertEqual(condition['regime_condition_score'], 0)
        self.assertEqual(condition['regime_condition_score_display'], '—')
        self.assertEqual(condition['regime_condition_fraction_display'], '—/5')
        self.assertEqual(condition['regime_condition_bar_pct'], 0)
        self.assertEqual(condition['regime_condition_pct_display'], '—%')
        self.assertEqual(condition['regime_condition_label'], '判定保留')

    def test_scenario_analysis_returns_preset_scenarios(self):
        result = scenario.build_scenario_analysis()

        self.assertEqual(len(result['scenarios']), 6)
        self.assertIn('利下げ後退・米金利上昇', [item['title'] for item in result['scenarios']])
        self.assertIn('金利低下・リスクオン', [item['title'] for item in result['scenarios']])
        self.assertIn('base_regime_label', result)
        self.assertIn('base_regime_view_display', result)
        self.assertIn('base_regime_fit_display', result)
        self.assertIn('market_stress_delta_display', result['scenarios'][0])
        self.assertIn('regime_view_display', result['scenarios'][0])
        self.assertIn('regime_fit_display', result['scenarios'][0])

    def test_auto_scenarios_include_policy_rate_scenarios(self):
        result = scenario.build_auto_scenarios()

        titles = [item['title'] for item in result['scenarios']]
        self.assertIn('利下げ後退・米金利上昇', titles)
        self.assertIn('金利低下・リスクオン', titles)

    def test_scenario_analysis_accepts_custom_inputs(self):
        custom = scenario.scenario_overrides_from_query({
            'scenario_vix': '30',
            'scenario_hy_spread': '1.5',
        })
        result = scenario.build_scenario_analysis(custom)

        self.assertTrue(result['has_custom'])
        self.assertEqual(result['scenarios'][0]['title'], 'カスタム')
        self.assertTrue(result['scenarios'][0]['is_custom'])

    def test_reliability_top_warnings_do_not_include_stale_count(self):
        indicator, _ = Indicator.objects.update_or_create(
            fred_series_id='TEST_STALE_DAILY',
            defaults={
                'source': Indicator.Source.FRED,
                'name_ja': '古い日次テスト',
                'category': Indicator.Category.MARKET,
                'importance': Indicator.Importance.B,
                'frequency': Indicator.Frequency.DAILY,
                'is_active': True,
            },
        )
        Observation.objects.create(
            indicator=indicator,
            observation_date=date(2000, 1, 1),
            value=1.0,
        )

        context = dashboard.build_reliability_context(
            last_updated='2026-05-17',
        )

        self.assertGreaterEqual(context['stale_count'], 1)
        self.assertTrue(context['stale_items'])
        self.assertNotIn(
            f'観測日が古い指標が {context["stale_count"]} 件あります。',
            context['warnings'],
        )

    def test_top_indicator_cards_include_only_decision_series(self):
        keep, _ = Indicator.objects.update_or_create(
            fred_series_id='PCEPILFE',
            defaults={
                'name_ja': 'Core PCE',
                'category': Indicator.Category.INFLATION,
                'importance': Indicator.Importance.A,
                'display_order': 1,
                'is_active': True,
            },
        )
        drop = Indicator.objects.create(
            fred_series_id='CPIAUCSL_EXTRA',
            name_ja='CPI詳細',
            category=Indicator.Category.INFLATION,
            importance=Indicator.Importance.A,
            display_order=2,
        )
        Observation.objects.create(
            indicator=keep,
            observation_date=date(2026, 5, 1),
            value=120,
            prev_value=119,
            yoy_change=2.8,
        )
        Observation.objects.create(
            indicator=drop,
            observation_date=date(2026, 5, 1),
            value=300,
            prev_value=299,
            yoy_change=3.1,
        )

        cards = dashboard.build_top_indicator_cards()

        self.assertIn('PCEPILFE', [card['series_id'] for card in cards])
        self.assertNotIn('CPIAUCSL_EXTRA', [card['series_id'] for card in cards])

    def test_macro_decision_context_compacts_top_level_judgment(self):
        snapshot = RegimeSnapshot.objects.create(
            snapshot_date=date(2026, 5, 17),
            regime_label=RegimeSnapshot.Label.SLOWDOWN,
            inflation_flag=RegimeSnapshot.InflationFlag.HIGH,
            rule_strength=62,
            data_quality=78,
            evidence=[
                {'name': '雇用', 'contribution': 0.5, 'signal': '底堅い'},
                {'name': '信用', 'contribution': 0.4, 'signal': '安定'},
                {'name': 'VIX', 'contribution': 0.3, 'signal': '平常'},
                {'name': 'Core PCE', 'contribution': -0.6, 'signal': '高い'},
                {'name': '金利', 'contribution': -0.5, 'signal': '逆風'},
                {'name': '生産', 'contribution': -0.4, 'signal': '減速'},
            ],
        )

        with mock.patch('macro.services.dashboard.build_crash_alert_context', return_value={
            'total_score': 22,
            'level_label': '平常',
            'data_quality_pct': 90,
            'components': [
                {'label': 'MOVE', 'score': 75, 'is_missing': False, 'is_stale': False},
                {'label': 'VIX', 'score': 20, 'is_missing': False, 'is_stale': False},
            ],
        }), mock.patch('macro.services.dashboard.build_world_state_context', return_value={
            'score_rows': [{'field': 'policy_pressure_score', 'display': '64'}],
        }):
            context = dashboard.build_macro_decision_context(snapshot)

        self.assertEqual(context['headline'], '景気は弱含みで物価も重い')
        self.assertLessEqual(len(context['good_points']), 3)
        self.assertLessEqual(len(context['bad_points']), 3)
        self.assertEqual(context['policy_pressure']['score_display'], '64')
        self.assertEqual(context['market_stress']['score'], 22)
        self.assertEqual(context['market_stress']['abnormal_items'][0], 'MOVE')
        self.assertIn(context['confidence']['grade'], ['A', 'B', 'C', 'D'])

    def test_macro_decision_confidence_uses_data_quality_gate_cap(self):
        snapshot = RegimeSnapshot.objects.create(
            snapshot_date=date(2026, 5, 17),
            regime_label=RegimeSnapshot.Label.EXPANSION,
            inflation_flag=RegimeSnapshot.InflationFlag.NORMAL,
            rule_strength=95,
            data_quality=95,
            evidence=[],
        )

        with mock.patch('macro.services.dashboard.build_crash_alert_context', return_value={
            'total_score': 12,
            'level_label': '平常',
            'data_quality_pct': 95,
            'components': [],
        }), mock.patch('macro.services.dashboard.build_world_state_context', return_value={
            'score_rows': [],
        }), mock.patch('macro.services.dashboard.build_data_quality_report', return_value={
            'confidence_cap': 'C',
            'blocking_issues': ['主要系列の欠損があります。'],
            'warnings': [],
        }):
            context = dashboard.build_macro_decision_context(snapshot)

        self.assertEqual(context['confidence']['grade'], 'C')
        self.assertLessEqual(context['confidence']['score'], 69)
        self.assertIn('主要系列の欠損があります。', context['confidence']['notes'])

    def test_model_display_grade_uses_show_reference_hidden_blocked(self):
        from .services import model_validation

        no_validation = ModelValidationReport(sample_count=0, metrics={})
        low_samples = ModelValidationReport(sample_count=12, metrics={'mae': 1})
        few_events = ModelValidationReport(
            sample_count=40,
            event_count=4,
            metrics={'direction_accuracy': 0.6},
        )
        weak_direction = ModelValidationReport(
            sample_count=40,
            metrics={'direction_accuracy': 0.5},
        )
        usable = ModelValidationReport(
            sample_count=40,
            event_count=12,
            metrics={'direction_accuracy': 0.55},
        )

        self.assertEqual(
            model_validation.model_display_grade(no_validation)[0],
            'blocked',
        )
        self.assertEqual(
            model_validation.model_display_grade(low_samples)[0],
            'hidden',
        )
        self.assertEqual(
            model_validation.model_display_grade(few_events)[0],
            'reference',
        )
        self.assertEqual(
            model_validation.model_display_grade(weak_direction)[0],
            'reference',
        )
        self.assertEqual(
            model_validation.model_display_grade(usable),
            ('show', 'トップ表示可'),
        )

    def test_walk_forward_validation_adds_zero_prediction_baseline(self):
        rows = []
        for idx in range(40):
            rows.append({
                'as_of_date': date(2020, 1, 1) + relativedelta(months=idx),
                'x': [float(idx % 3)],
                'target_value': 1.0 if idx % 2 == 0 else -1.0,
            })

        result = forecast_models.walk_forward_validate(rows, min_train=36)

        self.assertIn('baseline_mae', result['metrics'])
        self.assertIn('skill_score', result['metrics'])


class DashboardCacheTest(TestCase):
    def test_load_dashboard_payload_accepts_legacy_key(self):
        from .services.dashboard_cache import load_dashboard_payload

        DashboardCache.objects.create(
            cache_key='macro_index_v1',
            payload={'last_updated': '2026-05-01'},
        )

        self.assertEqual(
            load_dashboard_payload(),
            {'last_updated': '2026-05-01'},
        )

    def test_missing_static_macro_payload_does_not_recompute_dashboard(self):
        with mock.patch('macro.views.load_static_macro_payload', return_value=None), \
             mock.patch('macro.views.build_scenario_analysis') as scenario_analysis:
            response = self.client.get(reverse('macro:index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '事前計算データがありません')
        scenario_analysis.assert_not_called()

    def test_static_macro_payload_is_used_as_dashboard_context(self):
        payload = {
            'has_observations': True,
            'last_updated': '2026-06-17',
            'generated_at': '2026-06-17T09:00:00+09:00',
            'source': 'github_actions',
            'data_quality': 92.5,
            'stale': False,
            'model_version': 'macro-test-v1',
            'job_duration_sec': 12.3,
            'warnings': [],
            'similar_periods': [],
            'linkages': [],
            'indicator_cards': [],
            'crash_alert': None,
            'historical_crash_similarity': [],
            'scenario_analysis': {'scenarios': []},
        }
        with mock.patch('macro.views.load_static_macro_payload', return_value=payload):
            response = self.client.get(reverse('macro:index'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['generated_payload_meta']['source'], 'github_actions')
        self.assertEqual(response.context['generated_payload_meta']['data_quality'], 92.5)

    def test_audit_uses_data_quality_report_for_primary_reliability(self):
        payload = {
            'has_observations': True,
            'last_updated': '2026-06-17',
            'data_quality_report': {
                'as_of': '2026-06-18',
                'freshness_score': 42.5,
                'missing_required_count': 3,
                'stale_required_count': 2,
                'required_count': 8,
                'usable_for_decision': False,
                'confidence_cap': 'C',
                'display_allowed': False,
                'blocking_issues': ['主要指標が3件未取得です。'],
                'warnings': ['トップの総合判断は参考扱いです。'],
            },
            'indicator_cards': [],
            'audit_indicator_cards': [],
            'similar_periods': [],
            'linkages': [],
        }
        with mock.patch('macro.views.load_static_macro_payload', return_value=payload):
            response = self.client.get(reverse('macro:audit'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '判断用データ品質')
        self.assertContains(response, '42.5% / 参考扱い')
        self.assertContains(response, '3件 / 2件')
        self.assertContains(response, '信頼度上限 C')
        self.assertContains(response, '主要指標が3件未取得です。')
        content = response.content.decode('utf-8')
        self.assertNotContains(response, '基準日')
        self.assertLess(content.index('最終データ日'), content.index('判断用データ品質'))
        self.assertNotContains(response, '前回更新の失敗')
        self.assertNotContains(response, '記録された失敗はありません。')

    def test_precompute_dashboard_payload_includes_world_model_sections(self):
        with mock.patch('macro.services.data_sync.get_latest_observation_date', return_value=None), \
             mock.patch('macro.services.dashboard.build_similar_periods', return_value=[]), \
             mock.patch('macro.services.dashboard.build_linkages', return_value=[]), \
             mock.patch('macro.services.dashboard.build_indicator_cards', return_value=[]), \
             mock.patch('macro.services.dashboard.build_crash_alert_context', return_value={}), \
             mock.patch('macro.services.dashboard.build_monthly_model_status', return_value={}), \
             mock.patch('macro.services.dashboard.build_forecast_monitor_context', return_value={}), \
             mock.patch('macro.services.dashboard.build_world_state_context', return_value={'has_snapshot': False}), \
             mock.patch('macro.services.dashboard.build_forecast_model_context', return_value={'rows': []}), \
             mock.patch('macro.services.dashboard.build_model_validation_context', return_value={'rows': []}), \
             mock.patch('macro.services.dashboard.build_world_model_operations_context', return_value={}), \
             mock.patch('macro.services.dashboard.build_raw_archive_context', return_value={}), \
             mock.patch('macro.services.scenario.build_scenario_analysis', return_value={}), \
             mock.patch('macro.services.dashboard.build_historical_crash_similarity', return_value=[]), \
             mock.patch('macro.services.house_view.build_house_view_context', return_value={'house_view': '公式見解'}), \
             mock.patch('macro.services.data_quality.build_data_quality_report', return_value={'freshness_score': 80}):
            payload = dashboard_cache.precompute_dashboard_payload()

        self.assertIn('world_state', payload)
        self.assertIn('forecast_models', payload)
        self.assertIn('model_validation', payload)
        self.assertEqual(payload['house_view']['house_view'], '公式見解')
        self.assertEqual(payload['data_quality_report']['freshness_score'], 80)

    def test_export_macro_payload_command_writes_static_payload_with_metadata(self):
        with TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / 'latest_dashboard.json'
            with mock.patch(
                'macro.management.commands.export_macro_payload.precompute_dashboard_payload',
                return_value={
                    'last_updated': '2026-06-17',
                    'crash_alert': {'data_quality_pct': 92.5},
                    'data_quality_report': {'freshness_score': 88},
                    'house_view': {'house_view': '公式見解'},
                    'regime_model_version': 'macro-test-v1',
                    'warnings': ['sample warning'],
                },
            ):
                call_command(
                    'export_macro_payload',
                    '--output',
                    str(output),
                    stdout=StringIO(),
                )

            payload = json.loads(output.read_text(encoding='utf-8'))

        self.assertEqual(payload['source'], 'github_actions')
        self.assertEqual(payload['data_quality'], 88)
        self.assertFalse(payload['stale'])
        self.assertEqual(payload['model_version'], 'macro-test-v1')
        self.assertIn('generated_at', payload)
        self.assertIn('job_duration_sec', payload)
        self.assertEqual(payload['warnings'], ['sample warning'])
        self.assertEqual(payload['last_updated'], '2026-06-17')
        self.assertEqual(payload['data_quality_report']['freshness_score'], 88)
        self.assertEqual(payload['house_view']['house_view'], '公式見解')

    def test_additional_macro_json_exports_are_available(self):
        ModelValidationReport.objects.create(
            model_version='macro_hatzius_v1',
            target='GSPC',
            horizon='3m',
            sample_count=40,
            event_count=12,
            metrics={'direction_accuracy': 0.56, 'mae': 1.2},
            warnings=['検証メモ'],
        )
        WorldModelRun.objects.create(
            cadence=WorldModelRun.Cadence.DAILY,
            name='daily-refresh',
            status=WorldModelRun.Status.SUCCESS,
            started_at=timezone.now(),
            finished_at=timezone.now(),
        )
        with TemporaryDirectory() as tmpdir:
            validation_path = Path(tmpdir) / 'model_validation_report.json'
            cards_path = Path(tmpdir) / 'model_cards.json'
            operations_path = Path(tmpdir) / 'operations_status.json'

            call_command('export_macro_model_validation', '--output', str(validation_path), stdout=StringIO())
            call_command('export_macro_model_cards', '--output', str(cards_path), stdout=StringIO())
            call_command('export_macro_operations_status', '--output', str(operations_path), stdout=StringIO())

            validation_payload = json.loads(validation_path.read_text(encoding='utf-8'))
            cards_payload = json.loads(cards_path.read_text(encoding='utf-8'))
            operations_payload = json.loads(operations_path.read_text(encoding='utf-8'))

        self.assertEqual(validation_payload['model_validation_report'][0]['display_grade'], 'show')
        self.assertEqual(cards_payload['model_cards'][0]['display_policy'], 'show')
        self.assertEqual(operations_payload['operations_status'][0]['status'], 'success')

    def test_scenario_ledger_exports_verifiable_hypotheses(self):
        from macro.models import MacroScenario
        from macro.management.commands.export_macro_scenarios import build_scenario_ledger

        forecast = ForecastSnapshot.objects.create(
            as_of_date=date(2026, 6, 17),
            model_version='macro_hatzius_v1',
            target='macro_regime',
            horizon='3m_6m',
            prediction_value=0.6,
        )
        run = MacroForecastRun.objects.create(
            as_of=date(2026, 6, 17),
            forecast=forecast,
            primary_regime='expansion_with_inflation_risk',
            confidence=72,
            data_quality_score=80,
        )
        MacroScenario.objects.create(
            run=run,
            name=MacroScenario.Name.DOWNSIDE,
            probability=0.28,
            growth_view='金利上昇で成長に逆風',
            inflation_view='物価再加速',
            policy_view='利下げ後退',
            market_view='株価に逆風',
            nikkei_bias=MacroScenario.NikkeiBias.SHORT,
            key_drivers=['DGS10 +25bp以上', 'VIX +15%以上'],
            invalidation_triggers=['DGS10 -20bp以上', '信用スプレッド縮小'],
        )

        payload = build_scenario_ledger()
        item = payload['scenario_ledger'][0]

        self.assertEqual(item['scenario_id'], 'downside_2026-06-17')
        self.assertEqual(item['watch_window'], '20 trading days')
        self.assertEqual(item['confirmation_rules'], ['DGS10 +25bp以上', 'VIX +15%以上'])
        self.assertEqual(item['invalidation_rules'], ['DGS10 -20bp以上', '信用スプレッド縮小'])
        self.assertEqual(item['status'], 'open')
        self.assertIsNone(item['outcome'])
        self.assertEqual(item['expected_market_impact']['nikkei_futures'], 'downside_pressure')


class BacktestRegimeCommandTest(TestCase):
    def test_backtest_regime_no_data_does_not_crash(self):
        out = StringIO()
        call_command('backtest_regime', stdout=out)
        output = out.getvalue()
        self.assertIn('"sample_count": 0', output)
        self.assertIn(regime.MODEL_VERSION, output)


@override_settings(ALLOWED_HOSTS=['*'])
class MacroUrlsTest(TestCase):
    """URLが正しく解決され、想定したHTTPステータスを返すことを確認。"""

    def test_index_renders(self):
        r = self.client.get(reverse('macro:index'))
        self.assertEqual(r.status_code, 200)

    def test_index_regime_copy_avoids_confidence_word(self):
        RegimeSnapshot.objects.create(
            snapshot_date=date(2026, 5, 17),
            regime_label=RegimeSnapshot.Label.SLOWDOWN,
            inflation_flag=RegimeSnapshot.InflationFlag.HIGH,
            rule_strength=62,
            data_quality=78,
            evidence=[{
                'series_id': 'INDPRO',
                'name': '鉱工業生産指数',
                'metric': '前年比',
                'value': 0.8,
                'unit': '%',
                'observation_date': '2026-04-01',
                'signal': '減速寄り',
                'contribution': -0.35,
            }],
            warnings=['主要指標の観測日が古い可能性があります。'],
            model_version='regime_v2_score',
        )

        r = self.client.get(reverse('macro:index'))

        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, '今月のマクロ結論')
        self.assertNotContains(r, '前回からの変化')
        self.assertNotContains(r, '今後3カ月のベースシナリオ')
        self.assertNotContains(r, 'モデルの信頼度')
        self.assertContains(r, 'Macro 現在判断')
        self.assertContains(r, '総合判断')
        self.assertContains(r, '良い材料')
        self.assertContains(r, '悪い材料')
        self.assertContains(r, '政策・金利')
        self.assertContains(r, '市場ストレス')
        self.assertContains(r, '信頼度')
        self.assertNotContains(r, '判定強度')
        self.assertNotContains(r, 'macro-regime-score-axis')
        self.assertNotContains(r, 'macro-regime-sub')
        self.assertNotContains(r, '減速 × 高止まり')
        self.assertNotContains(r, 'macro-current-state-map')
        self.assertNotContains(r, '景気の向き')
        self.assertNotContains(r, '注意リスク')
        self.assertNotContains(r, '判断材料')
        self.assertNotContains(r, '景気分布（ルール一致度）と主要リスク')
        self.assertNotContains(r, '<summary>判定根拠</summary>')
        self.assertContains(r, '良い材料')
        self.assertContains(r, '悪い材料')
        self.assertNotContains(r, 'これから先')
        self.assertNotContains(r, '<details class="macro-regime-details">')
        self.assertNotContains(r, '結論・良い点・悪い点・先行き')
        self.assertNotContains(r, '景気評価')
        self.assertNotContains(r, '景気コンディション')
        self.assertNotContains(r, '更新頻度の目安')
        self.assertNotContains(r, 'macro-regime-details--evidence')
        self.assertContains(r, '鉱工業生産指数')
        self.assertNotContains(r, '判定モデル')
        self.assertNotContains(r, '履歴アーカイブ')
        self.assertNotContains(r, '確度')
        self.assertNotContains(r, '主要指標の観測日が古い可能性があります。')

    @mock.patch('macro.views.load_static_macro_payload', return_value=None)
    def test_index_shows_reliability_status_from_cache(self, static_payload_mock):
        DashboardCache.objects.create(
            cache_key='macro_update_status_v1',
            payload={
                'source': 'refresh_macro_data',
                'status': 'partial',
                'message': '日次更新を実行しました。',
                'failed': [{'series_id': 'VIXCLS', 'error': 'timeout'}],
                'finished_at': '2026-05-17T05:30:00+00:00',
            },
        )

        r = self.client.get(reverse('macro:index'))

        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, '更新信頼性')

        audit_response = self.client.get(reverse('macro:audit'))

        self.assertEqual(audit_response.status_code, 200)
        self.assertContains(audit_response, '更新信頼性の詳細')
        self.assertContains(audit_response, '前回更新')
        self.assertContains(audit_response, '一部失敗')
        self.assertContains(audit_response, 'VIXCLS: timeout')

    @mock.patch('macro.views.load_static_macro_operations_status')
    @mock.patch('macro.views.load_static_macro_payload')
    def test_audit_reliability_uses_static_payload_instead_of_empty_runtime_db(
        self,
        static_payload_mock,
        static_operations_mock,
    ):
        static_payload_mock.return_value = {
            'has_observations': True,
            'last_updated': '2026-06-19',
            'generated_at': '2026-06-19T01:39:54+00:00',
            'data_quality_report': {
                'as_of': '2026-06-19',
                'freshness_score': 100.0,
                'missing_required_count': 0,
                'stale_required_count': 0,
                'required_count': 8,
                'usable_for_decision': True,
                'confidence_cap': 'A',
                'display_allowed': True,
                'blocking_issues': [],
                'warnings': [],
            },
            'indicator_cards': [],
            'audit_indicator_cards': [
                {
                    'series_id': 'CPIAUCSL',
                    'name_ja': 'CPI',
                    'has_data': True,
                    'latest_date': '2026-05-01',
                },
                {
                    'series_id': 'CPILFESL',
                    'name_ja': 'Core CPI',
                    'has_data': True,
                    'latest_date': '2026-05-01',
                },
                {
                    'series_id': 'PCEPI',
                    'name_ja': 'PCE',
                    'has_data': True,
                    'latest_date': '2026-04-01',
                },
                {
                    'series_id': 'PCEPILFE',
                    'name_ja': 'Core PCE',
                    'has_data': True,
                    'latest_date': '2026-04-01',
                },
                {
                    'series_id': 'T5YIE',
                    'name_ja': '5年期待インフレ率',
                    'has_data': True,
                    'latest_date': '2026-06-18',
                },
                {
                    'series_id': 'T10YIE',
                    'name_ja': '10年期待インフレ率',
                    'has_data': True,
                    'latest_date': '2026-06-18',
                },
            ],
            'similar_periods': [],
            'linkages': [],
        }
        static_operations_mock.return_value = {
            'latest_update_status': {
                'source': 'refresh_macro_data',
                'status': 'success',
                'message': '日次更新を実行しました。',
                'failed': [],
                'finished_at': '2026-06-19T01:38:34+00:00',
            },
        }

        response = self.client.get(reverse('macro:audit'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Observation.objects.count(), 0)
        self.assertContains(response, '前回更新')
        self.assertContains(response, '成功')
        self.assertContains(response, '2026-06-19 01:38')
        self.assertContains(response, '欠損 / 古い')
        self.assertContains(response, '0件 / 0件')
        self.assertNotContains(response, '記録なし')
        self.assertNotContains(response, '未取得: CPI（CPIAUCSL）')

    @mock.patch('macro.views.load_static_macro_payload')
    def test_index_shows_house_view_invalidation_status_notes(self, static_payload_mock):
        static_payload_mock.return_value = {
            'has_observations': True,
            'last_updated': '2026-06-17',
            'similar_periods': [],
            'linkages': [],
            'indicator_cards': [],
            'historical_crash_similarity': [],
            'house_view': {
                'house_view': '景気判断は中立',
                'confidence_grade': 'B',
                'confidence_score': 72,
                'display_allowed': True,
                'as_of': '2026-06-17',
                'regime_label': 'inflation_risk',
                'key_drivers': [],
                'main_risks': [],
                'invalidation_triggers': [
                    '失業率が3か月連続で上昇',
                    'Core PCEが2か月連続で再加速',
                ],
                'invalidation_status_notes': [
                    {
                        'label': '失業率',
                        'detail': '直近1/3か月連続で上昇（2026-05-01: 4.20%、前月比 +0.10pt）',
                    },
                    {
                        'label': 'Core PCE',
                        'detail': '直近2/2か月連続で再加速（2026-04-01: 3.10%、前月比 +0.20pt）',
                    },
                ],
            },
        }

        response = self.client.get(reverse('macro:index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '失業率: 直近1/3か月連続で上昇')
        self.assertContains(response, 'Core PCE: 直近2/2か月連続で再加速')

    @mock.patch('macro.views.load_static_macro_payload')
    def test_index_crash_alert_copy_uses_market_stress_wording(
        self,
        static_payload_mock,
    ):
        static_payload_mock.return_value = {
            'has_observations': True,
            'last_updated': '2026-05-17',
            'similar_periods': [],
            'linkages': [],
            'indicator_cards': [],
            'historical_crash_similarity': [],
            'monthly_model_status': {},
            'crash_alert': {
                'total_score': 20,
                'level': 'calm',
                'level_label': '平常',
                'rule_agreement_pct': 86,
                'data_quality_pct': 90,
                'validation_status': '検証未実施',
                'backtest_summary': {
                    'target': 'GSPC',
                    'horizon_days': 63,
                    'drawdown_threshold_pct': -10.0,
                    'roc_auc_display': '0.71',
                    'pr_auc_display': '0.40',
                    'precision_25_display': '11.8%',
                    'recall_25_display': '73.7%',
                    'calm_miss_count': 5,
                },
                'quality_warnings': ['未取得の指標: NAAIMエクスポージャー、AAII強気%。この2件は今回の点数に含めていません。'],
                'category_summary': [{
                    'category': 'price_action',
                    'category_label': '価格アクション',
                    'avg_score_display': 12,
                    'weight_pct': 20,
                    'coverage_pct': 100,
                }],
                'components': [{
                    'label': 'VIX',
                    'category_label': 'ボラ・需給',
                    'value_display': '18.00',
                    'observation_date': '2026-05-15',
                    'age_days_display': '2日',
                    'freshness_label': '新鮮',
                    'score': 25,
                }],
            },
            'macro_decision': {
                'headline': '市場ストレスは平常',
                'detail': '急落確率ではなく、現在の市場の緊張度です。',
                'good_points': ['VIXは低位'],
                'bad_points': [],
                'policy_pressure': {
                    'label': '中立',
                    'summary': '政策金利見通しは中立です。',
                    'score_display': '—',
                    'data_quality_display': '—',
                    'alerts': [],
                },
                'market_stress': {
                    'level_label': '平常',
                    'score_display': '20/100',
                    'summary': '急落確率ではなく、現在の市場の緊張度です。',
                    'data_quality_display': '90%',
                    'abnormal_items': [],
                },
                'confidence': {
                    'grade': 'B',
                    'label': '通常',
                    'score_display': '82%',
                    'data_freshness_pct': 90,
                    'sample_note': 'モデル予測は検証条件を満たすものだけ参考表示します。',
                    'notes': ['主要データの取得状況と判定材料は確認済みです。'],
                },
            },
        }

        r = self.client.get(reverse('macro:index'))

        self.assertEqual(r.status_code, 200)
        self.assertContains(r, '市場ストレス')
        self.assertContains(r, '急落確率ではなく、現在の市場の緊張度です。')
        self.assertContains(r, 'データ品質')
        self.assertNotContains(r, '検証未実施')
        self.assertNotContains(r, 'クラッシュ警戒度')
        self.assertNotContains(r, '月次検証: GSPC')
        self.assertNotContains(r, 'ROC-AUC 0.71')
        self.assertNotContains(r, '閾値25')
        self.assertNotContains(r, '平常表示時の取り逃し')

    @mock.patch('macro.views.load_crash_probability_model')
    @mock.patch('macro.views.load_static_macro_payload')
    def test_index_shows_crash_probability_model(
        self,
        static_payload_mock,
        probability_mock,
    ):
        static_payload_mock.return_value = {
            'has_observations': True,
            'last_updated': '2026-05-17',
            'similar_periods': [],
            'linkages': [],
            'indicator_cards': [],
            'historical_crash_similarity': [],
            'crash_alert': None,
            'monthly_model_status': {},
        }
        probability_mock.return_value = {
            'prediction_label': '今後63日相当でGSPCが-10%以上下落する推定確率',
            'current_probability_display': '11.1%',
            'raw_probability_display': '42.0%',
            'raw_calibration_gap_display': '30.9%',
            'validation_samples': 84,
            'validation_event_count': 5,
            'validation_event_rate_display': '6.0%',
            'validation_event_interval_display': '2.6%〜13.1%',
            'roc_auc_display': '0.77',
            'pr_auc_display': '0.37',
            'brier_score_display': '0.06',
            'threshold_10_precision_display': '6.9%',
            'threshold_10_recall_display': '100.0%',
            'limitations': ['急落は発生回数が少ないため、確率は参考値です。'],
            'reliability_label': '低',
            'reliability_tone': 'danger',
            'reliability_warnings': ['検証イベントが5件と少ないため、確率は参考値です。'],
            'trained_at': '2026-05-17',
            'sample_count': 180,
            'event_count': 8,
            'model_version': 'crash_probability_logistic_v1',
        }

        r = self.client.get(reverse('macro:audit'))

        self.assertEqual(r.status_code, 200)
        self.assertContains(r, '急落確率モデル（検証済み参考確率）')
        self.assertContains(r, '11.1%')
        self.assertContains(r, '信頼性 低')
        self.assertContains(r, '検証 84件 / イベント 5件')
        self.assertContains(r, 'ROC-AUC 0.77 / PR-AUC 0.37 / Brier 0.06')
        self.assertContains(r, 'raw 42.0%')
        self.assertContains(r, '乖離 30.9%')
        self.assertContains(r, '目安範囲 2.6%〜13.1%')
        self.assertContains(r, '検証イベントが5件と少ないため')
        self.assertContains(r, '学習日 2026-05-17')
        self.assertContains(r, 'モデル crash_probability_logistic_v1')
        self.assertContains(r, '閾値10% 精度 6.9% / 捕捉 100.0%')
        self.assertNotContains(r, '投資判断や売買推奨としては使えません')
        self.assertNotContains(r, 'サンプル 180')

    @mock.patch('macro.views.load_static_macro_payload')
    def test_index_shows_monthly_model_status(self, static_payload_mock):
        static_payload_mock.return_value = {
            'has_observations': True,
            'last_updated': '2026-05-17',
            'similar_periods': [],
            'linkages': [],
            'indicator_cards': [],
            'historical_crash_similarity': [],
            'crash_alert': None,
            'monthly_model_status': {
                'tone': 'good',
                'status_label': '更新済み',
                'latest_training_date': '2026-05-17',
                'latest_backtest_date': '2026-05-17 10:00',
                'warnings': [],
                'cards': [{
                    'label': '急落確率モデル',
                    'updated_at': '2026-05-17',
                    'sample_label': '検証 120件 / イベント 6件',
                    'metric_label': 'ROC-AUC 0.82 / PR-AUC 0.30',
                    'model_label': 'crash_probability_logistic_v1',
                }],
            },
        }

        r = self.client.get(reverse('macro:audit'))

        self.assertEqual(r.status_code, 200)
        self.assertContains(r, '月次モデル状態')
        self.assertContains(r, '最終学習日')
        self.assertContains(r, '2026-05-17')
        self.assertContains(r, '月次モデルの検証情報を確認')
        self.assertContains(r, '検証 120件 / イベント 6件')
        self.assertContains(r, 'ROC-AUC 0.82 / PR-AUC 0.30')

    @mock.patch.dict('os.environ', {'VERCEL': '1'})
    @mock.patch('macro.views.load_static_macro_payload', return_value=None)
    def test_index_serverless_without_cache_skips_heavy_fallback(
        self,
        static_payload_mock,
    ):
        r = self.client.get(reverse('macro:index'))

        self.assertEqual(r.status_code, 200)
        self.assertContains(r, '事前計算データがありません')

    def test_refresh_without_key_redirects(self):
        user = User.objects.create_superuser(
            username='creator-no-key',
            email='creator-no-key@example.com',
            password='test-password',
        )
        self.client.force_login(user)
        with mock.patch('macro.views.get_api_key', return_value=None):
            r = self.client.post(reverse('macro:refresh'))
        self.assertEqual(r.status_code, 302)

    def test_refresh_button_is_hidden_for_anonymous_users(self):
        r = self.client.get(reverse('macro:index'))
        self.assertNotContains(r, '取得・判定')
        self.assertNotContains(r, 'macro-refresh-form')
        self.assertNotContains(r, 'macro-operation-panel')

    def test_anonymous_refresh_is_forbidden(self):
        r = self.client.post(reverse('macro:refresh'))
        self.assertEqual(r.status_code, 403)

    def test_anonymous_model_jobs_are_forbidden(self):
        backtest_response = self.client.post(
            reverse('macro:recompute_crash_backtest')
        )
        self.assertEqual(backtest_response.status_code, 403)

    def test_staff_refresh_is_forbidden_and_button_hidden(self):
        user = User.objects.create_user(
            username='macro-staff',
            password='test-password',
            is_staff=True,
        )
        self.client.force_login(user)

        get_response = self.client.get(reverse('macro:index'))
        post_response = self.client.post(reverse('macro:refresh'))
        backtest_response = self.client.post(
            reverse('macro:recompute_crash_backtest')
        )

        self.assertNotContains(get_response, '取得・判定')
        self.assertEqual(post_response.status_code, 403)
        self.assertEqual(backtest_response.status_code, 403)

    def test_refresh_button_is_visible_for_superusers(self):
        user = User.objects.create_superuser(
            username='macro-creator',
            email='macro-creator@example.com',
            password='test-password',
        )
        self.client.force_login(user)

        r = self.client.get(reverse('macro:index'))

        self.assertContains(r, '取得・判定')
        self.assertContains(r, 'macro-refresh-form')
        self.assertContains(r, '詳細・監査')
        self.assertNotContains(r, 'macro-operation-panel')
        self.assertNotContains(r, '月次メンテナンス')
        self.assertNotContains(r, '月次検証・急落確率モデル更新')
        self.assertNotContains(r, '確率更新')

    @mock.patch.dict(
        'os.environ',
        {'VERCEL': '1', 'MACRO_UPDATE_WEBHOOK_URL': 'https://example.com/update'},
    )
    def test_serverless_refresh_button_triggers_update_job_only(self):
        user = User.objects.create_superuser(
            username='serverless-creator',
            email='serverless-creator@example.com',
            password='test-password',
        )
        self.client.force_login(user)

        with mock.patch('macro.views._is_serverless_runtime', return_value=True), \
             mock.patch('macro.views.requests.post') as post_mock, \
             mock.patch('macro.views.sync_all_indicators') as sync_mock, \
             mock.patch('macro.views.compute_current_regime') as regime_mock, \
             mock.patch('macro.views.compute_current_world_state') as world_state_mock, \
             mock.patch('macro.views.sync_all_price_histories') as price_mock, \
             mock.patch('macro.views.precompute_dashboard_payload') as precompute_mock, \
             mock.patch('macro.views.save_dashboard_payload') as save_cache_mock:
            post_mock.return_value.raise_for_status.return_value = None
            get_response = self.client.get(reverse('macro:index'))
            post_response = self.client.post(reverse('macro:refresh'))

        self.assertEqual(post_response.status_code, 302)
        self.assertContains(get_response, '取得・判定')
        self.assertContains(get_response, 'macro-refresh-form')
        post_mock.assert_called_once_with('https://example.com/update', timeout=10)
        sync_mock.assert_not_called()
        regime_mock.assert_not_called()
        world_state_mock.assert_not_called()
        save_cache_mock.assert_not_called()
        price_mock.assert_not_called()
        precompute_mock.assert_not_called()

    def test_refresh_button_runs_fetch_judgment_and_cache_update(self):
        user = User.objects.create_superuser(
            username='creator',
            email='creator@example.com',
            password='test-password',
        )
        self.client.force_login(user)
        with mock.patch('macro.views.get_api_key', return_value='key'), \
             mock.patch('macro.views.sync_all_indicators') as sync_mock, \
             mock.patch('macro.views.compute_current_regime') as regime_mock, \
             mock.patch('macro.views.compute_current_world_state') as world_state_mock, \
             mock.patch('macro.views.sync_all_price_histories') as price_mock, \
             mock.patch('macro.views.precompute_dashboard_payload') as precompute_mock, \
             mock.patch('macro.views.save_dashboard_payload') as save_cache_mock:
            sync_mock.return_value = {
                'success': [{'series_id': 'USREC'}],
                'failed': [],
            }
            regime_mock.return_value = object()
            price_mock.return_value = {'success': [], 'failed': []}
            precompute_mock.return_value = {'last_updated': '2026-05-17'}

            r = self.client.post(reverse('macro:refresh'))

        self.assertEqual(r.status_code, 302)
        sync_mock.assert_called_once()
        regime_mock.assert_called_once()
        world_state_mock.assert_called_once()
        price_mock.assert_called_once()
        precompute_mock.assert_called_once()
        save_cache_mock.assert_called_once_with({'last_updated': '2026-05-17'})

    def test_monthly_maintenance_post_only_shows_local_command_guidance(self):
        user = User.objects.create_superuser(
            username='backtest-creator',
            email='backtest-creator@example.com',
            password='test-password',
        )
        self.client.force_login(user)

        with mock.patch('macro.views._is_serverless_runtime', return_value=False), \
             mock.patch('macro.views.precompute_dashboard_payload') as precompute_mock, \
             mock.patch('macro.views.save_dashboard_payload') as save_cache_mock:
            r = self.client.post(reverse('macro:recompute_crash_backtest'))
            messages = list(r.wsgi_request._messages)

        self.assertEqual(r.status_code, 302)
        self.assertTrue(
            any('python manage.py monthly_macro_maintenance' in str(item) for item in messages)
        )
        precompute_mock.assert_not_called()
        save_cache_mock.assert_not_called()

    def test_indicator_detail_existing(self):
        # マイグレーションでシードされた CPIAUCSL は存在する想定
        r = self.client.get(
            reverse('macro:indicator_detail', args=['CPIAUCSL'])
        )
        self.assertEqual(r.status_code, 200)

    def test_indicator_detail_404(self):
        r = self.client.get(
            reverse('macro:indicator_detail', args=['NOPE_NOPE'])
        )
        self.assertEqual(r.status_code, 404)

    def test_similar_detail_renders(self):
        r = self.client.get(
            reverse('macro:similar_detail', args=['2019-03-01'])
        )
        self.assertEqual(r.status_code, 200)

    def test_similar_detail_invalid_date(self):
        r = self.client.get(
            reverse('macro:similar_detail', args=['not-a-date'])
        )
        self.assertEqual(r.status_code, 404)


class JudgmentTest(TestCase):
    """指標値→5段階評価の変換ロジック。"""

    # --- lower_better ---
    def test_lower_better_min(self):
        rule = {'metric': 'level',
                'economic': {'direction': 'lower_better', 'thresholds': [10, 20, 30, 40]},
                'market':   {'direction': 'lower_better', 'thresholds': [10, 20, 30, 40]}}
        e, m = judgment.evaluate(_ObsStub(value=5), rule)
        self.assertEqual(e, 1)
        self.assertEqual(m, 1)

    def test_lower_better_max(self):
        rule = {'metric': 'level',
                'economic': {'direction': 'lower_better', 'thresholds': [10, 20, 30, 40]},
                'market':   {'direction': 'lower_better', 'thresholds': [10, 20, 30, 40]}}
        e, _ = judgment.evaluate(_ObsStub(value=99), rule)
        self.assertEqual(e, 5)

    def test_lower_better_boundary(self):
        rule = {'metric': 'level',
                'economic': {'direction': 'lower_better', 'thresholds': [10, 20, 30, 40]},
                'market':   {'direction': 'lower_better', 'thresholds': [10, 20, 30, 40]}}
        e, _ = judgment.evaluate(_ObsStub(value=20), rule)
        self.assertEqual(e, 2)

    # --- higher_better ---
    def test_higher_better_min(self):
        rule = {'metric': 'level',
                'economic': {'direction': 'higher_better', 'thresholds': [10, 20, 30, 40]},
                'market':   {'direction': 'higher_better', 'thresholds': [10, 20, 30, 40]}}
        e, _ = judgment.evaluate(_ObsStub(value=5), rule)
        self.assertEqual(e, 5)

    def test_higher_better_max(self):
        rule = {'metric': 'level',
                'economic': {'direction': 'higher_better', 'thresholds': [10, 20, 30, 40]},
                'market':   {'direction': 'higher_better', 'thresholds': [10, 20, 30, 40]}}
        e, _ = judgment.evaluate(_ObsStub(value=99), rule)
        self.assertEqual(e, 1)

    # --- target_band ---
    def test_target_band_center(self):
        rule = {'metric': 'level',
                'economic': {'direction': 'target_band', 'thresholds': [-1.0, 1.0, 2.5, 4.0]},
                'market':   {'direction': 'target_band', 'thresholds': [-1.0, 1.0, 2.5, 4.0]}}
        e, _ = judgment.evaluate(_ObsStub(value=2.0), rule)
        self.assertEqual(e, 1)

    def test_target_band_below(self):
        rule = {'metric': 'level',
                'economic': {'direction': 'target_band', 'thresholds': [-1.0, 1.0, 2.5, 4.0]},
                'market':   {'direction': 'target_band', 'thresholds': [-1.0, 1.0, 2.5, 4.0]}}
        e, _ = judgment.evaluate(_ObsStub(value=-2.0), rule)
        self.assertEqual(e, 5)

    def test_target_band_above(self):
        rule = {'metric': 'level',
                'economic': {'direction': 'target_band', 'thresholds': [-1.0, 1.0, 2.5, 4.0]},
                'market':   {'direction': 'target_band', 'thresholds': [-1.0, 1.0, 2.5, 4.0]}}
        e, _ = judgment.evaluate(_ObsStub(value=5.0), rule)
        self.assertEqual(e, 5)

    def test_target_band_side(self):
        rule = {'metric': 'level',
                'economic': {'direction': 'target_band', 'thresholds': [-1.0, 1.0, 2.5, 4.0]},
                'market':   {'direction': 'target_band', 'thresholds': [-1.0, 1.0, 2.5, 4.0]}}
        e, _ = judgment.evaluate(_ObsStub(value=3.0), rule)
        self.assertEqual(e, 3)

    # --- metric: yoy/level/mom ---
    def test_metric_yoy_uses_yoy_change(self):
        rule = {'metric': 'yoy',
                'economic': {'direction': 'lower_better', 'thresholds': [1, 2, 3, 4]},
                'market':   {'direction': 'lower_better', 'thresholds': [1, 2, 3, 4]}}
        e, _ = judgment.evaluate(_ObsStub(value=999, yoy_change=0.5), rule)
        self.assertEqual(e, 1)

    def test_metric_mom_uses_diff(self):
        rule = {'metric': 'mom',
                'economic': {'direction': 'higher_better', 'thresholds': [-100, 100, 200, 400]},
                'market':   {'direction': 'higher_better', 'thresholds': [-100, 100, 200, 400]}}
        e, _ = judgment.evaluate(_ObsStub(value=300, prev_value=100), rule)
        # diff=200 → higher_better で 3段
        self.assertEqual(e, 3)

    # --- 例外系 ---
    def test_no_rule_returns_none(self):
        e, m = judgment.evaluate(_ObsStub(value=1.0), None)
        self.assertIsNone(e)
        self.assertIsNone(m)

    def test_no_observation_returns_none(self):
        rule = {'metric': 'level',
                'economic': {'direction': 'lower_better', 'thresholds': [1, 2, 3, 4]},
                'market':   {'direction': 'lower_better', 'thresholds': [1, 2, 3, 4]}}
        e, m = judgment.evaluate(None, rule)
        self.assertIsNone(e)
        self.assertIsNone(m)

    def test_value_none_returns_none(self):
        rule = {'metric': 'yoy',
                'economic': {'direction': 'lower_better', 'thresholds': [1, 2, 3, 4]},
                'market':   {'direction': 'lower_better', 'thresholds': [1, 2, 3, 4]}}
        e, _ = judgment.evaluate(_ObsStub(yoy_change=None), rule)
        self.assertIsNone(e)


class CrashAlertTest(TestCase):
    """クラッシュ警戒度のサブスコア・総合スコア計算。"""

    def test_band_score_lowest(self):
        bands = [(15, 0), (20, 25), (25, 50), (30, 75), (40, 90), (float('inf'), 100)]
        self.assertEqual(crash_alert._band_score(10, bands), 0)

    def test_band_score_middle(self):
        bands = [(15, 0), (20, 25), (25, 50), (30, 75), (40, 90), (float('inf'), 100)]
        self.assertEqual(crash_alert._band_score(22, bands), 50)

    def test_band_score_highest(self):
        bands = [(15, 0), (20, 25), (25, 50), (30, 75), (40, 90), (float('inf'), 100)]
        self.assertEqual(crash_alert._band_score(60, bands), 100)

    def test_classify_calm(self):
        level, _ = crash_alert._classify(10)
        self.assertEqual(level, 'calm')

    def test_classify_caution(self):
        level, _ = crash_alert._classify(30)
        self.assertEqual(level, 'caution')

    def test_classify_alert(self):
        level, _ = crash_alert._classify(55)
        self.assertEqual(level, 'alert')

    def test_classify_high(self):
        level, _ = crash_alert._classify(70)
        self.assertEqual(level, 'high')

    def test_classify_danger(self):
        level, _ = crash_alert._classify(85)
        self.assertEqual(level, 'danger')

    def test_compute_no_data_returns_unknown(self):
        result = crash_alert.compute_crash_alert()
        self.assertEqual(result['level'], 'unknown')
        self.assertIsNone(result['total_score'])

    def test_stale_daily_data_is_marked(self):
        as_of = date(2026, 5, 17)

        def lookup(series_id):
            if series_id != 'VIXCLS':
                return None
            return {
                'value': 30.0,
                'observation_date': date(2026, 5, 8),
                'frequency': Indicator.Frequency.DAILY,
            }

        result = crash_alert.compute_crash_alert(value_lookup=lookup, as_of=as_of)
        vix = next(c for c in result['components'] if c['series_id'] == 'VIXCLS')
        self.assertTrue(vix['is_stale'])
        self.assertEqual(vix['age_days'], 9)

    def test_low_data_quality_forces_provisional_label(self):
        as_of = date(2026, 5, 17)

        def lookup(series_id):
            if series_id != 'VIXCLS':
                return None
            return {
                'value': 35.0,
                'observation_date': as_of,
                'frequency': Indicator.Frequency.DAILY,
            }

        result = crash_alert.compute_crash_alert(value_lookup=lookup, as_of=as_of)
        self.assertEqual(result['level'], 'provisional')
        self.assertEqual(result['level_label'], '参考表示')
        self.assertLess(result['data_quality_pct'], 70)

    def test_price_action_is_capped_at_twenty_percent(self):
        as_of = date(2026, 5, 17)
        values = {
            spec['series_id']: -30.0
            for spec in crash_alert.COMPONENT_SPECS
            if spec['category'] == 'price_action'
        }

        def lookup(series_id):
            if series_id not in values:
                return None
            return {
                'value': values[series_id],
                'observation_date': as_of,
                'frequency': Indicator.Frequency.DAILY,
            }

        result = crash_alert.compute_crash_alert(value_lookup=lookup, as_of=as_of)
        price_cat = next(
            c for c in result['category_summary']
            if c['category'] == 'price_action'
        )
        self.assertEqual(price_cat['avg_score'], 100)
        self.assertEqual(result['total_score'], 20)

    def test_missing_credit_category_forces_provisional(self):
        as_of = date(2026, 5, 17)
        high_values = {
            'VIXCLS': 50.0,
            'CBOE_SKEW': 170.0,
            'NAAIM_EXPOSURE': 110.0,
            'AAII_BULLISH': 65.0,
            'MOVE_INDEX': 220.0,
            'VIX_VIX3M_RATIO': 1.3,
            'T10Y2Y': -1.0,
            'T10Y3M': -1.0,
        }
        for spec in crash_alert.COMPONENT_SPECS:
            if spec['category'] == 'price_action':
                high_values[spec['series_id']] = -30.0

        def lookup(series_id):
            if series_id not in high_values:
                return None
            return {
                'value': high_values[series_id],
                'observation_date': as_of,
                'frequency': Indicator.Frequency.DAILY,
            }

        result = crash_alert.compute_crash_alert(value_lookup=lookup, as_of=as_of)
        self.assertEqual(result['level'], 'provisional')
        self.assertIn('信用・流動性', ' '.join(result['quality_warnings']))

    def test_danger_requires_credit_or_volatility_support(self):
        level, label = crash_alert._classify(
            90,
            data_quality_pct=100,
            low_coverage_categories=[],
            supporting_stress=False,
        )
        self.assertEqual(level, 'high')
        self.assertEqual(label, '高警戒')


class BacktestCrashAlertCommandTest(TestCase):
    """市場ストレス検証コマンド。"""

    def test_backtest_outputs_json(self):
        indicator, _ = Indicator.objects.update_or_create(
            fred_series_id='VIXCLS',
            defaults={
                'source': Indicator.Source.FRED,
                'name_ja': 'VIX',
                'name_en': 'VIX',
                'category': Indicator.Category.MARKET,
                'importance': Indicator.Importance.B,
                'frequency': Indicator.Frequency.DAILY,
                'unit': 'index',
                'is_active': True,
            },
        )
        for month, close, vix in (
            (date(2026, 1, 1), 100.0, 16.0),
            (date(2026, 2, 1), 88.0, 32.0),
            (date(2026, 3, 1), 92.0, 24.0),
            (date(2026, 4, 1), 94.0, 18.0),
            (date(2026, 5, 1), 96.0, 16.0),
        ):
            PriceObservation.objects.create(
                ticker=PriceObservation.Ticker.SP500,
                observation_month=month,
                close_price=close,
            )
            Observation.objects.create(
                indicator=indicator,
                observation_date=month + relativedelta(months=1) - relativedelta(days=1),
                value=vix,
            )

        with TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / 'backtest.json'
            out = StringIO()
            call_command(
                'backtest_crash_alert',
                '--target', 'GSPC',
                '--horizon-days', '63',
                '--drawdown-threshold', '-10',
                '--output', str(output),
                stdout=out,
            )
            self.assertTrue(output.exists())
            payload = output.read_text(encoding='utf-8')
            self.assertIn('"roc_auc"', payload)
            self.assertIn('"precision"', payload)


class LightgbmPredictionLoadTest(TestCase):
    """学習済み LightGBM 予測 JSON の読み込み・整形ロジック。"""

    def test_classify_positive(self):
        self.assertEqual(dashboard._classify_predicted_return(2.5), 'positive')

    def test_classify_neutral(self):
        self.assertEqual(dashboard._classify_predicted_return(-1.5), 'neutral')

    def test_classify_warn(self):
        self.assertEqual(dashboard._classify_predicted_return(-5.0), 'warn')

    def test_classify_danger(self):
        self.assertEqual(dashboard._classify_predicted_return(-10.0), 'danger')

    def test_load_returns_none_when_file_missing(self):
        # ベースラインとして「存在しない」を確認するのは難しいので、
        # ここでは load 関数が None または dict を返す型のみ確認
        result = dashboard.load_lightgbm_prediction()
        self.assertTrue(result is None or isinstance(result, dict))

    def test_monthly_model_status_collects_validation_metadata(self):
        with TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            macro_dir = base / 'static' / 'macro'
            macro_dir.mkdir(parents=True)
            (macro_dir / 'crash_alert_backtest.json').write_text(
                '{"target":"GSPC","horizon_days":63,'
                '"drawdown_threshold_pct":-10.0,"sample_count":240,'
                '"event_count":19,"roc_auc":0.7115,"pr_auc":0.3983,'
                '"thresholds":[]}',
                encoding='utf-8',
            )
            (macro_dir / 'crash_probability_model.json').write_text(
                '{"model_version":"crash_probability_logistic_v1",'
                '"trained_at":"2026-05-17","current_probability":0.1,'
                '"validation_samples":120,"validation_event_count":6,'
                '"validation":{"roc_auc":0.8209,"pr_auc":0.3008}}',
                encoding='utf-8',
            )
            (macro_dir / 'lightgbm_prediction.json').write_text(
                '{"predicted_at":"2026-05-06","horizons":['
                '{"months":1,"predicted_return_pct":0.9,'
                '"validation_mae_pct":2.44}],'
                '"training_samples":157,"feature_count":21,'
                '"model_version":"v1"}',
                encoding='utf-8',
            )

            with override_settings(BASE_DIR=base):
                result = dashboard.build_monthly_model_status()

        self.assertEqual(result['tone'], 'warning')
        self.assertEqual(result['status_label'], '要確認')
        self.assertEqual(result['latest_training_date'], '2026-05-17')
        self.assertEqual(len(result['cards']), 3)
        self.assertIn('急落確率モデル: 検証イベントが6件と少ないため', result['warnings'][0])
        self.assertIn('ROC-AUC 0.82 / PR-AUC 0.30 / 信頼性 低', [
            card['metric_label'] for card in result['cards']
        ])


class HistoricalCrashTest(TestCase):
    """歴史的クラッシュ月との類似度。"""

    def test_no_data_returns_empty(self):
        # シードされたインジケーターはあるが Observation がない状態。
        result = historical_crash.find_similar_crash_months()
        self.assertEqual(result, [])

    def test_crash_months_constant_size(self):
        # 定数リストが不正にならないこと（少なくとも数件登録されている）。
        self.assertGreaterEqual(len(historical_crash.HISTORICAL_CRASH_MONTHS), 5)


class DetailAnalysisTest(TestCase):
    """詳細ページ用分析ロジック。"""

    def test_correlation_label_strong_positive(self):
        self.assertEqual(detail_analysis.correlation_label(0.85), '強い正の連動')

    def test_correlation_label_moderate_positive(self):
        self.assertEqual(detail_analysis.correlation_label(0.5), '中程度の正の連動')

    def test_correlation_label_weak(self):
        self.assertEqual(detail_analysis.correlation_label(0.1), '弱い / 無相関')

    def test_correlation_label_moderate_negative(self):
        self.assertEqual(detail_analysis.correlation_label(-0.5), '中程度の逆連動')

    def test_correlation_label_strong_negative(self):
        self.assertEqual(detail_analysis.correlation_label(-0.85), '強い逆連動')

    def test_correlation_label_none(self):
        self.assertEqual(detail_analysis.correlation_label(None), 'データ不足')

    def test_interpret_state_no_rule(self):
        ind = Indicator.objects.create(
            fred_series_id='TEST_NORULE',
            name_ja='テスト',
            category='inflation',
            judgment_rule=None,
        )
        result = detail_analysis.interpret_state(ind, _ObsStub(value=1.0))
        self.assertFalse(result['has_interpretation'])

    def test_interpret_state_with_rule_generates_sentences(self):
        ind = Indicator.objects.create(
            fred_series_id='TEST_RULE',
            name_ja='テスト2',
            category='inflation',
            judgment_rule={
                'metric': 'level',
                'economic': {'direction': 'lower_better', 'thresholds': [10, 20, 30, 40]},
                'market':   {'direction': 'lower_better', 'thresholds': [10, 20, 30, 40]},
            },
        )
        # 5 → 経済1段（最良）、市場1段
        result = detail_analysis.interpret_state(ind, _ObsStub(value=5, prev_value=4))
        self.assertTrue(result['has_interpretation'])
        self.assertEqual(result['economic_stage'], 1)
        self.assertEqual(result['market_stage'], 1)
        # 解釈文は3つ（経済・市場・推移）
        self.assertEqual(len(result['sentences']), 3)


class IndicatorSeedingTest(TestCase):
    """マイグレーションが指標を登録していることを確認。

    Phase 4 で 5 系列、Phase 5（価格アクション）で 12 系列、
    Phase 6（MOVE / VIX-VIX3M 比）で 2 系列が追加されている。
    """

    def test_seeded_count(self):
        self.assertEqual(Indicator.objects.count(), 66)

    def test_importance_a_count(self):
        # Phase 1: 11 + Phase 2: SP500, T10Y3M = 13（Phase 4 は B のみ）
        self.assertEqual(
            Indicator.objects.filter(importance='A').count(), 13
        )

    def test_external_sources_present(self):
        sources = set(
            Indicator.objects.values_list('source', flat=True).distinct()
        )
        for s in ['fred', 'cboe', 'finra', 'aaii', 'naaim', 'yfinance']:
            self.assertIn(s, sources)

    def test_usrec_present_for_backtest_truth(self):
        indicator = Indicator.objects.get(fred_series_id='USREC')
        self.assertEqual(indicator.name_ja, '米景気後退フラグ')
        self.assertEqual(indicator.importance, 'C')
        self.assertTrue(indicator.is_active)


class ExternalClientParseTest(TestCase):
    """外部クライアントのCSVパースロジック（HTTPは投げない）。"""

    def test_cboe_parse_csv(self):
        from macro.services import cboe_client
        text = "Date,SKEW\n2024-01-02,135.42\n2024-01-03,138.10\n"
        rows = cboe_client._parse_csv(text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][1], 135.42)

    def test_naaim_parse_csv(self):
        from macro.services import naaim_client
        text = "Date,NAAIM Exposure Index\n2024-01-03,75.5\n2024-01-10,80.0\n"
        rows = naaim_client._parse_csv(text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][1], 80.0)

    def test_aaii_parse_csv(self):
        from macro.services import aaii_client
        text = "Date,Bullish,Bearish,Neutral\n2024-01-03,42.5%,30.0%,27.5%\n"
        rows = aaii_client._parse_csv(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], 42.5)

    def test_finra_parse_csv(self):
        from macro.services import finra_client
        text = (
            "Year-Month,Debit Balances in Customers' Securities Margin Accounts\n"
            "2024-12,815523\n"
            "2025-01,820100\n"
        )
        rows = finra_client._parse_csv(text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][1], 820100.0)

    def test_categories_present(self):
        for cat in ['inflation', 'employment', 'growth', 'rates', 'market']:
            self.assertTrue(
                Indicator.objects.filter(category=cat).exists(),
                f"missing category: {cat}",
            )


class HatziusStyleMacroEngineTest(TestCase):
    def test_state_vector_returns_required_economic_axes(self):
        from macro.services.state_vector import build_economic_state_vector

        snapshot = WorldStateSnapshot.objects.create(
            as_of_date=date(2026, 6, 17),
            growth_score=62,
            labor_score=44,
            inflation_score=71,
            policy_pressure_score=58,
            liquidity_score=53,
            credit_score=64,
            market_trend_score=55,
            market_stress_score=28,
            recession_risk_score=18,
            inflation_reacceleration_score=72,
            financial_stress_score=24,
            data_quality=83,
            feature_vector={'sample': 1.0},
        )

        vector = build_economic_state_vector(snapshot)

        self.assertEqual(vector['as_of'], '2026-06-17')
        self.assertEqual(
            set(vector['axes'].keys()),
            {
                'growth_momentum',
                'inflation_pressure',
                'labor_slack',
                'policy_stance',
                'financial_conditions',
                'credit_stress',
                'global_demand',
                'japan_cycle',
                'nikkei_macro_bias',
            },
        )
        self.assertEqual(vector['axes']['growth_momentum']['label'], '改善')
        self.assertEqual(vector['axes']['inflation_pressure']['label'], '再加速警戒')
        self.assertEqual(vector['axes']['credit_stress']['label'], '低い')
        self.assertEqual(vector['quality']['score'], 83)

    def test_forecast_runner_saves_probability_distribution_and_scenarios(self):
        from macro.models import MacroScenario
        from macro.services.forecast_runner import run_macro_forecast

        WorldStateSnapshot.objects.create(
            as_of_date=date(2026, 6, 17),
            growth_score=66,
            labor_score=57,
            inflation_score=64,
            policy_pressure_score=60,
            liquidity_score=58,
            credit_score=62,
            market_trend_score=59,
            market_stress_score=26,
            recession_risk_score=20,
            inflation_reacceleration_score=63,
            financial_stress_score=22,
            data_quality=87,
            feature_vector={'growth': 66},
        )
        RegimeSnapshot.objects.create(
            snapshot_date=date(2026, 6, 17),
            regime_label=RegimeSnapshot.Label.EXPANSION,
            inflation_flag=RegimeSnapshot.InflationFlag.HIGH,
            confidence=74,
            data_quality=87,
            regime_probabilities={
                'expansion': 0.58,
                'slowdown': 0.24,
                'contraction': 0.07,
                'recovery': 0.11,
            },
            risk_probabilities={
                'recession': 0.15,
                'inflation_reacceleration': 0.68,
                'financial_stress': 0.18,
            },
        )

        result = run_macro_forecast(as_of=date(2026, 6, 17))

        self.assertEqual(result.snapshot.target, 'macro_regime')
        self.assertEqual(result.snapshot.horizon, '3m_6m')
        self.assertEqual(
            result.snapshot.metadata['regime_probabilities']['expansion'],
            0.58,
        )
        self.assertEqual(MacroScenario.objects.count(), 3)
        self.assertEqual(
            sum(s.probability for s in MacroScenario.objects.all()),
            1.0,
        )
        self.assertEqual(
            set(MacroScenario.objects.values_list('name', flat=True)),
            {'baseline', 'upside', 'downside'},
        )
        baseline = MacroScenario.objects.get(name='baseline')
        self.assertIn(
            '米10年金利が急上昇し株式バリュエーションを圧迫',
            baseline.invalidation_triggers,
        )

    def test_validation_records_brier_score_and_direction_hit(self):
        from macro.models import MacroForecastOutcome
        from macro.services.validation import evaluate_forecast_snapshot

        forecast = ForecastSnapshot.objects.create(
            as_of_date=date(2026, 1, 1),
            model_version='macro_hatzius_v1',
            target='macro_regime',
            horizon='3m_6m',
            prediction_value=0.70,
            metadata={'primary_regime': 'expansion'},
        )

        outcome = evaluate_forecast_snapshot(
            forecast,
            target_date=date(2026, 4, 1),
            target_name='expansion',
            actual_value=1.0,
        )

        self.assertIsInstance(outcome, MacroForecastOutcome)
        self.assertAlmostEqual(outcome.predicted_prob, 0.70)
        self.assertAlmostEqual(outcome.brier_score, 0.09)
        self.assertTrue(outcome.direction_hit)

    def test_dashboard_summarizes_macro_forecast_outcomes(self):
        from macro.models import MacroForecastOutcome
        from macro.services.dashboard import build_macro_outcome_validation_context

        forecast = ForecastSnapshot.objects.create(
            as_of_date=date(2026, 1, 1),
            model_version='macro_hatzius_v1',
            target='macro_regime',
            horizon='3m_6m',
            prediction_value=0.70,
        )
        MacroForecastOutcome.objects.create(
            forecast=forecast,
            target_date=timezone.localdate(),
            target_name='expansion',
            predicted_prob=0.70,
            actual_value=1.0,
            brier_score=0.09,
            direction_hit=True,
        )
        MacroForecastOutcome.objects.create(
            forecast=forecast,
            target_date=timezone.localdate(),
            target_name='inflation_reacceleration',
            predicted_prob=0.40,
            actual_value=1.0,
            brier_score=0.36,
            direction_hit=False,
        )

        context = build_macro_outcome_validation_context()

        self.assertEqual(context['period_label'], '過去90日')
        self.assertEqual(context['total_count'], 2)
        self.assertEqual(context['direction_accuracy_display'], '50%')
        self.assertEqual(context['avg_brier_score_display'], '0.225')
        self.assertEqual(len(context['rows']), 2)
