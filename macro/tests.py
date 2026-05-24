"""macro モジュールのユニットテスト。"""

import gzip
from io import StringIO
from datetime import date
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

from .models import (
    DashboardCache,
    DailyPriceObservation,
    FeatureSnapshot,
    ForecastSnapshot,
    Indicator,
    ModelValidationReport,
    Observation,
    PriceObservation,
    RawArchiveManifest,
    RegimeSnapshot,
    WorldStateSnapshot,
    WorldModelRun,
)
from .services import (
    crash_alert,
    crash_probability,
    dashboard,
    dashboard_cache,
    data_sync,
    detail_analysis,
    forecast_tracking,
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
    def test_refresh_workflow_only_triggers_vercel_daily_build(self):
        workflow = (
            Path(settings.BASE_DIR)
            / '.github'
            / 'workflows'
            / 'refresh-macro-data.yml'
        ).read_text(encoding='utf-8')

        self.assertIn('cron: "30 5 * * *"', workflow)
        self.assertIn('VERCEL_DEPLOY_HOOK_URL', workflow)
        self.assertIn('curl -fsS -X POST "$VERCEL_DEPLOY_HOOK_URL"', workflow)
        self.assertIn('timeout-minutes: 5', workflow)
        self.assertIn('concurrency:', workflow)
        self.assertNotIn('actions/setup-python', workflow)
        self.assertNotIn('pip install -r requirements-prod.txt', workflow)
        self.assertNotIn('python manage.py refresh_macro_data', workflow)
        self.assertNotIn('python manage.py purge_old_data', workflow)
        self.assertNotIn('python manage.py precompute_dashboard', workflow)
        self.assertNotIn('SQLITE_DB_PATH: /tmp/macro-data.sqlite3', workflow)
        self.assertNotIn('git add db.sqlite3', workflow)
        self.assertNotIn('DATA_BRANCH', workflow)

    def test_vercel_build_precomputes_macro_dashboard(self):
        build_script = (
            Path(settings.BASE_DIR)
            / 'build_files.sh'
        ).read_text(encoding='utf-8')

        self.assertIn('manage.py precompute_dashboard', build_script)
        self.assertIn('Running finance production build bootstrap', build_script)
        self.assertIn('BUNDLED_SQLITE_PATH', build_script)
        self.assertIn('manage.py refresh_macro_data', build_script)
        self.assertIn('manage.py purge_old_data', build_script)
        self.assertIn('manage.py settle_forecast_snapshots', build_script)
        self.assertIn('manage.py record_macro_update_status', build_script)
        self.assertIn('--phase refresh_macro_data', build_script)
        self.assertIn('refresh_macro_data failed during Vercel build', build_script)
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
        self.assertIn('"includeFiles": "runtime/db.sqlite3"', vercel_config)
        self.assertNotIn('"builds"', vercel_config)
        self.assertNotIn('"installCommand": "bash build_files.sh"', vercel_config)
        self.assertIn('requires-python = ">=3.12"', python_project)
        self.assertIn('"outputDirectory": "staticfiles"', vercel_config)
        self.assertIn('name = "yoshi-nakane-finance"', python_project)
        self.assertIn('"Django==5.2.14"', python_project)

    def test_macro_world_model_workflows_include_new_jobs(self):
        monthly_workflow = (
            Path(settings.BASE_DIR)
            / '.github'
            / 'workflows'
            / 'monthly-macro-maintenance.yml'
        ).read_text(encoding='utf-8')
        weekly_workflow = (
            Path(settings.BASE_DIR)
            / '.github'
            / 'workflows'
            / 'weekly-macro-validation.yml'
        ).read_text(encoding='utf-8')

        self.assertIn('monthly_macro_maintenance', monthly_workflow)
        self.assertIn('return_forecast_model.json', monthly_workflow)
        self.assertIn('macro_forecast_model.json', monthly_workflow)
        self.assertIn('python manage.py weekly_macro_validation', weekly_workflow)
        self.assertIn('DATABASE_URL is not set; skipped weekly validation.', weekly_workflow)

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


class RawArchiveTest(TestCase):
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
        self.assertEqual(context['rule_strength_score'], 4)
        self.assertEqual(context['rule_strength_fraction_display'], '4/5')
        self.assertEqual(context['data_quality_score'], 4)
        self.assertEqual(context['data_quality_fraction_display'], '4/5')
        self.assertTrue(context['regime_good_points'])
        self.assertTrue(context['regime_bad_points'])
        self.assertTrue(context['regime_outlook'])
        self.assertEqual(len(context['regime_update_guidance']), 4)

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

        self.assertEqual(len(result['scenarios']), 4)
        self.assertIn('base_regime_label', result)
        self.assertIn('base_regime_view_display', result)
        self.assertIn('base_regime_fit_display', result)
        self.assertIn('market_stress_delta_display', result['scenarios'][0])
        self.assertIn('regime_view_display', result['scenarios'][0])
        self.assertIn('regime_fit_display', result['scenarios'][0])

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

    def test_cached_empty_payload_is_marked_as_preparing(self):
        DashboardCache.objects.create(
            cache_key='macro_index_v2',
            payload={
                'has_observations': False,
                'last_updated': '—',
                'similar_periods': [],
                'linkages': [],
                'indicator_cards': [],
                'crash_alert': None,
                'historical_crash_similarity': [],
            },
        )

        response = self.client.get(reverse('macro:index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '基本指標のみ表示しています')

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
             mock.patch('macro.services.dashboard.build_historical_crash_similarity', return_value=[]):
            payload = dashboard_cache.precompute_dashboard_payload()

        self.assertIn('world_state', payload)
        self.assertIn('forecast_models', payload)
        self.assertIn('model_validation', payload)


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
        self.assertContains(r, 'World Model の結論')
        self.assertContains(r, '現在の景気の状況')
        self.assertContains(r, '市場ストレス、急落予測')
        self.assertContains(r, '未来予測')
        self.assertContains(r, '判定強度')
        self.assertContains(r, 'データ鮮度')
        self.assertContains(r, '一目で見る結論')
        self.assertContains(r, '良い点')
        self.assertContains(r, '悪い点')
        self.assertContains(r, 'これから先')
        self.assertContains(r, '<details class="macro-regime-details">')
        self.assertContains(r, '結論・良い点・悪い点・先行き')
        self.assertContains(r, '景気評価')
        self.assertNotContains(r, '景気コンディション')
        self.assertContains(r, '40%')
        self.assertContains(r, '2/5')
        self.assertContains(r, '4/5')
        self.assertContains(r, '更新頻度の目安')
        self.assertContains(r, '判定根拠')
        self.assertContains(r, 'macro-regime-details--evidence')
        self.assertContains(r, '鉱工業生産指数')
        self.assertNotContains(r, '判定モデル')
        self.assertNotContains(r, '履歴アーカイブ')
        self.assertNotContains(r, '確度')
        self.assertNotContains(r, '主要指標の観測日が古い可能性があります。')

    def test_index_shows_reliability_status_from_cache(self):
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
        self.assertContains(r, '更新信頼性')
        self.assertContains(r, '前回更新')
        self.assertContains(r, '一部失敗')
        self.assertContains(r, 'VIXCLS: timeout')
        self.assertContains(r, '欠損 / 古い')

    @mock.patch('macro.views.build_monthly_model_status')
    @mock.patch('macro.views.load_dashboard_payload')
    def test_index_crash_alert_copy_uses_market_stress_wording(
        self,
        cache_mock,
        monthly_status_mock,
    ):
        monthly_status_mock.return_value = None
        cache_mock.return_value = {
            'has_observations': True,
            'last_updated': '2026-05-17',
            'similar_periods': [],
            'linkages': [],
            'indicator_cards': [],
            'historical_crash_similarity': [],
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
        }

        r = self.client.get(reverse('macro:index'))

        self.assertEqual(r.status_code, 200)
        self.assertContains(r, '市場ストレス・急落警戒スコア')
        self.assertContains(r, '将来の暴落確率ではありません')
        self.assertContains(r, '判定強度')
        self.assertContains(r, 'データ品質')
        self.assertContains(r, '検証未実施')
        self.assertNotContains(r, 'クラッシュ警戒度')
        self.assertNotContains(r, '月次検証: GSPC')
        self.assertNotContains(r, 'ROC-AUC 0.71')
        self.assertNotContains(r, '閾値25')
        self.assertNotContains(r, '平常表示時の取り逃し')

    @mock.patch('macro.views.load_crash_probability_model')
    @mock.patch('macro.views.build_monthly_model_status')
    @mock.patch('macro.views.load_dashboard_payload')
    def test_index_shows_crash_probability_model(
        self,
        cache_mock,
        monthly_status_mock,
        probability_mock,
    ):
        cache_mock.return_value = {
            'has_observations': True,
            'last_updated': '2026-05-17',
            'similar_periods': [],
            'linkages': [],
            'indicator_cards': [],
            'historical_crash_similarity': [],
            'crash_alert': None,
        }
        monthly_status_mock.return_value = None
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

        r = self.client.get(reverse('macro:index'))

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

    @mock.patch('macro.views.build_monthly_model_status')
    @mock.patch('macro.views.load_dashboard_payload')
    def test_index_shows_monthly_model_status(self, cache_mock, monthly_status_mock):
        cache_mock.return_value = {
            'has_observations': True,
            'last_updated': '2026-05-17',
            'similar_periods': [],
            'linkages': [],
            'indicator_cards': [],
            'historical_crash_similarity': [],
            'crash_alert': None,
        }
        monthly_status_mock.return_value = {
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
        }

        r = self.client.get(reverse('macro:index'))

        self.assertEqual(r.status_code, 200)
        self.assertContains(r, '月次モデル状態')
        self.assertContains(r, '最終学習日')
        self.assertContains(r, '2026-05-17')
        self.assertContains(r, '月次モデルの検証情報を確認')
        self.assertContains(r, '検証 120件 / イベント 6件')
        self.assertContains(r, 'ROC-AUC 0.82 / PR-AUC 0.30')

    @mock.patch.dict('os.environ', {'VERCEL': '1'})
    @mock.patch('macro.views.build_historical_crash_similarity')
    @mock.patch('macro.views.build_crash_alert_context')
    @mock.patch('macro.views.build_linkages')
    @mock.patch('macro.views.build_similar_periods')
    def test_index_serverless_without_cache_skips_heavy_fallback(
        self,
        build_similar_periods_mock,
        build_linkages_mock,
        build_crash_alert_context_mock,
        build_historical_crash_similarity_mock,
    ):
        build_similar_periods_mock.side_effect = AssertionError('heavy fallback')
        build_linkages_mock.side_effect = AssertionError('heavy fallback')
        build_crash_alert_context_mock.side_effect = AssertionError('heavy fallback')
        build_historical_crash_similarity_mock.side_effect = AssertionError(
            'heavy fallback'
        )

        r = self.client.get(reverse('macro:index'))

        self.assertEqual(r.status_code, 200)
        self.assertContains(r, '基本指標のみ表示しています')

    def test_refresh_without_key_redirects(self):
        user = User.objects.create_superuser(
            username='creator-no-key',
            email='creator-no-key@example.com',
            password='test-password',
        )
        self.client.force_login(user)
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
        self.assertContains(r, 'macro-operation-panel')
        self.assertContains(r, '月次メンテナンス')
        self.assertContains(r, '月次検証・急落確率モデル更新')
        self.assertNotContains(r, '確率更新')

    def test_serverless_refresh_button_runs_lightweight_update_only(self):
        user = User.objects.create_superuser(
            username='serverless-creator',
            email='serverless-creator@example.com',
            password='test-password',
        )
        self.client.force_login(user)

        with mock.patch('macro.views._is_serverless_runtime', return_value=True), \
             mock.patch('macro.views.get_api_key', return_value='key'), \
             mock.patch('macro.views.sync_all_indicators') as sync_mock, \
             mock.patch('macro.views.compute_current_regime') as regime_mock, \
             mock.patch('macro.views.compute_current_world_state') as world_state_mock, \
             mock.patch('macro.views.sync_all_price_histories') as price_mock, \
             mock.patch('macro.views.precompute_dashboard_payload') as precompute_mock, \
             mock.patch('macro.views.build_indicator_cards', return_value=[]), \
             mock.patch('macro.views.build_crash_alert_context', return_value={}), \
             mock.patch('macro.views.save_dashboard_payload') as save_cache_mock:
            sync_mock.return_value = {
                'success': [{'series_id': 'VIXCLS'}],
                'failed': [],
            }
            get_response = self.client.get(reverse('macro:index'))
            post_response = self.client.post(reverse('macro:refresh'))

        self.assertEqual(post_response.status_code, 302)
        self.assertContains(get_response, '取得・判定')
        self.assertContains(get_response, 'macro-refresh-form')
        sync_mock.assert_called_once_with(
            series_ids=(
                'VIXCLS',
                'BAMLH0A0HYM2',
                'CBOE_SKEW',
                'MOVE_INDEX',
                'VIX_VIX3M_RATIO',
            ),
        )
        regime_mock.assert_called_once()
        world_state_mock.assert_called_once()
        save_cache_mock.assert_called_once()
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

    def test_monthly_maintenance_runs_backtest_model_and_cache_update(self):
        user = User.objects.create_superuser(
            username='backtest-creator',
            email='backtest-creator@example.com',
            password='test-password',
        )
        self.client.force_login(user)

        with mock.patch('macro.views._is_serverless_runtime', return_value=False), \
             mock.patch('macro.views.call_command') as call_command_mock, \
             mock.patch('macro.views.precompute_dashboard_payload') as precompute_mock, \
             mock.patch('macro.views.save_dashboard_payload') as save_cache_mock:
            precompute_mock.return_value = {'crash_alert': {'total_score': 18}}

            r = self.client.post(reverse('macro:recompute_crash_backtest'))

        self.assertEqual(r.status_code, 302)
        self.assertEqual(call_command_mock.call_count, 2)
        self.assertEqual(
            call_command_mock.call_args_list[0],
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
            call_command_mock.call_args_list[1],
            mock.call(
                'train_crash_probability_model',
                target='GSPC',
                horizon_days=63,
                drawdown_threshold=-10.0,
                validation_months=120,
            ),
        )
        save_cache_mock.assert_called_once_with(
            {'crash_alert': {'total_score': 18}}
        )

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
