from datetime import timedelta, timezone as dt_timezone
from pathlib import Path
from io import StringIO
from tempfile import TemporaryDirectory
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from basecalc.models import MarketBar

from .models import ExplanationSnapshot, ExplanationTradeOutcome
from .services.audit_engine import evaluate_audit
from .services.basecalc_adapter import _near_level_price, load_basecalc_signal
from .services.contracts import BasecalcSignal, MacroSignal
from .services.freshness import build_explanation_refresh_status
from .services.fusion_engine import build_final_decision, build_trade_decision_v2
from .services.macro_adapter import load_macro_signal
from .services.scenario_builder import build_scenarios
from .services.serializer import snapshot_to_api, snapshot_to_view
from .services.target_selector import select_trade_targets
from .services.validation_engine import (
    build_basecalc_backtest_validation_summary,
    build_static_trade_validation_summary,
    build_trade_validation_summary,
    evaluate_trade_outcome,
)


class ExplanationDecisionEngineTests(SimpleTestCase):
    def _macro(self, bias='positive', **overrides):
        data = {
            'bias': bias,
            'summary': 'Macroは支援的。',
            'confidence_score': 78,
            'confidence_grade': 'B',
            'data_quality_score': 82,
        }
        data.update(overrides)
        return MacroSignal(**data)

    def _basecalc(self, bias='bullish', **overrides):
        data = {
            'bias': bias,
            'summary': '日経先物は上昇優勢。',
            'confidence_score': 72,
            'confidence_grade': 'B',
            'data_quality_score': 86,
            'readiness_level': 'ready',
            'can_show_prediction': True,
            'allowed_direction': 'allowed',
            'current_price': 42000,
            'support': 41600,
            'resistance': 42800,
            'invalidation': 41400,
            'bullish_invalidation': 41400,
            'bearish_invalidation': 42600,
            'direction_1d': 'up',
            'direction_3d': 'up',
            'direction_5d': 'up',
            'primary_direction': 'up',
            'primary_setup': 'trend_follow_long',
            'counter_bias': {'direction': 'down', 'score': 20, 'label': '反落警戒は限定的'},
            'scenario_probabilities': {'up_continuation': 68, 'range': 20, 'down_reversal': 12},
            'horizons': {'1d': {'expected_return_pct': 0.4}, '3d': {'expected_return_pct': 0.8}, '5d': {'expected_return_pct': 1.1}},
            'expected_return_1d': 0.4,
            'expected_return_3d': 0.8,
            'expected_return_5d': 1.1,
            'validated_targets': {
                'upside': [{'label': 'T1', 'price': 42800, 'probability': 0.62}],
                'downside': [{'label': 'T1', 'price': 41000, 'probability': 0.45}],
            },
            'reversal_risk_score': 20,
            'rebound_improvement_score': 10,
            'continuation_score': 72,
            'shock_score': 15,
        }
        data.update(overrides)
        return BasecalcSignal(**data)

    def test_trade_decision_v2_selects_single_long_with_target_stop_and_rr(self):
        macro = self._macro('positive')
        basecalc = self._basecalc()
        audit = evaluate_audit(macro, basecalc)

        decision = build_trade_decision_v2(macro, basecalc, audit)

        self.assertEqual(decision.selected_side, 'long')
        self.assertEqual(decision.decision_type, 'trend_follow')
        self.assertEqual(decision.target_1['price'], 42800)
        self.assertEqual(decision.stop_price, 41400)
        self.assertGreaterEqual(decision.reward_risk, 1.2)
        self.assertGreater(decision.long_score, decision.short_score)
        self.assertFalse(decision.blocked_reasons)

    def test_trade_decision_v2_marks_soft_limited_candidate_without_stopping_side(self):
        macro = self._macro('positive')
        basecalc = self._basecalc(
            confidence_score=72,
            confidence_grade='B',
            contract_status='limited',
            allowed_direction='up',
            can_show_prediction=True,
            stop_reasons=['局面別検証不足', '米国3指数確認が不足'],
            validation_gate_status={
                '1d': {'direction_allowed': True, 'validation_level': 'low'},
                '3d': {'direction_allowed': True, 'validation_level': 'low'},
                '5d': {'direction_allowed': True, 'validation_level': 'low'},
            },
            confidence_calibrated=False,
        )
        audit = evaluate_audit(macro, basecalc)

        decision = build_trade_decision_v2(macro, basecalc, audit)

        self.assertEqual(decision.selected_side, 'long')
        self.assertEqual(decision.decision_status, 'candidate_limited')
        self.assertEqual(decision.entry_permission, 'limited_entry')
        self.assertEqual(decision.validation_level, 'low')
        self.assertEqual(decision.position_size_pct, 25)
        self.assertEqual(decision.position_size_cap, 'max_25_percent')
        self.assertIn('局面別検証不足', decision.soft_warning_reasons)
        self.assertEqual(decision.hard_block_reasons, [])
        self.assertIn('basecalc_direction', decision.confidence_components)
        self.assertIn('validation_quality', decision.confidence_components)
        self.assertLessEqual(decision.confidence_score, 59)
        self.assertIn('局面別検証不足', decision.warnings)
        self.assertFalse(decision.blocked_reasons)

    def test_trade_decision_v2_keeps_soft_warning_visible_when_stop_reasons_are_hard_only(self):
        macro = self._macro('positive')
        basecalc = self._basecalc(
            confidence_score=72,
            confidence_grade='B',
            contract_status='limited',
            allowed_direction='up',
            can_show_prediction=True,
            stop_reasons=[],
            soft_warning_reasons=['局面別精度が基準未達のため信頼度を限定'],
            validation_warnings=['局面別精度が基準未達のため信頼度を限定'],
            validation_gate_status={
                '1d': {'direction_allowed': True, 'validation_level': 'limited'},
                '3d': {'direction_allowed': True, 'validation_level': 'confirmed'},
                '5d': {'direction_allowed': True, 'validation_level': 'confirmed'},
            },
            confidence_calibrated=True,
        )
        audit = evaluate_audit(macro, basecalc)

        decision = build_trade_decision_v2(macro, basecalc, audit)

        self.assertEqual(decision.decision_status, 'candidate_limited')
        self.assertEqual(decision.hard_block_reasons, [])
        self.assertEqual(decision.blocked_reasons, [])
        self.assertIn('局面別精度が基準未達のため信頼度を限定', decision.soft_warning_reasons)
        self.assertIn('局面別精度が基準未達のため信頼度を限定', decision.warnings)

    def test_trade_decision_confidence_components_include_basecalc_cap_reason(self):
        macro = self._macro('positive')
        basecalc = self._basecalc(
            contract_status='limited',
            allowed_direction='up',
            confidence_cap_reason='類似事例不足のため信頼度を限定',
            validation_warnings=['類似事例不足のため信頼度を限定'],
        )
        audit = evaluate_audit(macro, basecalc)

        decision = build_trade_decision_v2(macro, basecalc, audit)

        self.assertEqual(
            decision.confidence_components['confidence_cap_reason'],
            '類似事例不足のため信頼度を限定',
        )
        self.assertIn('類似事例不足のため信頼度を限定', decision.soft_warning_reasons)

    def test_trade_decision_v2_keeps_validation_warning_high_confidence_as_limited_candidate(self):
        macro = self._macro('positive', confidence_score=88, data_quality_score=90)
        basecalc = self._basecalc(
            confidence_score=88,
            data_quality_score=90,
            contract_status='ok',
            confidence_calibrated=True,
            validation_gate_status={
                '1d': {'direction_allowed': True, 'validation_level': 'confirmed'},
                '3d': {'direction_allowed': True, 'validation_level': 'confirmed'},
                '5d': {'direction_allowed': True, 'validation_level': 'confirmed'},
            },
            validation_warnings=['confidence calibration 不足'],
            us_index_available=True,
        )
        audit = evaluate_audit(macro, basecalc)

        decision = build_trade_decision_v2(macro, basecalc, audit)

        self.assertEqual(decision.selected_side, 'long')
        self.assertEqual(decision.decision_status, 'candidate_limited')
        self.assertEqual(decision.entry_permission, 'limited_entry')
        self.assertEqual(decision.position_size_pct, 50)
        self.assertIn('confidence calibration 不足', decision.soft_warning_reasons)

    def test_no_trade_decision_includes_extended_status_contract(self):
        decision = build_trade_decision_v2(
            self._macro('positive'),
            self._basecalc(
                contract_status='error',
                stop_reasons=['現在値と計算基準価格が不一致'],
            ),
            evaluate_audit(
                self._macro('positive'),
                self._basecalc(
                    contract_status='error',
                    stop_reasons=['現在値と計算基準価格が不一致'],
                ),
            ),
        )

        self.assertEqual(decision.decision_status, 'blocked')
        self.assertEqual(decision.entry_permission, 'no_entry')
        self.assertEqual(decision.validation_level, 'none')
        self.assertEqual(decision.position_size_pct, 0)
        self.assertIn('現在値と計算基準価格が不一致', decision.hard_block_reasons)
        self.assertIn('basecalc_direction', decision.confidence_components)

    def test_trade_decision_v2_ignores_stale_can_show_flag_when_contract_allows_direction(self):
        macro = self._macro('positive')
        basecalc = self._basecalc(
            contract_status='limited',
            allowed_direction='up',
            can_show_prediction=False,
            stop_reasons=['現行モデルがATRベースラインを下回るため', '米国3指数確認が不足'],
            validation_gate_status={
                '1d': {'direction_allowed': True, 'validation_level': 'limited'},
                '3d': {'direction_allowed': True, 'validation_level': 'confirmed'},
                '5d': {'direction_allowed': True, 'validation_level': 'confirmed'},
            },
            confidence_calibrated=False,
        )
        audit = evaluate_audit(macro, basecalc)

        decision = build_trade_decision_v2(macro, basecalc, audit)

        self.assertEqual(decision.selected_side, 'long')
        self.assertEqual(decision.decision_status, 'candidate_limited')
        self.assertEqual(decision.position_size_cap, 'max_25_percent')
        self.assertNotEqual(decision.decision_type, 'no_trade_direction_stopped')

    def test_trade_decision_v2_raises_low_basecalc_to_limited_candidate_when_macro_and_plan_support(self):
        macro = self._macro('positive', confidence_score=91, confidence_grade='A')
        basecalc = self._basecalc(
            confidence_score=44,
            confidence_grade='Low',
            contract_status='limited',
            allowed_direction='up',
            can_show_prediction=False,
            stop_reasons=['現行モデルがATRベースラインを下回るため', '米国3指数確認が不足'],
            validation_gate_status={
                '1d': {'direction_allowed': True, 'validation_level': 'limited'},
                '3d': {'direction_allowed': True, 'validation_level': 'confirmed'},
                '5d': {'direction_allowed': True, 'validation_level': 'confirmed'},
            },
            confidence_calibrated=False,
        )
        audit = evaluate_audit(macro, basecalc)

        decision = build_trade_decision_v2(macro, basecalc, audit)

        self.assertEqual(decision.selected_side, 'long')
        self.assertEqual(decision.decision_status, 'candidate_limited')
        self.assertEqual(decision.entry_permission, 'limited_entry')
        self.assertEqual(decision.position_size_pct, 25)
        self.assertEqual(decision.position_size_cap, 'max_25_percent')
        self.assertGreaterEqual(decision.confidence_score, 50)
        self.assertLessEqual(decision.confidence_score, 59)
        self.assertFalse(decision.blocked_reasons)

    def test_trade_confidence_uses_weighted_components_instead_of_simple_minimum(self):
        macro = self._macro('positive', confidence_score=91, confidence_grade='A', data_quality_score=90)
        basecalc = self._basecalc(
            confidence_score=44,
            confidence_grade='Low',
            data_quality_score=82,
            contract_status='limited',
            allowed_direction='up',
            can_show_prediction=True,
            soft_warning_reasons=['局面別検証不足'],
            validation_gate_status={
                '1d': {'direction_allowed': True, 'validation_level': 'low'},
                '3d': {'direction_allowed': True, 'validation_level': 'confirmed'},
                '5d': {'direction_allowed': True, 'validation_level': 'confirmed'},
            },
            confidence_calibrated=False,
            us_index_available=True,
        )
        audit = evaluate_audit(macro, basecalc)

        decision = build_trade_decision_v2(macro, basecalc, audit)

        self.assertGreater(decision.confidence_components['raw_score'], 44)
        self.assertLessEqual(decision.confidence_score, 59)
        self.assertEqual(decision.decision_status, 'candidate_limited')
        self.assertEqual(decision.confidence_components['confidence_formula'], 'weighted_components_v1')
        self.assertEqual(decision.confidence_components['basecalc_weight'], 0.35)
        self.assertEqual(decision.confidence_components['macro_weight'], 0.2)
        self.assertEqual(decision.confidence_components['validation_weight'], 0.15)

    def test_trade_confidence_target_quality_uses_target_stop_and_realized_rr_results(self):
        macro = self._macro('positive', confidence_score=72, data_quality_score=88)
        basecalc = self._basecalc(
            confidence_score=68,
            data_quality_score=84,
            allowed_direction='up',
            validation_gate_status={
                '1d': {
                    'direction_allowed': True,
                    'validation_level': 'confirmed',
                    'target_t1_hit_rate': 0.62,
                    'stop_hit_rate': 0.18,
                    'avg_realized_rr': 1.42,
                },
            },
        )
        audit = evaluate_audit(macro, basecalc)

        decision = build_trade_decision_v2(macro, basecalc, audit)

        self.assertEqual(decision.confidence_components['target_hit_rate'], 0.62)
        self.assertEqual(decision.confidence_components['stop_hit_rate'], 0.18)
        self.assertEqual(decision.confidence_components['avg_realized_rr'], 1.42)
        self.assertGreater(decision.confidence_components['target_quality'], 80)

    def test_trade_confidence_target_quality_penalizes_poor_target_stop_results(self):
        macro = self._macro('positive', confidence_score=72, data_quality_score=88)
        basecalc = self._basecalc(
            confidence_score=68,
            data_quality_score=84,
            allowed_direction='up',
            validation_gate_status={
                '1d': {
                    'direction_allowed': True,
                    'validation_level': 'confirmed',
                    'target_t1_hit_rate': 0.24,
                    'stop_hit_rate': 0.56,
                    'avg_realized_rr': 0.72,
                },
            },
        )
        audit = evaluate_audit(macro, basecalc)

        decision = build_trade_decision_v2(macro, basecalc, audit)

        self.assertEqual(decision.confidence_components['target_hit_rate'], 0.24)
        self.assertEqual(decision.confidence_components['stop_hit_rate'], 0.56)
        self.assertEqual(decision.confidence_components['avg_realized_rr'], 0.72)
        self.assertLess(decision.confidence_components['target_quality'], 80)

    def test_soft_warning_direction_stop_does_not_become_hard_block(self):
        macro = self._macro('positive', confidence_score=91, confidence_grade='A')
        basecalc = self._basecalc(
            confidence_score=44,
            confidence_grade='Low',
            contract_status='limited',
            allowed_direction='stopped',
            stop_reasons=['現行モデルがATRベースラインを下回るため', '米国3指数確認が不足'],
            hard_block_reasons=[],
            soft_warning_reasons=['現行モデルがATRベースラインを下回るため', '米国3指数確認が不足'],
            validation_gate_status={
                '1d': {'direction_allowed': True, 'validation_level': 'limited'},
                '3d': {'direction_allowed': True, 'validation_level': 'limited'},
                '5d': {'direction_allowed': True, 'validation_level': 'limited'},
            },
            confidence_calibrated=False,
        )
        audit = evaluate_audit(macro, basecalc)

        decision = build_trade_decision_v2(macro, basecalc, audit)

        self.assertNotEqual(decision.decision_type, 'no_trade_direction_stopped')
        self.assertEqual(decision.decision_status, 'watch_only')
        self.assertEqual(decision.hard_block_reasons, [])
        self.assertIn('米国3指数確認が不足', decision.soft_warning_reasons)

    def test_bullish_overheated_market_blocks_long_chasing_and_keeps_short_watch(self):
        macro = self._macro('negative')
        basecalc = self._basecalc(
            resistance=42220,
            bearish_invalidation=42450,
            reversal_risk_score=82,
            counter_bias={'direction': 'down', 'score': 82, 'label': '上昇優勢だが反落警戒'},
            scenario_probabilities={'up_continuation': 36, 'range': 24, 'down_reversal': 40},
            validated_targets={
                'upside': [{'label': 'T1', 'price': 42220, 'probability': 0.51}],
                'downside': [{'label': 'T1', 'price': 41000, 'probability': 0.57}],
            },
        )
        audit = evaluate_audit(macro, basecalc)

        decision = build_trade_decision_v2(macro, basecalc, audit)

        self.assertEqual(decision.selected_side, 'no_trade')
        self.assertEqual(decision.decision_type, 'no_chase_long')
        self.assertEqual(decision.reversal_watch['side'], 'short')
        self.assertIn('高値追い禁止', decision.warnings)

    def test_bearish_oversold_market_blocks_short_chasing_and_keeps_long_watch(self):
        macro = self._macro('positive')
        basecalc = self._basecalc(
            bias='bearish',
            summary='日経先物は下落優勢。',
            current_price=42000,
            support=41820,
            resistance=42600,
            invalidation=42600,
            bullish_invalidation=41400,
            bearish_invalidation=42600,
            direction_1d='down',
            direction_3d='down',
            direction_5d='down',
            primary_direction='down',
            primary_setup='trend_follow_short',
            expected_return_1d=-0.4,
            expected_return_3d=-0.8,
            expected_return_5d=-1.0,
            horizons={'1d': {'expected_return_pct': -0.4}, '3d': {'expected_return_pct': -0.8}, '5d': {'expected_return_pct': -1.0}},
            rebound_improvement_score=84,
            counter_bias={'direction': 'up', 'score': 84, 'label': '下落優勢だが買い戻し警戒'},
            scenario_probabilities={'down_continuation': 34, 'range': 25, 'up_reversal': 41},
            validated_targets={
                'upside': [{'label': 'T1', 'price': 43200, 'probability': 0.55}],
                'downside': [{'label': 'T1', 'price': 41820, 'probability': 0.58}],
            },
        )
        audit = evaluate_audit(macro, basecalc)

        decision = build_trade_decision_v2(macro, basecalc, audit)

        self.assertEqual(decision.selected_side, 'no_trade')
        self.assertEqual(decision.decision_type, 'no_chase_short')
        self.assertEqual(decision.reversal_watch['side'], 'long')
        self.assertIn('突っ込み売り禁止', decision.warnings)

    def test_trade_decision_v2_blocks_when_reward_risk_is_too_low(self):
        macro = self._macro('positive')
        basecalc = self._basecalc(
            validated_targets={
                'upside': [{'label': 'T1', 'price': 42300, 'probability': 0.62}],
                'downside': [{'label': 'T1', 'price': 41000, 'probability': 0.45}],
            },
        )
        audit = evaluate_audit(macro, basecalc)

        decision = build_trade_decision_v2(macro, basecalc, audit)

        self.assertEqual(decision.selected_side, 'no_trade')
        self.assertEqual(decision.decision_type, 'no_trade_conflict')
        self.assertIsNone(decision.entry_price)
        self.assertIsNone(decision.target_1)
        self.assertIsNone(decision.stop_price)
        self.assertIsNone(decision.invalidation_price)
        self.assertIsNone(decision.reward_risk)
        self.assertIn('R/R不足', decision.blocked_reasons)

    def test_trade_decision_v2_allows_limited_candidate_at_sixty_score_boundary(self):
        macro = self._macro('neutral', confidence_score=55)
        basecalc = self._basecalc(
            confidence_score=55,
            confidence_calibrated=True,
            continuation_score=0,
            expected_return_1d=0,
            expected_return_3d=0,
            expected_return_5d=0,
            horizons={
                '1d': {'expected_return_pct': 0},
                '3d': {'expected_return_pct': 0},
                '5d': {'expected_return_pct': 0},
            },
        )
        audit = evaluate_audit(macro, basecalc)

        decision = build_trade_decision_v2(macro, basecalc, audit)

        self.assertEqual(decision.selected_side, 'long')
        self.assertEqual(decision.decision_status, 'candidate_limited')
        self.assertGreaterEqual(decision.long_score, 60)
        self.assertFalse(decision.blocked_reasons)

    def test_trade_decision_v2_keeps_sixty_range_limited_candidate_with_half_position(self):
        macro = self._macro('positive', confidence_score=88, data_quality_score=90)
        basecalc = self._basecalc(
            confidence_score=88,
            data_quality_score=90,
            contract_status='limited',
            confidence_calibrated=True,
            validation_gate_status={
                '1d': {'direction_allowed': True, 'validation_level': 'confirmed'},
                '3d': {'direction_allowed': True, 'validation_level': 'confirmed'},
                '5d': {'direction_allowed': True, 'validation_level': 'confirmed'},
            },
            us_index_available=True,
        )
        audit = evaluate_audit(macro, basecalc)

        decision = build_trade_decision_v2(macro, basecalc, audit)

        self.assertEqual(decision.selected_side, 'long')
        self.assertEqual(decision.decision_status, 'candidate_limited')
        self.assertEqual(decision.entry_permission, 'limited_entry')
        self.assertGreaterEqual(decision.confidence_score, 60)
        self.assertLessEqual(decision.confidence_score, 69)
        self.assertEqual(decision.position_size_pct, 50)
        self.assertEqual(decision.position_size_cap, 'max_50_percent')

    def test_trade_decision_v2_no_trade_conflict_does_not_keep_reference_short_plan(self):
        macro = self._macro('neutral')
        basecalc = self._basecalc(
            bias='bullish',
            primary_direction='up',
            primary_setup='trend_follow_long',
            reversal_risk_score=78,
            counter_bias={'direction': 'down', 'score': 78, 'label': '反落警戒'},
            scenario_probabilities={'up_continuation': 38, 'range': 22, 'down_reversal': 40},
            validated_targets={
                'upside': [{'label': 'T1', 'price': 42800, 'probability': 0.54}],
                'downside': [{'label': 'T1', 'price': 41000, 'probability': 0.62}],
            },
        )
        audit = evaluate_audit(macro, basecalc)

        decision = build_trade_decision_v2(macro, basecalc, audit)

        self.assertEqual(decision.selected_side, 'no_trade')
        self.assertTrue(decision.decision_type.startswith('no_'))
        self.assertIsNone(decision.entry_price)
        self.assertIsNone(decision.target_1)
        self.assertIsNone(decision.target_2)
        self.assertIsNone(decision.stop_price)
        self.assertIsNone(decision.reward_risk)
        self.assertEqual(decision.hard_block_reasons, [])
        self.assertIn('明確な反転警戒', decision.blocked_reasons)

    def test_trade_decision_v2_blocks_when_confidence_is_below_candidate_threshold(self):
        macro = self._macro('positive', confidence_score=49, confidence_grade='C')
        basecalc = self._basecalc(confidence_score=49, confidence_grade='Low', confidence_calibrated=True)
        audit = evaluate_audit(macro, basecalc)

        decision = build_trade_decision_v2(macro, basecalc, audit)

        self.assertEqual(decision.selected_side, 'long')
        self.assertEqual(decision.decision_status, 'watch_only')
        self.assertEqual(decision.entry_permission, 'watch_only')
        self.assertEqual(decision.position_size_pct, 0)
        self.assertFalse(decision.blocked_reasons)

    def test_trade_decision_v2_blocks_when_basecalc_direction_is_stopped(self):
        macro = self._macro('positive')
        basecalc = self._basecalc(
            contract_status='limited',
            allowed_direction='stopped',
            can_show_prediction=False,
            stop_reasons=['方向予測停止'],
        )
        audit = evaluate_audit(macro, basecalc)

        decision = build_trade_decision_v2(macro, basecalc, audit)

        self.assertEqual(decision.selected_side, 'no_trade')
        self.assertEqual(decision.decision_type, 'no_trade_direction_stopped')
        self.assertIsNone(decision.entry_price)
        self.assertIsNone(decision.target_1)
        self.assertIsNone(decision.target_2)
        self.assertIsNone(decision.stop_price)
        self.assertIsNone(decision.reward_risk)
        self.assertIn('方向予測停止', decision.blocked_reasons)

    def test_trade_decision_v2_blocks_contract_error_and_hides_targets(self):
        macro = self._macro('positive')
        basecalc = self._basecalc(
            contract_status='error',
            stop_reasons=['現在値と計算基準価格が不一致'],
        )
        audit = evaluate_audit(macro, basecalc)

        decision = build_trade_decision_v2(macro, basecalc, audit)

        self.assertEqual(decision.selected_side, 'no_trade')
        self.assertEqual(decision.decision_type, 'no_trade_data_blocked')
        self.assertIsNone(decision.target_1)
        self.assertIsNone(decision.probability)
        self.assertIn('現在値と計算基準価格が不一致', decision.blocked_reasons)

    def test_target_selector_uses_side_specific_target_stop_and_rr(self):
        basecalc = self._basecalc()

        long_plan = select_trade_targets('long', 42000, basecalc)
        short_plan = select_trade_targets('short', 42000, basecalc)

        self.assertEqual(long_plan.target_1['price'], 42800)
        self.assertEqual(long_plan.stop_price, 41400)
        self.assertEqual(long_plan.reward_risk, 1.33)
        self.assertEqual(short_plan.target_1['price'], 41000)
        self.assertEqual(short_plan.stop_price, 42600)
        self.assertEqual(short_plan.reward_risk, 1.67)

    def test_target_selector_prefers_best_positive_expected_value_target(self):
        basecalc = self._basecalc(
            validated_targets={
                'upside': [
                    {'label': 'T1', 'price': 42300, 'probability': 0.45},
                    {'label': 'T2', 'price': 43200, 'probability': 0.62},
                ],
            },
        )

        plan = select_trade_targets('long', 42000, basecalc)

        self.assertEqual(plan.target_1['label'], 'T2')
        self.assertEqual(plan.target_1['price'], 43200)
        self.assertEqual(plan.target_2['label'], 'T1')
        self.assertEqual(plan.reward_risk, 2.0)
        self.assertGreater(plan.expected_value, 0)

    def test_target_selector_blocks_when_best_target_expected_value_is_not_positive(self):
        basecalc = self._basecalc(
            validated_targets={
                'upside': [
                    {'label': 'T1', 'price': 42800, 'probability': 0.25},
                    {'label': 'T2', 'price': 43200, 'probability': 0.30},
                ],
            },
        )

        plan = select_trade_targets('long', 42000, basecalc)

        self.assertIn('期待値不足', plan.blocked_reasons)
        self.assertLessEqual(plan.expected_value, 0)

    def test_opposite_macro_and_basecalc_is_timeframe_divergence_not_conflict(self):
        macro = self._macro('positive')
        basecalc = self._basecalc(
            bias='bearish',
            summary='日経先物は下落優勢。',
            direction_1d='down',
            direction_3d='down',
            direction_5d='down',
            primary_direction='down',
            primary_setup='trend_follow_short',
            expected_return_1d=-0.4,
            expected_return_3d=-0.8,
            expected_return_5d=-1.0,
            horizons={'1d': {'expected_return_pct': -0.4}, '3d': {'expected_return_pct': -0.8}, '5d': {'expected_return_pct': -1.0}},
            validated_targets={
                'upside': [{'label': 'T1', 'price': 43200, 'probability': 0.55}],
                'downside': [{'label': 'T1', 'price': 41000, 'probability': 0.62}],
            },
        )

        audit = evaluate_audit(macro, basecalc)
        decision = build_trade_decision_v2(macro, basecalc, audit)

        self.assertEqual(audit.alignment_status, 'timeframe_divergence')
        self.assertNotIn('macroとbasecalcの方向が矛盾', audit.items)
        self.assertEqual(decision.selected_side, 'short')

    def test_stale_basecalc_blocks_final_trade_judgment(self):
        macro = self._macro('positive', as_of=timezone.now())
        basecalc = self._basecalc(as_of=timezone.now() - timedelta(days=2))

        audit = evaluate_audit(macro, basecalc)
        decision = build_trade_decision_v2(macro, basecalc, audit)

        self.assertEqual(audit.status, 'blocked')
        self.assertEqual(audit.alignment_status, 'blocked')
        self.assertIn('Basecalcデータが古いため判定停止', audit.items)
        self.assertEqual(decision.selected_side, 'no_trade')
        self.assertEqual(decision.decision_type, 'no_trade_data_blocked')

    def test_bullish_basecalc_with_macro_inflation_risk_is_conditional_when_audit_warns(self):
        macro = MacroSignal(
            bias='neutral_inflation_risk',
            summary='景気判断は中立。ただし物価再加速リスクが高い。',
            confidence_score=89,
            confidence_grade='B',
            data_quality_score=90,
            warnings=['PCE/Core PCEが古い'],
        )
        basecalc = BasecalcSignal(
            bias='bullish',
            summary='日経先物は上昇優勢。1d/3d/5dは上方向。',
            confidence_score=68,
            confidence_grade='Middle',
            data_quality_score=96,
            readiness_level='ready',
            can_show_prediction=False,
            support=67620,
            resistance=71180,
            invalidation=62350,
            direction_1d='up',
            direction_3d='up',
            direction_5d='up',
            us_index_available=False,
        )

        audit = evaluate_audit(macro, basecalc)
        decision = build_final_decision(macro, basecalc, audit)

        self.assertEqual(decision.final_label, '条件付き上昇優勢')
        self.assertEqual(decision.final_stance, 'conditional_bullish')
        self.assertLess(decision.confidence_score, basecalc.confidence_score)
        self.assertIn('米国3指数確認が不足', audit.items)
        self.assertIn('予測ゲート停止中', audit.items)

    def test_blocked_basecalc_withholds_final_decision(self):
        macro = MacroSignal(
            bias='positive',
            summary='景気は拡大寄り。',
            confidence_score=82,
            confidence_grade='B',
            data_quality_score=88,
        )
        basecalc = BasecalcSignal(
            bias='bullish',
            summary='日経先物は上昇優勢。',
            confidence_score=72,
            confidence_grade='B',
            data_quality_score=0,
            readiness_level='blocked',
            can_show_prediction=False,
            us_index_available=False,
        )

        audit = evaluate_audit(macro, basecalc)
        decision = build_final_decision(macro, basecalc, audit)

        self.assertEqual(audit.status, 'blocked')
        self.assertEqual(decision.final_label, '判定保留')
        self.assertEqual(decision.final_stance, 'withhold')

    def test_scenarios_reuse_basecalc_levels(self):
        macro = MacroSignal(
            bias='neutral',
            summary='景気判断は中立。',
            confidence_score=70,
            confidence_grade='B',
            data_quality_score=80,
        )
        basecalc = BasecalcSignal(
            bias='bullish',
            summary='日経先物は上昇優勢。',
            confidence_score=68,
            confidence_grade='Middle',
            data_quality_score=96,
            readiness_level='ready',
            can_show_prediction=False,
            support=67620,
            resistance=71180,
            invalidation=62350,
        )

        scenario = build_scenarios(macro, basecalc)

        self.assertEqual(scenario['levels']['resistance'], 71180)
        self.assertEqual(scenario['levels']['support'], 67620)
        self.assertEqual(scenario['levels']['invalidation'], 62350)

    def test_scenarios_hide_upside_extension_when_us_indices_are_missing(self):
        macro = MacroSignal(
            bias='neutral',
            summary='景気判断は中立。',
            confidence_score=70,
            confidence_grade='B',
            data_quality_score=80,
        )
        basecalc = BasecalcSignal(
            bias='bullish',
            summary='日経先物は上昇優勢。',
            confidence_score=68,
            confidence_grade='Middle',
            data_quality_score=96,
            readiness_level='ready',
            can_show_prediction=True,
            support=67620,
            resistance=71180,
            invalidation=62350,
            contract_status='limited',
            us_index_available=False,
        )

        scenario = build_scenarios(macro, basecalc)

        self.assertIn('上値拡張は米国3指数確認まで表示停止', scenario['upside']['text'])
        self.assertEqual(scenario['levels']['resistance'], 71180)


