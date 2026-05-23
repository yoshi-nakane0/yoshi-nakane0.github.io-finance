import json
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .anchor_snapshot import load_anchor_snapshot
from .futures_sentiment import calculate_futures_sentiment
from .indicators import calculate_atr, calculate_ema, calculate_macd, calculate_rsi
from . import market_shock
from .models import MarketSnapshot, PredictionOutcome, WorldModelPrediction
from .outcomes import evaluate_due_predictions, improvement_insights
from .targets import build_targets
from .world_model import build_world_model
from macro.models import Indicator, Observation


class BasecalcUpdateSecurityTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_get_update_true_does_not_fetch_external_data(self):
        with (
            patch('basecalc.views.get_nikkei_per_values') as per_values,
            patch('basecalc.views.get_jgb10y_yield_percent') as jgb_yield,
        ):
            response = self.client.get(
                reverse('basecalc:index'),
                {'update': 'true'},
            )

        self.assertEqual(response.status_code, 200)
        per_values.assert_not_called()
        jgb_yield.assert_not_called()

    def test_anonymous_post_update_is_forbidden(self):
        response = self.client.post(
            reverse('basecalc:index'),
            {'action': 'update'},
        )

        self.assertEqual(response.status_code, 403)

    def test_refresh_button_is_hidden_for_anonymous_users(self):
        response = self.client.get(reverse('basecalc:index'), {'price': '41000'})

        self.assertNotContains(response, 'id="price-refresh"')

    def test_get_without_price_uses_cached_manual_price_only(self):
        cache.set('nikkei_price', 41000, timeout=300)

        with (
            patch('basecalc.views.get_nikkei_per_values') as per_values,
            patch('basecalc.views.get_jgb10y_yield_percent') as jgb_yield,
        ):
            response = self.client.get(reverse('basecalc:index'))

        self.assertEqual(response.status_code, 200)
        per_values.assert_not_called()
        jgb_yield.assert_not_called()
        self.assertEqual(response.context['world_model']['price'], 41000)
        self.assertEqual(cache.get('nikkei_price'), 41000)

    def test_manual_price_input_is_kept_for_next_view(self):
        response = self.client.get(reverse('basecalc:index'), {'price': '42000'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(cache.get('nikkei_price'), 42000)
        self.assertContains(response, 'name="price" class="erp-input price-input" value="42000"')

        response = self.client.get(reverse('basecalc:index'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['world_model']['price'], 42000)
        self.assertContains(response, 'name="price" class="erp-input price-input" value="42000"')

    def test_staff_post_update_fetches_external_data(self):
        user = User.objects.create_user(
            username='basecalc-staff',
            password='test-password',
            is_staff=True,
        )
        self.client.force_login(user)

        with (
            patch(
                'basecalc.views.get_nikkei_per_values',
                return_value={
                    'index_based': 18.5,
                    'dividend_yield_index_based': 1.8,
                },
            ) as per_values,
            patch(
                'basecalc.views.get_jgb10y_yield_percent',
                return_value=1.2,
            ) as jgb_yield,
        ):
            response = self.client.post(
                reverse('basecalc:index'),
                {'action': 'update', 'price': '40000'},
            )

        self.assertEqual(response.status_code, 200)
        per_values.assert_called_once()
        jgb_yield.assert_called_once()
        self.assertEqual(cache.get('nikkei_forward_per'), 18.5)
        self.assertEqual(cache.get('nikkei_jgb10y_yield_percent'), 1.2)
        self.assertEqual(cache.get('nikkei_price'), 40000)


class BasecalcMarketShockTest(TestCase):
    """米国3指数の急変判定。"""

    def _create_price_action(self, series_id, value):
        indicator, _ = Indicator.objects.update_or_create(
            fred_series_id=series_id,
            defaults={
                'name_ja': series_id,
                'category': Indicator.Category.MARKET,
                'importance': Indicator.Importance.B,
                'frequency': Indicator.Frequency.DAILY,
                'source': Indicator.Source.YFINANCE_DAILY,
                'is_active': True,
            },
        )
        Observation.objects.filter(indicator=indicator).delete()
        Observation.objects.create(
            indicator=indicator,
            observation_date=date(2026, 5, 18),
            value=value,
        )

    def test_drop_with_credit_stress_is_continuation_biased(self):
        self._create_price_action('PA_GSPC_MOM20', -8.5)
        self._create_price_action('PA_GSPC_DD200', -4.0)
        self._create_price_action('PA_GSPC_DD52W', -14.0)
        alert = {
            'market_stress_score': 62,
            'category_summary': [
                {'category': 'volatility_sentiment', 'avg_score': 65},
                {'category': 'credit_liquidity', 'avg_score': 72},
            ],
        }

        result = market_shock.build_market_shock_context(
            alert=alert,
            as_of=date(2026, 5, 18),
        )

        gspc = next(row for row in result['rows'] if row['symbol'] == 'GSPC')
        self.assertEqual(gspc['direction'], 'drop')
        self.assertEqual(gspc['continuation_label'], '継続寄り')
        self.assertIn('S&P500', result['summary'])

    def test_surge_with_low_stress_is_continuation_biased(self):
        self._create_price_action('PA_IXIC_MOM20', 8.1)
        self._create_price_action('PA_IXIC_DD200', 6.0)
        self._create_price_action('PA_IXIC_DD52W', -2.0)
        alert = {
            'market_stress_score': 18,
            'category_summary': [
                {'category': 'volatility_sentiment', 'avg_score': 24},
                {'category': 'credit_liquidity', 'avg_score': 12},
            ],
        }

        result = market_shock.build_market_shock_context(
            alert=alert,
            as_of=date(2026, 5, 18),
        )

        nasdaq = next(row for row in result['rows'] if row['symbol'] == 'IXIC')
        self.assertEqual(nasdaq['direction'], 'surge')
        self.assertEqual(nasdaq['continuation_label'], '継続寄り')

    def test_basecalc_page_shows_us_index_shock_judgment(self):
        with patch('basecalc.views.build_market_shock_context') as shock_mock:
            shock_mock.return_value = {
                'has_data': True,
                'tone': 'negative',
                'summary': 'S&P500の急落は継続寄りです。',
                'rows': [{
                    'label': 'S&P500',
                    'headline': '急落 中 / 継続寄り',
                    'tone': 'negative',
                    'direction': 'drop',
                    'momentum_20d_display': '-8.5%',
                    'dd200_display': '-4.0%',
                    'dd52w_display': '-14.0%',
                    'continuation_score_display': '80%',
                    'reason': '下落にボラ・信用・トレンド悪化が重なっています。',
                }],
            }
            response = self.client.get(reverse('basecalc:index'), {'price': '41000'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '米国3指数の急変判定')
        self.assertContains(response, 'S&amp;P500の急落は継続寄りです。')
        self.assertContains(response, '急落 中 / 継続寄り')


class BasecalcAnchorSnapshotTests(TestCase):
    def test_anchor_values_override_top_level_latest_values(self):
        payload = {
            'source': 'test',
            'date': '2026.05.01',
            'index_based': 99.0,
            'dividend_yield': {
                'index_based': 9.9,
            },
            'basecalc_anchor': {
                'anchor_date': '2025.12',
                'anchor_price': 50339,
                'forward_per': 23.87,
                'jgb10y_yield_percent': 2.236,
                'dividend_yield_index_percent': 1.48,
                'erp_method': 'method_a',
                'growth_core_ratio': 0.6,
                'growth_wide_ratio': 0.7,
            },
        }
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'nikkei_per.json'
            path.write_text(json.dumps(payload), encoding='utf-8')

            snapshot = load_anchor_snapshot(path=path)

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot['forward_per'], 23.87)
        self.assertEqual(snapshot['dividend_yield_index_percent'], 1.48)
        self.assertEqual(snapshot['fair_price_mid'], 50339.0)

    def test_view_compares_current_price_against_anchor_fair_range(self):
        anchor_snapshot = {
            'anchor_date': '2025.12',
            'anchor_price': 50339,
            'forward_per': 23.87,
            'jgb10y_yield_percent': 2.236,
            'dividend_yield_index_percent': 1.48,
            'erp_method': 'method_a',
            'erp_growth_percent': None,
            'growth_core_ratio': 0.6,
            'growth_wide_ratio': 0.7,
        }

        with patch(
            'basecalc.views.load_anchor_snapshot',
            return_value=anchor_snapshot,
        ):
            response = self.client.get(
                reverse('basecalc:index'),
                {'price': '53000'},
            )

        self.assertEqual(response.status_code, 200)
        data = response.context['data']
        self.assertEqual(data['price'], 53000)
        self.assertEqual(data['fair_price_mid'], 50339.0)
        self.assertEqual(data['valuation_label'], 'Over +')
        self.assertEqual(data['fair_price_gap_pct_display'], '+5.29%')

    def test_view_marks_current_price_inside_anchor_range_as_fair(self):
        anchor_snapshot = {
            'anchor_date': '2025.12',
            'anchor_price': 50339,
            'forward_per': 23.87,
            'jgb10y_yield_percent': 2.236,
            'dividend_yield_index_percent': 1.48,
            'erp_method': 'method_a',
            'erp_growth_percent': None,
            'growth_core_ratio': 0.6,
            'growth_wide_ratio': 0.7,
        }

        with patch(
            'basecalc.views.load_anchor_snapshot',
            return_value=anchor_snapshot,
        ):
            response = self.client.get(
                reverse('basecalc:index'),
                {'price': '50339'},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['data']['valuation_label'], 'Fair')


class BasecalcFuturesSentimentTests(TestCase):
    def test_bullish_continuation_outputs_buyback_and_targets(self):
        result = calculate_futures_sentiment(
            53000,
            50339,
            49165,
            51570,
            48045,
            52863,
            market_snapshot={
                'previous_close': 52000,
                'change_pct': 1.92,
                'closes': [50000, 51000, 52000, 53000],
                'recent_high': 53000,
                'recent_low': 50000,
                'avg_abs_move_pct': 1.0,
            },
        )

        self.assertEqual(result['sentiment_label'], '上目線強め')
        self.assertEqual(result['continuity_label'], '継続しやすい')
        self.assertEqual(result['strategy_label'], '買い戻し優勢')
        self.assertGreater(result['upper_target'], 53000)

    def test_bearish_continuation_outputs_sell_rallies(self):
        result = calculate_futures_sentiment(
            47000,
            50339,
            49165,
            51570,
            48045,
            52863,
            market_snapshot={
                'previous_close': 48000,
                'change_pct': -2.08,
                'closes': [50000, 49000, 48000, 47000],
                'recent_high': 50000,
                'recent_low': 47000,
                'avg_abs_move_pct': 1.1,
            },
        )

        self.assertEqual(result['sentiment_label'], '下目線強め')
        self.assertEqual(result['continuity_label'], '継続しやすい')
        self.assertEqual(result['strategy_label'], '戻り売り優勢')
        self.assertLess(result['lower_target'], 47000)


class BasecalcTechnicalIndicatorTests(TestCase):
    def test_indicators_return_latest_values(self):
        closes = [100 + index for index in range(40)]
        highs = [close + 2 for close in closes]
        lows = [close - 2 for close in closes]

        self.assertGreater(calculate_ema(closes, 5)[-1], calculate_ema(closes, 20)[-1])
        self.assertGreater(calculate_rsi(closes, 14)[-1], 50)
        self.assertGreater(calculate_macd(closes)['histogram'][-1], -1)
        self.assertIsNotNone(calculate_atr(highs, lows, closes, 14)[-1])


class BasecalcWorldModelTests(TestCase):
    def test_world_model_outputs_required_dashboard_fields(self):
        closes = [40000 + index * 80 for index in range(80)]
        snapshot = {
            'symbol': 'NIY=F',
            'price': closes[-1],
            'previous_close': closes[-2],
            'change_pct': 0.2,
            'opens': [close - 30 for close in closes],
            'highs': [close + 120 for close in closes],
            'lows': [close - 140 for close in closes],
            'closes': closes,
            'volumes': [1000 + index for index in range(80)],
            'timestamps': [1700000000 + index * 86400 for index in range(80)],
        }

        result = build_world_model(closes[-1], snapshot)

        self.assertTrue(result['is_ready'])
        self.assertGreaterEqual(result['sentiment_score'], -100)
        self.assertLessEqual(result['sentiment_score'], 100)
        self.assertGreaterEqual(len(result['upside_targets']), 2)
        self.assertGreaterEqual(len(result['downside_targets']), 2)
        self.assertGreaterEqual(len(result['evidence']), 3)

    def test_world_model_uses_intraday_timeframes(self):
        daily_closes = [40000 + index * 30 for index in range(80)]
        intraday_closes = [42300 + index * 10 for index in range(48)]
        snapshot = {
            'symbol': 'NIY=F',
            'price': intraday_closes[-1],
            'previous_close': daily_closes[-2],
            'change_pct': 0.4,
            'opens': [close - 20 for close in daily_closes],
            'highs': [close + 100 for close in daily_closes],
            'lows': [close - 100 for close in daily_closes],
            'closes': daily_closes,
            'volumes': [1000 for _ in daily_closes],
            'timeframes': {
                '1d': {
                    'opens': [close - 20 for close in daily_closes],
                    'highs': [close + 100 for close in daily_closes],
                    'lows': [close - 100 for close in daily_closes],
                    'closes': daily_closes,
                    'volumes': [1000 for _ in daily_closes],
                },
                '15m': {
                    'opens': [close - 5 for close in intraday_closes],
                    'highs': [close + 20 for close in intraday_closes],
                    'lows': [close - 20 for close in intraday_closes],
                    'closes': intraday_closes,
                    'volumes': [100 for _ in intraday_closes],
                    'timestamps': [1700000000 + index * 900 for index in range(48)],
                },
            },
        }

        result = build_world_model(intraday_closes[-1], snapshot)

        self.assertTrue(result['timeframe_summary'])
        self.assertEqual(result['chart_points']['timeframe'], '15m')
        self.assertIsInstance(result['chart_points']['points'][0]['time'], int)

    def test_targets_are_split_above_and_below_price(self):
        targets = build_targets(
            {
                'price': 41000,
                'atr14': 300,
                'previous_high': 41200,
                'previous_low': 40700,
                'recent_high': 41400,
                'recent_low': 40500,
                'vwap': 40850,
                'ema20': 40900,
                'pivots': {'r1': 41300, 'r2': 41600, 's1': 40600, 's2': 40300},
            }
        )

        self.assertTrue(all(target['price'] > 41000 for target in targets['upside']))
        self.assertTrue(all(target['price'] < 41000 for target in targets['downside']))

    def test_snapshot_api_returns_world_model_json(self):
        response = self.client.get(reverse('basecalc:snapshot_api'), {'price': '41000'})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn('sentiment_score', payload)
        self.assertIn('targets', payload)

    def test_staff_refresh_saves_prediction(self):
        user = User.objects.create_user(
            username='basecalc-world-model-staff',
            password='test-password',
            is_staff=True,
        )
        self.client.force_login(user)
        closes = [40000 + index * 50 for index in range(80)]

        with (
            patch(
                'basecalc.views.get_nikkei_per_values',
                return_value={'index_based': 18.5, 'dividend_yield_index_based': 1.8},
            ),
            patch('basecalc.views.get_jgb10y_yield_percent', return_value=1.2),
        ):
            response = self.client.post(
                reverse('basecalc:index'),
                {'action': 'update', 'price': str(closes[-1])},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(WorldModelPrediction.objects.count(), 1)

    def test_staff_refresh_does_not_replace_manual_price_with_last_good_snapshot(self):
        user = User.objects.create_user(
            username='basecalc-fallback-staff',
            password='test-password',
            is_staff=True,
        )
        self.client.force_login(user)
        cache.set(
            'nikkei_futures_snapshot_last_good',
            {
                'price': 41200,
                'previous_close': 41000,
                'change_pct': 0.49,
                'opens': [41000, 41100],
                'highs': [41300, 41400],
                'lows': [40900, 41050],
                'closes': [41000, 41200],
                'volumes': [1000, 1000],
            },
            timeout=None,
        )

        with (
            patch(
                'basecalc.views.get_nikkei_per_values',
                return_value={'index_based': 18.5, 'dividend_yield_index_based': 1.8},
            ),
            patch('basecalc.views.get_jgb10y_yield_percent', return_value=1.2),
        ):
            response = self.client.post(
                reverse('basecalc:index'),
                {'action': 'update', 'price': '40000'},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['world_model']['price'], 40000)
        self.assertEqual(cache.get('nikkei_price'), 40000)

    def test_history_page_shows_prediction_rows(self):
        prediction = WorldModelPrediction.objects.create(
            price=41000,
            state_key='return_sell',
            state_label='戻り売り',
            direction='down',
            sentiment_score=-45,
            continuation_score=70,
            shock_score=20,
            confidence='Middle',
            main_scenario='test',
            invalidation_price=41400,
            upside_targets=[{'price': 41400}, {'price': 41800}],
            downside_targets=[{'price': 40600}, {'price': 40200}],
            evidence=[],
            features={'symbol': 'NIY=F'},
        )
        PredictionOutcome.objects.create(
            prediction=prediction,
            horizon='1d',
            evaluated_at=timezone.now(),
            price_at_evaluation=40500,
            realized_return_pct=-1.22,
            direction_hit=True,
            downside_t1_hit=True,
        )

        response = self.client.get(reverse('basecalc:history'), {'horizon': '1d'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '予測履歴')
        self.assertContains(response, '戻り売り')
        self.assertContains(response, '方向 一致')

    def test_improvement_insights_detect_weak_state(self):
        for index in range(5):
            prediction = WorldModelPrediction.objects.create(
                price=41000,
                state_key='return_sell',
                state_label='戻り売り',
                direction='down',
                sentiment_score=-45,
                continuation_score=70,
                shock_score=20,
                confidence='Middle',
                main_scenario='test',
                invalidation_price=41400,
                upside_targets=[{'price': 41400}, {'price': 41800}],
                downside_targets=[{'price': 40600}, {'price': 40200}],
                evidence=[],
                features={'symbol': 'NIY=F'},
            )
            PredictionOutcome.objects.create(
                prediction=prediction,
                horizon='1d',
                evaluated_at=timezone.now(),
                price_at_evaluation=41600 + index,
                realized_return_pct=1.46,
                direction_hit=False,
                invalidation_hit=True,
            )

        insights = improvement_insights('1d')

        self.assertTrue(any('方向判定' in item['title'] for item in insights))
        self.assertTrue(any('無効化ライン' in item['title'] for item in insights))

    def test_history_page_shows_improvement_insights(self):
        for index in range(5):
            prediction = WorldModelPrediction.objects.create(
                price=41000,
                state_key='dip_buy',
                state_label='押し目買い',
                direction='up',
                sentiment_score=42,
                continuation_score=62,
                shock_score=25,
                confidence='Middle',
                main_scenario='test',
                invalidation_price=40600,
                upside_targets=[{'price': 41400}, {'price': 41800}],
                downside_targets=[{'price': 40600}, {'price': 40200}],
                evidence=[],
                features={'symbol': 'NIY=F'},
            )
            PredictionOutcome.objects.create(
                prediction=prediction,
                horizon='1d',
                evaluated_at=timezone.now(),
                price_at_evaluation=40500 - index,
                realized_return_pct=-1.22,
                direction_hit=False,
                invalidation_hit=True,
            )

        response = self.client.get(reverse('basecalc:history'), {'horizon': '1d'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '改善候補')
        self.assertContains(response, '押し目買い')

    def test_outcome_evaluation_includes_3d_and_5d_horizons(self):
        created_at = timezone.now() - timezone.timedelta(days=6)
        prediction = WorldModelPrediction.objects.create(
            price=41000,
            state_key='bull_trend_continuation',
            state_label='上昇継続',
            direction='up',
            sentiment_score=55,
            continuation_score=70,
            shock_score=20,
            confidence='Middle',
            main_scenario='test',
            invalidation_price=40500,
            upside_targets=[{'price': 41400}, {'price': 41800}],
            downside_targets=[{'price': 40600}, {'price': 40200}],
            evidence=[],
            features={'symbol': 'NIY=F'},
        )
        WorldModelPrediction.objects.filter(id=prediction.id).update(created_at=created_at)
        prediction.refresh_from_db()
        MarketSnapshot.objects.create(
            symbol='NIY=F',
            price=41900,
            timeframe='1d',
            source='test',
        )

        evaluate_due_predictions(41600, now=timezone.now())

        horizons = set(
            PredictionOutcome.objects.filter(prediction=prediction).values_list(
                'horizon',
                flat=True,
            )
        )
        self.assertTrue({'1h', '4h', '1d', '3d', '5d'}.issubset(horizons))
