from django.test import SimpleTestCase
from django.core.management import call_command
from io import StringIO

from django.core.management.base import CommandError
from django.test import TestCase

from .snapshot import write_basecalc_snapshot


class BasecalcOutputContractTests(SimpleTestCase):
    def test_contract_blocks_stale_targets_after_display_price_changes(self):
        from .output_contract import apply_output_contract

        world_model = {
            'price': 66670,
            'direction': 'up',
            'direction_label': '上昇優勢',
            'readiness_level': 'ready',
            'directional_allowed': True,
            'upside_targets': [{'label': 'T1', 'price': 68570, 'probability': 0.62}],
            'downside_targets': [{'label': 'T1', 'price': 64770, 'probability': 0.45}],
            'target_ranges': [{'horizon': '1d', 'low': 64960, 'high': 68380}],
            'horizons': {'1d': {'main_bias': 'up', 'expected_return_pct': 0.4}},
            'similar_summary': {'case_count': 42, 'is_statistically_valid': True},
            'confidence_score': 72,
            'us_index_confirmation': {
                'readiness': {'usable': True},
                'components': {'nasdaq100': {}, 'sp500': {}, 'dow': {}},
            },
        }

        contract = apply_output_contract(world_model, display_price=71620)

        self.assertEqual(contract['contract_status'], 'error')
        self.assertFalse(contract['directional_allowed'])
        self.assertFalse(contract['target_display_allowed'])
        self.assertFalse(contract['probability_display_allowed'])
        self.assertEqual(world_model['upside_targets'], [])
        self.assertEqual(world_model['target_ranges'], [])
        self.assertIn('現在値と計算基準価格が不一致', contract['stop_reasons'])
        self.assertIn('上値目標が現在値より下にあります', contract['stop_reasons'])
        self.assertIn('レンジ上限が現在値より下にあります', contract['stop_reasons'])

    def test_contract_stops_direction_when_expected_return_conflicts(self):
        from .output_contract import apply_output_contract

        world_model = {
            'price': 41000,
            'direction': 'up',
            'direction_label': '上昇優勢',
            'readiness_level': 'ready',
            'directional_allowed': True,
            'upside_targets': [{'label': 'T1', 'price': 41800, 'probability': 0.62}],
            'downside_targets': [{'label': 'T1', 'price': 40400, 'probability': 0.45}],
            'target_ranges': [{'horizon': '1d', 'low': 40500, 'high': 41500}],
            'horizons': {
                '1d': {'main_bias': 'up', 'expected_return_pct': -0.39},
                '3d': {'main_bias': 'down', 'expected_return_pct': 0.21},
            },
            'similar_summary': {'case_count': 30, 'is_statistically_valid': True},
            'confidence_score': 61,
            'us_index_confirmation': {
                'readiness': {'usable': True},
                'components': {'nasdaq100': {}, 'sp500': {}, 'dow': {}},
            },
        }

        contract = apply_output_contract(world_model, display_price=41000)

        self.assertEqual(contract['contract_status'], 'error')
        self.assertFalse(contract['allowed_horizons']['1d']['direction_allowed'])
        self.assertFalse(contract['allowed_horizons']['3d']['direction_allowed'])
        self.assertEqual(world_model['horizons']['1d']['main_bias'], 'range')
        self.assertEqual(world_model['direction_label'], '方向判断停止')
        self.assertIn('方向と期待リターンが矛盾', contract['stop_reasons'])

    def test_validation_gate_blocks_horizon_when_atr_baseline_beats_model(self):
        from .output_contract import apply_output_contract

        world_model = {
            'price': 41000,
            'direction': 'up',
            'direction_label': '上昇優勢',
            'readiness_level': 'ready',
            'directional_allowed': True,
            'upside_targets': [{'label': 'T1', 'price': 41800, 'probability': 0.62}],
            'downside_targets': [{'label': 'T1', 'price': 40400, 'probability': 0.45}],
            'target_ranges': [{'horizon': '1d', 'low': 40500, 'high': 41500}],
            'horizons': {'1d': {'main_bias': 'up', 'expected_return_pct': 0.4}},
            'similar_summary': {'case_count': 35, 'is_statistically_valid': True},
            'confidence_score': 65,
            'state_key': 'exhaustion_top',
            'state_label': '過熱・反落警戒',
            'us_index_confirmation': {
                'readiness': {'usable': True},
                'components': {'nasdaq100': {}, 'sp500': {}, 'dow': {}},
            },
        }
        validation_report = {
            'horizons': {
                '1d': {
                    'summary': {
                        'baseline_comparison': {
                            'sample_count': 30,
                            'rows': [
                                {'key': 'model', 'risk_adjusted_return_pct': -0.08, 'balanced_accuracy': 0.32},
                                {'key': 'atr_range', 'risk_adjusted_return_pct': 0.54, 'balanced_accuracy': 0.63},
                            ],
                        },
                    },
                    'state_summaries': [
                        {'state_key': 'exhaustion_top', 'avg_return_pct': -0.39, 'directional_accuracy': 0.31},
                    ],
                },
            },
        }

        contract = apply_output_contract(world_model, display_price=41000, validation_report=validation_report)

        self.assertEqual(contract['validation_gate_status']['1d']['display_mode'], 'range_only')
        self.assertFalse(contract['allowed_horizons']['1d']['direction_allowed'])
        self.assertFalse(contract['directional_allowed'])
        self.assertIn('現行モデルがATRベースラインを下回るため', contract['stop_reasons'])
        self.assertIn('過熱・反落警戒の過去成績が弱いため', contract['stop_reasons'])

    def test_confidence_is_capped_when_uncalibrated_or_state_is_weak(self):
        from .output_contract import apply_output_contract

        world_model = {
            'price': 41000,
            'direction': 'up',
            'direction_label': '上昇優勢',
            'readiness_level': 'ready',
            'directional_allowed': True,
            'confidence': 'High',
            'confidence_score': 78,
            'state_key': 'exhaustion_top',
            'state_label': '過熱・反落警戒',
            'upside_targets': [{'label': 'T1', 'price': 41800, 'probability': None}],
            'downside_targets': [{'label': 'T1', 'price': 40400, 'probability': None}],
            'target_ranges': [{'horizon': '1d', 'low': 40500, 'high': 41500}],
            'horizons': {'1d': {'main_bias': 'up', 'expected_return_pct': 0.4}},
            'similar_summary': {'case_count': 8, 'is_statistically_valid': False},
            'us_index_confirmation': {
                'readiness': {'usable': True},
                'components': {'nasdaq100': {}, 'sp500': {}, 'dow': {}},
            },
        }
        validation_report = {
            'horizons': {
                '1d': {
                    'state_summaries': [
                        {'state_key': 'exhaustion_top', 'avg_return_pct': -0.39, 'directional_accuracy': 0.31},
                    ],
                    'confidence_calibration_rows': [
                        {'bucket': '50台', 'avg_return_pct': 0.2},
                        {'bucket': '70台', 'avg_return_pct': -0.1},
                    ],
                },
            },
        }

        contract = apply_output_contract(world_model, display_price=41000, validation_report=validation_report)

        self.assertFalse(contract['confidence_calibrated'])
        self.assertEqual(contract['confidence_status'], '未較正')
        self.assertEqual(world_model['confidence_score'], 49)
        self.assertEqual(world_model['confidence'], 'Low')
        self.assertIn('信頼度が未較正です', contract['stop_reasons'])
        self.assertIn('類似事例不足のため信頼度を50未満に制限', contract['stop_reasons'])


