from django.test import SimpleTestCase
from django.core.management import call_command
from io import StringIO

from django.core.management.base import CommandError
from django.test import TestCase

from .snapshot import write_basecalc_snapshot


class BasecalcOutputContractTests(SimpleTestCase):
    def test_validation_gate_penalizes_low_sample_direction_evidence_by_ten_without_blocking(self):
        from .validation_gate import build_validation_gate

        gate = build_validation_gate(
            {
                'direction': 'up',
                'state_key': 'dip_buy',
                'state_label': '押し目買い',
            },
            validation_report={
                'horizons': {
                    '1d': {
                        'state_direction_summaries': [
                            {
                                'state_key': 'dip_buy',
                                'direction': 'up',
                                'total_predictions': 8,
                                'directional_accuracy': 0.48,
                                'avg_return_pct': 0.03,
                            },
                        ],
                    },
                },
            },
        )

        self.assertTrue(gate['1d']['direction_allowed'])
        self.assertEqual(gate['1d']['validation_level'], 'low')
        self.assertEqual(gate['1d']['confidence_penalty'], 10)
        self.assertEqual(gate['1d']['reasons'], [])
        self.assertEqual(gate['1d']['hard_reasons'], [])
        self.assertIn('押し目買いの上方向検証件数が不足しているため', gate['1d']['warnings'])
        self.assertIn('押し目買いの上方向検証件数が不足しているため', gate['1d']['soft_reasons'])

    def test_validation_gate_exposes_hard_and_soft_reason_fields(self):
        from .validation_gate import build_validation_gate

        gate = build_validation_gate(
            {
                'direction': 'up',
                'state_key': 'dip_buy',
                'state_label': '押し目買い',
            },
            validation_report={
                'horizons': {
                    '1d': {
                        'state_direction_summaries': [
                            {
                                'state_key': 'dip_buy',
                                'direction': 'up',
                                'total_predictions': 40,
                                'directional_accuracy': 0.42,
                                'avg_return_pct': 0.03,
                            },
                        ],
                    },
                    '3d': {
                        'state_direction_summaries': [
                            {
                                'state_key': 'dip_buy',
                                'direction': 'up',
                                'total_predictions': 8,
                                'directional_accuracy': 0.48,
                                'avg_return_pct': 0.03,
                            },
                        ],
                    },
                },
            },
        )

        self.assertFalse(gate['1d']['direction_allowed'])
        self.assertIn('押し目買いの上方向精度が基準未達のため', gate['1d']['hard_reasons'])
        self.assertEqual(gate['1d']['soft_reasons'], [])
        self.assertTrue(gate['3d']['direction_allowed'])
        self.assertEqual(gate['3d']['hard_reasons'], [])
        self.assertIn('押し目買いの上方向検証件数が不足しているため', gate['3d']['soft_reasons'])

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
        self.assertEqual(contract['explanation_allowed'], 'blocked')
        self.assertFalse(contract['directional_allowed'])
        self.assertFalse(contract['target_display_allowed'])
        self.assertFalse(contract['probability_display_allowed'])
        self.assertEqual(world_model['upside_targets'], [])
        self.assertEqual(world_model['target_ranges'], [])
        self.assertIn('現在値と計算基準価格が不一致', contract['stop_reasons'])
        self.assertIn('上値目標が現在値より下にあります', contract['stop_reasons'])
        self.assertIn('レンジ上限が現在値より下にあります', contract['stop_reasons'])

    def test_contract_exposes_hard_stop_reasons_separately_from_soft_warnings(self):
        from .output_contract import apply_output_contract

        world_model = {
            'price': 66670,
            'direction': 'up',
            'direction_label': '上昇優勢',
            'readiness_level': 'ready',
            'directional_allowed': True,
            'upside_targets': [{'label': 'T1', 'price': 68570, 'probability': None}],
            'downside_targets': [{'label': 'T1', 'price': 64770, 'probability': None}],
            'target_ranges': [{'horizon': '1d', 'low': 64960, 'high': 68380}],
            'horizons': {'1d': {'main_bias': 'up', 'expected_return_pct': 0.4}},
            'similar_summary': {'case_count': 42, 'is_statistically_valid': True},
            'confidence_score': 72,
            'us_index_confirmation': {
                'readiness': {'usable': False},
                'components': {},
            },
        }

        contract = apply_output_contract(world_model, display_price=71620)

        self.assertEqual(contract['contract_status'], 'error')
        self.assertIn('現在値と計算基準価格が不一致', contract['hard_stop_reasons'])
        self.assertIn('上値目標が現在値より下にあります', contract['hard_stop_reasons'])
        self.assertNotIn('米国3指数確認が不足', contract['hard_stop_reasons'])
        self.assertIn('米国3指数確認が不足', contract['soft_warning_reasons'])
        self.assertEqual(contract['hard_stop_reasons'], contract['hard_block_reasons'])

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

        self.assertEqual(contract['validation_gate_status']['1d']['display_mode'], 'directional')
        self.assertTrue(contract['allowed_horizons']['1d']['direction_allowed'])
        self.assertTrue(contract['directional_allowed'])
        self.assertTrue(contract['target_display_allowed'])
        self.assertTrue(contract['probability_display_allowed'])
        self.assertEqual(contract['available_display'], '方向・目標・レンジ')
        self.assertIn('現行モデルがATRベースラインを下回るため', contract['soft_warning_reasons'])
        self.assertIn('過熱・反落警戒の過去成績が弱い可能性があるため', contract['soft_warning_reasons'])

    def test_validation_gate_keeps_direction_when_evidence_is_insufficient(self):
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
            'confidence': 'High',
            'confidence_score': 72,
            'state_key': 'dip_buy',
            'state_label': '押し目買い',
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
                            'sample_count': 24,
                            'rows': [
                                {'key': 'model', 'risk_adjusted_return_pct': 0.1, 'balanced_accuracy': 0.49},
                                {'key': 'atr_range', 'risk_adjusted_return_pct': 0.2, 'balanced_accuracy': 0.52},
                            ],
                        },
                    },
                    'state_direction_summaries': [
                        {
                            'state_key': 'dip_buy',
                            'direction': 'up',
                            'total_predictions': 8,
                            'directional_accuracy': 0.48,
                            'avg_return_pct': 0.03,
                        },
                    ],
                },
            },
        }

        contract = apply_output_contract(world_model, display_price=41000, validation_report=validation_report)

        self.assertEqual(contract['contract_status'], 'limited')
        self.assertTrue(contract['allowed_horizons']['1d']['direction_allowed'])
        self.assertTrue(contract['directional_allowed'])
        self.assertEqual(contract['allowed_direction'], 'up')
        self.assertEqual(contract['validation_gate_status']['1d']['validation_level'], 'low')
        self.assertIn('押し目買いの上方向検証件数が不足しているため', contract['soft_warning_reasons'])
        self.assertIn('現行モデルがATRベースラインを下回るため', contract['soft_warning_reasons'])
        self.assertEqual(contract['hard_block_reasons'], [])
        self.assertGreaterEqual(world_model['confidence_score'], 50)

    def test_validation_gate_blocks_only_when_sufficient_evidence_is_bad(self):
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
            'state_key': 'dip_buy',
            'state_label': '押し目買い',
            'us_index_confirmation': {
                'readiness': {'usable': True},
                'components': {'nasdaq100': {}, 'sp500': {}, 'dow': {}},
            },
        }
        validation_report = {
            'horizons': {
                '1d': {
                    'state_direction_summaries': [
                        {
                            'state_key': 'dip_buy',
                            'direction': 'up',
                            'total_predictions': 90,
                            'directional_accuracy': 0.42,
                            'avg_return_pct': -0.18,
                        },
                    ],
                },
            },
        }

        contract = apply_output_contract(world_model, display_price=41000, validation_report=validation_report)

        self.assertEqual(contract['contract_status'], 'limited')
        self.assertFalse(contract['allowed_horizons']['1d']['direction_allowed'])
        self.assertFalse(contract['directional_allowed'])
        self.assertIn('押し目買いの上方向精度が基準未達のため', contract['hard_block_reasons'])
        self.assertIn('押し目買いの上方向平均損益が逆方向のため', contract['hard_block_reasons'])

    def test_direction_gate_uses_state_direction_precision_when_available(self):
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
                '1d': {'main_bias': 'up', 'expected_return_pct': 0.4},
                '3d': {'main_bias': 'up', 'expected_return_pct': 0.8},
                '5d': {'main_bias': 'up', 'expected_return_pct': 1.0},
            },
            'similar_summary': {'case_count': 35, 'is_statistically_valid': True},
            'confidence_score': 65,
            'state_key': 'dip_buy',
            'state_label': '押し目買い',
            'us_index_confirmation': {
                'readiness': {'usable': True},
                'components': {'nasdaq100': {}, 'sp500': {}, 'dow': {}},
            },
        }
        weak_baseline = {
            'baseline_comparison': {
                'sample_count': 300,
                'rows': [
                    {'key': 'model', 'risk_adjusted_return_pct': -0.08, 'balanced_accuracy': 0.32, 'directional_accuracy': 0.39},
                    {'key': 'atr_range', 'risk_adjusted_return_pct': 0.54, 'balanced_accuracy': 0.63, 'directional_accuracy': 0.63},
                ],
            },
        }
        validation_report = {
            'horizons': {
                '1d': {
                    'summary': weak_baseline,
                    'state_direction_summaries': [
                        {'state_key': 'dip_buy', 'direction': 'up', 'total_predictions': 383, 'directional_accuracy': 0.44, 'avg_return_pct': 0.07},
                    ],
                },
                '3d': {
                    'summary': weak_baseline,
                    'state_direction_summaries': [
                        {'state_key': 'dip_buy', 'direction': 'up', 'total_predictions': 382, 'directional_accuracy': 0.61, 'avg_return_pct': 0.14},
                    ],
                },
                '5d': {
                    'summary': weak_baseline,
                    'state_direction_summaries': [
                        {'state_key': 'dip_buy', 'direction': 'up', 'total_predictions': 383, 'directional_accuracy': 0.66, 'avg_return_pct': 0.48},
                    ],
                },
            },
        }

        contract = apply_output_contract(world_model, display_price=41000, validation_report=validation_report)

        self.assertFalse(contract['allowed_horizons']['1d']['direction_allowed'])
        self.assertTrue(contract['allowed_horizons']['3d']['direction_allowed'])
        self.assertTrue(contract['allowed_horizons']['5d']['direction_allowed'])
        self.assertTrue(contract['directional_allowed'])
        self.assertEqual(contract['allowed_direction'], 'up')
        self.assertEqual(contract['available_display'], '方向・目標・レンジ')
        self.assertIn('押し目買いの上方向精度が基準未達のため', contract['stop_reasons'])

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
        self.assertEqual(world_model['confidence_score'], 59)
        self.assertEqual(world_model['confidence'], 'Middle')
        self.assertIn('信頼度が未較正です', contract['stop_reasons'])
        self.assertIn('類似事例不足のため信頼度を限定', contract['stop_reasons'])

    def test_warning_only_contract_keeps_direction_and_targets_visible(self):
        from .output_contract import apply_output_contract

        world_model = {
            'price': 41000,
            'direction': 'up',
            'direction_label': '上昇優勢',
            'readiness_level': 'ready',
            'directional_allowed': True,
            'confidence': 'Middle',
            'confidence_score': 58,
            'upside_targets': [{'label': 'T1', 'price': 41800, 'probability': None}],
            'downside_targets': [{'label': 'T1', 'price': 40400, 'probability': None}],
            'target_ranges': [{'horizon': '1d', 'low': 40500, 'high': 41500}],
            'horizons': {'1d': {'main_bias': 'up', 'expected_return_pct': 0.4}},
            'similar_summary': {'case_count': 0, 'is_statistically_valid': False},
            'us_index_confirmation': {
                'readiness': {'usable': True},
                'components': {'nasdaq100': {}, 'sp500': {}, 'dow': {}},
            },
        }

        contract = apply_output_contract(world_model, display_price=41000)

        self.assertEqual(contract['contract_status'], 'limited')
        self.assertEqual(contract['display_status'], 'limited_candidate')
        self.assertEqual(contract['explanation_allowed'], 'limited')
        self.assertTrue(contract['directional_allowed'])
        self.assertTrue(contract['target_display_allowed'])
        self.assertFalse(contract['probability_display_allowed'])
        self.assertEqual(contract['allowed_direction'], 'up')
        self.assertIn('類似事例不足のため信頼度を限定', contract['stop_reasons'])
        self.assertIn('類似事例不足のため信頼度を限定', contract['confidence_cap_reason'])
        self.assertIn('類似事例不足のため信頼度を限定', contract['validation_warnings'])
        self.assertEqual(contract['hard_block_reasons'], [])

    def test_output_contract_exposes_confirmed_display_status_when_no_limits_remain(self):
        from .output_contract import apply_output_contract

        world_model = {
            'price': 41000,
            'direction': 'up',
            'direction_label': '上昇優勢',
            'readiness_level': 'ready',
            'directional_allowed': True,
            'confidence': 'High',
            'confidence_score': 78,
            'upside_targets': [{'label': 'T1', 'price': 41800, 'probability': 0.62}],
            'downside_targets': [{'label': 'T1', 'price': 40400, 'probability': 0.45}],
            'target_ranges': [{'horizon': '1d', 'low': 40500, 'high': 41500}],
            'horizons': {'1d': {'main_bias': 'up', 'expected_return_pct': 0.4}},
            'similar_summary': {'case_count': 40, 'is_statistically_valid': True},
            'state_key': 'dip_buy',
            'state_label': '押し目買い',
            'us_index_confirmation': {
                'readiness': {'usable': True},
                'components': {'nasdaq100': {}, 'sp500': {}, 'dow': {}},
            },
        }
        validation_report = {
            'schema': 'basecalc_validation_report_v1',
            'horizons': {
                '1d': {
                    'state_direction_summaries': [
                        {
                            'state_key': 'dip_buy',
                            'direction': 'up',
                            'total_predictions': 40,
                            'directional_accuracy': 0.68,
                            'avg_return_pct': 0.41,
                        },
                    ],
                    'confidence_calibration_rows': [
                        {'bucket': '50台', 'avg_return_pct': 0.1},
                        {'bucket': '70台', 'avg_return_pct': 0.3},
                    ],
                },
            },
        }

        contract = apply_output_contract(world_model, display_price=41000, validation_report=validation_report)

        self.assertEqual(contract['contract_status'], 'confirmed')
        self.assertEqual(contract['display_status'], 'candidate_confirmed')
        self.assertEqual(contract['explanation_allowed'], 'confirmed')
        self.assertEqual(contract['validation_warnings'], [])
        self.assertEqual(contract['confidence_cap_reason'], '')
        self.assertTrue(contract['directional_allowed'])

    def test_output_contract_exposes_allowed_explanation_status_for_uncapped_candidate(self):
        from .output_contract import apply_output_contract

        world_model = {
            'price': 41000,
            'direction': 'neutral',
            'direction_label': 'レンジ',
            'readiness_level': 'ready',
            'directional_allowed': True,
            'confidence': 'High',
            'confidence_score': 78,
            'upside_targets': [],
            'downside_targets': [],
            'target_ranges': [{'horizon': '1d', 'low': 40500, 'high': 41500}],
            'horizons': {'1d': {'main_bias': 'range', 'expected_return_pct': 0.0}},
            'similar_summary': {'case_count': 40, 'is_statistically_valid': True},
            'us_index_confirmation': {
                'readiness': {'usable': True},
                'components': {'nasdaq100': {}, 'sp500': {}, 'dow': {}},
            },
        }
        validation_report = {
            'schema': 'basecalc_validation_report_v1',
            'horizons': {
                '1d': {
                    'confidence_calibration_rows': [
                        {'bucket': '50台', 'avg_return_pct': 0.1},
                        {'bucket': '70台', 'avg_return_pct': 0.3},
                    ],
                },
            },
        }

        contract = apply_output_contract(world_model, display_price=41000, validation_report=validation_report)

        self.assertEqual(contract['contract_status'], 'ok')
        self.assertEqual(contract['display_status'], 'watch_only')
        self.assertEqual(contract['explanation_allowed'], 'allowed')


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