class ExplanationBasecalcAdapterTests(SimpleTestCase):
    def test_near_level_price_uses_first_available_side_level(self):
        world_model = {
            'near_levels': {
                'upside': [
                    {'price': 66700, 'reason': '100円刻み'},
                    {'price': 67000, 'reason': '500円刻み'},
                ],
                'downside': [
                    {'price': 66600, 'reason': '100円刻み'},
                ],
            },
        }

        self.assertEqual(_near_level_price(world_model, 'upside'), 66700)
        self.assertEqual(_near_level_price(world_model, 'downside'), 66600)

    def test_load_basecalc_signal_ignores_validation_report_older_than_saved_snapshot(self):
        snapshot = {
            'generated_at': '2026-06-23T09:39:11+00:00',
            'world_model': {
                'direction': 'neutral',
                'direction_label': '方向判断停止',
                'price': 69770,
                'last_updated_display': '2026-06-23 15:45 JST',
                'state_key': 'bull_trend_continuation',
                'state_label': '上昇継続',
                'confidence': 'Middle',
                'confidence_score': 69,
                'data_quality': {'level': 'good', 'score': 90, 'fallback_used': False},
                'data_quality_score': 90,
                'readiness_level': 'ready',
                'similar_summary': {'case_count': 30, 'is_statistically_valid': True},
                'horizons': {
                    '1d': {'main_bias': 'range', 'expected_return_pct': -0.26},
                    '3d': {'main_bias': 'range', 'expected_return_pct': -0.44},
                    '5d': {'main_bias': 'range', 'expected_return_pct': -0.55},
                },
                'practical_lines': {
                    'current_price': 69770,
                    'upside_resistance': 72090,
                    'downside_support': 67450,
                    'near_upside': 69800,
                    'near_downside': 69700,
                },
                'near_levels': {
                    'upside': [{'price': 69800}],
                    'downside': [{'price': 69700}],
                },
                'us_index_confirmation': {
                    'readiness': {'usable': True},
                    'components': {'nasdaq100_futures': {}, 'sp500_futures': {}, 'dow_futures': {}},
                    'evidence': [],
                },
                'output_contract': {
                    'contract_status': 'ok',
                    'display_price': 69770,
                    'generated_at': '2026-06-23T09:39:11+00:00',
                    'directional_allowed': True,
                    'allowed_horizons': {
                        '1d': {'direction_allowed': True, 'target_probability_allowed': True},
                        '3d': {'direction_allowed': True, 'target_probability_allowed': True},
                        '5d': {'direction_allowed': True, 'target_probability_allowed': True},
                    },
                    'stop_reasons': [],
                },
            },
            'decision': {
                'confidence': 'Middle',
                'confidence_score': 69,
                'data_quality_score': 90,
                'readiness_level': 'ready',
                'can_show_prediction': False,
            },
            'basecalc_status_rows': [],
            'market_shock': {'has_data': False},
            'backtest_performance_by_horizon': {},
        }
        stale_validation_report = {
            'schema': 'basecalc_validation_report_v1',
            'generated_at': '2026-06-23T07:22:57+00:00',
            'horizons': {
                '1d': {
                    'state_summaries': [
                        {
                            'state_key': 'bull_trend_continuation',
                            'directional_accuracy': 0.35,
                            'avg_return_pct': -0.4,
                        },
                    ],
                },
            },
        }

        with (
            mock.patch('explanation.services.basecalc_adapter.load_basecalc_snapshot', return_value=snapshot),
            mock.patch('explanation.services.basecalc_adapter.load_validation_report', return_value=stale_validation_report),
        ):
            signal = load_basecalc_signal()

        self.assertEqual(signal.confidence_score, 64)
        self.assertEqual(signal.confidence_grade, 'Middle')
        self.assertNotIn('局面別成績が弱いため信頼度を50未満に制限', signal.warnings)

    def test_load_basecalc_signal_keeps_validation_warnings_visible_when_stop_reasons_are_hard_only(self):
        snapshot = {
            'generated_at': '2026-06-23T09:39:11+00:00',
            'world_model': {
                'direction': 'up',
                'direction_label': '上昇優勢',
                'price': 69770,
                'confidence': 'Middle',
                'confidence_score': 59,
                'data_quality': {'level': 'good', 'score': 90, 'fallback_used': False},
                'data_quality_score': 90,
                'readiness_level': 'ready',
                'similar_summary': {'case_count': 30, 'is_statistically_valid': True},
                'horizons': {'1d': {'main_bias': 'up', 'expected_return_pct': 0.4}},
                'near_levels': {
                    'upside': [{'price': 69800}],
                    'downside': [{'price': 69700}],
                },
                'us_index_confirmation': {
                    'readiness': {'usable': True},
                    'components': {'nasdaq100_futures': {}, 'sp500_futures': {}, 'dow_futures': {}},
                    'evidence': [],
                },
                'output_contract': {
                    'contract_status': 'limited',
                    'display_price': 69770,
                    'directional_allowed': True,
                    'allowed_direction': 'up',
                    'stop_reasons': [],
                    'hard_block_reasons': [],
                    'soft_warning_reasons': [],
                    'validation_warnings': ['局面別精度が基準未達のため信頼度を限定'],
                    'confidence_cap_reason': '局面別精度が基準未達のため信頼度を限定',
                },
            },
            'decision': {
                'confidence': 'Middle',
                'confidence_score': 59,
                'data_quality_score': 90,
                'readiness_level': 'ready',
                'can_show_prediction': True,
            },
            'basecalc_status_rows': [],
            'market_shock': {'has_data': False},
            'backtest_performance_by_horizon': {},
        }

        with (
            mock.patch('explanation.services.basecalc_adapter.load_basecalc_snapshot', return_value=snapshot),
            mock.patch('explanation.services.basecalc_adapter.load_validation_report', return_value=None),
            mock.patch('explanation.services.basecalc_adapter.apply_output_contract'),
        ):
            signal = load_basecalc_signal()

        self.assertEqual(signal.stop_reasons, [])
        self.assertEqual(signal.hard_block_reasons, [])
        self.assertEqual(signal.validation_warnings, ['局面別精度が基準未達のため信頼度を限定'])
        self.assertIn('局面別精度が基準未達のため信頼度を限定', signal.warnings)


