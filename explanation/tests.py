from django.template.loader import render_to_string
from django.test import SimpleTestCase
from django.utils import timezone

from .models import ExplanationSnapshot
from .services.audit_engine import evaluate_audit
from .services.contracts import BasecalcSignal, MacroSignal
from .services.fusion_engine import build_final_decision
from .services.scenario_builder import build_scenarios
from .services.serializer import snapshot_to_view


class ExplanationDecisionEngineTests(SimpleTestCase):
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

        self.assertEqual(context['long_judgment']['label'], 'ロング判断')
        self.assertEqual(context['long_judgment']['price'], '71,180円')
        self.assertEqual(context['long_judgment']['probability'], '5%')
        self.assertEqual(context['short_judgment']['label'], 'ショート判断')
        self.assertEqual(context['short_judgment']['price'], '67,620円')
        self.assertEqual(context['short_judgment']['probability'], '8%')
        self.assertEqual(
            [item['horizon'] for item in context['world_model_predictions']],
            ['1d', '3d', '5d'],
        )
        self.assertEqual(context['world_model_predictions'][0]['expected_return'], '-0.02%')

    def test_template_renders_priority_sections_before_details(self):
        context = snapshot_to_view(self._snapshot())
        context['is_preview'] = False

        html = render_to_string('explanation/index.html', context)

        long_index = html.index('ロング判断')
        short_index = html.index('ショート判断')
        world_index = html.index('world model 予測数値')
        final_index = html.index('最終判断')

        self.assertLess(long_index, short_index)
        self.assertLess(short_index, world_index)
        self.assertLess(world_index, final_index)
