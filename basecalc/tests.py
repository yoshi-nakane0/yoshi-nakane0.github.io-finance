import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from .anchor_snapshot import load_anchor_snapshot
from .futures_sentiment import calculate_futures_sentiment


class BasecalcUpdateSecurityTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_get_update_true_does_not_fetch_external_data(self):
        with (
            patch('basecalc.views.get_nikkei_per_values') as per_values,
            patch('basecalc.views.get_jgb10y_yield_percent') as jgb_yield,
            patch('basecalc.views.get_nikkei_futures_snapshot') as futures_price,
        ):
            response = self.client.get(
                reverse('basecalc:index'),
                {'update': 'true'},
            )

        self.assertEqual(response.status_code, 200)
        per_values.assert_not_called()
        jgb_yield.assert_not_called()
        futures_price.assert_not_called()

    def test_anonymous_post_update_is_forbidden(self):
        response = self.client.post(
            reverse('basecalc:index'),
            {'action': 'update'},
        )

        self.assertEqual(response.status_code, 403)

    def test_refresh_button_is_hidden_for_anonymous_users(self):
        response = self.client.get(reverse('basecalc:index'))

        self.assertNotContains(response, 'id="price-refresh"')

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
            patch(
                'basecalc.views.get_nikkei_futures_snapshot',
                return_value={
                    'price': 41000,
                    'previous_close': 40000,
                    'change_pct': 2.5,
                    'closes': [39000, 39500, 40000, 41000],
                    'recent_high': 41000,
                    'recent_low': 39000,
                    'avg_abs_move_pct': 1.2,
                },
            ) as futures_price,
        ):
            response = self.client.post(
                reverse('basecalc:index'),
                {'action': 'update', 'price': '40000'},
            )

        self.assertEqual(response.status_code, 200)
        per_values.assert_called_once()
        jgb_yield.assert_called_once()
        futures_price.assert_called_once()
        self.assertEqual(cache.get('nikkei_forward_per'), 18.5)
        self.assertEqual(cache.get('nikkei_jgb10y_yield_percent'), 1.2)
        self.assertEqual(cache.get('nikkei_price'), 41000)


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