class ExplanationViewCompositionTests(SimpleTestCase):
    def _snapshot(self):
        return ExplanationSnapshot(
            as_of=timezone.now(),
            final_label='条件付き上昇優勢',
            final_stance='conditional_bullish',
            action_posture='押し目待ち。高値追いは避ける。',
            confidence_score=68,
            confidence_grade='B-',
            macro_bias='positive',
            basecalc_bias='bullish',
            alignment_status='aligned',
            data_quality_score=80,
            audit_level='valid',
            audit_items=['監査では判断を止める問題は確認されていない。'],
            scenario={
                'baseline': {'title': '基本シナリオ', 'text': '押し目確認を優先。'},
                'upside': {'title': '上振れシナリオ', 'text': '上値抵抗を突破。'},
                'downside': {'title': '下振れシナリオ', 'text': '下値支持を割り込み。'},
                'levels': {
                    'resistance': 71180,
                    'support': 67620,
                    'invalidation': 62350,
                    'resistance_display': '71,180',
                    'support_display': '67,620',
                    'invalidation_display': '62,350',
                },
            },
            evidence=['Basecalcは上方向。', 'Macroは支援的。'],
            trade_decision={
                'selected_side': 'long',
                'decision_type': 'trend_follow',
                'horizon': '3d',
                'current_price': 69400,
                'entry_price': 69400,
                'entry_zone_low': 69296,
                'entry_zone_high': 69452,
                'target_1': {'label': 'T1', 'price': 71180, 'probability': 0.05},
                'target_2': None,
                'stop_price': 67620,
                'invalidation_price': 67620,
                'reward_risk': 1.0,
                'expected_return_pct': 0.4,
                'probability': 0.05,
                'confidence_score': 68,
                'confidence_grade': 'B-',
                'long_score': 70,
                'short_score': 30,
                'no_trade_score': 35,
                'trend_follow_score': 72,
                'reversal_score': 20,
                'counter_scenario': {'label': '反落警戒は限定的'},
                'reversal_watch': {},
                'reasons': ['ロング採用。'],
                'warnings': [],
                'blocked_reasons': [],
                'model_version': 'explanation_v2',
                'price_source': 'market_data',
            },
            source_snapshots={
                'macro': {'summary': 'Macroは支援的。'},
                'basecalc': {
                    'summary': '日経先物は上昇優勢。1d/3d/5dは上方向。',
                    'raw': {
                        'world_model': {
                            'direction_label': '上昇優勢',
                            'price': 69400,
                            'confidence_score': 68,
                            'horizons': {
                                '1d': {
                                    'main_bias': 'up',
                                    'setup_label': '上昇トレンド継続',
                                    'expected_return_pct': -0.02,
                                },
                                '3d': {
                                    'main_bias': 'up',
                                    'setup_label': '上昇トレンド継続',
                                    'expected_return_pct': -0.04,
                                },
                                '5d': {
                                    'main_bias': 'up',
                                    'setup_label': '上昇トレンド継続',
                                    'expected_return_pct': -0.05,
                                },
                            },
                            'upside_targets': [
                                {'label': 'T1', 'price': 71180, 'probability_display': '5%'},
                            ],
                            'downside_targets': [
                                {'label': 'T1', 'price': 67620, 'probability_display': '8%'},
                            ],
                            'invalidation_price': 62350,
                        },
                    },
                },
            },
            score_breakdown={},
        )

    def test_view_context_prioritizes_long_short_and_world_model_predictions(self):
        context = snapshot_to_view(self._snapshot())

        self.assertEqual(context['decision_card']['label'], 'ロング')
        self.assertEqual(context['decision_card']['target'], '71,180円')
        self.assertEqual(context['decision_card']['stop'], '67,620円')
        self.assertEqual(context['long_judgment']['label'], 'ロング判断')
        self.assertEqual(context['long_judgment']['price'], '71,180円')
        self.assertEqual(context['long_judgment']['probability'], '5%')
        self.assertEqual(context['short_judgment']['label'], 'ショート判断')
        self.assertEqual(context['short_judgment']['stance'], '非採用')
        self.assertEqual(context['short_judgment']['price'], 'N/A')
        self.assertEqual(context['short_judgment']['probability'], '参考')
        self.assertEqual(
            [item['horizon'] for item in context['world_model_predictions']],
            ['1d', '3d', '5d'],
        )
        self.assertEqual(context['world_model_predictions'][0]['expected_return'], '-0.02%')
        self.assertEqual(context['world_model_predictions'][0]['expected_price'], '69,386円')
        self.assertEqual(context['world_model_predictions'][0]['base_price'], '69,400円')

    def test_world_model_predictions_use_manual_price_when_available(self):
        snapshot = self._snapshot()
        snapshot.source_snapshots['basecalc']['raw']['manual_price_override'] = {
            'active': True,
            'price': 42000,
            'price_display': '42,000',
        }

        context = snapshot_to_view(snapshot)

        self.assertEqual(context['world_model_predictions'][0]['expected_return'], '-0.02%')
        self.assertEqual(context['world_model_predictions'][0]['expected_price'], '41,992円')
        self.assertEqual(context['world_model_predictions'][0]['base_price'], '42,000円')

    def test_view_context_adds_integrated_decision_summary(self):
        context = snapshot_to_view(self._snapshot())

        self.assertEqual(context['integrated_decision']['posture'], 'ロング候補')
        self.assertEqual(context['alignment_summary']['macro'], '追い風')
        self.assertEqual(context['alignment_summary']['basecalc'], '上方向')
        self.assertEqual(context['alignment_summary']['status'], '同方向')
        self.assertEqual(context['adoption_summary']['primary'], 'ロング候補')
        self.assertLessEqual(len(context['adoption_summary']['reasons']), 3)
        self.assertLessEqual(len(context['adoption_summary']['warnings']), 3)
        self.assertIn('Long', context['adoption_summary']['long_condition'])
        self.assertIn('Short', context['adoption_summary']['short_condition'])

    def test_view_context_labels_timeframe_divergence_as_integration_relation(self):
        snapshot = self._snapshot()
        snapshot.macro_bias = 'positive'
        snapshot.basecalc_bias = 'bearish'
        snapshot.alignment_status = 'timeframe_divergence'

        context = snapshot_to_view(snapshot)

        self.assertEqual(context['alignment_summary']['status'], '時間軸分岐')
        self.assertIn('短期と中期を分けて扱い', context['alignment_summary']['action'])

    def test_view_context_labels_neutral_range_as_no_direction_alignment(self):
        snapshot = self._snapshot()
        snapshot.macro_bias = 'neutral'
        snapshot.basecalc_bias = 'range'
        snapshot.alignment_status = 'aligned'

        context = snapshot_to_view(snapshot)

        self.assertEqual(context['alignment_summary']['macro'], '中立')
        self.assertEqual(context['alignment_summary']['basecalc'], 'レンジ')
        self.assertEqual(context['alignment_summary']['status'], '方向なしで一致')
        self.assertIn('順張り候補ではなく待機', context['alignment_summary']['action'])

    def test_world_model_predictions_hide_values_when_horizon_direction_is_stopped(self):
        snapshot = self._snapshot()
        snapshot.source_snapshots['basecalc']['raw']['world_model']['output_contract'] = {
            'contract_status': 'limited',
            'allowed_horizons': {
                '1d': {'direction_allowed': False},
                '3d': {'direction_allowed': False},
                '5d': {'direction_allowed': False},
            },
        }

        context = snapshot_to_view(snapshot)

        for row in context['world_model_predictions']:
            self.assertEqual(row['bias'], '停止 / 参考')
            self.assertEqual(row['expected_return'], 'N/A')
            self.assertEqual(row['expected_price'], 'N/A')
            self.assertEqual(row['setup'], '方向ゲート停止中（売買判定には未使用）')

    def test_world_model_predictions_hide_all_values_when_trade_decision_is_no_trade(self):
        snapshot = self._snapshot()
        snapshot.trade_decision.update({
            'selected_side': 'no_trade',
            'decision_type': 'no_trade_direction_stopped',
            'target_1': None,
            'target_2': None,
            'stop_price': None,
            'reward_risk': None,
            'blocked_reasons': ['方向予測停止'],
        })
        snapshot.source_snapshots['basecalc']['raw']['world_model']['output_contract'] = {
            'contract_status': 'limited',
            'directional_allowed': True,
            'allowed_horizons': {
                '1d': {'direction_allowed': False},
                '3d': {'direction_allowed': True},
                '5d': {'direction_allowed': True},
            },
        }

        context = snapshot_to_view(snapshot)

        for row in context['world_model_predictions']:
            self.assertEqual(row['bias'], '停止 / 参考')
            self.assertEqual(row['expected_return'], 'N/A')
            self.assertEqual(row['expected_price'], 'N/A')
            self.assertEqual(row['setup'], '方向ゲート停止中（売買判定には未使用）')

    def test_world_model_section_explains_stop_reason_and_restart_conditions(self):
        snapshot = self._snapshot()
        snapshot.audit_items = ['米国3指数確認不足', '予測ゲート停止中']
        snapshot.trade_decision.update({
            'selected_side': 'no_trade',
            'decision_type': 'no_trade_direction_stopped',
            'target_1': None,
            'target_2': None,
            'stop_price': None,
            'reward_risk': None,
            'blocked_reasons': ['信頼度不足'],
        })
        context = snapshot_to_view(snapshot)
        context['is_preview'] = False
        context['refresh_status'] = {'needs_refresh': False}
        context['can_precompute_explanation'] = False
        context['trade_validation_summary'] = {'available': False}

        html = render_to_string('explanation/index.html', context)

        world_model_html = html.split('world model 予測数値', 1)[1]
        self.assertIn('停止理由', world_model_html)
        self.assertIn('信頼度不足 / 米国3指数確認不足 / 予測ゲート停止中', world_model_html)
        self.assertIn('表示再開条件', world_model_html)
        self.assertIn('方向ゲート再開 / 米国3指数確認 / 信頼度回復', world_model_html)
        self.assertLess(world_model_html.index('停止理由'), world_model_html.index('1d / 停止 / 参考'))

    def test_reasons_are_normalized_before_display(self):
        snapshot = self._snapshot()
        snapshot.evidence = ['重要指標の発表前後のため一段階下げます。のため、強い判断にはしない。']

        context = snapshot_to_view(snapshot)

        rendered_text = ' '.join(context['beginner_decision']['reasons'] + context['advanced_detail']['decision_inputs']['materials'])
        self.assertNotIn('。のため', rendered_text)
        self.assertNotIn('ます。のため', rendered_text)
        self.assertIn('重要指標の発表前後のため、強い判断にはしない。', rendered_text)

    def test_static_metadata_is_exposed_in_decision_inputs(self):
        snapshot = self._snapshot()
        snapshot.static_metadata = {
            'snapshot_key': 'snapshot-key',
            'git_sha': 'abcdef1234567890',
            'workflow_run_id': '12345',
            'generated_at': '2026-07-01T12:00:00+00:00',
        }

        context = snapshot_to_view(snapshot)
        rows = {row['label']: row['value'] for row in context['decision_inputs']['rows']}

        self.assertEqual(rows['Snapshot Key'], 'snapshot-key')
        self.assertEqual(rows['Git SHA'], 'abcdef123456')
        self.assertEqual(rows['Workflow Run ID'], '12345')

    def test_explanation_template_removes_low_priority_duplicate_sections(self):
        context = snapshot_to_view(self._snapshot())
        context['is_preview'] = False
        context['refresh_status'] = {'needs_refresh': False}
        context['can_precompute_explanation'] = False
        context['trade_validation_summary'] = {
            'available': True,
            'total_count': 1,
            'actionable_count': 1,
            'wait_count': 0,
            'one_line': '検証 1件 / 売買候補 1件 / 待機観測 0件',
            'horizon_rows': [{'label': '1d', 'sample_count': 1, 'direction_hit_rate': '100%', 'target_1_hit_rate': '0%', 'stop_hit_rate': '0%'}],
            'side_rows': [{'label': 'Long', 'sample_count': 1, 'direction_hit_rate': '100%', 'target_1_hit_rate': '0%', 'stop_hit_rate': '0%'}],
            'style_rows': [{'label': 'Trend', 'sample_count': 1, 'direction_hit_rate': '100%', 'target_1_hit_rate': '0%', 'stop_hit_rate': '0%'}],
            'confidence_rows': [{'label': 'B', 'sample_count': 1, 'direction_hit_rate': '100%', 'target_1_hit_rate': '0%', 'stop_hit_rate': '0%'}],
        }

        html = render_to_string('explanation/index.html', context)

        self.assertLess(html.index('現在判断'), html.index('詳細を表示'))
        self.assertIn('<summary class="common-section-title">詳細を表示</summary>', html)
        top_html = html.split('詳細を表示', 1)[0]
        self.assertNotIn('world model 予測数値', top_html)
        self.assertNotIn('ロング条件詳細', top_html)
        self.assertNotIn('ショート条件詳細', top_html)
        self.assertNotIn('従来の統合ラベル', html)
        self.assertNotIn('Macro / Basecalc 詳細', html)
        self.assertNotIn('理由の詳細', html)
        self.assertNotIn('シナリオ詳細', html)
        self.assertIn('検証成績詳細', html)
        self.assertNotIn('見るべき水準の詳細', html)
        self.assertNotIn('Long / Short / No Trade 別', html)
        self.assertNotIn('Trend / Reversal 別', html)
        self.assertNotIn('信頼度別', html)
        self.assertIn('採用理由 / 警戒理由', html)
        self.assertIn('Macro / Basecalc 統合関係', html)
        self.assertIn('検証成績', html)
        self.assertIn('次に見る条件', html)
        self.assertIn('/macro/', html)
        self.assertIn('/basecalc/', html)
        self.assertIn('world model 予測数値', html)
        self.assertIn('69,386円 / -0.02%', html)

    def test_explanation_template_top_validation_says_unverified_when_no_trade_outcomes(self):
        context = snapshot_to_view(self._snapshot())
        context['is_preview'] = False
        context['refresh_status'] = {'needs_refresh': False}
        context['can_precompute_explanation'] = False
        context['trade_validation_summary'] = {'available': False}

        html = render_to_string('explanation/index.html', context)

        self.assertIn('判定検証 0件', html)
        self.assertIn('まだ保存済み判定の評価結果がありません。', html)
        self.assertNotIn('実運用結果 0件', html)
        self.assertNotIn('実運用結果はまだ0件です。', html)
        self.assertNotIn('検証状態: 少ない', html)

    def test_explanation_template_separates_live_results_from_backtest_results(self):
        context = snapshot_to_view(self._snapshot())
        context['is_preview'] = False
        context['refresh_status'] = {'needs_refresh': False}
        context['can_precompute_explanation'] = False
        context['trade_validation_summary'] = {
            'available': True,
            'total_count': 8,
            'actionable_count': 0,
            'wait_count': 8,
            'missed_opportunity_count': 0,
            'risk_avoided_count': 0,
            'pending_count': 0,
            'one_line': '検証 8件 / 売買候補 0件 / 待機観測 8件 / 機会損失候補 0件',
            'horizon_rows': [],
        }
        from .services.readiness_score import build_readiness_score
        context['readiness_score'] = build_readiness_score(self._snapshot(), context['trade_validation_summary'])
        context['basecalc_validation_summary'] = {
            'available': True,
            'one_line': '過去データ検証 4,990件 / 1日 方向一致 39% / T1到達 25%',
            'detail_line': '4,990件 / 1日 方向一致 39% / T1到達 25%',
            'generated_at': '2026-06-25T13:25:11+00:00',
            'rows': [
                {
                    'horizon': '1d',
                    'sample_count_display': '4,990件',
                    'directional_accuracy_display': '39%',
                    'target_t1_hit_rate_display': '25%',
                    'avg_return_pct_display': '0.05%',
                },
            ],
        }

        html = render_to_string('explanation/index.html', context)
        validation_section = html.split('<h2 class="common-section-title">検証成績</h2>', 1)[1].split('</section>', 1)[0]
        validation_detail = html.split('<h2 class="common-section-title">検証成績詳細</h2>', 1)[1]

        self.assertIn('検証状態', validation_section)
        self.assertIn('売買候補の実績', validation_section)
        self.assertIn('不足', validation_section)
        self.assertIn('注意', validation_section)
        self.assertIn('検証不足のため建玉サイズを制限', validation_section)
        self.assertNotIn('本番判定検証', validation_section)
        self.assertNotIn('待機観測', validation_section)
        self.assertNotIn('機会損失候補', validation_section)
        self.assertNotIn('90点条件まで', validation_section)
        self.assertNotIn('過去データ検証', validation_section)
        self.assertNotIn('0件 / 全8件', validation_section)
        self.assertIn('本番判定検証', validation_detail)
        self.assertIn('売買候補', validation_detail)
        self.assertIn('0件', validation_detail)
        self.assertIn('過去データ検証', validation_detail)
        self.assertIn('4,990件', validation_detail)
        self.assertIn('1日 方向一致 39%', validation_detail)

    def test_explanation_template_places_manual_price_before_final_decision(self):
        context = snapshot_to_view(self._snapshot())
        context['is_preview'] = False
        context['refresh_status'] = {'needs_refresh': False}
        context['can_precompute_explanation'] = False
        context['trade_validation_summary'] = {'available': False}

        html = render_to_string('explanation/index.html', context)

        self.assertLess(html.index('日経先物価格'), html.index('日経先物 1日〜5日 現在判断'))

    def test_explanation_template_top_uses_beginner_decision_and_hides_no_trade_execution_prices(self):
        snapshot = self._snapshot()
        snapshot.trade_decision.update({
            'selected_side': 'no_trade',
            'decision_type': 'no_trade_conflict',
            'entry_price': 72430,
            'entry_zone_low': 72376,
            'entry_zone_high': 72539,
            'target_1': {'label': 'T1', 'price': 72400, 'probability': 0.21},
            'stop_price': 75800,
            'reward_risk': 0.01,
            'confidence_score': 41,
            'confidence_grade': 'C',
            'long_score': 45,
            'short_score': 30,
            'no_trade_score': 70,
            'blocked_reasons': ['R/R不足'],
        })
        context = snapshot_to_view(snapshot)
        context['is_preview'] = False
        context['refresh_status'] = {'needs_refresh': False}
        context['can_precompute_explanation'] = False
        context['trade_validation_summary'] = {'available': False}

        html = render_to_string('explanation/index.html', context)
        top_html = html.split('詳細を表示', 1)[0]
        decision_card_html = top_html.split('</section>', 1)[0]

        self.assertIn('日経先物 1日〜5日 現在判断', decision_card_html)
        self.assertIn('現在判断：見送り', decision_card_html)
        self.assertIn('行動：入らない', decision_card_html)
        self.assertIn('理由：ロング採用。 / R/R不足', decision_card_html)
        self.assertNotIn('common-choice-card__score', decision_card_html)
        self.assertNotIn('<span>ロング</span>', decision_card_html)
        self.assertNotIn('<span>ショート</span>', decision_card_html)
        self.assertNotIn('エントリー', decision_card_html)
        self.assertNotIn('売買可否', decision_card_html)
        self.assertNotIn('第1目標', decision_card_html)
        self.assertNotIn('<span>R/R</span>', decision_card_html)
        self.assertIn('売買条件', top_html)
        self.assertIn('<span class="common-choice-card__score">70点</span>', top_html)
        self.assertIn('見送り / 条件未達', top_html)
        self.assertNotIn('売買不可 / 条件待ち', top_html)
        self.assertNotIn('参考 72,376〜72,539円', top_html)
        self.assertNotIn('72,400円', top_html)
        self.assertNotIn('75,800円', top_html)

    def test_explanation_template_trade_availability_names_blocked_state(self):
        snapshot = self._snapshot()
        snapshot.source_snapshots['basecalc']['raw']['world_model']['output_contract'] = {
            'contract_status': 'error',
            'directional_allowed': False,
            'stop_reasons': ['現在値と計算基準価格が不一致'],
        }
        snapshot.trade_decision.update({
            'selected_side': 'no_trade',
            'decision_type': 'no_trade_data_blocked',
            'decision_status': 'blocked',
            'entry_permission': 'no_entry',
            'current_price': 72430,
            'confidence_score': 35,
            'confidence_grade': 'D',
            'blocked_reasons': ['現在値と計算基準価格が不一致'],
            'hard_block_reasons': ['現在値と計算基準価格が不一致'],
        })
        context = snapshot_to_view(snapshot)
        context['is_preview'] = False
        context['refresh_status'] = {'needs_refresh': False}
        context['can_precompute_explanation'] = False
        context['trade_validation_summary'] = {'available': False}

        html = render_to_string('explanation/index.html', context)

        self.assertIn('判定停止', html)
        self.assertNotIn('売買不可 / 条件待ち', html)

    def test_explanation_template_top_card_is_three_line_decision_summary(self):
        snapshot = self._snapshot()
        snapshot.source_snapshots['basecalc']['raw']['world_model']['practical_lines'] = {
            'upside_resistance': 72110,
            'downside_support': 68800,
        }
        snapshot.trade_decision.update({
            'selected_side': 'long',
            'decision_type': 'trend_follow',
            'decision_status': 'candidate_limited',
            'entry_permission': 'limited_entry',
            'position_size_pct': 25,
            'current_price': 72430,
            'entry_price': 72430,
            'entry_zone_low': 72376,
            'entry_zone_high': 72539,
            'target_1': {'label': 'T1', 'price': 73800, 'probability': 0.62},
            'stop_price': 71600,
            'invalidation_price': 71600,
            'reward_risk': 1.65,
            'confidence_score': 55,
            'confidence_grade': 'C+',
            'long_score': 70,
            'short_score': 30,
            'no_trade_score': 45,
            'reasons': ['basecalcは上方向', 'target/stop/R/Rは成立'],
            'soft_warning_reasons': ['局面別検証不足'],
        })
        context = snapshot_to_view(snapshot)
        context['is_preview'] = False
        context['refresh_status'] = {'needs_refresh': False}
        context['can_precompute_explanation'] = False
        context['trade_validation_summary'] = {'available': False}

        html = render_to_string('explanation/index.html', context)
        decision_card_html = html.split('</section>', 1)[0]

        self.assertIn('現在判断：限定ロング候補', decision_card_html)
        self.assertIn('行動：条件付きで入る', decision_card_html)
        self.assertIn('理由：basecalcは上方向 / target/stop/R/Rは成立 / 局面別検証不足', decision_card_html)
        self.assertNotIn('行動：押し目まで待つ。成行追撃は不可。建玉は通常の25%。', decision_card_html)
        self.assertNotIn('信頼度：C+ / 55%', decision_card_html)
        self.assertNotIn('建玉上限：通常の25%まで', decision_card_html)
        self.assertNotIn('無効化：71,600円', decision_card_html)
        self.assertNotIn('次の条件：上値抵抗 72,110円 を終値で突破 / 米国3指数が改善 / 下値支持 68,800円 を終値で割り込み', decision_card_html)
        self.assertNotIn('common-choice-card__score', decision_card_html)
        self.assertNotIn('エントリー', decision_card_html)
        self.assertNotIn('第1目標', decision_card_html)
        self.assertNotIn('<span>R/R</span>', decision_card_html)

    def test_explanation_template_trade_conditions_include_candidate_limits_below_top_card(self):
        snapshot = self._snapshot()
        snapshot.source_snapshots['basecalc']['raw']['world_model']['practical_lines'] = {
            'upside_resistance': 72110,
            'downside_support': 68800,
        }
        snapshot.trade_decision.update({
            'selected_side': 'long',
            'decision_type': 'trend_follow',
            'decision_status': 'candidate_limited',
            'entry_permission': 'limited_entry',
            'position_size_pct': 25,
            'current_price': 72430,
            'entry_price': 72430,
            'entry_zone_low': 72376,
            'entry_zone_high': 72539,
            'target_1': {'label': 'T1', 'price': 73800, 'probability': 0.62},
            'stop_price': 71600,
            'invalidation_price': 71600,
            'reward_risk': 1.65,
            'confidence_score': 55,
            'confidence_grade': 'C+',
            'long_score': 70,
            'short_score': 30,
            'no_trade_score': 45,
        })
        context = snapshot_to_view(snapshot)
        context['is_preview'] = False
        context['refresh_status'] = {'needs_refresh': False}
        context['can_precompute_explanation'] = False
        context['trade_validation_summary'] = {'available': False}

        html = render_to_string('explanation/index.html', context)
        top_card_html = html.split('</section>', 1)[0]
        trade_condition_html = html.split('<h2 class="common-section-title">売買条件</h2>', 1)[1].split('</section>', 1)[0]

        self.assertNotIn('建玉上限：通常の25%まで', top_card_html)
        self.assertNotIn('無効化：71,600円', top_card_html)
        self.assertIn('建玉上限', trade_condition_html)
        self.assertIn('通常の25%まで', trade_condition_html)
        self.assertIn('無効化', trade_condition_html)
        self.assertIn('71,600円', trade_condition_html)
        self.assertIn('次の条件', trade_condition_html)
        self.assertIn('上値抵抗 72,110円 を終値で突破', trade_condition_html)
        self.assertIn('米国3指数が改善', trade_condition_html)
        self.assertIn('下値支持 68,800円 を終値で割り込み', trade_condition_html)
        self.assertIn('売買可否', trade_condition_html)
        self.assertIn('限定候補', trade_condition_html)

    def test_explanation_template_candidate_trade_conditions_name_watch_only_state(self):
        snapshot = self._snapshot()
        snapshot.trade_decision.update({
            'selected_side': 'long',
            'decision_type': 'trend_follow',
            'decision_status': 'watch_only',
            'entry_permission': 'watch_only',
            'position_size_pct': 0,
            'current_price': 72430,
            'entry_price': 72430,
            'entry_zone_low': 72376,
            'entry_zone_high': 72539,
            'target_1': {'label': 'T1', 'price': 73800, 'probability': 0.62},
            'stop_price': 71600,
            'invalidation_price': 71600,
            'reward_risk': 1.65,
            'confidence_score': 45,
            'confidence_grade': 'C',
            'long_score': 58,
            'short_score': 30,
            'no_trade_score': 52,
        })
        context = snapshot_to_view(snapshot)
        context['is_preview'] = False
        context['refresh_status'] = {'needs_refresh': False}
        context['can_precompute_explanation'] = False
        context['trade_validation_summary'] = {'available': False}

        html = render_to_string('explanation/index.html', context)
        trade_condition_html = html.split('<h2 class="common-section-title">売買条件</h2>', 1)[1].split('</section>', 1)[0]

        self.assertIn('売買可否', trade_condition_html)
        self.assertIn('待機 / 条件待ち', trade_condition_html)
        self.assertIn('監視のみ', html)
        self.assertIn('エントリー', trade_condition_html)
        self.assertNotIn('72,376〜72,539円', trade_condition_html)

    def test_explanation_template_hides_reference_candidate_when_no_trade_has_blocked_reasons(self):
        snapshot = self._snapshot()
        snapshot.trade_decision.update({
            'selected_side': 'no_trade',
            'decision_type': 'no_trade_conflict',
            'current_price': 72430,
            'entry_price': 72430,
            'entry_zone_low': 72376,
            'entry_zone_high': 72539,
            'target_1': {'label': 'T1', 'price': 71800, 'probability': 0.21},
            'stop_price': 72950,
            'reward_risk': 0.95,
            'confidence_score': 41,
            'confidence_grade': 'C',
            'blocked_reasons': ['R/R不足'],
        })
        context = snapshot_to_view(snapshot)
        context['is_preview'] = False
        context['refresh_status'] = {'needs_refresh': False}
        context['can_precompute_explanation'] = False
        context['trade_validation_summary'] = {'available': False}

        html = render_to_string('explanation/index.html', context)
        top_html = html.split('詳細を表示', 1)[0]

        self.assertNotIn('参考候補', top_html)
        self.assertNotIn('参考ショート候補', top_html)
        self.assertNotIn('71,800円', top_html)

    def test_decision_card_hides_reference_levels_when_no_trade_has_candidate_plan(self):
        snapshot = self._snapshot()
        snapshot.trade_decision.update({
            'selected_side': 'no_trade',
            'decision_type': 'no_trade_conflict',
            'entry_price': 42400,
            'entry_zone_low': 42336,
            'entry_zone_high': 42432,
            'target_1': {'label': 'T1', 'price': 42550, 'probability': 0.21},
            'stop_price': 42290,
            'invalidation_price': 42290,
            'reward_risk': 1.36,
            'blocked_reasons': ['スコア差不足'],
        })

        context = snapshot_to_view(snapshot)

        self.assertEqual(context['decision_card']['label'], '見送り')
        self.assertEqual(context['decision_card']['entry'], 'なし')
        self.assertEqual(context['decision_card']['target'], 'N/A')
        self.assertEqual(context['decision_card']['stop'], 'N/A')
        self.assertEqual(context['decision_card']['invalidation'], 'N/A')
        self.assertEqual(context['decision_card']['confidence'], '参考判定（B- / 68%）')

    def test_beginner_decision_hides_execution_prices_when_no_trade_has_candidate_plan(self):
        snapshot = self._snapshot()
        snapshot.trade_decision.update({
            'selected_side': 'no_trade',
            'decision_type': 'no_trade_conflict',
            'entry_price': 72430,
            'entry_zone_low': 72376,
            'entry_zone_high': 72539,
            'target_1': {'label': 'T1', 'price': 72400, 'probability': 0.21},
            'stop_price': 75800,
            'invalidation_price': 75800,
            'reward_risk': 0.01,
            'confidence_score': 41,
            'confidence_grade': 'C',
            'blocked_reasons': ['R/R不足'],
        })

        context = snapshot_to_view(snapshot)
        beginner = context['beginner_decision']

        self.assertFalse(beginner['tradable'])
        self.assertIn(beginner['status'], {'wait', 'no_trade'})
        self.assertEqual(beginner['entry_permission_label'], '入らない')
        self.assertEqual(beginner['no_candidate_reason_display'], 'R/R不足')
        self.assertFalse(beginner['execution_allowed'])
        self.assertEqual(beginner['entry_display'], 'なし')
        self.assertEqual(beginner['target_1_display'], '—')
        self.assertEqual(beginner['stop_display'], '—')
        self.assertEqual(beginner['reward_risk_display'], '不採用（1.2未満）')
        self.assertIn('R/R不足', beginner['warnings'])

    def test_beginner_decision_keeps_watch_only_reference_levels_when_reward_risk_is_low(self):
        snapshot = self._snapshot()
        snapshot.trade_decision.update({
            'selected_side': 'long',
            'decision_type': 'trend_follow',
            'decision_status': 'watch_only',
            'entry_permission': 'watch_only',
            'current_price': 72430,
            'entry_price': 72430,
            'target_1': {'label': 'T1', 'price': 72800, 'probability': 0.42},
            'stop_price': 71600,
            'invalidation_price': 71600,
            'reward_risk': 0.45,
            'confidence_score': 45,
            'confidence_grade': 'C',
            'long_score': 58,
            'short_score': 30,
            'no_trade_score': 52,
            'warnings': ['R/R不足のため監視のみ'],
        })

        context = snapshot_to_view(snapshot)
        beginner = context['beginner_decision']

        self.assertEqual(beginner['label'], '監視のみ')
        self.assertEqual(beginner['entry_permission_label'], '監視のみ')
        self.assertTrue(beginner['candidate_visible'])
        self.assertFalse(beginner['execution_allowed'])
        self.assertEqual(beginner['entry_display'], '監視のみ')
        self.assertEqual(beginner['target_1_display'], '72,800円')
        self.assertEqual(beginner['stop_display'], '71,600円')
        self.assertEqual(beginner['reward_risk_display'], '不採用（1.2未満）')

    def test_beginner_decision_prioritizes_blocked_decision_status_over_leftover_candidate_prices(self):
        snapshot = self._snapshot()
        snapshot.trade_decision.update({
            'selected_side': 'long',
            'decision_type': 'trend_follow',
            'decision_status': 'blocked',
            'entry_permission': 'no_entry',
            'current_price': 72430,
            'entry_price': 72430,
            'target_1': {'label': 'T1', 'price': 73800, 'probability': 0.62},
            'stop_price': 71600,
            'invalidation_price': 71600,
            'reward_risk': 1.65,
            'confidence_score': 72,
            'confidence_grade': 'B',
            'long_score': 70,
            'short_score': 30,
            'no_trade_score': 35,
            'hard_block_reasons': ['現在値と計算基準価格が不一致'],
        })

        context = snapshot_to_view(snapshot)
        beginner = context['beginner_decision']

        self.assertEqual(beginner['status'], 'data_blocked')
        self.assertEqual(beginner['label'], '判定停止')
        self.assertEqual(beginner['entry_permission_label'], '停止')
        self.assertFalse(beginner['candidate_visible'])
        self.assertFalse(beginner['execution_allowed'])
        self.assertEqual(beginner['entry_display'], '停止')
        self.assertEqual(beginner['target_1_display'], '—')
        self.assertEqual(beginner['stop_display'], '—')
        self.assertEqual(beginner['reward_risk_display'], '—')

    def test_explanation_template_shows_no_candidate_reason_when_not_visible(self):
        snapshot = self._snapshot()
        snapshot.trade_decision.update({
            'selected_side': 'no_trade',
            'decision_type': 'no_trade_conflict',
            'current_price': 72430,
            'target_1': {'label': 'T1', 'price': 72400, 'probability': 0.21},
            'stop_price': 75800,
            'reward_risk': 0.01,
            'blocked_reasons': ['R/R不足'],
        })
        context = snapshot_to_view(snapshot)
        context['is_preview'] = False
        context['refresh_status'] = {'needs_refresh': False}
        context['can_precompute_explanation'] = False
        context['trade_validation_summary'] = {'available': False}

        html = render_to_string('explanation/index.html', context)
        trade_condition_html = html.split('<h2 class="common-section-title">売買条件</h2>', 1)[1].split('</section>', 1)[0]

        self.assertIn('候補外理由', trade_condition_html)
        self.assertIn('R/R不足', trade_condition_html)

    def test_beginner_decision_shows_limited_candidate_plan(self):
        snapshot = self._snapshot()
        snapshot.trade_decision.update({
            'selected_side': 'long',
            'decision_type': 'trend_follow',
            'decision_status': 'candidate_limited',
            'entry_permission': 'limited_entry',
            'position_size_pct': 25,
            'current_price': 72430,
            'entry_price': 72430,
            'entry_zone_low': 72376,
            'entry_zone_high': 72539,
            'target_1': {'label': 'T1', 'price': 73800, 'probability': 0.62},
            'stop_price': 71600,
            'invalidation_price': 71600,
            'reward_risk': 1.65,
            'confidence_score': 55,
            'confidence_grade': 'C+',
            'long_score': 70,
            'short_score': 30,
            'no_trade_score': 45,
            'soft_warning_reasons': ['局面別検証不足'],
        })

        context = snapshot_to_view(snapshot)
        beginner = context['beginner_decision']

        self.assertEqual(beginner['status'], 'buy_candidate')
        self.assertEqual(beginner['label'], '限定ロング候補')
        self.assertEqual(beginner['entry_permission_label'], '条件付きで入る')
        self.assertEqual(beginner['plain_action'], '押し目まで待つ。成行追撃は不可。建玉は通常の25%。')
        self.assertTrue(beginner['candidate_visible'])
        self.assertTrue(beginner['execution_allowed'])
        self.assertFalse(beginner['position_allowed'])
        self.assertEqual(beginner['entry_permission'], 'limited_entry')
        self.assertEqual(beginner['position_size_pct'], 25)
        self.assertNotEqual(beginner['target_1_display'], '—')
        self.assertNotEqual(beginner['stop_display'], '—')
        self.assertEqual(beginner['reward_risk_display'], '1.65')
        self.assertIn('局面別検証不足', beginner['warnings'])

    def test_beginner_decision_exposes_confidence_component_rows(self):
        snapshot = self._snapshot()
        snapshot.trade_decision.update({
            'selected_side': 'long',
            'decision_type': 'trend_follow',
            'decision_status': 'candidate_limited',
            'entry_permission': 'limited_entry',
            'position_size_pct': 25,
            'current_price': 72430,
            'entry_price': 72430,
            'entry_zone_low': 72376,
            'entry_zone_high': 72539,
            'target_1': {'label': 'T1', 'price': 73800, 'probability': 0.62},
            'stop_price': 71600,
            'invalidation_price': 71600,
            'reward_risk': 1.65,
            'confidence_score': 55,
            'confidence_grade': 'C+',
            'long_score': 70,
            'short_score': 30,
            'no_trade_score': 45,
            'confidence_components': {
                'basecalc_direction': 44,
                'macro_alignment': 62,
                'validation_quality': 35,
                'target_quality': 80,
                'target_hit_rate': 0.62,
                'stop_hit_rate': 0.18,
                'avg_realized_rr': 1.42,
                'data_quality': 71,
                'intermarket_confirmation': -5,
                'event_penalty': 6,
                'audit_penalty': 4,
                'confidence_cap_reason': '検証不足のため信頼度を限定',
            },
        })

        context = snapshot_to_view(snapshot)
        rows = context['beginner_decision']['confidence_component_rows']

        self.assertEqual(rows[0], {'label': '総合信頼度', 'value': 'C+ / 55%'})
        self.assertIn({'label': 'basecalc方向', 'value': '44点'}, rows)
        self.assertIn({'label': 'macro整合', 'value': '62点'}, rows)
        self.assertIn({'label': '検証品質', 'value': '35点'}, rows)
        self.assertIn({'label': 'target品質', 'value': '80点'}, rows)
        self.assertIn({'label': 'T1到達率', 'value': '62%'}, rows)
        self.assertIn({'label': 'stop到達率', 'value': '18%'}, rows)
        self.assertIn({'label': '実績R/R', 'value': '1.42'}, rows)
        self.assertIn({'label': '米国指数', 'value': '-5点'}, rows)
        self.assertIn({'label': 'イベント', 'value': '-6点'}, rows)
        self.assertIn({'label': '監査', 'value': '-4点'}, rows)
        self.assertIn({'label': '上限理由', 'value': '検証不足のため信頼度を限定'}, rows)

    def test_template_renders_confidence_component_rows(self):
        snapshot = self._snapshot()
        snapshot.trade_decision.update({
            'confidence_components': {
                'basecalc_direction': 44,
                'macro_alignment': 62,
                'validation_quality': 35,
                'target_quality': 80,
                'data_quality': 71,
                'intermarket_confirmation': -5,
                'event_penalty': 6,
                'audit_penalty': 4,
                'confidence_cap_reason': '検証不足のため信頼度を限定',
            },
        })
        context = snapshot_to_view(snapshot)
        context['is_preview'] = False
        context['refresh_status'] = {'needs_refresh': False}
        context['can_precompute_explanation'] = False
        context['trade_validation_summary'] = {'available': False}

        html = render_to_string('explanation/index.html', context)

        self.assertIn('信頼度内訳', html)
        self.assertIn('総合信頼度', html)
        self.assertIn('basecalc方向', html)
        self.assertIn('macro整合', html)
        self.assertIn('上限理由', html)
        self.assertIn('検証不足のため信頼度を限定', html)

    def test_beginner_decision_splits_wait_reasons_for_direction_rr_and_confidence(self):
        snapshot = self._snapshot()
        snapshot.confidence_score = 41
        snapshot.confidence_grade = 'C'
        snapshot.source_snapshots['basecalc']['raw']['world_model']['output_contract'] = {
            'contract_status': 'limited',
            'directional_allowed': False,
            'stop_reasons': ['ATRとの差が小さいため方向予測停止'],
        }
        snapshot.trade_decision.update({
            'selected_side': 'no_trade',
            'decision_type': 'no_trade_conflict',
            'current_price': 72430,
            'entry_price': 72430,
            'entry_zone_low': 72376,
            'entry_zone_high': 72539,
            'target_1': {'label': 'T1', 'price': 72400, 'probability': 0.21},
            'stop_price': 75800,
            'reward_risk': 0.95,
            'confidence_score': 41,
            'confidence_grade': 'C',
            'long_score': 45,
            'short_score': 30,
            'no_trade_score': 70,
            'blocked_reasons': ['R/R不足'],
        })

        context = snapshot_to_view(snapshot)
        beginner = context['beginner_decision']

        self.assertEqual(beginner['status'], 'wait')
        self.assertEqual(beginner['wait_reason_summary'], '待機：方向予測停止 / R/R不足 / 信頼度不足')
        self.assertIn('方向予測停止', beginner['wait_reasons'])
        self.assertIn('R/R不足', beginner['wait_reasons'])
        self.assertIn('信頼度不足', beginner['wait_reasons'])
        self.assertIn('ATR基準に届かないため、方向予測は参考表示にしています。', beginner['warnings'])

    def test_beginner_decision_wait_reason_cards_include_unlock_conditions(self):
        snapshot = self._snapshot()
        snapshot.confidence_score = 41
        snapshot.confidence_grade = 'C'
        snapshot.source_snapshots['macro']['factor_vector'] = {'event_risk_score': 80}
        snapshot.trade_decision.update({
            'selected_side': 'no_trade',
            'decision_type': 'no_trade_conflict',
            'current_price': 72430,
            'entry_price': 72430,
            'entry_zone_low': 72376,
            'entry_zone_high': 72539,
            'target_1': {'label': 'T1', 'price': 72400, 'probability': 0.21},
            'stop_price': 75800,
            'reward_risk': 0.95,
            'confidence_score': 41,
            'confidence_grade': 'C',
            'long_score': 45,
            'short_score': 30,
            'no_trade_score': 70,
            'soft_warning_reasons': ['局面別検証不足'],
            'blocked_reasons': ['R/R不足'],
        })

        context = snapshot_to_view(snapshot)
        cards = context['beginner_decision']['wait_reason_cards']

        self.assertIn(
            {'label': 'R/R不足', 'detail': '期待値が基準を下回っています', 'unlock_condition': '押し目・戻りを待つ'},
            cards,
        )
        self.assertIn(
            {'label': '検証不足', 'detail': '過去の確認件数がまだ足りません', 'unlock_condition': '方向は参考、限定候補扱い'},
            cards,
        )
        self.assertIn(
            {'label': 'イベント警戒', 'detail': '重要イベント前後で値動きが荒れやすい状態です', 'unlock_condition': '発表通過後に再判定'},
            cards,
        )

    def test_beginner_decision_hides_reference_candidate_when_no_trade_has_blocked_reasons(self):
        snapshot = self._snapshot()
        snapshot.trade_decision.update({
            'selected_side': 'no_trade',
            'decision_type': 'no_trade_conflict',
            'current_price': 72430,
            'entry_price': 72430,
            'entry_zone_low': 72376,
            'entry_zone_high': 72539,
            'target_1': {'label': 'T1', 'price': 71800, 'probability': 0.21},
            'stop_price': 72950,
            'reward_risk': 0.95,
            'confidence_score': 41,
            'confidence_grade': 'C',
            'long_score': 0,
            'short_score': 15,
            'no_trade_score': 80,
            'blocked_reasons': ['R/R不足'],
        })

        context = snapshot_to_view(snapshot)
        candidate = context['beginner_decision']['reference_candidate']

        self.assertFalse(candidate['available'])

    def test_beginner_decision_blocks_invalid_long_target_direction(self):
        snapshot = self._snapshot()
        snapshot.trade_decision.update({
            'selected_side': 'long',
            'decision_type': 'trend_follow',
            'current_price': 72430,
            'entry_price': 72430,
            'entry_zone_low': 72300,
            'entry_zone_high': 72450,
            'target_1': {'label': 'T1', 'price': 72400, 'probability': 0.62},
            'stop_price': 71900,
            'reward_risk': 1.65,
            'confidence_score': 72,
            'confidence_grade': 'B',
            'blocked_reasons': [],
        })

        context = snapshot_to_view(snapshot)
        beginner = context['beginner_decision']

        self.assertFalse(beginner['tradable'])
        self.assertEqual(beginner['status'], 'wait')
        self.assertEqual(beginner['target_1_display'], '—')
        self.assertIn('target/stop が現在値と整合していません', beginner['warnings'])

    def test_beginner_decision_allows_valid_sell_candidate(self):
        snapshot = self._snapshot()
        snapshot.macro_bias = 'negative'
        snapshot.basecalc_bias = 'bearish'
        snapshot.alignment_status = 'aligned'
        snapshot.trade_decision.update({
            'selected_side': 'short',
            'decision_type': 'trend_follow',
            'current_price': 72430,
            'entry_price': 72430,
            'entry_zone_low': 72500,
            'entry_zone_high': 72650,
            'target_1': {'label': 'T1', 'price': 71800, 'probability': 0.62},
            'stop_price': 72950,
            'reward_risk': 1.55,
            'confidence_score': 64,
            'confidence_grade': 'B-',
            'long_score': 35,
            'short_score': 72,
            'no_trade_score': 35,
            'blocked_reasons': [],
        })

        context = snapshot_to_view(snapshot)
        beginner = context['beginner_decision']

        self.assertTrue(beginner['tradable'])
        self.assertEqual(beginner['status'], 'sell_candidate')
        self.assertEqual(beginner['label'], 'ショート候補')
        self.assertEqual(beginner['target_1_display'], '71,800円')
        self.assertEqual(beginner['stop_display'], '72,950円')

    def test_template_top_badge_uses_trade_side_for_buy_candidate(self):
        snapshot = self._snapshot()
        snapshot.trade_decision.update({
            'reward_risk': 1.6,
            'target_1': {'label': 'T1', 'price': 71180, 'probability': 0.62},
            'stop_price': 67620,
            'confidence_score': 68,
            'confidence_grade': 'B-',
        })
        context = snapshot_to_view(snapshot)
        context['is_preview'] = False
        context['refresh_status'] = {'needs_refresh': False}
        context['can_precompute_explanation'] = False
        context['trade_validation_summary'] = {'available': False}

        html = render_to_string('explanation/index.html', context)
        decision_card_html = html.split('</section>', 1)[0]

        self.assertIn('現在判断：ロング候補', decision_card_html)
        self.assertIn('行動：入る候補', decision_card_html)
        self.assertNotIn('common-choice-card__score', decision_card_html)

    def test_template_top_badge_uses_trade_side_for_sell_candidate(self):
        snapshot = self._snapshot()
        snapshot.macro_bias = 'negative'
        snapshot.basecalc_bias = 'bearish'
        snapshot.trade_decision.update({
            'selected_side': 'short',
            'current_price': 72430,
            'entry_price': 72430,
            'entry_zone_low': 72500,
            'entry_zone_high': 72650,
            'target_1': {'label': 'T1', 'price': 71800, 'probability': 0.62},
            'stop_price': 72950,
            'reward_risk': 1.55,
            'confidence_score': 64,
            'confidence_grade': 'B-',
            'long_score': 35,
            'short_score': 72,
            'no_trade_score': 35,
            'blocked_reasons': [],
        })
        context = snapshot_to_view(snapshot)
        context['is_preview'] = False
        context['refresh_status'] = {'needs_refresh': False}
        context['can_precompute_explanation'] = False
        context['trade_validation_summary'] = {'available': False}

        html = render_to_string('explanation/index.html', context)
        decision_card_html = html.split('</section>', 1)[0]

        self.assertIn('現在判断：ショート候補', decision_card_html)
        self.assertIn('行動：入る候補', decision_card_html)
        self.assertNotIn('common-choice-card__score', decision_card_html)

    def test_template_reference_candidate_labels_monitoring_line_not_reference_candidate(self):
        snapshot = self._snapshot()
        snapshot.trade_decision.update({
            'selected_side': 'long',
            'decision_type': 'trend_follow',
            'current_price': 72430,
            'entry_price': 72430,
            'entry_zone_low': 72300,
            'entry_zone_high': 72450,
            'target_1': {'label': 'T1', 'price': 73100, 'probability': 0.62},
            'stop_price': 71900,
            'reward_risk': 1.34,
            'confidence_score': 64,
            'confidence_grade': 'B-',
            'long_score': 58,
            'short_score': 35,
            'no_trade_score': 40,
            'blocked_reasons': [],
        })
        context = snapshot_to_view(snapshot)
        context['is_preview'] = False
        context['refresh_status'] = {'needs_refresh': False}
        context['can_precompute_explanation'] = False
        context['trade_validation_summary'] = {'available': False}

        html = render_to_string('explanation/index.html', context)
        top_html = html.split('詳細を表示', 1)[0]

        self.assertIn('この水準は売買候補ではありません。条件変化を確認するための目安です。', top_html)
        self.assertNotIn('参考候補', top_html)

    def test_beginner_decision_requires_side_score_to_clear_no_trade(self):
        snapshot = self._snapshot()
        snapshot.trade_decision.update({
            'selected_side': 'long',
            'decision_type': 'trend_follow',
            'current_price': 72430,
            'entry_price': 72430,
            'entry_zone_low': 72300,
            'entry_zone_high': 72450,
            'target_1': {'label': 'T1', 'price': 73100, 'probability': 0.62},
            'stop_price': 71900,
            'reward_risk': 1.34,
            'confidence_score': 64,
            'confidence_grade': 'B-',
            'long_score': 58,
            'short_score': 35,
            'no_trade_score': 62,
            'blocked_reasons': [],
        })

        context = snapshot_to_view(snapshot)
        beginner = context['beginner_decision']

        self.assertFalse(beginner['tradable'])
        self.assertEqual(beginner['status'], 'wait')
        self.assertIn('スコア不足', beginner['wait_reasons'])
        self.assertIn('no_tradeより弱い', beginner['wait_reasons'])

    def test_decision_card_marks_low_confidence_blocked_trade_as_reference(self):
        snapshot = self._snapshot()
        snapshot.trade_decision.update({
            'selected_side': 'no_trade',
            'decision_type': 'no_trade_conflict',
            'confidence_score': 49,
            'confidence_grade': 'C',
            'blocked_reasons': ['類似事例不足のため信頼度を限定'],
        })

        context = snapshot_to_view(snapshot)

        self.assertEqual(context['decision_card']['confidence'], '参考判定（C / 49%）')

    def test_contract_error_hides_trade_targets_and_marks_basecalc_stopped(self):
        snapshot = self._snapshot()
        raw = snapshot.source_snapshots['basecalc']['raw']
        raw['world_model']['contract_status'] = 'error'
        raw['world_model']['stop_reasons'] = ['現在値と計算基準価格が不一致']
        raw['world_model']['output_contract'] = {
            'contract_status': 'error',
            'stop_reasons': ['現在値と計算基準価格が不一致'],
            'target_display_allowed': False,
            'probability_display_allowed': False,
            'allowed_horizons': {},
        }

        context = snapshot_to_view(snapshot)

        self.assertEqual(context['decision_card']['label'], 'ロング')
        self.assertEqual(context['long_judgment']['stance'], '採用')
        snapshot.trade_decision = {}
        context = snapshot_to_view(snapshot)
        self.assertEqual(context['long_judgment']['stance'], '停止')
        self.assertEqual(context['long_judgment']['price'], 'N/A')
        self.assertEqual(context['long_judgment']['probability'], '表示停止')
        self.assertEqual(context['short_judgment']['stance'], '停止')
        self.assertEqual(context['basecalc']['summary'], 'basecalcの方向判断は停止。理由：現在値と計算基準価格が不一致')

    def test_template_renders_priority_sections_before_details(self):
        context = snapshot_to_view(self._snapshot())
        context['is_preview'] = False
        context['refresh_status'] = {'needs_refresh': False}
        context['can_precompute_explanation'] = False

        html = render_to_string('explanation/index.html', context)

        decision_index = html.index('現在判断')
        trigger_index = html.index('次に見る条件')
        alignment_index = html.index('Macro / Basecalc 統合関係')
        detail_index = html.index('詳細を表示')
        long_index = html.index('ロング条件詳細')
        short_index = html.index('ショート条件詳細')
        world_index = html.index('world model 予測数値')

        self.assertLess(decision_index, trigger_index)
        self.assertLess(trigger_index, alignment_index)
        self.assertLess(alignment_index, detail_index)
        self.assertLess(detail_index, long_index)
        self.assertLess(long_index, short_index)
        self.assertLess(short_index, world_index)

    def test_api_includes_trade_decision_selected_side(self):
        payload = snapshot_to_api(self._snapshot())

        self.assertEqual(payload['version'], 'explanation_v2')
        self.assertEqual(payload['trade_decision']['selected_side'], 'long')
        self.assertEqual(payload['trade_decision']['target_1']['price'], 71180)

    def test_api_includes_separated_readiness_score_bundle(self):
        snapshot = self._snapshot()
        snapshot.trade_decision.update({
            'decision_status': 'candidate_limited',
            'entry_permission': 'limited_entry',
            'confidence_score': 58,
            'confidence_grade': 'C+',
            'hard_block_reasons': [],
            'soft_warning_reasons': ['局面別検証不足'],
        })

        payload = snapshot_to_api(snapshot)

        score_bundle = payload['score_bundle']
        self.assertEqual(score_bundle['score_type'], 'score_bundle')
        self.assertGreaterEqual(score_bundle['system_quality_score'], 90)
        self.assertEqual(score_bundle['decision_confidence_score'], 58)
        self.assertIn('validation_readiness_score', score_bundle)
        self.assertEqual(score_bundle['decision_confidence_label'], '限定候補')

    def test_api_prefers_persisted_score_bundle_from_static_snapshot(self):
        snapshot = self._snapshot()
        snapshot.score_breakdown = {
            'score_bundle': {
                'score_type': 'score_bundle',
                'system_quality_score': 91,
                'system_quality_label': '保存済み',
                'decision_confidence_score': 52,
                'decision_confidence_label': '保存済み限定',
                'validation_readiness_score': 33,
                'validation_readiness_label': '保存済み検証',
                'system_quality_components': [],
            },
        }

        payload = snapshot_to_api(snapshot)

        self.assertEqual(payload['score_bundle']['system_quality_score'], 91)
        self.assertEqual(payload['score_bundle']['system_quality_label'], '保存済み')
        self.assertEqual(payload['score_bundle']['decision_confidence_label'], '保存済み限定')

    def test_static_snapshot_round_trips_for_json_artifact(self):
        from .services.static_snapshot import (
            append_static_explanation_history,
            load_static_snapshot_history,
            load_static_explanation_snapshot,
            write_static_explanation_snapshot,
        )

        with TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / 'latest_snapshot.json'
            history = Path(tmpdir) / 'snapshot_history.json'

            written_payload = write_static_explanation_snapshot(self._snapshot(), output)
            loaded = load_static_explanation_snapshot(output)
            snapshot = self._snapshot()
            repeated_snapshot = self._snapshot()
            repeated_snapshot.as_of = snapshot.as_of + timedelta(minutes=5)
            first = append_static_explanation_history(snapshot, history)
            second = append_static_explanation_history(repeated_snapshot, history)
            rows = load_static_snapshot_history(history)

        self.assertEqual(loaded.final_label, '条件付き上昇優勢')
        self.assertTrue(loaded.source_snapshots)
        self.assertEqual(loaded.trade_decision['selected_side'], 'long')
        self.assertEqual(written_payload['score_bundle']['score_type'], 'score_bundle')
        self.assertEqual(loaded.score_breakdown['score_bundle']['score_type'], 'score_bundle')
        self.assertEqual(loaded.source_snapshots['macro']['summary'], 'Macroは支援的。')
        self.assertIn('snapshot_key', rows[0])
        self.assertEqual(first['added'], True)
        self.assertEqual(second['added'], True)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]['as_of'], repeated_snapshot.as_of.isoformat())

    def test_static_validation_summary_reads_requested_outcomes_path(self):
        with TemporaryDirectory() as tmpdir:
            outcomes = Path(tmpdir) / 'trade_outcomes.json'
            outcomes.write_text(
                '''
                {
                  "schema": "explanation_trade_outcomes_v1",
                  "generated_at": "2026-07-01T00:00:00+09:00",
                  "outcomes": [
                    {
                      "snapshot_key": "abc",
                      "explanation_as_of": "2026-06-30T00:00:00+09:00",
                      "horizon": "1d",
                      "evaluated_at": "2026-07-01T00:00:00+09:00",
                      "selected_side": "no_trade",
                      "decision_type": "no_trade_direction_stopped",
                      "direction_hit": false,
                      "horizon_return_pct": 1.1
                    }
                  ]
                }
                ''',
                encoding='utf-8',
            )

            summary = build_static_trade_validation_summary(outcomes)

        self.assertTrue(summary['available'])
        self.assertEqual(summary['total_count'], 1)
        self.assertEqual(summary['actionable_count'], 0)
        self.assertEqual(summary['wait_count'], 1)
        self.assertEqual(summary['side_rows'][0]['direction_hit_rate'], 'N/A')
        self.assertIn('pending_count', summary)
        self.assertIn('wait_quality_rows', summary)

    def test_trade_outcomes_json_payload_has_summary_snapshot_key_and_sanitized_wait_rows(self):
        from .services.static_snapshot import trade_outcomes_payload

        payload = trade_outcomes_payload(outcomes=[], static_rows=[
            {
                'snapshot_key': 'abc',
                'explanation_as_of': '2026-06-30T00:00:00+09:00',
                'horizon': '1d',
                'evaluated_at': '2026-07-01T00:00:00+09:00',
                'selected_side': 'no_trade',
                'decision_type': 'no_trade_direction_stopped',
                'direction_hit': False,
                'target_1_hit': False,
                'target_1_price': 71000,
                'target_2_hit': False,
                'stop_hit': False,
                'stop_price': 70000,
                'realized_rr': 1.2,
                'expected_rr': 1.5,
                'horizon_return_pct': 0.2,
            },
        ])

        row = payload['outcomes'][0]
        self.assertEqual(payload['summary']['total_count'], 1)
        self.assertEqual(payload['summary']['wait_count'], 1)
        self.assertEqual(row['snapshot_key'], 'abc')
        self.assertIsNone(row['direction_hit'])
        self.assertIsNone(row['target_1_hit'])
        self.assertIsNone(row['target_2_hit'])
        self.assertIsNone(row['stop_hit'])
        self.assertIsNone(row['target_1_price'])
        self.assertIsNone(row['stop_price'])
        self.assertIsNone(row['realized_rr'])
        self.assertIsNone(row['expected_rr'])

    def test_due_snapshot_without_market_price_is_kept_as_pending_outcome(self):
        from .services.static_snapshot import explanation_snapshot_payload
        from .services.validation_engine import build_pending_trade_outcomes

        snapshot = self._snapshot()
        snapshot.as_of = timezone.now() - timedelta(days=2)
        payload = explanation_snapshot_payload(snapshot)

        with mock.patch('explanation.services.validation_engine.nearest_bar_for_horizon', return_value=None):
            rows = build_pending_trade_outcomes([payload], [], horizon='1d', now=timezone.now())

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['snapshot_key'], payload['snapshot_key'])
        self.assertEqual(rows[0]['horizon'], '1d')
        self.assertEqual(rows[0]['outcome_kind'], 'pending')
        self.assertEqual(rows[0]['selected_side'], 'long')

    def test_not_due_snapshot_is_not_written_as_pending(self):
        from .services.static_snapshot import explanation_snapshot_payload
        from .services.validation_engine import build_pending_trade_outcomes

        snapshot = self._snapshot()
        snapshot.as_of = timezone.now()
        payload = explanation_snapshot_payload(snapshot)

        rows = build_pending_trade_outcomes([payload], [], horizon='1d', now=timezone.now())

        self.assertEqual(rows, [])

    def test_wait_missed_opportunity_uses_horizon_specific_thresholds(self):
        from .services.validation_engine import _outcome_metrics

        decision = {
            'selected_side': 'no_trade',
            'current_price': 100,
            'counter_scenario': {'direction': 'up'},
        }

        one_day = _outcome_metrics(decision, [], 100.8, horizon='1d')
        three_day_small = _outcome_metrics(decision, [], 101.0, horizon='3d')
        three_day_large = _outcome_metrics(decision, [], 101.3, horizon='3d')
        five_day_small = _outcome_metrics(decision, [], 101.4, horizon='5d')
        five_day_large = _outcome_metrics(decision, [], 101.6, horizon='5d')

        self.assertTrue(one_day['missed_opportunity'])
        self.assertFalse(three_day_small['missed_opportunity'])
        self.assertTrue(three_day_large['missed_opportunity'])
        self.assertFalse(five_day_small['missed_opportunity'])
        self.assertTrue(five_day_large['missed_opportunity'])

    def test_readiness_score_cannot_reach_90_with_less_than_50_results(self):
        from .services.readiness_score import build_readiness_score

        snapshot = self._snapshot()
        summary = {'available': True, 'total_count': 49}

        readiness = build_readiness_score(snapshot, summary)

        self.assertLess(readiness['score'], 90)
        self.assertNotEqual(readiness['label'], '実績確認済み')
        self.assertEqual(readiness['minimum_required_results'], 50)
        self.assertEqual(readiness['remaining_results_to_90'], 1)

    def test_readiness_score_exposes_beginner_validation_displays(self):
        from .services.readiness_score import build_readiness_score

        snapshot = self._snapshot()

        low = build_readiness_score(snapshot, {'available': True, 'total_count': 8, 'actionable_count': 0})
        partial = build_readiness_score(snapshot, {'available': True, 'total_count': 32, 'actionable_count': 4})
        enough = build_readiness_score(snapshot, {'available': True, 'total_count': 70, 'actionable_count': 12})

        self.assertEqual(low['validation_state_display'], '検証中')
        self.assertEqual(low['actionable_result_display'], '不足')
        self.assertEqual(low['validation_attention_display'], '検証不足のため建玉サイズを制限')
        self.assertEqual(partial['validation_state_display'], '一部検証済み')
        self.assertEqual(partial['actionable_result_display'], '蓄積中')
        self.assertEqual(enough['validation_state_display'], '検証済み')
        self.assertEqual(enough['actionable_result_display'], '十分')
        self.assertEqual(enough['validation_attention_display'], '検証済み。通常のリスク管理を継続')

    def test_readiness_score_separates_system_quality_decision_confidence_and_live_validation(self):
        from .services.readiness_score import build_readiness_score

        snapshot = self._snapshot()
        snapshot.trade_decision.update({
            'decision_status': 'candidate_limited',
            'entry_permission': 'limited_entry',
            'confidence_score': 58,
            'confidence_grade': 'C+',
            'hard_block_reasons': [],
            'soft_warning_reasons': ['局面別検証不足'],
        })

        readiness = build_readiness_score(snapshot, {'available': True, 'total_count': 8, 'actionable_count': 2})

        self.assertEqual(readiness['score_type'], 'score_bundle')
        self.assertGreaterEqual(readiness['system_quality_score'], 90)
        self.assertEqual(readiness['system_quality_label'], '実用表示可')
        self.assertEqual(readiness['decision_confidence_score'], 58)
        self.assertEqual(readiness['decision_confidence_label'], '限定候補')
        self.assertLess(readiness['validation_readiness_score'], 90)
        self.assertEqual(readiness['validation_readiness_label'], '検証参考')
        self.assertEqual(readiness['score'], readiness['validation_readiness_score'])
        labels = [row['label'] for row in readiness['system_quality_components']]
        values = [row['value'] for row in readiness['system_quality_components']]
        self.assertEqual(labels, ['判断材料', '理由分離', '停止状態', '判定契約', '表示文言'])
        self.assertIn('20/20', values)
        self.assertTrue(all(row['status'] == 'OK' for row in readiness['system_quality_components']))

    def test_readiness_score_prioritizes_hard_block_over_stale_candidate_status(self):
        from .services.readiness_score import build_readiness_score

        snapshot = self._snapshot()
        snapshot.audit_level = 'blocked'
        snapshot.trade_decision.update({
            'decision_status': 'candidate_limited',
            'entry_permission': 'limited_entry',
            'confidence_score': 58,
            'confidence_grade': 'C+',
            'hard_block_reasons': ['現在値と計算基準価格が不一致'],
            'soft_warning_reasons': [],
        })

        readiness = build_readiness_score(snapshot, {'available': True, 'total_count': 70, 'actionable_count': 12})

        self.assertLess(readiness['system_quality_score'], 90)
        self.assertEqual(readiness['system_quality_label'], '改善中')
        self.assertLess(readiness['decision_confidence_score'], 40)
        self.assertEqual(readiness['decision_confidence_label'], '判定停止')
        stopped = next(row for row in readiness['system_quality_components'] if row['label'] == '停止状態')
        self.assertEqual(stopped['status'], '要確認')
        self.assertIn('判定停止', stopped['message'])

    def test_template_renders_separate_quality_confidence_and_validation_scores(self):
        snapshot = self._snapshot()
        snapshot.trade_decision.update({
            'decision_status': 'candidate_limited',
            'entry_permission': 'limited_entry',
            'confidence_score': 58,
            'confidence_grade': 'C+',
            'hard_block_reasons': [],
            'soft_warning_reasons': ['局面別検証不足'],
        })
        context = snapshot_to_view(snapshot)
        context['is_preview'] = False
        context['refresh_status'] = {'needs_refresh': False}
        context['can_precompute_explanation'] = False
        context['trade_validation_summary'] = {'available': True, 'total_count': 8, 'actionable_count': 2, 'horizon_rows': []}
        from .services.readiness_score import build_readiness_score
        context['readiness_score'] = build_readiness_score(snapshot, context['trade_validation_summary'])

        html = render_to_string('explanation/index.html', context)
        validation_section = html.split('<h2 class="common-section-title">検証成績</h2>', 1)[1].split('</section>', 1)[0]

        self.assertIn('ページ完成度', validation_section)
        self.assertIn('今回の判断信頼度', validation_section)
        self.assertIn('ライブ検証', validation_section)
        self.assertIn('実用表示可', validation_section)
        self.assertIn('限定候補', validation_section)
        self.assertIn('ページ完成度内訳', validation_section)
        self.assertIn('理由分離', validation_section)
        self.assertIn('OK', validation_section)

    def test_template_renders_system_quality_component_messages_when_attention_is_needed(self):
        snapshot = self._snapshot()
        snapshot.audit_level = 'blocked'
        snapshot.trade_decision.update({
            'decision_status': 'candidate_limited',
            'entry_permission': 'limited_entry',
            'confidence_score': 58,
            'confidence_grade': 'C+',
            'hard_block_reasons': ['現在値と計算基準価格が不一致'],
            'soft_warning_reasons': [],
        })
        context = snapshot_to_view(snapshot)
        context['is_preview'] = False
        context['refresh_status'] = {'needs_refresh': False}
        context['can_precompute_explanation'] = False
        context['trade_validation_summary'] = {'available': True, 'total_count': 70, 'actionable_count': 12, 'horizon_rows': []}
        from .services.readiness_score import build_readiness_score
        context['readiness_score'] = build_readiness_score(snapshot, context['trade_validation_summary'])

        html = render_to_string('explanation/index.html', context)
        validation_section = html.split('<h2 class="common-section-title">検証成績</h2>', 1)[1].split('</section>', 1)[0]

        self.assertIn('停止状態 要確認 8/20', validation_section)
        self.assertIn('判定停止理由あり', validation_section)
        self.assertIn('判定停止', validation_section)

    def test_template_keeps_quality_scores_visible_without_live_validation_results(self):
        snapshot = self._snapshot()
        snapshot.trade_decision.update({
            'decision_status': 'candidate_limited',
            'entry_permission': 'limited_entry',
            'confidence_score': 58,
            'confidence_grade': 'C+',
            'hard_block_reasons': [],
            'soft_warning_reasons': ['局面別検証不足'],
        })
        context = snapshot_to_view(snapshot)
        context['is_preview'] = False
        context['refresh_status'] = {'needs_refresh': False}
        context['can_precompute_explanation'] = False
        context['trade_validation_summary'] = {'available': False}
        from .services.readiness_score import build_readiness_score
        context['readiness_score'] = build_readiness_score(snapshot, context['trade_validation_summary'])

        html = render_to_string('explanation/index.html', context)
        validation_section = html.split('<h2 class="common-section-title">検証成績</h2>', 1)[1].split('</section>', 1)[0]

        self.assertIn('ページ完成度', validation_section)
        self.assertIn('今回の判断信頼度', validation_section)
        self.assertIn('ライブ検証', validation_section)
        self.assertIn('判定検証 0件', validation_section)
        self.assertIn('実用表示可', validation_section)
        self.assertIn('限定候補', validation_section)

    def test_template_shows_refresh_warning_and_precompute_button(self):
        context = snapshot_to_view(self._snapshot())
        context['is_preview'] = False
        context['refresh_status'] = {
            'needs_refresh': True,
            'message': 'Macro / Basecalc が更新されています。Explanation の再作成が必要です。',
            'latest_source_label': 'Macro',
        }
        context['can_precompute_explanation'] = True

        html = render_to_string('explanation/index.html', context)

        self.assertIn('Explanation の再作成が必要です。', html)
        self.assertIn('/explanation/precompute/', html)
        self.assertIn('Explanationを再作成', html)

    def test_template_renders_manual_price_form(self):
        context = snapshot_to_view(self._snapshot())
        context['is_preview'] = True
        context['refresh_status'] = {'needs_refresh': False}
        context['can_precompute_explanation'] = False
        context['manual_price'] = {
            'active': True,
            'price': 42000,
            'price_display': '42,000',
        }

        html = render_to_string('explanation/index.html', context)

        self.assertIn('name="price"', html)
        self.assertIn('value="42000"', html)
        self.assertNotIn('手入力価格を使用中: 42,000', html)
        self.assertIn('判定に使った材料', html)
        self.assertIn('Macro判定作成時刻', html)
        self.assertIn('Basecalc判定作成時刻', html)
        self.assertIn('Basecalc市場価格取得時刻', html)
        self.assertIn('表示価格', html)
        self.assertIn('価格ソース', html)
        self.assertIn('米国3指数', html)
        self.assertNotIn('手入力価格による一時総合判定です。', html)
        self.assertNotIn('保存済み判断がないため', html)

    def test_manual_price_context_explains_source_inputs(self):
        snapshot = self._snapshot()
        snapshot.confidence_grade = 'D'
        snapshot.confidence_score = 38
        snapshot.source_snapshots['basecalc']['raw']['manual_price_override'] = {
            'active': True,
            'price': 42000,
            'price_display': '42,000',
        }
        snapshot.source_snapshots['basecalc']['raw']['manual_price_mode'] = {
            'basis': 'saved_basecalc_with_manual_price',
            'macro_source': '保存済み最新判断',
            'basecalc_source': '保存済みチャート判断に手入力価格を反映',
        }

        context = snapshot_to_view(snapshot)

        self.assertTrue(context['manual_price']['active'])
        self.assertEqual(context['confidence_display'], '参考判定（価格は手入力）')
        self.assertEqual(context['manual_price']['status_label'], '手入力価格による一時総合判定。')
        self.assertIn('42,000円', context['manual_price']['summary'])
        self.assertEqual(
            context['manual_price']['source_rows'][0],
            {'label': '判定対象価格', 'value': '42,000円（手入力）'},
        )

    def test_decision_inputs_show_update_times_manual_price_us_indices_and_materials(self):
        snapshot = self._snapshot()
        snapshot.source_snapshots['macro']['raw'] = {
            'generated_at': '2026-06-19T08:30:00+00:00',
        }
        snapshot.source_snapshots['basecalc']['raw']['generated_at'] = '2026-06-19T09:40:00+00:00'
        snapshot.source_snapshots['basecalc']['raw']['manual_price_override'] = {
            'active': True,
            'price': 42000,
            'price_display': '42,000',
        }
        snapshot.source_snapshots['basecalc']['raw']['world_model']['us_index_confirmation'] = {
            'readiness': {'usable': True},
            'components': {'nasdaq100_futures': {}, 'sp500_futures': {}, 'dow_futures': {}},
        }

        context = snapshot_to_view(snapshot)

        self.assertEqual(
            context['decision_inputs']['rows'][1:],
            [
                {'label': 'Macro判定作成時刻', 'value': '2026-06-19 17:30 JST'},
                {'label': 'Basecalc判定作成時刻', 'value': '2026-06-19 18:40 JST'},
                {'label': 'Basecalc市場価格取得時刻', 'value': 'N/A'},
                {'label': '表示価格', 'value': '69,400円'},
                {'label': '価格ソース', 'value': 'market_data'},
                {'label': '手入力価格', 'value': '42,000円'},
                {'label': '米国3指数', 'value': 'あり'},
                {'label': 'Snapshot Key', 'value': 'N/A'},
                {'label': 'Git SHA', 'value': 'N/A'},
                {'label': 'Workflow Run ID', 'value': 'N/A'},
            ],
        )
        self.assertEqual(context['decision_inputs']['rows'][0]['label'], 'Explanation作成時刻')
        self.assertEqual(
            context['decision_inputs']['materials'],
            ['Basecalcは上方向。', 'Macroは支援的。'],
        )


