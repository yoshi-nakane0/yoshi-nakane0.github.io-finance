from datetime import timedelta, timezone as dt_timezone
from unittest import mock

from django.contrib.auth import get_user_model
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
from .services.validation_engine import evaluate_trade_outcome


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
        self.assertEqual(decision.entry_price, 42000)
        self.assertEqual(decision.target_1['price'], 42300)
        self.assertEqual(decision.stop_price, 41400)
        self.assertEqual(decision.invalidation_price, 41400)
        self.assertIn('R/R不足', decision.blocked_reasons)

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

        self.assertEqual(signal.confidence_score, 69)
        self.assertEqual(signal.confidence_grade, 'Middle')
        self.assertNotIn('局面別成績が弱いため信頼度を50未満に制限', signal.warnings)


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

    def test_view_context_adds_integrated_decision_summary(self):
        context = snapshot_to_view(self._snapshot())

        self.assertEqual(context['integrated_decision']['posture'], 'ロング候補')
        self.assertEqual(context['alignment_summary']['macro'], '追い風')
        self.assertEqual(context['alignment_summary']['basecalc'], '上方向')
        self.assertEqual(context['alignment_summary']['status'], '一致')
        self.assertEqual(context['adoption_summary']['primary'], 'ロング候補')
        self.assertLessEqual(len(context['adoption_summary']['reasons']), 3)
        self.assertLessEqual(len(context['adoption_summary']['warnings']), 3)
        self.assertIn('Long', context['adoption_summary']['long_condition'])
        self.assertIn('Short', context['adoption_summary']['short_condition'])

    def test_explanation_template_hides_detailed_source_sections_by_default(self):
        context = snapshot_to_view(self._snapshot())
        context['is_preview'] = False
        context['refresh_status'] = {'needs_refresh': False}
        context['can_precompute_explanation'] = False

        html = render_to_string('explanation/index.html', context)

        self.assertLess(html.index('最終統合判定'), html.index('統合判断の詳細'))
        self.assertIn('<summary class="common-section-title">統合判断の詳細</summary>', html)
        self.assertIn('<summary class="common-section-title">Macro / Basecalc 詳細</summary>', html)
        self.assertIn('<summary class="common-section-title">world model 予測数値</summary>', html)

    def test_decision_card_shows_reference_levels_when_no_trade_has_candidate_plan(self):
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
        self.assertEqual(context['decision_card']['entry'], '参考 42,336〜42,432円')
        self.assertEqual(context['decision_card']['target'], '42,550円')
        self.assertEqual(context['decision_card']['stop'], '42,290円')
        self.assertEqual(context['decision_card']['invalidation'], '42,290円')
        self.assertEqual(context['decision_card']['confidence'], '参考判定（B- / 68%）')

    def test_decision_card_marks_low_confidence_blocked_trade_as_reference(self):
        snapshot = self._snapshot()
        snapshot.trade_decision.update({
            'selected_side': 'no_trade',
            'decision_type': 'no_trade_conflict',
            'confidence_score': 49,
            'confidence_grade': 'C',
            'blocked_reasons': ['類似事例不足のため信頼度を50未満に制限'],
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

        decision_index = html.index('最終判定')
        long_index = html.index('ロング判断')
        short_index = html.index('ショート判断')
        world_index = html.index('world model 予測数値')
        final_index = html.index('最終判断')

        self.assertLess(decision_index, long_index)
        self.assertLess(long_index, short_index)
        self.assertLess(short_index, world_index)
        self.assertLess(world_index, final_index)

    def test_api_includes_trade_decision_selected_side(self):
        payload = snapshot_to_api(self._snapshot())

        self.assertEqual(payload['version'], 'explanation_v2')
        self.assertEqual(payload['trade_decision']['selected_side'], 'long')
        self.assertEqual(payload['trade_decision']['target_1']['price'], 71180)

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
        self.assertIn('Macroデータ更新時刻', html)
        self.assertIn('Basecalcデータ更新時刻', html)
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
            context['decision_inputs']['rows'],
            [
                {'label': 'Macroデータ更新時刻', 'value': '2026-06-19 17:30 JST'},
                {'label': 'Basecalcデータ更新時刻', 'value': '2026-06-19 18:40 JST'},
                {'label': '手入力価格', 'value': '42,000円'},
                {'label': '米国3指数', 'value': 'あり'},
            ],
        )
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
    def test_latest_or_preview_uses_current_preview_when_saved_snapshot_is_stale(self):
        from explanation.views import _latest_or_preview

        ExplanationSnapshot.objects.create(
            as_of=timezone.now() - timedelta(hours=2),
            final_label='古い判定',
            final_stance='conditional_bullish',
            action_posture='古い表示',
            confidence_score=49,
            confidence_grade='C',
            macro_bias='positive',
            basecalc_bias='bullish',
            alignment_status='partial',
            data_quality_score=70,
            audit_level='warning',
            source_snapshots={},
            version='explanation_v2',
        )
        fresh = ExplanationSnapshot(
            as_of=timezone.now(),
            final_label='最新判定',
            final_stance='withhold',
            action_posture='最新表示',
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
            mock.patch('explanation.views.build_explanation_refresh_status', return_value={'needs_refresh': True}),
            mock.patch('explanation.views.build_explanation_snapshot', return_value=fresh) as build_snapshot,
        ):
            snapshot, is_preview = _latest_or_preview()

        self.assertTrue(is_preview)
        self.assertEqual(snapshot.final_label, '最新判定')
        build_snapshot.assert_called_once_with(save=False)

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
                source='test',
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
            'source': '225navi',
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
        self.assertEqual(raw['world_model']['confidence_score'], 49)
        self.assertEqual(raw['manual_price_mode']['basis'], 'saved_basecalc_with_manual_price_recalc_unavailable')
        self.assertEqual(response.context['long_judgment']['price'], 'N/A')
        self.assertEqual(response.context['snapshot'].final_label, '判定保留')

    def test_manual_price_recalculates_trade_decision_from_saved_market_bars(self):
        self._create_market_bars()
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
            response = self.client.get('/explanation/', {'price': '42400'})

        self.assertEqual(response.status_code, 200)
        raw = response.context['snapshot'].source_snapshots['basecalc']['raw']
        trade_decision = response.context['snapshot'].trade_decision
        self.assertEqual(raw['manual_price_mode']['basis'], 'recalculated_basecalc_with_manual_price')
        self.assertEqual(raw['world_model']['price'], 42400)
        self.assertNotEqual(raw['world_model']['contract_status'], 'error')
        self.assertEqual(trade_decision['current_price'], 42400)
        self.assertIsNotNone(trade_decision['entry_price'])
        self.assertIsNotNone(trade_decision['target_1'])
        self.assertIsNotNone(trade_decision['stop_price'])