class BasecalcOutputContractCommandTests(TestCase):
    def test_check_command_passes_when_snapshot_contract_stops_directional_display(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path

        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'latest_snapshot.json'
            write_basecalc_snapshot(
                {
                    'world_model': {
                        'price': 71620,
                        'direction': 'up',
                        'readiness_level': 'ready',
                        'data_quality_score': 90,
                        'upside_targets': [{'label': 'T1', 'price': 68570}],
                        'downside_targets': [{'label': 'T1', 'price': 64770}],
                        'target_ranges': [{'horizon': '1d', 'low': 64960, 'high': 68380}],
                        'horizons': {'1d': {'main_bias': 'up', 'expected_return_pct': -0.39}},
                        'similar_summary': {'case_count': 40, 'is_statistically_valid': True},
                        'us_index_confirmation': {
                            'readiness': {'usable': True},
                            'components': {'nasdaq100': {}, 'sp500': {}, 'dow': {}},
                        },
                    },
                },
                path=path,
            )

            stdout = StringIO()
            call_command('check_basecalc_output_contract', '--snapshot', str(path), stdout=stdout)

            output = stdout.getvalue()
            self.assertIn('basecalc output contract stopped directional display', output)
            self.assertIn('basecalc output contract ok', output)

    def test_check_command_fails_when_snapshot_is_missing(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path

        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'missing.json'

            with self.assertRaisesMessage(CommandError, 'snapshot is missing'):
                call_command('check_basecalc_output_contract', '--snapshot', str(path))