class ExplanationFreshnessTests(SimpleTestCase):
    def _snapshot(self):
        return ExplanationSnapshot(
            as_of=timezone.datetime(2026, 6, 19, 8, 0, tzinfo=timezone.utc),
            final_label='条件付き上昇優勢',
            final_stance='conditional_bullish',
            action_posture='押し目待ち。',
            confidence_score=68,
            confidence_grade='B-',
            macro_bias='positive',
            basecalc_bias='bullish',
            alignment_status='aligned',
            data_quality_score=80,
            audit_level='valid',
            audit_items=[],
            scenario={},
            evidence=[],
            source_snapshots={
                'macro': {'raw': {'generated_at': '2026-06-19T08:00:00+00:00'}},
                'basecalc': {'raw': {'generated_at': '2026-06-19T08:00:00+00:00'}},
            },
            score_breakdown={},
        )

    def test_refresh_needed_when_macro_payload_is_newer_than_saved_source(self):
        status = build_explanation_refresh_status(
            self._snapshot(),
            macro_payload={'generated_at': '2026-06-19T08:30:00+00:00'},
            basecalc_snapshot={'generated_at': '2026-06-19T08:00:00+00:00'},
        )

        self.assertTrue(status['needs_refresh'])
        self.assertEqual(status['latest_source_label'], 'Macro')
        self.assertIn('再作成が必要', status['message'])

    def test_refresh_not_needed_when_sources_are_not_newer(self):
        status = build_explanation_refresh_status(
            self._snapshot(),
            macro_payload={'generated_at': '2026-06-19T08:00:00+00:00'},
            basecalc_snapshot={'generated_at': '2026-06-19T07:59:00+00:00'},
        )

        self.assertFalse(status['needs_refresh'])


class ExplanationIntegrityCommandTests(SimpleTestCase):
    def test_integrity_requires_manifest_explanation_tracking_metadata(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            latest = root / 'latest_snapshot.json'
            history = root / 'snapshot_history.json'
            outcomes = root / 'trade_outcomes.json'
            manifest = root / 'finance_data_manifest.json'
            latest.write_text(
                """
                {
                  "snapshot_key": "snapshot-1",
                  "schema": "explanation_snapshot_v1",
                  "generated_at": "2026-06-25T01:16:00+00:00",
                  "git_sha": "abcdef1234567890",
                  "workflow_run_id": "12345",
                  "as_of": "2026-06-25T01:15:00+00:00",
                  "version": "explanation_v2",
                  "final": {
                    "label": "見送り",
                    "stance": "neutral_wait",
                    "action_posture": "待機",
                    "confidence_score": 50,
                    "confidence_grade": "C",
                    "status": "limited"
                  },
                  "macro": {"bias": "neutral"},
                  "basecalc": {"bias": "range"},
                  "alignment_status": "aligned",
                  "data_quality_score": 50,
                  "audit": {"level": "limited", "items": []},
                  "trade_decision": {
                    "selected_side": "no_trade",
                    "decision_type": "no_trade_direction_stopped",
                    "current_price": 70000
                  },
                  "source_snapshots": {},
                  "score_breakdown": {}
                }
                """,
                encoding='utf-8',
            )
            history.write_text(
                '{"schema":"explanation_snapshot_history_v1","generated_at":null,"max_rows":500,"snapshots":[]}',
                encoding='utf-8',
            )
            outcomes.write_text(
                '{"schema":"explanation_trade_outcomes_v1","generated_at":null,"summary":{},"outcomes":[]}',
                encoding='utf-8',
            )
            manifest.write_text(
                '{"schema":"finance_data_manifest_v1","explanation_as_of":"2026-06-25T01:15:00+00:00"}',
                encoding='utf-8',
            )

            with self.assertRaises(CommandError):
                call_command(
                    'check_explanation_integrity',
                    latest=str(latest),
                    history=str(history),
                    outcomes=str(outcomes),
                    manifest=str(manifest),
                    stdout=StringIO(),
                )

    def test_integrity_rejects_no_trade_snapshot_with_execution_levels(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            latest = root / 'latest_snapshot.json'
            history = root / 'snapshot_history.json'
            outcomes = root / 'trade_outcomes.json'
            manifest = root / 'finance_data_manifest.json'
            latest.write_text(
                """
                {
                  "snapshot_key": "snapshot-1",
                  "schema": "explanation_snapshot_v1",
                  "generated_at": "2026-06-25T01:16:00+00:00",
                  "git_sha": "abcdef1234567890",
                  "workflow_run_id": "12345",
                  "as_of": "2026-06-25T01:15:00+00:00",
                  "version": "explanation_v2",
                  "final": {
                    "label": "見送り",
                    "stance": "neutral_wait",
                    "action_posture": "待機",
                    "confidence_score": 50,
                    "confidence_grade": "C",
                    "status": "limited"
                  },
                  "macro": {"bias": "neutral"},
                  "basecalc": {"bias": "range"},
                  "alignment_status": "aligned",
                  "data_quality_score": 50,
                  "audit": {"level": "valid", "items": []},
                  "trade_decision": {
                    "selected_side": "no_trade",
                    "decision_type": "no_trade_conflict",
                    "current_price": 70000,
                    "target_1": {"label": "T1", "price": 69000},
                    "stop_price": 70500,
                    "reward_risk": 2.0
                  },
                  "source_snapshots": {},
                  "score_breakdown": {}
                }
                """,
                encoding='utf-8',
            )
            history.write_text(
                '{"schema":"explanation_snapshot_history_v1","generated_at":null,"max_rows":500,"snapshots":[]}',
                encoding='utf-8',
            )
            outcomes.write_text(
                '{"schema":"explanation_trade_outcomes_v1","generated_at":null,"summary":{},"outcomes":[]}',
                encoding='utf-8',
            )
            manifest.write_text(
                """
                {
                  "schema": "finance_data_manifest_v1",
                  "explanation_as_of": "2026-06-25T01:15:00+00:00",
                  "explanation_generated_at": "2026-06-25T01:16:00+00:00",
                  "git_sha": "abcdef1234567890",
                  "workflow_run_id": "12345"
                }
                """,
                encoding='utf-8',
            )

            with self.assertRaises(CommandError):
                call_command(
                    'check_explanation_integrity',
                    latest=str(latest),
                    history=str(history),
                    outcomes=str(outcomes),
                    manifest=str(manifest),
                    stdout=StringIO(),
                )


class ExplanationTradeOutcomeValidationTests(TestCase):
    def test_evaluate_trade_outcome_records_target_stop_direction_and_rr(self):
        as_of = timezone.now() - timedelta(days=4)
        snapshot = ExplanationSnapshot.objects.create(
            as_of=as_of,
            final_label='強気継続',
            final_stance='bullish',
            action_posture='上昇継続。',
            confidence_score=70,
            confidence_grade='B',
            macro_bias='positive',
            basecalc_bias='bullish',
            alignment_status='aligned',
            data_quality_score=80,
            audit_level='valid',
            audit_items=[],
            scenario={},
            evidence=[],
            trade_decision={
                'selected_side': 'long',
                'decision_type': 'trend_follow',
                'current_price': 42000,
                'entry_price': 42000,
                'target_1': {'price': 42800},
                'target_2': {'price': 43200},
                'stop_price': 41400,
                'reward_risk': 1.33,
                'confidence_score': 70,
            },
            source_snapshots={
                'basecalc': {
                    'raw': {
                        'world_model': {
                            'features': {
                                'source_symbol': 'NIY=F',
                                'instrument_key': 'cme_nikkei_futures',
                            },
                            'similar_summary': {'case_count': 24},
                        },
                    },
                },
            },
            score_breakdown={},
        )
        MarketBar.objects.create(
            symbol='NIY=F',
            timeframe='1d',
            timestamp=as_of + timedelta(days=1),
            open=42000,
            high=42900,
            low=41900,
            close=42750,
            source='test',
            instrument_key='cme_nikkei_futures',
        )

        outcome = evaluate_trade_outcome(snapshot, '1d')

        self.assertIsNotNone(outcome)
        self.assertEqual(ExplanationTradeOutcome.objects.count(), 1)
        self.assertEqual(outcome.selected_side, 'long')
        self.assertTrue(outcome.target_1_hit)
        self.assertFalse(outcome.stop_hit)
        self.assertTrue(outcome.direction_hit)
        self.assertEqual(outcome.expected_rr, 1.33)
        self.assertEqual(outcome.confidence_bucket, 'high')
        self.assertEqual(outcome.sample_count_at_decision, 24)

    def test_validation_summary_reads_static_outcomes_and_excludes_no_trade_from_hit_rate(self):
        static_rows = [
            {
                'explanation_as_of': timezone.now().isoformat(),
                'horizon': '1d',
                'evaluated_at': timezone.now().isoformat(),
                'selected_side': 'no_trade',
                'decision_type': 'no_trade_conflict',
                'trend_or_reversal': 'no_trade',
                'direction_hit': False,
                'target_1_hit': False,
                'stop_hit': False,
                'expected_rr': 1.1,
                'macro_regime': 'neutral',
                'technical_regime': 'range',
                'confidence_bucket': 'low',
            },
            {
                'explanation_as_of': (timezone.now() - timedelta(hours=1)).isoformat(),
                'horizon': '1d',
                'evaluated_at': timezone.now().isoformat(),
                'selected_side': 'long',
                'decision_type': 'trend_follow',
                'trend_or_reversal': 'trend',
                'direction_hit': True,
                'target_1_hit': True,
                'stop_hit': False,
                'expected_rr': 1.6,
                'macro_regime': 'positive',
                'technical_regime': 'bullish',
                'confidence_bucket': 'high',
            },
        ]

        with mock.patch('explanation.services.validation_engine.load_static_trade_outcomes', return_value=static_rows):
            summary = build_trade_validation_summary(include_static=True)

        self.assertTrue(summary['available'])
        self.assertEqual(summary['total_count'], 2)
        self.assertEqual(summary['actionable_count'], 1)
        self.assertEqual(summary['wait_count'], 1)
        self.assertIn('検証 2件 / 売買候補 1件 / 待機観測 1件', summary['one_line'])
        no_trade_row = next(row for row in summary['side_rows'] if row['label'] == 'no_trade')
        long_row = next(row for row in summary['side_rows'] if row['label'] == 'long')
        self.assertEqual(no_trade_row['direction_hit_rate'], 'N/A')
        self.assertEqual(long_row['direction_hit_rate'], '100%')

    def test_basecalc_backtest_validation_summary_uses_saved_report(self):
        report = {
            'schema': 'basecalc_validation_report_v1',
            'generated_at': '2026-06-25T13:25:11+00:00',
            'filters': {'is_backtest': True},
            'horizons': {
                '1d': {
                    'summary': {
                        'total_predictions': 4990,
                        'directional_accuracy': 0.39,
                        'target_t1_hit_rate': 0.25,
                        'avg_return_pct': 0.05,
                    },
                },
                '3d': {
                    'summary': {
                        'total_predictions': 4980,
                        'directional_accuracy': 0.36,
                        'target_t1_hit_rate': 0.47,
                        'avg_return_pct': 0.09,
                    },
                },
            },
        }

        with mock.patch('explanation.services.validation_engine.load_validation_report', return_value=report):
            summary = build_basecalc_backtest_validation_summary()

        self.assertTrue(summary['available'])
        self.assertEqual(summary['total_count'], 9970)
        self.assertEqual(summary['rows'][0]['horizon'], '1d')
        self.assertEqual(summary['rows'][0]['sample_count_display'], '4,990件')
        self.assertEqual(summary['rows'][0]['directional_accuracy_display'], '39%')
        self.assertIn('過去データ検証 9,970件', summary['one_line'])
        self.assertIn('1日 方向一致 39%', summary['one_line'])


class ExplanationSnapshotFactoryPersistenceTests(TestCase):
    def test_build_snapshot_serializes_date_values_before_saving_json_fields(self):
        from datetime import date

        from .services.contracts import BasecalcSignal, MacroSignal
        from .services.factory import build_explanation_snapshot

        macro = MacroSignal(
            bias='positive',
            summary='Macroは支援的。',
            confidence_score=78,
            confidence_grade='B',
            data_quality_score=82,
            source={'generated_on': date(2026, 6, 25)},
            as_of=timezone.now(),
        )
        basecalc = BasecalcSignal(
            bias='bullish',
            summary='日経先物は上昇優勢。',
            confidence_score=72,
            confidence_grade='B',
            data_quality_score=86,
            readiness_level='ready',
            can_show_prediction=True,
            source={'world_model': {'model_version': 'wm_test', 'session_date': date(2026, 6, 25)}},
            as_of=timezone.now(),
        )

        with (
            mock.patch('explanation.services.factory.load_macro_signal', return_value=macro),
            mock.patch('explanation.services.factory.load_basecalc_signal', return_value=basecalc),
        ):
            snapshot = build_explanation_snapshot(save=True)

        self.assertEqual(ExplanationSnapshot.objects.count(), 1)
        self.assertEqual(
            snapshot.source_snapshots['macro']['raw']['generated_on'],
            '2026-06-25',
        )


class ExplanationMacroAdapterTests(SimpleTestCase):
    def test_macro_signal_keeps_static_payload_generated_at(self):
        with mock.patch(
            'explanation.services.macro_adapter.build_house_view_context',
            return_value={
                'display_allowed': True,
                'confidence_score': 75,
                'confidence_grade': 'B',
                'house_view': 'Macro判断',
                'regime_label': 'expansion',
                'display_status': 'reference',
                'publish_status': 'reference',
            },
        ):
            with mock.patch(
                'explanation.services.macro_adapter.load_static_macro_payload',
                return_value={'generated_at': '2026-06-19T08:30:00+00:00'},
            ):
                signal = load_macro_signal()

        self.assertEqual(signal.source['generated_at'], '2026-06-19T08:30:00+00:00')
        self.assertEqual(signal.display_status, 'reference')
        self.assertEqual(signal.publish_status, 'reference')
        self.assertEqual(signal.as_of, timezone.datetime(2026, 6, 19, 8, 30, tzinfo=dt_timezone.utc))


class ExplanationPrecomputeViewTests(TestCase):
    def test_latest_or_preview_uses_static_snapshot_as_production_source(self):
        from explanation.views import _latest_or_preview

        ExplanationSnapshot.objects.create(
            as_of=timezone.now(),
            final_label='DB最新判定',
            final_stance='conditional_bullish',
            action_posture='DB表示',
            confidence_score=68,
            confidence_grade='B-',
            macro_bias='positive',
            basecalc_bias='bullish',
            alignment_status='aligned',
            data_quality_score=80,
            audit_level='valid',
            source_snapshots={},
            version='explanation_v2',
        )
        static_snapshot = ExplanationSnapshot(
            as_of=timezone.now() - timedelta(days=2),
            final_label='静的判定',
            final_stance='withhold',
            action_posture='静的表示',
            confidence_score=38,
            confidence_grade='D',
            macro_bias='neutral',
            basecalc_bias='range',
            alignment_status='partial',
            data_quality_score=40,
            audit_level='blocked',
            source_snapshots={},
            version='explanation_v2',
        )

        with (
            self.settings(DEBUG=False),
            mock.patch(
                'explanation.views.load_static_explanation_snapshot',
                return_value=static_snapshot,
            ),
            mock.patch('explanation.views.build_explanation_snapshot') as build_snapshot,
        ):
            snapshot, is_preview = _latest_or_preview()

        self.assertFalse(is_preview)
        self.assertEqual(snapshot.final_label, '静的判定')
        build_snapshot.assert_not_called()

    def test_latest_or_preview_does_not_rebuild_stale_static_snapshot_in_production(self):
        from explanation.views import _latest_or_preview

        static_snapshot = ExplanationSnapshot(
            as_of=timezone.now(),
            final_label='静的判定',
            final_stance='withhold',
            action_posture='静的表示',
            confidence_score=54,
            confidence_grade='C+',
            macro_bias='neutral',
            basecalc_bias='range',
            alignment_status='partial',
            data_quality_score=80,
            audit_level='warning',
            source_snapshots={},
            version='explanation_v2',
        )

        with (
            self.settings(DEBUG=False),
            mock.patch(
                'explanation.views.load_static_explanation_snapshot',
                return_value=static_snapshot,
            ),
            mock.patch(
                'explanation.views.build_explanation_refresh_status',
                return_value={'needs_refresh': True},
            ),
            mock.patch('explanation.views.build_explanation_snapshot') as build_snapshot,
        ):
            snapshot, is_preview = _latest_or_preview()

        self.assertFalse(is_preview)
        self.assertEqual(snapshot.final_label, '静的判定')
        build_snapshot.assert_not_called()

    def test_latest_or_preview_fails_closed_when_production_static_snapshot_is_missing(self):
        from explanation.views import _latest_or_preview

        with (
            self.settings(DEBUG=False),
            mock.patch(
                'explanation.views.load_static_explanation_snapshot',
                return_value=None,
            ),
            mock.patch('explanation.views.build_explanation_snapshot') as build_snapshot,
        ):
            with self.assertRaises(RuntimeError):
                _latest_or_preview()

        build_snapshot.assert_not_called()

    def test_latest_or_preview_ignores_manual_price_in_production_static_mode(self):
        from explanation.views import _latest_or_preview

        static_snapshot = ExplanationSnapshot(
            as_of=timezone.now(),
            final_label='静的判定',
            final_stance='withhold',
            action_posture='静的表示',
            confidence_score=54,
            confidence_grade='C+',
            macro_bias='neutral',
            basecalc_bias='range',
            alignment_status='partial',
            data_quality_score=80,
            audit_level='warning',
            source_snapshots={},
            version='explanation_v2',
        )

        with (
            self.settings(DEBUG=False),
            mock.patch(
                'explanation.views.load_static_explanation_snapshot',
                return_value=static_snapshot,
            ),
            mock.patch('explanation.views.build_explanation_snapshot') as build_snapshot,
        ):
            snapshot, is_preview = _latest_or_preview(price_override=42000)

        self.assertFalse(is_preview)
        self.assertEqual(snapshot.final_label, '静的判定')
        build_snapshot.assert_not_called()

    @mock.patch.dict('os.environ', {'VERCEL': '1'})
    def test_latest_or_preview_uses_static_snapshot_on_vercel_even_when_debug_is_true(self):
        from explanation.views import _latest_or_preview

        static_snapshot = ExplanationSnapshot(
            as_of=timezone.now(),
            final_label='静的判定',
            final_stance='withhold',
            action_posture='静的表示',
            confidence_score=54,
            confidence_grade='C+',
            macro_bias='neutral',
            basecalc_bias='range',
            alignment_status='partial',
            data_quality_score=80,
            audit_level='warning',
            source_snapshots={},
            version='explanation_v2',
        )

        with (
            self.settings(DEBUG=True),
            mock.patch(
                'explanation.views.load_static_explanation_snapshot',
                return_value=static_snapshot,
            ),
            mock.patch('explanation.views.build_explanation_snapshot') as build_snapshot,
        ):
            snapshot, is_preview = _latest_or_preview(price_override=42000)

        self.assertFalse(is_preview)
        self.assertEqual(snapshot.final_label, '静的判定')
        build_snapshot.assert_not_called()

    @mock.patch.dict('os.environ', {'VERCEL': '1'})
    def test_validation_summary_uses_static_outcomes_on_vercel_even_when_debug_is_true(self):
        from explanation.views import _safe_trade_validation_summary

        static_summary = {
            'available': True,
            'total_count': 8,
            'one_line': '検証 8件 / 売買候補 0件 / 待機観測 8件 / 機会損失候補 0件',
        }

        with (
            self.settings(DEBUG=True),
            mock.patch('explanation.views.build_static_trade_validation_summary', return_value=static_summary),
            mock.patch('explanation.views.build_trade_validation_summary') as db_summary,
        ):
            summary = _safe_trade_validation_summary()

        self.assertEqual(summary['total_count'], 8)
        db_summary.assert_not_called()

    def test_staff_user_can_precompute_explanation(self):
        user = get_user_model().objects.create_user(
            username='staff',
            password='password',
            is_staff=True,
        )
        self.client.force_login(user)

        with self.settings(DEBUG=False):
            with mock.patch('explanation.views.build_explanation_snapshot') as build_snapshot:
                build_snapshot.return_value = ExplanationSnapshot(
                    as_of=timezone.now(),
                    final_label='判定',
                    final_stance='neutral',
                    action_posture='様子見',
                    confidence_score=50,
                    confidence_grade='C',
                    macro_bias='neutral',
                    basecalc_bias='neutral',
                    alignment_status='mixed',
                    data_quality_score=50,
                    audit_level='valid',
                )
                response = self.client.post('/explanation/precompute/')

        build_snapshot.assert_called_once_with(save=True)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/explanation/')

    def test_anonymous_user_cannot_precompute_explanation(self):
        with self.settings(DEBUG=False):
            response = self.client.post('/explanation/precompute/')

        self.assertEqual(response.status_code, 403)


class ExplanationManualPriceViewTests(TestCase):
    def setUp(self):
        cache.clear()

    def _create_market_bars(self, length=80, start=40000, step=30):
        start_at = timezone.now() - timedelta(days=length)
        for index in range(length):
            close = start + index * step
            MarketBar.objects.create(
                symbol='NIY=F',
                timeframe='1d',
                timestamp=start_at + timedelta(days=index),
                open=close - 20,
                high=close + 80,
                low=close - 80,
                close=close,
                volume=1000,
                source='cme_daily_bulletin',
                instrument_key='cme_nikkei_futures',
                instrument_type='futures',
            )

    def _saved_basecalc_snapshot(self):
        return {
            'generated_at': timezone.now().isoformat(),
            'data': {'price_display': '66,670'},
            'world_model': {
                'direction': 'up',
                'direction_label': '上昇優勢',
                'price': 66670,
                'confidence': 'Middle',
                'confidence_score': 68,
                'data_quality_score': 96,
                'readiness_level': 'ready',
                'features': {
                    'price': 66670,
                    'close': 66670,
                    'source_symbol': 'NIY=F',
                    'source_name': '225navi',
                    'instrument_key': 'cme_nikkei_futures',
                    'instrument_type': 'futures',
                },
                'horizons': {
                    '1d': {'main_bias': 'up', 'setup_label': '上昇トレンド継続'},
                    '3d': {'main_bias': 'up', 'setup_label': '上昇トレンド継続'},
                    '5d': {'main_bias': 'up', 'setup_label': '上昇トレンド継続'},
                },
                'upside_targets': [{'label': 'T1', 'price': 71180, 'probability_display': '5%'}],
                'downside_targets': [{'label': 'T1', 'price': 67620, 'probability_display': '8%'}],
                'invalidation_price': 62350,
                'data_quality': {'level': 'good', 'score': 96, 'fallback_used': False},
            },
            'decision': {
                'direction': 'up',
                'direction_label': '上昇優勢',
                'confidence': 'Middle',
                'confidence_score': 68,
                'readiness_level': 'ready',
                'can_show_prediction': True,
                'upside_target': {'label': 'T1', 'price': 71180, 'probability_display': '5%'},
                'downside_target': {'label': 'T1', 'price': 67620, 'probability_display': '8%'},
            },
            'basecalc_status': {},
            'basecalc_status_rows': [],
            'market_shock': {},
            'intermarket_technicals': {
                'readiness': {'usable': True},
                'evidence': [],
            },
            'backtest_performance_by_horizon': {'1d': {'total_predictions': 80}},
        }

    def test_manual_price_rebuilds_preview_without_saving_explanation_snapshot(self):
        macro = MacroSignal(
            bias='positive',
            summary='Macroは支援的。',
            confidence_score=75,
            confidence_grade='B',
            data_quality_score=80,
        )
        base_snapshot = {
            'symbol': 'NIY=F',
            'source': 'cme_daily_bulletin',
            'price': 41000,
            'previous_close': 40900,
            'change_pct': 0.2,
            'fetched_at': timezone.now(),
            'fallback_used': False,
            'opens': [40000 + index * 10 for index in range(80)],
            'highs': [40080 + index * 10 for index in range(80)],
            'lows': [39920 + index * 10 for index in range(80)],
            'closes': [40000 + index * 10 for index in range(80)],
            'volumes': [1000 for _ in range(80)],
            'timestamps': [1700000000 + index * 86400 for index in range(80)],
        }

        with (
            self.settings(DEBUG=True),
            mock.patch('explanation.services.factory.load_macro_signal', return_value=macro),
            mock.patch(
                'explanation.services.basecalc_adapter.load_basecalc_snapshot',
                return_value={'generated_at': timezone.now().isoformat()},
            ),
            mock.patch(
                'explanation.services.basecalc_adapter.get_stale_futures_snapshot',
                return_value=base_snapshot,
                create=True,
            ),
            mock.patch(
                'explanation.services.basecalc_adapter.performance_summary',
                return_value={'total_predictions': 0},
                create=True,
            ),
        ):
            response = self.client.get('/explanation/', {'price': '42000'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ExplanationSnapshot.objects.count(), 0)
        self.assertIn('manual_price', response.context)
        self.assertTrue(response.context['manual_price']['active'])
        self.assertEqual(response.context['manual_price']['price'], 42000)
        self.assertEqual(response.context['manual_price']['price_display'], '42,000')
        raw = response.context['snapshot'].source_snapshots['basecalc']['raw']
        self.assertEqual(raw['world_model']['price'], 42000)

    def test_manual_price_uses_saved_basecalc_when_recalc_data_is_unavailable(self):
        macro = MacroSignal(
            bias='positive',
            summary='Macroは支援的。',
            confidence_score=75,
            confidence_grade='B',
            data_quality_score=80,
        )

        with (
            self.settings(DEBUG=True),
            mock.patch('explanation.services.factory.load_macro_signal', return_value=macro),
            mock.patch(
                'explanation.services.basecalc_adapter.load_basecalc_snapshot',
                return_value=self._saved_basecalc_snapshot(),
            ),
            mock.patch(
                'explanation.services.basecalc_adapter.get_stale_futures_snapshot',
                return_value=None,
                create=True,
            ),
        ):
            response = self.client.get('/explanation/', {'price': '42000'})

        self.assertEqual(response.status_code, 200)
        raw = response.context['snapshot'].source_snapshots['basecalc']['raw']
        self.assertEqual(raw['world_model']['price'], 66670)
        self.assertEqual(raw['world_model']['output_contract']['display_price'], 42000)
        self.assertEqual(raw['world_model']['contract_status'], 'error')
        self.assertEqual(raw['world_model']['confidence_score'], 59)
        self.assertEqual(raw['manual_price_mode']['basis'], 'saved_basecalc_with_manual_price_recalc_unavailable')
        self.assertEqual(response.context['long_judgment']['price'], 'N/A')
        self.assertEqual(response.context['snapshot'].final_label, '判定保留')

    def test_manual_price_recalculates_trade_decision_from_saved_market_bars(self):
        self._create_market_bars()
        saved_snapshot = self._saved_basecalc_snapshot()
        saved_snapshot['world_model']['features']['source_name'] = 'cme_daily_bulletin'
        macro = MacroSignal(
            bias='positive',
            summary='Macroは支援的。',
            confidence_score=90,
            confidence_grade='A',
            data_quality_score=90,
            factor_vector={
                'macro_long_filter': 2,
                'growth_score': 80,
                'fx_support_score': 80,
                'risk_appetite_score': 80,
            },
        )

        with (
            self.settings(DEBUG=True),
            mock.patch('explanation.services.factory.load_macro_signal', return_value=macro),
            mock.patch(
                'explanation.services.basecalc_adapter.load_basecalc_snapshot',
                return_value=saved_snapshot,
            ),
            mock.patch(
                'explanation.services.basecalc_adapter.get_stale_futures_snapshot',
                return_value=None,
                create=True,
            ),
        ):
            response = self.client.get('/explanation/', {'price': '42400'})

        self.assertEqual(response.status_code, 200)
        raw = response.context['snapshot'].source_snapshots['basecalc']['raw']
        trade_decision = response.context['snapshot'].trade_decision
        self.assertEqual(raw['manual_price_mode']['basis'], 'recalculated_basecalc_with_manual_price')
        self.assertEqual(raw['world_model']['price'], 42400)
        self.assertNotEqual(raw['world_model']['contract_status'], 'error')
        self.assertEqual(trade_decision['current_price'], 42400)
        if trade_decision['selected_side'] in {'long', 'short'}:
            self.assertIsNotNone(trade_decision['entry_price'])
            self.assertIsNotNone(trade_decision['target_1'])
            self.assertIsNotNone(trade_decision['stop_price'])
        else:
            self.assertIsNone(trade_decision['target_1'])
            self.assertIsNone(trade_decision['stop_price'])
