import json
from datetime import date, datetime
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .anchor_snapshot import load_anchor_snapshot
from .confidence import calculate_confidence_score
from .data_quality import evaluate_snapshot_quality
from .futures_sentiment import calculate_futures_sentiment
from .indicators import calculate_atr, calculate_ema, calculate_macd, calculate_rsi
from .intermarket_technicals import (
    US_INDEX_SYMBOLS,
    build_us_index_technical_context,
    evaluate_intermarket_readiness,
    get_intermarket_technical_snapshot,
)
from . import market_shock, nikkei_bias
from .baselines import baseline_comparison_summary
from .backtesting import run_basecalc_backtest
from .calibration import confidence_calibration_summary
from .validation import validation_design_summary
from .market_context import (
    calculate_context_score,
    fetch_intraday_context,
    get_market_context_snapshot,
    judge_nikkei_lead_context,
)
from .market_bars import prune_market_bars
from .models import MarketBar, MarketSnapshot, PredictionOutcome, WorldModelPrediction
from .outcomes import (
    apply_confidence_adjustment,
    apply_sentiment_score_adjustment,
    calibration_summary,
    confidence_adjustment_for_state,
    evaluate_due_predictions,
    improvement_insights,
    intermarket_comparison_summary,
    performance_summary,
    save_prediction,
    state_performance_summary,
)
from .persistence import export_basecalc_history, import_basecalc_history
from .readiness import evaluate_world_model_readiness
from .scoring import calculate_sentiment_score
from .similarity import find_similar_cases
from .status import intermarket_status_entry, status_display_rows
from .state_machine import STATE_DEFINITIONS, estimate_expected_returns, estimate_transition_probabilities
from .scenario_engine import build_scenarios
from .services.decision_context import can_show_prediction
from .targets import build_targets
from .views import (
    get_futures_snapshot_for_update,
    get_stale_futures_snapshot,
    get_jgb10y_yield_for_update,
    get_nikkei_per_values_for_update,
)
from .world_model import build_world_model
from macro.models import Indicator, Observation


def _ready_snapshot(length=80, symbol='NIY=F', source='yahoo', fetched_at=None, volume=1000):
    closes = [40000 + index * 25 for index in range(length)]
    return {
        'symbol': symbol,
        'source': source,
        'price': closes[-1] if closes else 40000,
        'previous_close': closes[-2] if len(closes) >= 2 else 39900,
        'change_pct': 0.2,
        'fetched_at': fetched_at or timezone.now(),
        'fallback_used': source == 'stooq',
        'opens': [close - 20 for close in closes],
        'highs': [close + 80 for close in closes],
        'lows': [close - 80 for close in closes],
        'closes': closes,
        'volumes': [volume for _ in closes],
        'timestamps': [1700000000 + index * 86400 for index in range(length)],
    }


def _create_market_bar_series(count=80, symbol='NIY=F', instrument_key='cme_nikkei_futures', start=None):
    start = start or (timezone.now() - timezone.timedelta(days=count + 10))
    rows = []
    for index in range(count):
        close = 40000 + index * 25
        rows.append(
            MarketBar.objects.create(
                symbol=symbol,
                timeframe='1d',
                timestamp=start + timezone.timedelta(days=index),
                open=close - 20,
                high=close + 80,
                low=close - 80,
                close=close,
                volume=1000,
                source='yahoo',
                instrument_key=instrument_key,
                instrument_type='futures' if instrument_key == 'cme_nikkei_futures' else 'index_fallback',
            )
        )
    return rows


class BasecalcUpdateSecurityTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_get_update_true_does_not_fetch_external_data(self):
        with (
            patch('basecalc.views.get_nikkei_per_values') as per_values,
            patch('basecalc.views.get_jgb10y_yield_percent') as jgb_yield,
            patch('basecalc.views.get_futures_snapshot_for_update') as futures_snapshot,
        ):
            response = self.client.get(
                reverse('basecalc:index'),
                {'update': 'true'},
            )

        self.assertEqual(response.status_code, 200)
        per_values.assert_not_called()
        jgb_yield.assert_not_called()
        futures_snapshot.assert_not_called()

    def test_anonymous_post_update_is_forbidden(self):
        response = self.client.post(
            reverse('basecalc:index'),
            {'action': 'update'},
        )

        self.assertEqual(response.status_code, 403)

    def test_refresh_button_is_hidden_for_anonymous_users(self):
        response = self.client.get(reverse('basecalc:index'))

        self.assertNotContains(response, 'id="price-refresh"')

    @override_settings(
        BASECALC_REFRESH_WORKFLOW_REPOSITORY='owner/repo',
        BASECALC_REFRESH_WORKFLOW_FILE='refresh-basecalc.yml',
        BASECALC_REFRESH_WORKFLOW_TOKEN='test-token',
    )
    def test_staff_can_dispatch_refresh_workflow(self):
        user = User.objects.create_user(
            username='basecalc-workflow-staff',
            password='test-password',
            is_staff=True,
        )
        self.client.force_login(user)

        with patch(
            'basecalc.github_actions.requests.post',
            return_value=mock.Mock(status_code=204, text='', json=mock.Mock(return_value={})),
        ) as post:
            response = self.client.post(reverse('basecalc:workflow_dispatch'))

        self.assertEqual(response.status_code, 302)
        post.assert_called_once()
        self.assertEqual(
            post.call_args.args[0],
            'https://api.github.com/repos/owner/repo/actions/workflows/refresh-basecalc.yml/dispatches',
        )
        self.assertEqual(
            post.call_args.kwargs['json'],
            {'ref': 'main'},
        )
        self.assertEqual(cache.get('basecalc_refresh_workflow_state')['status'], 'running')

    def test_anonymous_refresh_workflow_dispatch_is_forbidden(self):
        response = self.client.post(reverse('basecalc:workflow_dispatch'))

        self.assertEqual(response.status_code, 403)

    @override_settings(
        BASECALC_REFRESH_WORKFLOW_REPOSITORY='owner/repo',
        BASECALC_REFRESH_WORKFLOW_FILE='refresh-basecalc.yml',
        BASECALC_REFRESH_WORKFLOW_TOKEN='test-token',
    )
    def test_running_refresh_workflow_blocks_duplicate_dispatch(self):
        cache.set(
            'basecalc_refresh_workflow_state',
            {'status': 'running', 'message': '実行中', 'updated_at': '2026-06-19 10:00'},
            timeout=300,
        )
        user = User.objects.create_user(
            username='basecalc-workflow-running-staff',
            password='test-password',
            is_staff=True,
        )
        self.client.force_login(user)

        with patch('basecalc.github_actions.requests.post') as post:
            response = self.client.post(reverse('basecalc:workflow_dispatch'))

        self.assertEqual(response.status_code, 302)
        post.assert_not_called()

    @override_settings(
        BASECALC_REFRESH_WORKFLOW_REPOSITORY='owner/repo',
        BASECALC_REFRESH_WORKFLOW_FILE='refresh-basecalc.yml',
    )
    def test_staff_index_shows_workflow_status(self):
        user = User.objects.create_user(
            username='basecalc-workflow-status-staff',
            password='test-password',
            is_staff=True,
        )
        self.client.force_login(user)
        cache.set(
            'basecalc_refresh_workflow_state',
            {'status': 'running', 'message': 'GitHub Actions 実行中', 'updated_at': '2026-06-19 10:00'},
            timeout=300,
        )

        response = self.client.get(reverse('basecalc:index'))

        self.assertContains(response, 'GitHub Actions 実行中')
        self.assertContains(response, 'id="basecalc-workflow-dispatch"')
        self.assertContains(response, 'disabled')

    def test_refresh_workflow_run_state_maps_success_and_failure(self):
        from .github_actions import state_from_workflow_run

        success = state_from_workflow_run({
            'status': 'completed',
            'conclusion': 'success',
            'updated_at': '2026-06-19T10:00:00Z',
        })
        failure = state_from_workflow_run({
            'status': 'completed',
            'conclusion': 'failure',
            'updated_at': '2026-06-19T10:05:00Z',
        })

        self.assertEqual(success['status'], 'success')
        self.assertEqual(success['message'], 'GitHub Actions 成功')
        self.assertEqual(failure['status'], 'failure')
        self.assertEqual(failure['message'], 'GitHub Actions 失敗')

    def test_get_without_price_uses_snapshot_without_external_fetch(self):
        cache.set('nikkei_price', 41000, timeout=300)

        with (
            patch('basecalc.views.get_nikkei_per_values') as per_values,
            patch('basecalc.views.get_jgb10y_yield_percent') as jgb_yield,
            patch('basecalc.views.get_futures_snapshot_for_update') as futures_snapshot,
        ):
            response = self.client.get(reverse('basecalc:index'))

        self.assertEqual(response.status_code, 200)
        per_values.assert_not_called()
        jgb_yield.assert_not_called()
        futures_snapshot.assert_not_called()
        self.assertNotEqual(response.context['world_model']['price'], 41000)
        self.assertEqual(cache.get('nikkei_price'), 41000)

    def test_get_uses_latest_snapshot_without_rebuilding_context(self):
        snapshot = {
            'data': {'price_display': '41,000'},
            'world_model': {
                'direction': 'up',
                'price': 41000,
                'last_updated_display': '2026-06-17 09:00',
                'direction_label': '上目線',
                'state_label': '押し目買い優勢',
                'confidence': 'Middle',
                'confidence_score': 58,
                'data_quality': {
                    'level': 'good',
                    'score': 90,
                    'fallback_used': False,
                },
                'data_quality_score': 90,
                'readiness_level': 'ready',
                'readiness_display': {
                    'daily_bars': 80,
                    'valid_major_indicators': 6,
                },
                'readiness': {'reason_codes': [], 'warnings': []},
                'similar_summary': {'is_statistically_valid': False},
                'target_ranges': [],
                'market_context': {},
            },
            'market_shock': {'has_data': False},
            'market_context': {},
            'basecalc_status': {},
            'basecalc_status_rows': [],
            'performance': {},
            'performance_by_horizon': {},
            'backtest_performance_by_horizon': {},
            'updated': False,
            'erp_method': 'fixed',
            'erp_growth_input': '',
            'price_param': '41000',
            'growth_core_ratio_input': '0.6',
            'growth_wide_ratio_input': '0.7',
        }

        from django.http import HttpResponse

        with patch('basecalc.views.load_basecalc_snapshot', return_value=snapshot), \
             patch('basecalc.views.build_context') as build_context, \
             patch('basecalc.views.render', return_value=HttpResponse('ok')) as render_mock:
            response = self.client.get(reverse('basecalc:index'))

        self.assertEqual(response.status_code, 200)
        rendered_context = render_mock.call_args.args[2]
        self.assertEqual(rendered_context['world_model']['price'], 41000)
        self.assertEqual(rendered_context['decision']['price'], 41000)
        self.assertEqual(rendered_context['decision']['direction_label'], '上目線')
        build_context.assert_not_called()

    def test_get_updates_saved_snapshot_current_price_from_newer_market_snapshot(self):
        snapshot = {
            'data': {'price_display': '41,000', 'world_model': {'price': 41000}},
            'world_model': {
                'direction': 'up',
                'price': 41000,
                'last_updated_display': '2026-06-17 09:00 JST',
                'direction_label': '上目線',
                'state_label': '押し目買い優勢',
                'confidence': 'Middle',
                'confidence_score': 58,
                'data_quality': {
                    'level': 'good',
                    'score': 90,
                    'fallback_used': False,
                },
                'data_quality_score': 90,
                'readiness_level': 'ready',
                'readiness_display': {
                    'daily_bars': 80,
                    'valid_major_indicators': 6,
                },
                'readiness': {'reason_codes': [], 'warnings': []},
                'similar_summary': {'is_statistically_valid': False},
                'target_ranges': [],
                'market_context': {},
            },
            'market_shock': {'has_data': True},
            'basecalc_status': {
                'price_data': {
                    'last_success_at': '2026-06-17T00:00:00+00:00',
                    'source': '225navi:NIY=F',
                }
            },
            'basecalc_status_rows': [],
            'performance': {},
            'performance_by_horizon': {},
            'backtest_performance_by_horizon': {},
            'updated': False,
            'price_param': '41000',
        }
        MarketSnapshot.objects.create(
            symbol='NIY=F',
            timeframe='1d',
            fetched_at=timezone.make_aware(datetime(2026, 6, 18, 0, 0)),
            price=42500,
            open=42000,
            high=42600,
            low=41900,
            close=42500,
            source='225navi',
            instrument_key='cme_nikkei_futures',
            instrument_type='futures',
            readiness_level='ready',
        )

        from django.http import HttpResponse

        with patch('basecalc.views.load_basecalc_snapshot', return_value=snapshot), \
             patch('basecalc.views.build_context') as build_context, \
             patch('basecalc.views.render', return_value=HttpResponse('ok')) as render_mock:
            response = self.client.get(reverse('basecalc:index'))

        self.assertEqual(response.status_code, 200)
        rendered_context = render_mock.call_args.args[2]
        self.assertEqual(rendered_context['world_model']['price'], 42500)
        self.assertEqual(rendered_context['data']['price_display'], '42,500')
        self.assertEqual(rendered_context['data']['world_model']['price'], 42500)
        self.assertEqual(rendered_context['decision']['price'], 42500)
        self.assertEqual(rendered_context['price_param'], '42500')
        build_context.assert_not_called()

    def test_get_updates_saved_snapshot_current_price_when_saved_timestamp_is_newer_but_price_differs(self):
        snapshot = {
            'data': {'price_display': '66,670', 'world_model': {'price': 66670}},
            'world_model': {
                'direction': 'up',
                'price': 66670,
                'last_updated_display': '2026-06-19 11:39 JST',
                'direction_label': '上目線',
                'state_label': '押し目買い優勢',
                'confidence': 'Middle',
                'confidence_score': 58,
                'data_quality': {
                    'level': 'good',
                    'score': 90,
                    'fallback_used': False,
                },
                'data_quality_score': 90,
                'readiness_level': 'ready',
                'readiness_display': {
                    'daily_bars': 80,
                    'valid_major_indicators': 6,
                },
                'readiness': {'reason_codes': [], 'warnings': []},
                'similar_summary': {'is_statistically_valid': False},
                'target_ranges': [],
                'market_context': {},
            },
            'market_shock': {'has_data': True},
            'basecalc_status': {
                'price_data': {
                    'last_success_at': '2026-06-19T02:39:39+00:00',
                    'source': '225navi:NIY=F',
                }
            },
            'basecalc_status_rows': [],
            'performance': {},
            'performance_by_horizon': {},
            'backtest_performance_by_horizon': {},
            'updated': False,
            'price_param': '66670',
            'generated_at': '2026-06-19T02:39:39+00:00',
        }
        MarketSnapshot.objects.create(
            symbol='NIY=F',
            timeframe='1d',
            fetched_at=timezone.make_aware(datetime(2026, 6, 18, 0, 0)),
            price=71240,
            open=70590,
            high=71530,
            low=70330,
            close=71240,
            source='225navi',
            instrument_key='cme_nikkei_futures',
            instrument_type='futures',
            readiness_level='ready',
        )

        from django.http import HttpResponse

        with patch('basecalc.views.load_basecalc_snapshot', return_value=snapshot), \
             patch('basecalc.views.render', return_value=HttpResponse('ok')) as render_mock:
            response = self.client.get(reverse('basecalc:index'))

        self.assertEqual(response.status_code, 200)
        rendered_context = render_mock.call_args.args[2]
        self.assertEqual(rendered_context['world_model']['price'], 71240)
        self.assertEqual(rendered_context['data']['price_display'], '71,240')
        self.assertEqual(rendered_context['decision']['price'], 71240)
        self.assertEqual(rendered_context['price_param'], '71240')

    def test_get_keeps_latest_daily_bar_when_stale_snapshot_has_newer_fetch_time(self):
        snapshot = {
            'data': {'price_display': '71,240', 'world_model': {'price': 71240}},
            'world_model': {
                'direction': 'up',
                'price': 71240,
                'last_updated_display': '2026-06-19 11:50 JST',
                'direction_label': '上目線',
                'state_label': '押し目買い優勢',
                'confidence': 'Low',
                'confidence_score': 44,
                'data_quality': {
                    'level': 'good',
                    'score': 96,
                    'fallback_used': False,
                },
                'data_quality_score': 96,
                'readiness_level': 'ready',
                'readiness_display': {
                    'daily_bars': 3342,
                    'valid_major_indicators': 6,
                },
                'readiness': {'reason_codes': [], 'warnings': []},
                'similar_summary': {'is_statistically_valid': False},
                'target_ranges': [],
                'market_context': {},
            },
            'market_shock': {'has_data': True},
            'basecalc_status': {
                'price_data': {
                    'last_success_at': '2026-06-19T02:50:37+00:00',
                    'source': '225navi:NIY=F',
                }
            },
            'basecalc_status_rows': [],
            'performance': {},
            'performance_by_horizon': {},
            'backtest_performance_by_horizon': {},
            'updated': False,
            'price_param': '71240',
            'generated_at': '2026-06-19T02:50:37+00:00',
        }
        MarketBar.objects.create(
            symbol='NIY=F',
            timeframe='1d',
            timestamp=timezone.make_aware(datetime(2026, 6, 18)),
            open=70590,
            high=71530,
            low=70330,
            close=71240,
            source='225navi',
            instrument_key='cme_nikkei_futures',
            instrument_type='futures',
        )
        MarketSnapshot.objects.create(
            symbol='NIY=F',
            timeframe='1d',
            fetched_at=timezone.make_aware(datetime(2026, 6, 19, 2, 6)),
            price=69400,
            open=68300,
            high=69840,
            low=68200,
            close=69400,
            source='225navi',
            instrument_key='cme_nikkei_futures',
            instrument_type='futures',
            readiness_level='ready',
        )

        from django.http import HttpResponse

        with patch('basecalc.views.load_basecalc_snapshot', return_value=snapshot), \
             patch('basecalc.views.render', return_value=HttpResponse('ok')) as render_mock:
            response = self.client.get(reverse('basecalc:index'))

        self.assertEqual(response.status_code, 200)
        rendered_context = render_mock.call_args.args[2]
        self.assertEqual(rendered_context['world_model']['price'], 71240)
        self.assertEqual(rendered_context['data']['price_display'], '71,240')
        self.assertEqual(rendered_context['decision']['price'], 71240)
        self.assertEqual(rendered_context['price_param'], '71240')

    def test_basecalc_top_stops_prediction_when_gate_is_not_met(self):
        snapshot = {
            'data': {'price_display': '41,000'},
            'world_model': {
                'direction': 'up',
                'price': 41000,
                'last_updated_display': '2026-06-17 09:00',
                'direction_label': '上目線',
                'state_label': '押し目買い優勢',
                'confidence': 'Low',
                'confidence_score': 30,
                'data_quality': {
                    'level': 'good',
                    'score': 78,
                    'fallback_used': True,
                },
                'data_quality_score': 78,
                'readiness_level': 'ready',
                'readiness_display': {
                    'daily_bars': 80,
                    'valid_major_indicators': 6,
                },
                'readiness': {'reason_codes': [], 'warnings': []},
                'similar_summary': {
                    'case_count': 8,
                    'is_statistically_valid': False,
                },
                'target_ranges': [{'label': '1日', 'low': 40500, 'high': 41500, 'basis': 'ATR'}],
                'upside_targets': [{'label': 'T1', 'price': 41800, 'reason': '前日高値'}],
                'downside_targets': [{'label': 'T1', 'price': 40400, 'reason': '前日安値'}],
                'invalidation_display': '40,200',
                'market_context': {'risk_label': 'neutral', 'risk_score': 0, 'components': {}},
                'evidence': ['EMA20を上回る', '20日勢いが強い'],
                'expected_return_1d': 0.4,
                'expected_return_5d': 1.2,
                'expected_return_label': '過去類似',
            },
            'market_shock': {'has_data': False},
            'market_context': {},
            'basecalc_status': {},
            'basecalc_status_rows': [],
            'performance': {},
            'performance_by_horizon': {},
            'backtest_performance_by_horizon': {},
            'updated': False,
            'erp_method': 'fixed',
            'erp_growth_input': '',
            'price_param': '41000',
            'growth_core_ratio_input': '0.6',
            'growth_wide_ratio_input': '0.7',
        }

        with patch('basecalc.views.load_basecalc_snapshot', return_value=snapshot):
            response = self.client.get(reverse('basecalc:index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '予測表示停止')
        top_html = response.content.decode('utf-8').split('data-field="model_detail"')[0]
        self.assertNotIn('期待 1d', top_html)

    def test_index_summarizes_targets_in_plain_japanese(self):
        snapshot = {
            'data': {'price_display': '41,000'},
            'world_model': {
                'direction': 'up',
                'price': 41000,
                'last_updated_display': '2026-06-17 09:00',
                'direction_label': '上目線',
                'state_label': '押し目買い優勢',
                'confidence': 'Middle',
                'confidence_score': 58,
                'data_quality': {
                    'level': 'good',
                    'score': 90,
                    'fallback_used': False,
                },
                'data_quality_score': 90,
                'readiness_level': 'ready',
                'readiness_display': {
                    'daily_bars': 80,
                    'valid_major_indicators': 6,
                },
                'readiness': {'reason_codes': [], 'warnings': []},
                'similar_summary': {
                    'case_count': 60,
                    'is_statistically_valid': True,
                },
                'target_ranges': [{'label': '1日', 'low': 40500, 'high': 41500, 'basis': 'ATR'}],
                'upside_targets': [
                    {
                        'label': 'T1',
                        'price': 41800,
                        'reason': '前日高値',
                        'probability_display': '62%',
                        'distance_pct': 1.95,
                        'sample_count': 42,
                        'reliability': 'medium',
                    },
                    {
                        'label': 'T2',
                        'price': 42200,
                        'reason': '節目突破',
                        'probability_display': '48%',
                        'distance_pct': 2.93,
                        'sample_count': 42,
                        'reliability': 'low',
                    },
                ],
                'downside_targets': [
                    {
                        'label': 'T1',
                        'price': 40400,
                        'reason': '前日安値',
                        'probability_display': '45%',
                        'distance_pct': -1.46,
                        'sample_count': 42,
                        'reliability': 'medium',
                    }
                ],
                'near_levels': {
                    'upside': [{'price': 41200, 'reason': '近い節目', 'distance_pct': 0.49}],
                    'downside': [{'price': 40800, 'reason': '近い節目', 'distance_pct': -0.49}],
                },
                'invalidation_display': '40,200',
                'market_context': {'risk_label': 'neutral', 'risk_score': 0, 'components': {}},
                'evidence': ['EMA20を上回る', '20日勢いが強い'],
                'expected_return_1d': 0.4,
                'expected_return_5d': 1.2,
                'expected_return_label': '過去類似',
            },
            'market_shock': {'has_data': False},
            'market_context': {},
            'basecalc_status': {},
            'basecalc_status_rows': [],
            'performance': {},
            'performance_by_horizon': {},
            'backtest_performance_by_horizon': {},
            'updated': False,
            'erp_method': 'fixed',
            'erp_growth_input': '',
            'price_param': '41000',
            'growth_core_ratio_input': '0.6',
            'growth_wide_ratio_input': '0.7',
        }

        with patch('basecalc.views.load_basecalc_snapshot', return_value=snapshot):
            response = self.client.get(reverse('basecalc:index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '上値 第1候補')
        self.assertContains(response, '下値 第1候補')
        self.assertContains(response, '判定を弱める価格')
        self.assertContains(response, '詳細ターゲット')
        self.assertContains(response, '第2候補')
        self.assertContains(response, '到達しやすさ')
        self.assertContains(response, '根拠:')
        self.assertNotContains(response, 'ターゲット全件')
        self.assertNotContains(response, 'targetではなく節目')

    def test_validation_page_reads_saved_report_without_live_aggregation(self):
        report = {
            'schema': 'basecalc_validation_report_v1',
            'generated_at': '2026-06-18T08:00:00+09:00',
            'filters': {
                'instrument_key': 'cme_nikkei_futures',
                'readiness_level': 'ready',
                'is_backtest': True,
            },
            'horizons': {
                '1d': {
                    'summary': {
                        'total_predictions': 120,
                        'directional_accuracy': 0.56,
                        'target_t1_hit_rate': 0.44,
                        'invalidation_rate': 0.08,
                        'avg_return_pct': 0.21,
                        'avg_confidence_score': 62.4,
                    },
                    'validation_design': {
                        'walk_forward': [],
                        'period_splits': [],
                        'recent_window': {
                            'label': '直近60日',
                            'sample_count': 32,
                            'directional_accuracy': 0.59,
                            'avg_return_pct': 0.3,
                            'target_t1_hit_rate': 0.47,
                            'sample_quality': 'reliable',
                        },
                        'volatility_regimes': [],
                        'market_regimes': [],
                    },
                    'calibration_rows': [],
                    'confidence_calibration_rows': [],
                    'state_summaries': [],
                    'improvement_insights': [],
                }
            },
        }

        with (
            patch('basecalc.views.load_validation_report', return_value=report),
            patch('basecalc.validation_report.performance_summary') as performance,
            patch('basecalc.validation_report.calibration_summary') as calibration,
            patch('basecalc.validation_report.confidence_calibration_summary') as confidence,
            patch('basecalc.validation_report.validation_design_summary') as validation_design,
        ):
            response = self.client.get(reverse('basecalc:validation'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '検証レポート')
        self.assertContains(response, '保存済み結果')
        self.assertContains(response, '120')
        self.assertContains(response, '0.56')
        performance.assert_not_called()
        calibration.assert_not_called()
        confidence.assert_not_called()
        validation_design.assert_not_called()

    def test_manual_price_get_recalculates_world_model_without_mutating_price_cache(self):
        cached_snapshot = _ready_snapshot(source='225navi')

        with (
            patch('basecalc.views.get_cached_futures_snapshot', return_value=cached_snapshot),
            patch('basecalc.views.get_cached_intermarket_technical_context', return_value={}),
            patch('basecalc.views.load_basecalc_snapshot') as saved_snapshot,
        ):
            response = self.client.get(reverse('basecalc:index'), {'price': '42000'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['world_model']['price'], 42000)
        self.assertEqual(response.context['world_model']['features']['price'], 42000)
        self.assertEqual(response.context['world_model']['features']['close'], 42000)
        self.assertEqual(response.context['decision']['price'], 42000)
        self.assertEqual(response.context['data']['price_display'], '42,000')
        self.assertEqual(response.context['price_param'], '42000')
        self.assertIsNone(cache.get('nikkei_price'))
        self.assertNotEqual(cached_snapshot['closes'][-1], 42000)
        saved_snapshot.assert_not_called()

    def test_manual_price_get_adds_separate_status_row(self):
        cached_snapshot = _ready_snapshot(source='225navi')

        with (
            patch('basecalc.views.get_cached_futures_snapshot', return_value=cached_snapshot),
            patch('basecalc.views.get_cached_intermarket_technical_context', return_value={}),
        ):
            response = self.client.get(reverse('basecalc:index'), {'price': '71800'})

        self.assertEqual(response.status_code, 200)
        override = response.context['manual_price_override']
        self.assertTrue(override['active'])
        self.assertEqual(override['price'], 71800)
        self.assertEqual(override['price_display'], '71,800')
        rows = response.context['basecalc_status_rows']
        manual_row = next(row for row in rows if row['key'] == 'manual_price')
        price_row = next(row for row in rows if row['key'] == 'price_data')

        self.assertEqual(manual_row['label'], '手入力価格')
        self.assertEqual(manual_row['age_display'], '適用中')
        self.assertEqual(manual_row['source'], '71,800')
        self.assertEqual(manual_row['decision_label'], '一時判定')
        self.assertEqual(price_row['label'], '価格データ')
        self.assertIn('225navi', price_row['source'])
        self.assertNotEqual(price_row['source'], manual_row['source'])
        self.assertContains(response, '手入力価格を判定に使用中')

    def test_index_shows_backtest_performance_separately_from_live_performance(self):
        def fake_performance_summary(horizon='1d', *args, **kwargs):
            is_backtest = kwargs.get('is_backtest', False)
            return {
                'total_predictions': 600 if is_backtest else 1,
                'directional_accuracy': 0.53 if is_backtest else 0.0,
                'target_t1_hit_rate': 0.87 if is_backtest else 1.0,
                'target_t2_hit_rate': 0.72 if is_backtest else 1.0,
                'invalidation_rate': 0.07 if is_backtest else 1.0,
                'avg_return_pct': 0.36 if is_backtest else -3.52,
                'median_return_pct': 0.31 if is_backtest else -3.52,
                'avg_mfe_pct': 1.86 if is_backtest else 0,
                'avg_mae_pct': -2.26 if is_backtest else -6.48,
                'avg_confidence_score': 54.7,
                'median_mae_pct': -1.36 if is_backtest else -6.48,
                'median_mfe_pct': 1.42 if is_backtest else 0,
                'sample_quality': 'reliable' if is_backtest else 'insufficient',
                'statistical_warning': '' if is_backtest else 'サンプル数が不足しています',
            }

        from .snapshot import load_basecalc_snapshot

        payload = load_basecalc_snapshot()
        payload['performance_by_horizon'] = {
            horizon: fake_performance_summary(horizon)
            for horizon in ("1d", "3d", "5d")
        }
        payload['backtest_performance_by_horizon'] = {
            horizon: fake_performance_summary(horizon, is_backtest=True)
            for horizon in ("1d", "3d", "5d")
        }
        with patch('basecalc.views.load_basecalc_snapshot', return_value=payload):
            response = self.client.get(reverse('basecalc:index'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['performance_by_horizon']['1d']['total_predictions'], 1)
        self.assertEqual(response.context['backtest_performance_by_horizon']['1d']['total_predictions'], 600)
        self.assertContains(response, '過去検証')
        self.assertContains(response, '検証 600')

    def test_staff_post_update_does_not_fetch_valuation_data(self):
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
            patch('basecalc.views.get_stale_futures_snapshot', return_value=None) as futures_snapshot,
            patch('basecalc.views.get_intermarket_technical_snapshot', return_value={}),
        ):
            response = self.client.post(
                reverse('basecalc:index'),
                {'action': 'update', 'price': '40000'},
            )

        self.assertEqual(response.status_code, 200)
        per_values.assert_not_called()
        jgb_yield.assert_not_called()
        self.assertGreaterEqual(futures_snapshot.call_count, 1)
        self.assertIsNone(cache.get('nikkei_forward_per'))
        self.assertIsNone(cache.get('nikkei_jgb10y_yield_percent'))
        self.assertEqual(cache.get('nikkei_price'), 40000)

    def test_staff_post_update_caches_saved_futures_snapshot(self):
        user = User.objects.create_user(
            username='basecalc-futures-staff',
            password='test-password',
            is_staff=True,
        )
        self.client.force_login(user)
        latest_ts = int(timezone.now().timestamp())
        daily_timestamps = [
            latest_ts - 172800,
            latest_ts - 86400,
            latest_ts,
        ]
        snapshot = {
            'symbol': 'NIY=F',
            'source': '225navi',
            'price': 41100,
            'previous_close': 40900,
            'change_pct': 0.49,
            'timeframes': {
                '1d': {
                    'symbol': 'NIY=F',
                    'source': '225navi',
                    'opens': [40500, 40700, 40900],
                    'highs': [40800, 41000, 41200],
                    'lows': [40400, 40600, 40800],
                    'closes': [40700, 40900, 41100],
                    'volumes': [100, 110, 120],
                    'timestamps': daily_timestamps,
                },
            },
        }

        with (
            patch(
                'basecalc.views.get_nikkei_per_values',
                return_value={'index_based': 18.5, 'dividend_yield_index_based': 1.8},
            ),
            patch('basecalc.views.get_jgb10y_yield_percent', return_value=1.2),
            patch('basecalc.views.get_stale_futures_snapshot', return_value=snapshot),
            patch('basecalc.views.get_intermarket_technical_snapshot', return_value={}),
        ):
            response = self.client.post(
                reverse('basecalc:index'),
                {'action': 'update'},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['world_model']['price'], 41100)
        self.assertEqual(cache.get('nikkei_price'), 41100)
        self.assertEqual(cache.get('nikkei_futures_snapshot')['price'], 41100)
        self.assertEqual(cache.get('nikkei_futures_snapshot_last_good')['price'], 41100)
        self.assertEqual(MarketBar.objects.count(), 0)

    def test_sync_nikkei_futures_daily_uses_225navi_and_creates_ready_snapshot(self):
        _create_market_bar_series(
            count=80,
            start=timezone.make_aware(datetime(2026, 3, 1)),
        )
        MarketBar.objects.create(
            symbol='NIY=F',
            timeframe='1d',
            timestamp=timezone.make_aware(datetime(2026, 6, 4)),
            open=67650,
            high=67910,
            low=66950,
            close=67640,
            volume=None,
            source='csv',
            instrument_key='cme_nikkei_futures',
            instrument_type='futures',
        )
        rows = [
            {
                'date': date(2026, 6, 5),
                'open': 67350,
                'high': 67410,
                'low': 65890,
                'close': 66670,
                'volume': None,
                'source': '225navi',
            },
        ]

        with (
            patch('basecalc.daily_sync.fetch_225navi_daily_bars', return_value=rows) as navi_fetch,
            patch('basecalc.daily_sync.write_basecalc_status'),
        ):
            call_command('sync_nikkei_futures_daily')

        navi_fetch.assert_called_once()
        latest_bar = MarketBar.objects.order_by('-timestamp').first()
        self.assertEqual(latest_bar.close, 66670)
        self.assertEqual(latest_bar.source, '225navi')
        latest_snapshot = MarketSnapshot.objects.order_by('-created_at').first()
        self.assertEqual(latest_snapshot.price, 66670)
        self.assertEqual(latest_snapshot.source, '225navi')
        self.assertEqual(latest_snapshot.readiness_level, 'ready')
        cache.clear()

    def test_sync_does_not_create_snapshot_from_old_csv_when_no_source_returns_rows(self):
        MarketBar.objects.create(
            symbol='NIY=F',
            timeframe='1d',
            timestamp=timezone.make_aware(datetime(2026, 5, 22)),
            open=62427.5,
            high=63802.5,
            low=62347.5,
            close=63295,
            volume=None,
            source='csv',
            instrument_key='cme_nikkei_futures',
            instrument_type='futures',
        )

        with (
            patch('basecalc.daily_sync.fetch_225navi_daily_bars', return_value=[]),
            patch('basecalc.daily_sync.write_basecalc_status') as status_write,
        ):
            from basecalc.daily_sync import sync_nikkei_futures_daily

            result = sync_nikkei_futures_daily()

        self.assertEqual(result['sync_status'], 'failed')
        self.assertEqual(result['source'], '')
        self.assertEqual(result['rows_fetched'], 0)
        self.assertFalse(result['snapshot_created'])
        self.assertEqual(MarketSnapshot.objects.count(), 0)
        status_entry = status_write.call_args.args[0]['price_data']
        self.assertIsNone(status_entry['last_success_at'])
        self.assertIsNotNone(status_entry['last_failed_at'])
        self.assertEqual(status_entry['decision_level'], 'blocked')
        cache.clear()

    def test_sync_upgrades_existing_csv_bar_when_225navi_fetches_same_date(self):
        _create_market_bar_series(
            count=80,
            start=timezone.make_aware(datetime(2026, 3, 1)),
        )
        MarketBar.objects.create(
            symbol='NIY=F',
            timeframe='1d',
            timestamp=timezone.make_aware(datetime(2026, 6, 3)),
            open=66800,
            high=67200,
            low=66400,
            close=67100,
            volume=None,
            source='csv',
            instrument_key='cme_nikkei_futures',
            instrument_type='futures',
        )
        MarketBar.objects.create(
            symbol='NIY=F',
            timeframe='1d',
            timestamp=timezone.make_aware(datetime(2026, 6, 4)),
            open=62427.5,
            high=63802.5,
            low=62347.5,
            close=63295,
            volume=None,
            source='csv',
            instrument_key='cme_nikkei_futures',
            instrument_type='futures',
        )
        rows = [
            {
                'date': date(2026, 6, 4),
                'open': 67650,
                'high': 67910,
                'low': 66950,
                'close': 67640,
                'volume': None,
                'source': '225navi',
            },
        ]

        with (
            patch('basecalc.daily_sync.fetch_225navi_daily_bars', return_value=rows),
            patch('basecalc.daily_sync.write_basecalc_status'),
        ):
            call_command('sync_nikkei_futures_daily')

        upgraded_bar = MarketBar.objects.get(
            symbol='NIY=F',
            timeframe='1d',
            timestamp=timezone.make_aware(datetime(2026, 6, 4)),
        )
        self.assertEqual(upgraded_bar.source, '225navi')
        self.assertEqual(upgraded_bar.close, 67640)
        latest_snapshot = MarketSnapshot.objects.order_by('-created_at').first()
        self.assertEqual(latest_snapshot.source, '225navi')
        self.assertEqual(latest_snapshot.price, 67640)
        self.assertEqual(latest_snapshot.readiness_level, 'ready')
        cache.clear()

    def test_sync_command_logs_source_attempts_and_snapshot_source(self):
        rows = [
            {
                'date': date(2026, 6, 4),
                'open': 67650,
                'high': 67910,
                'low': 66950,
                'close': 67640,
                'volume': None,
                'source': '225navi',
            },
        ]
        output = StringIO()

        with (
            patch('basecalc.daily_sync.fetch_225navi_daily_bars', return_value=rows),
            patch('basecalc.daily_sync.write_basecalc_status'),
        ):
            call_command('sync_nikkei_futures_daily', stdout=output)

        text = output.getvalue()
        self.assertIn('status=fallback', text)
        self.assertIn('attempts=225navi:fetched=1', text)
        self.assertIn('snapshot_source=225navi', text)
        cache.clear()

    def test_sync_command_exports_latest_snapshot_json(self):
        _create_market_bar_series(
            count=80,
            start=timezone.make_aware(datetime(2026, 3, 1)),
        )
        rows = [
            {
                'date': date(2026, 6, 5),
                'open': 67350,
                'high': 67410,
                'low': 65890,
                'close': 66670,
                'volume': None,
                'source': '225navi',
            },
        ]

        with TemporaryDirectory() as tmpdir:
            snapshot_path = Path(tmpdir) / 'latest_snapshot.json'
            with (
                patch('basecalc.daily_sync.fetch_225navi_daily_bars', return_value=rows),
                patch('basecalc.daily_sync.write_basecalc_status'),
            ):
                call_command(
                    'sync_nikkei_futures_daily',
                    '--export-snapshot-path',
                    str(snapshot_path),
                    stdout=StringIO(),
                )

            payload = json.loads(snapshot_path.read_text(encoding='utf-8'))

        self.assertEqual(payload['source'], 'github_actions')
        self.assertEqual(payload['world_model']['price'], 66670)
        self.assertEqual(payload['data']['price_display'], '66,670')
        self.assertIn('generated_at', payload)
        cache.clear()

    def test_225navi_parser_reads_day_session_ohlc_rows(self):
        from basecalc.daily_sync import parse_225navi_daily_text
        text = """
        大証 日経225先物期近 日足 4本値
        日付
        日中
        夜間
        始値
        高値
        安値
        終値
        始値
        高値
        安値
        終値
        2026/6/5
        67350
        67410
        65890
        66670
        66680
        67170
        63500
        63820
        2026/6/4
        67650
        67910
        66950
        67640
        67740
        67890
        66930
        67610
        """

        rows = parse_225navi_daily_text(text)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['date'], date(2026, 6, 5))
        self.assertEqual(rows[0]['open'], 67350)
        self.assertEqual(rows[0]['high'], 67410)
        self.assertEqual(rows[0]['low'], 65890)
        self.assertEqual(rows[0]['close'], 66670)
        self.assertEqual(rows[0]['source'], '225navi')

    def test_225navi_fetch_records_http_failure_diagnostics(self):
        from requests import RequestException

        from basecalc.daily_sync import fetch_225navi_daily_bars

        class BlockedResponse:
            status_code = 403
            headers = {'content-type': 'text/html'}
            text = 'blocked'

            def raise_for_status(self):
                raise RequestException('403 Client Error')

        diagnostics = {}

        with patch('basecalc.daily_sync.requests.get', return_value=BlockedResponse()):
            rows = fetch_225navi_daily_bars(
                end=date(2026, 6, 7),
                diagnostics=diagnostics,
            )

        self.assertEqual(rows, [])
        self.assertIn('history:http=403', diagnostics['details'])

    def test_sync_uses_225navi_snapshot_even_when_newer_old_source_bar_exists(self):
        _create_market_bar_series(
            count=80,
            start=timezone.make_aware(datetime(2026, 3, 1)),
        )
        MarketBar.objects.create(
            symbol='NIY=F',
            timeframe='1d',
            timestamp=timezone.make_aware(datetime(2026, 6, 3)),
            open=66800,
            high=67200,
            low=66400,
            close=67100,
            volume=None,
            source='csv',
            instrument_key='cme_nikkei_futures',
            instrument_type='futures',
        )
        MarketBar.objects.create(
            symbol='NIY=F',
            timeframe='1d',
            timestamp=timezone.make_aware(datetime(2026, 6, 5)),
            open=67795,
            high=67865,
            low=63775,
            close=64245,
            volume=510000,
            source='investing.com',
            instrument_key='cme_nikkei_futures',
            instrument_type='futures',
        )
        rows = [
            {
                'date': date(2026, 6, 4),
                'open': 67650,
                'high': 67910,
                'low': 66950,
                'close': 67640,
                'volume': None,
                'source': '225navi',
            },
        ]

        with (
            patch('basecalc.daily_sync.fetch_225navi_daily_bars', return_value=rows),
            patch('basecalc.daily_sync.write_basecalc_status'),
        ):
            call_command('sync_nikkei_futures_daily', update_existing=True)

        latest_snapshot = MarketSnapshot.objects.order_by('-created_at').first()
        self.assertEqual(latest_snapshot.source, '225navi')
        self.assertEqual(latest_snapshot.fetched_at.date(), date(2026, 6, 4))
        self.assertEqual(latest_snapshot.price, 67640)
        cache.clear()

    def test_import_history_upgrades_existing_market_bar_to_225navi(self):
        timestamp = timezone.make_aware(datetime(2026, 6, 5))
        MarketBar.objects.create(
            symbol='NIY=F',
            timeframe='1d',
            timestamp=timestamp,
            open=67795,
            high=67865,
            low=63775,
            close=64245,
            volume=510000,
            source='investing.com',
            instrument_key='cme_nikkei_futures',
            instrument_type='futures',
        )
        payload = {
            'schema': 'basecalc_history_v2',
            'market_bars': [{
                'symbol': 'NIY=F',
                'timeframe': '1d',
                'timestamp': timestamp.isoformat(),
                'open': 67350,
                'high': 67410,
                'low': 65890,
                'close': 66670,
                'volume': None,
                'source': '225navi',
                'instrument_key': 'cme_nikkei_futures',
                'instrument_type': 'futures',
            }],
        }
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'basecalc_history.json'
            path.write_text(json.dumps(payload), encoding='utf-8')
            result = import_basecalc_history(str(path))

        bar = MarketBar.objects.get(symbol='NIY=F', timeframe='1d', timestamp=timestamp)
        self.assertEqual(result['market_bars_created'], 0)
        self.assertEqual(result['market_bars_updated'], 1)
        self.assertEqual(bar.source, '225navi')
        self.assertEqual(bar.close, 66670)

    def test_stale_futures_snapshot_prefers_latest_225navi_snapshot(self):
        older = timezone.make_aware(datetime(2026, 6, 5))
        MarketSnapshot.objects.create(
            symbol='NIY=F',
            timeframe='1d',
            fetched_at=older,
            created_at=older,
            price=64245,
            close=64245,
            source='investing.com',
            instrument_key='cme_nikkei_futures',
            instrument_type='futures',
            readiness_level='blocked',
        )
        navi_created = timezone.make_aware(datetime(2026, 6, 7, 13, 28))
        MarketSnapshot.objects.create(
            symbol='NIY=F',
            timeframe='1d',
            fetched_at=older,
            created_at=navi_created,
            price=66670,
            open=67350,
            high=67410,
            low=65890,
            close=66670,
            source='225navi',
            instrument_key='cme_nikkei_futures',
            instrument_type='futures',
            readiness_level='limited',
        )
        MarketBar.objects.create(
            symbol='NIY=F',
            timeframe='1d',
            timestamp=older,
            open=67350,
            high=67410,
            low=65890,
            close=66670,
            source='225navi',
            instrument_key='cme_nikkei_futures',
            instrument_type='futures',
        )

        snapshot = get_stale_futures_snapshot()

        self.assertEqual(snapshot['source'], '225navi')
        self.assertEqual(snapshot['price'], 66670)
        self.assertEqual(snapshot['closes'][-1], 66670)

    def test_refresh_basecalc_data_uses_saved_225navi_without_live_futures_fetch(self):
        from .operations import refresh_basecalc_data

        _create_market_bar_series(
            count=80,
            start=timezone.now() - timezone.timedelta(days=90),
        )
        latest_date = timezone.localdate()
        MarketBar.objects.filter(
            symbol='NIY=F',
            timestamp__date__gte=latest_date - timezone.timedelta(days=4),
        ).delete()
        rows = [
            (latest_date - timezone.timedelta(days=4), 66250, 67240, 66240, 67080),
            (latest_date - timezone.timedelta(days=3), 67070, 67220, 65580, 66750),
            (latest_date - timezone.timedelta(days=2), 67220, 68800, 67190, 68560),
            (latest_date - timezone.timedelta(days=1), 67650, 67910, 66950, 67640),
            (latest_date, 67350, 67410, 65890, 66670),
        ]
        for row_date, open_price, high, low, close in rows:
            MarketBar.objects.create(
                symbol='NIY=F',
                timeframe='1d',
                timestamp=timezone.make_aware(datetime.combine(row_date, datetime.min.time())),
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=None,
                source='225navi',
                instrument_key='cme_nikkei_futures',
                instrument_type='futures',
            )
        MarketSnapshot.objects.create(
            symbol='NIY=F',
            timeframe='1d',
            fetched_at=timezone.now(),
            price=66670,
            open=67350,
            high=67410,
            low=65890,
            close=66670,
            source='225navi',
            instrument_key='cme_nikkei_futures',
            instrument_type='futures',
            readiness_level='limited',
        )

        with (
            patch('basecalc.operations.get_intermarket_technical_snapshot', return_value={}),
            patch('basecalc.operations.write_basecalc_status'),
        ):
            result = refresh_basecalc_data(save=False, use_lock=False)

        self.assertTrue(result['updated'])
        self.assertEqual(result['price'], 66670)
        self.assertEqual(result['readiness_level'], 'ready')
        self.assertNotEqual(result['state_key'], 'limited_reference')
        self.assertEqual(result['source_status']['source'], '225navi')

    def test_fresh_futures_cache_skips_external_refetch(self):
        cached = {
            'symbol': 'NIY=F',
            'source': 'yahoo',
            'price': 41100,
            'fetched_at': timezone.now(),
            'timeframes': {
                '1d': {
                    'closes': [41000, 41100],
                    'timestamps': [1710086400, 1710172800],
                },
            },
        }
        cache.set('nikkei_futures_snapshot', cached, timeout=300)

        with patch('basecalc.views.get_stale_futures_snapshot') as futures_snapshot:
            result = get_futures_snapshot_for_update()

        futures_snapshot.assert_not_called()
        self.assertEqual(result['price'], 41100)

    def test_fresh_per_cache_skips_external_refetch(self):
        cache.set('nikkei_forward_per', 18.5, timeout=None)
        cache.set('nikkei_dividend_yield_index', 1.8, timeout=None)
        cache.set('nikkei_per_fetched_at', timezone.now(), timeout=None)

        with patch('basecalc.views.get_nikkei_per_values') as per_values:
            result = get_nikkei_per_values_for_update()

        per_values.assert_not_called()
        self.assertEqual(result['index_based'], 18.5)
        self.assertEqual(result['dividend_yield_index_based'], 1.8)

    def test_intermarket_status_uses_us_index_confirmation_readiness(self):
        status = intermarket_status_entry(
            {
                'components': {
                    'nasdaq100_futures': {'score': 30},
                    'sp500_futures': {'score': 25},
                    'dow_futures': {'score': 10},
                },
                'readiness': {'level': 'ready'},
                'fetched_at': timezone.now(),
            }
        )

        self.assertEqual(status['source'], 'NQ=F / ES=F / YM=F')
        self.assertEqual(status['asset_count'], 3)
        self.assertEqual(status['decision_level'], 'ready')

    def test_nikkei_per_values_prefers_newer_local_payload_over_old_remote(self):
        remote = {
            'index_based': 23.87,
            'dividend_yield_index_based': 1.48,
            'date': '2026.03.11',
            'source': 'remote-old',
        }
        local = {
            'index_based': 23.93,
            'dividend_yield_index_based': 1.36,
            'date': '2026.06.12',
            'source': 'local-new',
        }

        with (
            patch.object(nikkei_bias, '_load_nikkei_per_data_url', return_value=remote),
            patch.object(nikkei_bias, '_load_nikkei_per_data_file', return_value=local),
        ):
            result = nikkei_bias.get_nikkei_per_values()

        self.assertEqual(result['date'], '2026.06.12')
        self.assertEqual(result['index_based'], 23.93)
        self.assertEqual(result['source'], 'local-new')

    def test_fresh_jgb_cache_skips_external_refetch(self):
        cache.set('nikkei_jgb10y_yield_percent', 1.2, timeout=3600)
        cache.set('nikkei_jgb10y_fetched_at', timezone.now(), timeout=None)

        with patch('basecalc.views.get_jgb10y_yield_percent') as jgb_yield:
            result = get_jgb10y_yield_for_update()

        jgb_yield.assert_not_called()
        self.assertEqual(result, 1.2)

    def test_market_bar_pruning_keeps_storage_bounded(self):
        old_time = timezone.now() - timezone.timedelta(days=365 * 16)
        recent_time = timezone.now()
        for index in range(3):
            MarketBar.objects.create(
                symbol='NIY=F',
                timeframe='1d',
                timestamp=old_time + timezone.timedelta(days=index),
                close=40000 + index,
                source='test',
            )
        MarketBar.objects.create(
            symbol='NIY=F',
            timeframe='1d',
            timestamp=recent_time,
            close=41000,
            source='test',
        )

        deleted = prune_market_bars({'NIY=F'})

        self.assertEqual(deleted, 3)
        self.assertEqual(MarketBar.objects.count(), 1)

    def test_refresh_management_command_uses_periodic_operation(self):
        out = StringIO()
        with patch(
            'basecalc.management.commands.refresh_basecalc_data.refresh_basecalc_data',
            return_value={
                'updated': True,
                'price': 41100,
                'state_key': 'dip_buy',
                'direction': 'up',
                'prediction_saved': True,
                'outcomes_created': 2,
            },
        ) as refresh:
            call_command('refresh_basecalc_data', stdout=out)

        refresh.assert_called_once_with(
            save=True,
            use_lock=True,
            export_history=False,
            export_path='basecalc/data/basecalc_history.json',
            export_snapshot_path='basecalc/data/latest_snapshot.json',
        )
        self.assertIn('basecalc refresh complete', out.getvalue())

    def test_refresh_management_command_passes_export_options(self):
        out = StringIO()
        with patch(
            'basecalc.management.commands.refresh_basecalc_data.refresh_basecalc_data',
            return_value={
                'updated': True,
                'price': 41100,
                'state_key': 'dip_buy',
                'direction': 'up',
                'prediction_saved': True,
                'outcomes_created': 2,
                'exported': True,
            },
        ) as refresh:
            call_command(
                'refresh_basecalc_data',
                '--export-history',
                '--export-path',
                'basecalc/data/test_history.json',
                stdout=out,
            )

        refresh.assert_called_once_with(
            save=True,
            use_lock=True,
            export_history=True,
            export_path='basecalc/data/test_history.json',
            export_snapshot_path='basecalc/data/latest_snapshot.json',
        )

    def test_refresh_basecalc_data_exports_latest_snapshot(self):
        from .operations import refresh_basecalc_data

        with TemporaryDirectory() as tmpdir:
            snapshot_path = Path(tmpdir) / 'latest_snapshot.json'
            with (
                patch('basecalc.operations.get_cached_futures_snapshot', return_value=_ready_snapshot()),
                patch('basecalc.operations.get_intermarket_technical_snapshot', return_value={}),
                patch('basecalc.operations.write_basecalc_status'),
                patch('basecalc.operations.save_prediction', return_value=None),
                patch('basecalc.operations.evaluate_due_predictions', return_value=0),
                patch('basecalc.operations.prune_prediction_history', return_value=0),
            ):
                result = refresh_basecalc_data(
                    save=False,
                    use_lock=False,
                    export_snapshot_path=str(snapshot_path),
                )

            payload = json.loads(snapshot_path.read_text(encoding='utf-8'))

        self.assertTrue(result['snapshot_exported'])
        self.assertEqual(payload['source'], 'github_actions')
        self.assertIn('generated_at', payload)
        self.assertIn('job_duration_sec', payload)
        self.assertIn('world_model', payload)
        self.assertIn('data', payload)
        self.assertIn('performance_by_horizon', payload)


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
        from .snapshot import load_basecalc_snapshot

        payload = load_basecalc_snapshot()
        payload['market_shock'] = {
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
        with patch('basecalc.views.load_basecalc_snapshot', return_value=payload):
            response = self.client.get(reverse('basecalc:index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '市場ストレス・急落予測')
        self.assertContains(response, 'S&amp;P500の急落は継続寄りです。')
        self.assertContains(response, '急落 中 / 継続寄り')

    def test_snapshot_view_hydrates_missing_stress_and_us_index_cards(self):
        from .snapshot import load_basecalc_snapshot

        self._create_price_action('PA_GSPC_MOM20', -7.0)
        self._create_price_action('PA_DJI_MOM20', -5.0)
        self._create_price_action('PA_IXIC_MOM20', -8.0)
        payload = load_basecalc_snapshot()
        payload['market_shock'] = {'has_data': False}
        world_model = payload['world_model']
        world_model['us_index_confirmation'] = {
            'confirmation_score': 0,
            'confirmation_label': 'mixed',
            'components': {},
            'evidence': ['米国3指数データなし'],
            'readiness': {'level': 'blocked', 'reason': '米国3指数データなし'},
        }
        world_model['intermarket_technicals'] = world_model['us_index_confirmation']
        world_model['features'] = {
            **(world_model.get('features') or {}),
            'symbol': 'NIY=F',
            'change_1d_pct': -3.8,
        }
        payload['backtest_performance_by_horizon']['1d']['baseline_comparison'] = {
            'sample_count': 300,
            'rows': [{'key': 'model', 'label': '現行モデル', 'sample_count': 300}],
            'best_baseline': {'key': 'model'},
        }

        with patch('basecalc.views.load_basecalc_snapshot', return_value=payload):
            response = self.client.get(reverse('basecalc:index'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '米国3指数データなし')
        self.assertContains(response, '下落確認')
        self.assertContains(response, '日経先物')
        self.assertNotContains(response, 'ベースライン比較未整備')

    def test_build_context_uses_basecalc_market_shock_context(self):
        request = mock.Mock(
            GET={},
            POST={},
            user=mock.Mock(is_authenticated=False, is_staff=False),
        )
        shock_context = {
            'has_data': True,
            'tone': 'negative',
            'summary': 'S&P500の急落は継続寄りです。',
            'rows': [],
        }

        with (
            patch('basecalc.views.get_cached_futures_snapshot', return_value=_ready_snapshot()),
            patch('basecalc.views.get_cached_intermarket_technical_context', return_value={}),
            patch('basecalc.views.build_market_shock_context', return_value=shock_context),
        ):
            from .views import build_context

            context = build_context(request, force_update=False)

        self.assertEqual(context['market_shock']['summary'], shock_context['summary'])
        self.assertIn(shock_context['summary'], context['decision']['market_stress']['reasons'])

    def test_market_shock_context_includes_basecalc_assets(self):
        result = market_shock.build_market_shock_context(
            alert={'market_stress_score': 30, 'category_summary': []},
            as_of=date(2026, 5, 18),
            base_snapshot={'change_pct': -4.2, 'price': 66670},
            intermarket_context={
                'components': {
                    'nasdaq100_futures': {'change_pct': -2.1, 'score': -35},
                }
            },
        )

        labels = {row['label'] for row in result['rows']}
        self.assertIn('日経先物', labels)
        self.assertIn('NASDAQ', labels)
        nasdaq = next(row for row in result['rows'] if row['label'] == 'NASDAQ')
        self.assertEqual(nasdaq['futures']['label'], 'NASDAQ100先物')
        self.assertTrue(result['has_data'])
        self.assertIn('急落', result['summary'])

    def test_market_shock_merges_us_index_and_futures_rows(self):
        self._create_price_action('PA_GSPC_MOM20', -7.0)
        self._create_price_action('PA_DJI_MOM20', -4.0)
        self._create_price_action('PA_IXIC_MOM20', -8.0)

        result = market_shock.build_market_shock_context(
            alert={'market_stress_score': 30, 'category_summary': []},
            as_of=date(2026, 5, 18),
            intermarket_context={
                'components': {
                    'sp500_futures': {'change_pct': -1.8, 'score': -30},
                    'dow_futures': {'change_pct': -1.2, 'score': -20},
                    'nasdaq100_futures': {'change_pct': -2.4, 'score': -45},
                },
            },
        )

        labels = [row['label'] for row in result['rows']]
        self.assertEqual(labels.count('S&P500'), 1)
        self.assertEqual(labels.count('NYダウ'), 1)
        self.assertEqual(labels.count('NASDAQ'), 1)
        self.assertNotIn('S&P500先物', labels)
        self.assertNotIn('NYダウ先物', labels)
        self.assertNotIn('NASDAQ100先物', labels)
        sp500 = next(row for row in result['rows'] if row['label'] == 'S&P500')
        self.assertEqual(sp500['futures']['label'], 'S&P500先物')


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

    def test_view_keeps_basecalc_data_technical_without_anchor_valuation(self):
        from .views import build_context

        request = mock.Mock(
            GET={'price': '53000'},
            POST={},
            user=mock.Mock(is_authenticated=False, is_staff=False),
        )
        with patch('basecalc.views.get_intermarket_technical_snapshot', return_value={}):
            context = build_context(request)

        data = context['data']
        self.assertEqual(data['price'], 53000)
        self.assertEqual(data['price_display'], '53,000')
        self.assertNotIn('fair_price_mid', data)
        self.assertNotIn('valuation_label', data)

    def test_view_does_not_emit_fair_value_label(self):
        from .views import build_context

        request = mock.Mock(
            GET={'price': '50339'},
            POST={},
            user=mock.Mock(is_authenticated=False, is_staff=False),
        )
        with patch('basecalc.views.get_intermarket_technical_snapshot', return_value={}):
            context = build_context(request)

        self.assertEqual(context['data']['price'], 50339)
        self.assertNotIn('valuation_label', context['data'])


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


class BasecalcWorldModelV2SupportTests(TestCase):
    def test_data_quality_scores_good_yahoo_snapshot(self):
        result = evaluate_snapshot_quality(
            {
                'symbol': 'NIY=F',
                'source': 'yahoo',
                'price': 41000,
                'previous_close': 40900,
                'change_pct': 0.24,
                'fetched_at': timezone.now(),
            }
        )

        self.assertGreaterEqual(result['score'], 80)
        self.assertEqual(result['level'], 'good')
        self.assertEqual(result['instrument_type'], 'futures')

    def test_data_quality_marks_index_fallback_and_stale_data(self):
        result = evaluate_snapshot_quality(
            {
                'symbol': '^nkx',
                'source': 'stooq',
                'price': 41000,
                'previous_close': 40900,
                'fallback_used': True,
                'fetched_at': timezone.now() - timezone.timedelta(hours=1),
            }
        )

        self.assertTrue(result['is_stale'])
        self.assertEqual(result['instrument_type'], 'index_fallback')
        self.assertLess(result['score'], 80)

    def test_confidence_score_caps_bad_quality(self):
        result = calculate_confidence_score(
            features={},
            sentiment_score=90,
            continuation_score=90,
            shock_score=10,
            similar_summary={'case_count': 12, 'directional_accuracy': 0.9},
            performance_adjustment=None,
            data_quality={'score': 30, 'level': 'bad', 'is_stale': True},
        )

        self.assertEqual(result['label'], 'Low')
        self.assertLess(result['score'], 45)

    def test_confidence_score_uses_full_100_point_scale_for_complete_evidence(self):
        result = calculate_confidence_score(
            features={
                'readiness_level': 'ready',
                'directional_allowed': True,
                'bar_counts': {'1d': 120},
                'indicator_validity': {
                    'ema20': True,
                    'ema60': True,
                    'rsi14': True,
                    'atr14': True,
                },
                'performance_total_predictions': 120,
            },
            sentiment_score=100,
            continuation_score=100,
            shock_score=20,
            similar_summary={
                'case_count': 30,
                'used_case_count': 30,
                'is_statistically_valid': True,
                'directional_accuracy': 1.0,
            },
            performance_adjustment=None,
            data_quality={
                'score': 100,
                'level': 'good',
                'is_stale': False,
                'fallback_used': False,
                'instrument_type': 'futures',
            },
        )

        self.assertEqual(result['score'], 100)
        self.assertEqual(result['label'], 'High')

    def test_state_machine_definitions_and_probabilities(self):
        required = {'label', 'phase_label', 'base_bias', 'next_states'}
        for definition in STATE_DEFINITIONS.values():
            self.assertTrue(required.issubset(definition))

        transitions = estimate_transition_probabilities(
            'dip_buy',
            {'sentiment_score': 45, 'continuation_score': 70, 'shock_score': 20},
        )

        self.assertAlmostEqual(sum(row['probability'] for row in transitions), 1.0, places=2)

    def test_state_machine_uses_learned_transition_matrix(self):
        transitions = estimate_transition_probabilities(
            'range_neutral',
            {'sentiment_score': 5, 'continuation_score': 30, 'shock_score': 20},
            performance_stats={
                'transition_matrix': {
                    'range_neutral': {
                        'dip_buy': {'count': 8, 'probability': 0.8},
                        'return_sell': {'count': 1, 'probability': 0.1},
                    },
                },
                'transition_sample_count': 10,
            },
        )

        self.assertEqual(transitions[0]['state_key'], 'dip_buy')
        self.assertGreater(transitions[0]['probability'], 0.5)
        self.assertEqual(transitions[0]['source'], 'learned')

    def test_scenarios_include_price_path_simulation(self):
        scenarios = build_scenarios(
            'up',
            {'primary_setup_label': '押し目買い'},
            {
                'upside': [{'label': 'T1', 'price': 41800, 'probability': 0.62}],
                'downside': [{'label': 'T1', 'price': 40400, 'probability': 0.31}],
                'invalidation': {'price': 40200},
            },
            {'confirmation_label': 'confirm_up', 'confirmation_score': 35},
            '40,200を割ると上昇判定は弱まる',
        )

        path = scenarios['upside']['path']
        self.assertEqual(path['direction'], 'up')
        self.assertEqual(path['target_label'], 'T1')
        self.assertEqual(path['target_price'], 41800)
        self.assertGreater(path['adjusted_probability'], 0.62)
        self.assertIn('price_paths', scenarios)

    def test_market_context_score_handles_risk_on_mock(self):
        result = calculate_context_score(
            {
                'assets': {
                    'nasdaq100_futures': {'change_pct': 1.2},
                    'sp500_futures': {'change_pct': 0.8},
                    'vix': {'change_pct': -2.0},
                }
            }
        )

        self.assertEqual(result['risk_label'], 'risk_on')
        self.assertGreater(result['risk_score'], 0)

    def test_market_context_score_includes_neutral_lead_market_when_data_waiting(self):
        result = calculate_context_score({})

        self.assertIn('lead_market', result)
        self.assertIn('先行マーケット', result['lead_market']['summary'])
        self.assertFalse(result['lead_market']['risk_on'])
        self.assertFalse(result['lead_market']['risk_off'])

    def test_market_context_score_backfills_lead_market_for_cached_context(self):
        result = calculate_context_score({
            'risk_score': 0,
            'risk_label': 'neutral',
            'components': {},
            'evidence': ['保存済み外部市場'],
        })

        self.assertIn('lead_market', result)
        self.assertIn('lead_lag_score', result['lead_market'])

    def test_judge_nikkei_lead_context_detects_risk_on_and_headwinds(self):
        result = judge_nikkei_lead_context({
            'nq_15m': 0.45,
            'es_15m': 0.22,
            'usd_jpy_15m': -0.25,
            'vix_15m': -0.8,
            'us2y_1h_bp': 6.5,
            'nq_1h': -0.15,
        })

        self.assertTrue(result['yen_headwind'])
        self.assertTrue(result['policy_headwind'])
        self.assertFalse(result['risk_off'])
        self.assertIn('円高', result['alerts'][0])
        self.assertIn('lead_lag_score', result)
        self.assertIn('hit_rate', result)
        self.assertIn('false_signal_rate', result)
        self.assertGreaterEqual(result['lead_lag_score'], 0)
        self.assertLessEqual(result['lead_lag_score'], 100)

    def test_fetch_intraday_context_uses_short_interval_chart_data(self):
        payload = {
            'chart': {
                'result': [{
                    'meta': {'regularMarketPrice': 101.0},
                    'timestamp': [1, 2, 3, 4],
                    'indicators': {
                        'quote': [{
                            'close': [100.0, 100.5, 100.8, 101.0],
                            'open': [100.0, 100.4, 100.7, 100.9],
                            'high': [100.2, 100.6, 100.9, 101.1],
                            'low': [99.8, 100.3, 100.6, 100.8],
                            'volume': [1, 1, 1, 1],
                        }]
                    },
                }],
                'error': None,
            }
        }

        with patch('basecalc.market_context.requests.get') as get_mock:
            get_mock.return_value.raise_for_status.return_value = None
            get_mock.return_value.json.return_value = payload
            snapshot = fetch_intraday_context('NQ=F', interval='5m', range_='1d')

        get_mock.assert_called_once()
        self.assertEqual(get_mock.call_args.kwargs['params']['interval'], '5m')
        self.assertEqual(snapshot['symbol'], 'NQ=F')
        self.assertIn('change_15m_pct', snapshot)
        self.assertGreater(snapshot['change_15m_pct'], 0)

    def test_market_context_snapshot_contains_lead_market_cards(self):
        assets = {
            'nasdaq100_futures': {
                'symbol': 'NQ=F',
                'change_pct': 0.4,
                'change_15m_pct': 0.3,
                'change_1h_pct': -0.2,
            },
            'sp500_futures': {
                'symbol': 'ES=F',
                'change_pct': 0.2,
                'change_15m_pct': 0.1,
                'change_1h_pct': 0.1,
            },
            'usd_jpy': {
                'symbol': 'JPY=X',
                'change_pct': -0.1,
                'change_15m_pct': -0.3,
                'change_1h_pct': -0.4,
            },
            'vix': {
                'symbol': '^VIX',
                'change_pct': -0.5,
                'change_15m_pct': -0.2,
                'change_1h_pct': -0.1,
            },
            'us2y': {
                'symbol': '^IRX',
                'change_pct': 0.0,
                'change_1h_bp': 7.0,
            },
        }

        result = calculate_context_score({'assets': assets})

        self.assertIn('lead_market', result)
        self.assertIn('alerts', result['lead_market'])
        self.assertTrue(result['lead_market']['yen_headwind'])
        self.assertIn('先行マーケット', result['lead_market']['summary'])
        self.assertIn('lead_lag_score', result['lead_market'])
        self.assertIn('hit_rate', result['lead_market'])
        self.assertIn('false_signal_rate', result['lead_market'])

    def test_market_context_falls_back_to_saved_price_action_when_yahoo_fails(self):
        indicator, _ = Indicator.objects.update_or_create(
            fred_series_id='PA_GSPC_MOM20',
            defaults={
                'name_ja': 'PA_GSPC_MOM20',
                'category': Indicator.Category.MARKET,
                'importance': Indicator.Importance.B,
                'frequency': Indicator.Frequency.DAILY,
                'source': Indicator.Source.YFINANCE_DAILY,
                'is_active': True,
            },
        )
        Observation.objects.create(
            indicator=indicator,
            observation_date=timezone.localdate(),
            value=5.0,
        )

        with (
            patch('basecalc.market_context.fetch_intraday_context', return_value=None),
            patch('basecalc.market_context._fetch_context_symbol', return_value=None),
        ):
            result = get_market_context_snapshot()

        self.assertIn('sp500_futures', result['assets'])
        self.assertEqual(result['assets']['sp500_futures']['source'], 'macro_price_action')
        self.assertGreater(result['risk_score'], 0)

    def test_intermarket_readiness_blocks_when_all_three_us_indexes_missing(self):
        result = evaluate_intermarket_readiness({'assets': {'vix': {'change_pct': -5.0}}})

        self.assertEqual(result['level'], 'blocked')
        self.assertFalse(result['usable'])
        self.assertIn('米国3指数データなし', result['reason'])

    def test_us_index_technical_context_uses_only_nasdaq_sp500_and_dow(self):
        assets = {
            'nasdaq100_futures': {
                'symbol': 'NQ=F',
                'price': 160,
                'previous_close': 150,
                'change_pct': 1.5,
                'closes': [100, 110, 120, 130, 140, 150, 160],
                'highs': [101, 111, 121, 131, 141, 151, 161],
                'lows': [99, 109, 119, 129, 139, 149, 159],
            },
            'sp500_futures': {
                'symbol': 'ES=F',
                'price': 460,
                'previous_close': 450,
                'change_pct': 0.9,
                'closes': [400, 410, 420, 430, 440, 450, 460],
                'highs': [401, 411, 421, 431, 441, 451, 461],
                'lows': [399, 409, 419, 429, 439, 449, 459],
            },
            'dow_futures': {
                'symbol': 'YM=F',
                'price': 360,
                'previous_close': 350,
                'change_pct': 0.4,
                'closes': [320, 325, 330, 340, 345, 350, 360],
                'highs': [321, 326, 331, 341, 346, 351, 361],
                'lows': [319, 324, 329, 339, 344, 349, 359],
            },
            'usd_jpy': {'symbol': 'JPY=X', 'change_pct': 3.0},
            'vix': {'symbol': '^VIX', 'change_pct': -12.0},
            'crude_oil': {'symbol': 'CL=F', 'change_pct': -4.0},
        }

        result = build_us_index_technical_context(assets)

        self.assertEqual(set(result['components'].keys()), set(US_INDEX_SYMBOLS.keys()))
        self.assertEqual(result['risk_label'], 'technical_confirm')
        self.assertGreaterEqual(result['confirmation_score'], 25)
        self.assertIn(result['confirmation_label'], {'confirm_up', 'mixed'})
        self.assertNotIn('usd_jpy', result['components'])
        self.assertNotIn('vix', result['components'])

    def test_us_index_snapshot_falls_back_to_saved_price_action(self):
        for series_id, value in (
            ('PA_GSPC_MOM20', -6.0),
            ('PA_DJI_MOM20', -5.0),
            ('PA_IXIC_MOM20', -8.0),
        ):
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
            Observation.objects.create(
                indicator=indicator,
                observation_date=timezone.localdate(),
                value=value,
            )

        with (
            patch('basecalc.market_context.fetch_intraday_context', return_value=None),
            patch('basecalc.market_context._fetch_context_symbol', return_value=None),
        ):
            result = get_intermarket_technical_snapshot()

        self.assertEqual(result['readiness']['level'], 'ready')
        self.assertEqual(set(result['components'].keys()), set(US_INDEX_SYMBOLS.keys()))
        self.assertEqual(result['confirmation_label'], 'confirm_down')

    def test_sentiment_score_uses_us_index_confirmation_not_broad_market_context(self):
        base_features = {
            'price': 41000,
            'ema5': 41200,
            'ema20': 41100,
            'ema60': 40500,
            'vwap': 40900,
            'macd': 120,
            'macd_signal': 90,
            'rsi14': 58,
            'change_5d_pct': 1.0,
            'daily_change_pct': 0.4,
            'atr_ratio': 1.0,
            'structure_bias': 1,
            'indicator_validity': {
                'ema20': True,
                'ema60': True,
                'vwap': True,
                'macd': True,
                'rsi14': True,
                'atr14': True,
            },
            'data_quality': {'level': 'good', 'score': 90},
        }

        without_confirmation = calculate_sentiment_score({
            **base_features,
            'context_risk_score': -100,
            'us_index_confirmation_score': 0,
        })
        with_confirmation = calculate_sentiment_score({
            **base_features,
            'context_risk_score': -100,
            'us_index_confirmation_score': 60,
        })

        self.assertEqual(without_confirmation['external_context_score'], 0)
        self.assertGreater(with_confirmation['external_context_score'], 0)

    def test_world_model_exports_basecalc_signal_contract_scope_and_exclusions(self):
        snapshot = _ready_snapshot(80)
        intermarket = build_us_index_technical_context({
            key: {
                'symbol': symbol,
                'price': 110,
                'previous_close': 100,
                'change_pct': 1.0,
                'closes': [90, 94, 98, 100, 104, 108, 110],
                'highs': [91, 95, 99, 101, 105, 109, 111],
                'lows': [89, 93, 97, 99, 103, 107, 109],
            }
            for key, symbol in US_INDEX_SYMBOLS.items()
        })

        result = build_world_model(snapshot['price'], snapshot, intermarket)

        self.assertEqual(result['basecalc_signal']['scope'], 'technical_with_us_index_confirmation')
        self.assertEqual(result['basecalc_signal']['source'], 'basecalc')
        self.assertIn('fx', result['basecalc_signal']['excluded_inputs'])
        self.assertIn('vix', result['basecalc_signal']['excluded_inputs'])
        self.assertIn('nasdaq100_futures_price_action', result['basecalc_signal']['included_inputs'])
        self.assertIn('us_index_confirmation_score', result['features'])


class BasecalcReliabilitySpecTests(TestCase):
    def test_225navi_niy_snapshot_is_official_quality(self):
        snapshot = _ready_snapshot(80, source='225navi')
        snapshot['fallback_used'] = False
        data_quality = evaluate_snapshot_quality(snapshot)

        self.assertEqual(data_quality['source'], '225navi')
        self.assertEqual(data_quality['score'], 96)
        self.assertEqual(data_quality['level'], 'good')

    def test_status_rows_show_age_fallback_and_decision(self):
        rows = status_display_rows(
            {
                'price_data': {
                    'age_minutes': 12,
                    'source': 'yahoo:NIY=F',
                    'fallback_used': False,
                    'decision_level': 'ready',
                    'decision_label': '判定可能',
                },
                'intermarket': {
                    'age_minutes': 1440,
                    'source': 'NQ=F / ES=F / YM=F',
                    'fallback_used': False,
                    'decision_level': 'limited',
                    'decision_label': '参考',
                },
            }
        )

        self.assertEqual(rows[0]['age_display'], '12分前')
        self.assertEqual(rows[0]['fallback_display'], 'なし')
        self.assertEqual(rows[0]['decision_label'], '判定可能')
        self.assertEqual(rows[1]['age_display'], '1日前')
        self.assertEqual(rows[1]['decision_label'], '参考')

    def test_good_yahoo_niy_snapshot_is_ready(self):
        snapshot = _ready_snapshot(80)
        data_quality = evaluate_snapshot_quality(snapshot)
        readiness = evaluate_world_model_readiness(
            price=snapshot['price'],
            snapshot=snapshot,
            data_quality=data_quality,
            daily_ohlcv={
                'opens': snapshot['opens'],
                'highs': snapshot['highs'],
                'lows': snapshot['lows'],
                'closes': snapshot['closes'],
                'volumes': snapshot['volumes'],
                'real_counts': {'opens': 80, 'highs': 80, 'lows': 80, 'closes': 80, 'volumes': 80},
            },
        )

        self.assertEqual(readiness['level'], 'ready')
        self.assertTrue(readiness['directional_allowed'])

    def test_stooq_fallback_is_limited_and_direction_blocked(self):
        snapshot = _ready_snapshot(80, symbol='NK.F', source='stooq')
        result = build_world_model(snapshot['price'], snapshot)

        self.assertEqual(result['readiness_level'], 'limited')
        self.assertFalse(result['directional_allowed'])
        self.assertEqual(result['direction'], 'neutral')
        self.assertEqual(result['state_key'], 'limited_reference')
        self.assertEqual(result['confidence'], 'Low')

    def test_index_fallback_is_blocked(self):
        snapshot = _ready_snapshot(80, symbol='^NKX', source='stooq')
        snapshot['instrument_type'] = 'index_fallback'
        result = build_world_model(snapshot['price'], snapshot)

        self.assertEqual(result['readiness_level'], 'blocked')
        self.assertFalse(result['directional_allowed'])
        self.assertEqual(result['state_key'], 'data_unavailable')

    def test_insufficient_daily_bars_blocks_directional_state(self):
        snapshot = _ready_snapshot(34)
        result = build_world_model(snapshot['price'], snapshot)

        self.assertEqual(result['readiness_level'], 'blocked')
        self.assertEqual(result['direction'], 'neutral')
        self.assertEqual(result['upside_targets'], [])

    def test_35_to_59_daily_bars_is_limited(self):
        snapshot = _ready_snapshot(45)
        result = build_world_model(snapshot['price'], snapshot)

        self.assertEqual(result['readiness_level'], 'limited')
        self.assertEqual(result['direction'], 'neutral')
        self.assertEqual(result['state_key'], 'limited_reference')

    def test_vwap_invalid_when_volume_is_synthetic(self):
        snapshot = _ready_snapshot(80, volume=1)
        result = build_world_model(snapshot['price'], snapshot)

        self.assertFalse(result['readiness']['indicator_validity']['vwap'])
        self.assertEqual(result['features']['vwap'], None)
        self.assertEqual(result['components'].get('trend', 0), result['components'].get('trend', 0))

    def test_pivot_invalid_without_real_previous_high_low_close(self):
        snapshot = _ready_snapshot(80)
        snapshot['highs'] = []
        snapshot['lows'] = []
        result = build_world_model(snapshot['price'], snapshot)

        self.assertFalse(result['readiness']['indicator_validity']['pivot'])
        self.assertEqual(result['features']['pivots'], {})

    def test_limited_world_model_does_not_show_bull_trend_continuation(self):
        snapshot = _ready_snapshot(45, symbol='NK.F', source='stooq')
        result = build_world_model(snapshot['price'], snapshot)

        self.assertNotEqual(result['state_label'], '上昇継続')
        self.assertNotEqual(result['state_label'], '押し目買い')
        self.assertEqual(result['state_label'], '参考表示')

    def test_similar_cases_require_same_instrument_key(self):
        _create_market_bar_series(120, symbol='^NKX', instrument_key='nikkei_index_fallback')

        result = find_similar_cases(
            {'sentiment_score': 20, 'instrument_key': 'cme_nikkei_futures'},
            {'opens': [], 'highs': [], 'lows': [], 'closes': [], 'volumes': []},
            instrument_key='cme_nikkei_futures',
        )

        self.assertEqual(result['searched_case_count'], 0)
        self.assertFalse(result['is_statistically_valid'])

    def test_similar_cases_with_small_sample_are_not_statistically_valid(self):
        snapshot = _ready_snapshot(80)
        result = build_world_model(snapshot['price'], snapshot)

        self.assertFalse(result['similar_summary']['is_statistically_valid'])
        self.assertEqual(result['components']['similar'], 0)

    def test_similar_cases_expand_similarity_when_primary_sample_is_short(self):
        primary = {
            'case_count': 24,
            'used_case_count': 24,
            'searched_case_count': 3000,
            'is_statistically_valid': False,
            'min_similarity': 0.35,
        }
        expanded = {
            'case_count': 30,
            'used_case_count': 30,
            'searched_case_count': 3000,
            'is_statistically_valid': True,
            'min_similarity': 0.28,
        }

        with patch(
            'basecalc.similarity._find_similar_cases_from_ohlcv',
            side_effect=[primary, expanded],
        ) as finder:
            result = find_similar_cases(
                {'sentiment_score': 60, 'instrument_key': 'missing_futures'},
                {'opens': [1] * 80, 'highs': [2] * 80, 'lows': [1] * 80, 'closes': [1] * 80, 'volumes': [1] * 80},
            )

        self.assertEqual(result['case_count'], 30)
        self.assertTrue(result['is_statistically_valid'])
        self.assertEqual(result['min_similarity'], 0.28)
        self.assertTrue(result['similarity_expanded'])
        self.assertEqual(finder.call_count, 2)

    def test_world_model_passes_backtest_sample_count_to_confidence_gate(self):
        snapshot = _ready_snapshot(120)

        with patch(
            'basecalc.world_model.performance_summary',
            return_value={'total_predictions': 600},
            create=True,
        ) as performance:
            result = build_world_model(snapshot['price'], snapshot)

        performance.assert_called_once_with('1d', is_backtest=True)
        self.assertEqual(result['features']['performance_total_predictions'], 600)

    def test_similar_cases_normalize_current_macd_histogram_by_atr(self):
        closes = [40000 for _ in range(120)]
        ohlcv = {
            'opens': closes,
            'highs': [40100 for _ in closes],
            'lows': [39900 for _ in closes],
            'closes': closes,
            'volumes': [1000 for _ in closes],
        }
        result = find_similar_cases(
            {
                'ema5_gap_pct': 0,
                'ema20_gap_pct': 0,
                'ema60_gap_pct': 0,
                'vwap_gap_pct': 0,
                'rsi14': 50,
                'macd_histogram': 1000,
                'atr14': 200,
                'atr_ratio': 1,
                'bb_width_pct': 0,
                'change_3d_pct': 0,
                'change_5d_pct': 0,
                'distance_recent_high_pct': 0,
                'distance_recent_low_pct': 0,
                'structure_bias': 0,
                'sentiment_score': 0,
            },
            ohlcv,
            instrument_key='missing_futures',
        )

        self.assertGreaterEqual(result['case_count'], 10)
        self.assertGreaterEqual(result['searched_case_count'], 50)

    def test_backtest_prediction_timestamp_uses_bar_timestamp(self):
        bars = _create_market_bar_series(80)

        result = run_basecalc_backtest(min_bars=80, limit=80, write=True)

        self.assertEqual(result['created'], 1)
        prediction = WorldModelPrediction.objects.get()
        self.assertEqual(prediction.prediction_timestamp, bars[-1].timestamp)
        self.assertTrue(prediction.is_backtest)

    def test_backtest_does_not_mix_into_live_performance(self):
        live = WorldModelPrediction.objects.create(
            price=41000,
            state_key='range_neutral',
            state_label='レンジ中立',
            direction='neutral',
            sentiment_score=0,
            continuation_score=30,
            shock_score=0,
            confidence='Low',
            main_scenario='test',
            evidence=[],
            features={'symbol': 'NIY=F'},
            instrument_key='cme_nikkei_futures',
            readiness_level='ready',
        )
        backtest = WorldModelPrediction.objects.create(
            price=41000,
            state_key='range_neutral',
            state_label='レンジ中立',
            direction='neutral',
            sentiment_score=0,
            continuation_score=30,
            shock_score=0,
            confidence='Low',
            main_scenario='test',
            evidence=[],
            features={'symbol': 'NIY=F'},
            instrument_key='cme_nikkei_futures',
            readiness_level='ready',
            is_backtest=True,
        )
        for prediction in (live, backtest):
            PredictionOutcome.objects.create(
                prediction=prediction,
                horizon='1d',
                evaluated_at=timezone.now(),
                price_at_evaluation=41000,
                realized_return_pct=0,
                direction_hit=True,
            )

        self.assertEqual(performance_summary(is_backtest=False)['total_predictions'], 1)
        self.assertEqual(performance_summary(is_backtest=True)['total_predictions'], 1)

    def test_outcome_uses_prediction_timestamp_not_created_at(self):
        prediction_time = timezone.now() - timezone.timedelta(days=5)
        prediction = WorldModelPrediction.objects.create(
            prediction_timestamp=prediction_time,
            price=41000,
            state_key='range_neutral',
            state_label='レンジ中立',
            direction='neutral',
            sentiment_score=0,
            continuation_score=30,
            shock_score=0,
            confidence='Low',
            main_scenario='test',
            evidence=[],
            features={'symbol': 'NIY=F'},
            instrument_key='cme_nikkei_futures',
            readiness_level='ready',
        )
        MarketBar.objects.create(
            symbol='NIY=F',
            timeframe='1d',
            timestamp=prediction_time + timezone.timedelta(days=1),
            close=41050,
            source='yahoo',
            instrument_key='cme_nikkei_futures',
        )

        evaluate_due_predictions(now=timezone.now())

        outcome = PredictionOutcome.objects.get(prediction=prediction, horizon='1d')
        self.assertEqual(outcome.price_at_evaluation, 41050)

    def test_export_import_v2_preserves_reliability_fields(self):
        prediction = WorldModelPrediction.objects.create(
            prediction_timestamp=timezone.now(),
            price=41000,
            state_key='range_neutral',
            state_label='レンジ中立',
            direction='neutral',
            sentiment_score=0,
            continuation_score=30,
            shock_score=0,
            confidence='Low',
            main_scenario='test',
            evidence=[],
            features={'symbol': 'NIY=F'},
            instrument_key='cme_nikkei_futures',
            instrument_type='futures',
            source_symbol='NIY=F',
            source_name='yahoo',
            readiness_level='ready',
            directional_allowed=True,
            bar_counts={'1d': 80},
            indicator_validity={'ema20': True},
            is_backtest=True,
        )

        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'basecalc_history.json'
            export_basecalc_history(str(path))
            payload = json.loads(path.read_text(encoding='utf-8'))
            WorldModelPrediction.objects.all().delete()
            import_basecalc_history(str(path))

        imported = WorldModelPrediction.objects.get()
        self.assertEqual(payload['schema'], 'basecalc_history_v2')
        self.assertEqual(imported.instrument_key, prediction.instrument_key)
        self.assertEqual(imported.readiness_level, 'ready')
        self.assertTrue(imported.directional_allowed)
        self.assertTrue(imported.is_backtest)
        self.assertEqual(imported.bar_counts['1d'], 80)
        self.assertTrue(imported.indicator_validity['ema20'])

    def test_import_v1_history_defaults_to_limited_or_blocked(self):
        payload = {
            'schema': 'basecalc_history_v1',
            'predictions': [{
                'created_at': timezone.now().isoformat(),
                'price': 41000,
                'state_key': 'dip_buy',
                'state_label': '押し目買い',
                'direction': 'up',
                'sentiment_score': 40,
                'continuation_score': 60,
                'shock_score': 0,
                'confidence': 'Middle',
                'main_scenario': 'test',
                'features': {'symbol': 'NIY=F', 'source': 'yahoo'},
                'data_quality_score': 95,
            }],
        }
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'basecalc_history.json'
            path.write_text(json.dumps(payload), encoding='utf-8')
            import_basecalc_history(str(path))

        prediction = WorldModelPrediction.objects.get()
        self.assertEqual(prediction.readiness_level, 'limited')
        self.assertFalse(prediction.directional_allowed)

    def test_check_basecalc_data_integrity_detects_directional_limited_prediction(self):
        payload = {
            'schema': 'basecalc_history_v2',
            'predictions': [{
                'created_at': timezone.now().isoformat(),
                'prediction_timestamp': timezone.now().isoformat(),
                'price': 41000,
                'state_key': 'dip_buy',
                'state_label': '押し目買い',
                'direction': 'up',
                'readiness_level': 'limited',
                'instrument_key': 'cme_nikkei_futures',
                'source_symbol': 'NIY=F',
            }],
        }
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'basecalc_history.json'
            path.write_text(json.dumps(payload), encoding='utf-8')
            with self.assertRaises(Exception):
                call_command('check_basecalc_data_integrity', '--input', str(path))


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
            'source': 'yahoo',
            'price': closes[-1],
            'previous_close': closes[-2],
            'change_pct': 0.2,
            'fetched_at': timezone.now(),
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
        self.assertEqual(result['model_version'], 'wm_v2.0.0')
        self.assertGreaterEqual(result['confidence_score'], 0)
        self.assertLessEqual(result['confidence_score'], 100)
        self.assertIn('data_quality', result)
        self.assertIn('transition_probs', result)
        self.assertIn('expected_returns', result)
        self.assertAlmostEqual(
            sum(row['probability'] for row in result['transition_probs']),
            1.0,
            places=2,
        )
        self.assertGreaterEqual(len(result['upside_targets']), 2)
        self.assertGreaterEqual(len(result['downside_targets']), 2)
        self.assertGreaterEqual(len(result['evidence']), 3)

    def test_world_model_reports_performance_adjustment(self):
        closes = [40000 + index * 30 for index in range(80)]
        snapshot = {
            'symbol': 'NIY=F',
            'source': 'yahoo',
            'price': closes[-1],
            'previous_close': closes[-2],
            'change_pct': 0.2,
            'fetched_at': timezone.now(),
            'opens': [close - 30 for close in closes],
            'highs': [close + 120 for close in closes],
            'lows': [close - 140 for close in closes],
            'closes': closes,
            'volumes': [1000 + index for index in range(80)],
            'timestamps': [1700000000 + index * 86400 for index in range(80)],
        }
        adjustment = {
            'applied': True,
            'horizon': '1d',
            'sample_count': 5,
            'directional_accuracy': 0.2,
            'invalidation_rate': 0.6,
            'avg_return_pct': -0.5,
            'downgrade': 1,
            'reasons': ['方向一致率が低い'],
        }

        with patch(
            'basecalc.world_model.confidence_adjustment_for_state',
            return_value=adjustment,
        ):
            result = build_world_model(closes[-1], snapshot)

        self.assertTrue(result['performance_adjustment']['applied'])
        self.assertIn(result['confidence'], ('Low', 'Middle'))

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
        self.assertTrue(
            all(
                target['probability'] is None or 0 <= target['probability'] <= 1
                for target in targets['upside']
            )
        )
        self.assertIn('source', targets['upside'][0])
        self.assertIn('bullish_reason', targets['invalidation'])
        self.assertIn('target_ranges', targets)
        self.assertIn('near_levels', targets)

    def test_near_round_numbers_are_not_promoted_to_targets(self):
        targets = build_targets(
            {
                'price': 41050,
                'atr14': 300,
                'previous_high': 41100,
                'previous_low': 41000,
                'recent_high': 41100,
                'recent_low': 41000,
                'vwap': 41020,
                'ema20': 41010,
                'pivots': {'r1': 41100, 'r2': 41500, 's1': 41000, 's2': 40600},
            },
            {'case_count': 0, 'is_statistically_valid': False},
        )

        min_distance = 300 * 0.5
        self.assertTrue(
            all(target['distance_abs'] >= min_distance for target in targets['upside'])
        )
        self.assertTrue(
            all(target['distance_abs'] >= min_distance for target in targets['downside'])
        )
        self.assertIn(41100, [level['price'] for level in targets['near_levels']['upside']])
        self.assertNotIn(41100, [target['price'] for target in targets['upside']])
        self.assertIsNone(targets['upside'][0]['probability'])
        self.assertEqual(targets['upside'][0]['probability_source'], 'hidden_low_sample')
        self.assertEqual(targets['upside'][0]['reliability'], 'low')

    def test_expected_returns_mark_sentiment_fallback_as_low_reliability(self):
        result = estimate_expected_returns(
            'range_neutral',
            {'sentiment_score': 40},
            similar_summary={'case_count': 0, 'is_statistically_valid': False},
        )

        self.assertEqual(result['1d']['source'], 'sentiment_fallback')
        self.assertEqual(result['1d']['reliability'], 'low')
        self.assertEqual(result['1d']['display_label'], '未検証の参考値')

    def test_intermarket_comparison_summary_returns_four_variants(self):
        prediction = WorldModelPrediction.objects.create(
            price=41000,
            state_key='dip_buy',
            state_label='押し目買い',
            direction='up',
            sentiment_score=30,
            continuation_score=65,
            shock_score=20,
            confidence='Middle',
            confidence_score=55,
            main_scenario='scenario',
            sub_scenario='sub',
            features={
                'nikkei_technical_score': 22,
                'us_index_confirmation_score': 50,
                'us_index_components': {
                    'nasdaq100_futures': {'score': 70},
                    'sp500_futures': {'score': 45},
                    'dow_futures': {'score': 20},
                },
            },
            instrument_key='cme_nikkei_futures',
            readiness_level='ready',
        )
        PredictionOutcome.objects.create(
            prediction=prediction,
            horizon='1d',
            evaluated_at=timezone.now(),
            price_at_evaluation=41400,
            realized_return_pct=1.0,
            direction_hit=True,
            upside_t1_hit=True,
            mfe_pct=1.4,
            mae_pct=-0.3,
        )

        result = intermarket_comparison_summary('1d')

        self.assertEqual(
            set(result.keys()),
            {
                'nikkei_only',
                'nikkei_plus_nasdaq100',
                'nikkei_plus_sp500_dow',
                'nikkei_plus_us3',
            },
        )
        self.assertEqual(result['nikkei_plus_us3']['sample_count'], 1)
        self.assertIn('brier_score', result['nikkei_plus_us3'])
        self.assertIn('avg_mae_pct', result['nikkei_plus_us3'])

    def test_snapshot_api_returns_world_model_json(self):
        response = self.client.get(reverse('basecalc:snapshot_api'), {'price': '41000'})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn('sentiment_score', payload)
        self.assertIn('targets', payload)
        self.assertIn('model_version', payload)
        self.assertIn('confidence_score', payload)
        self.assertIn('data_quality', payload)
        self.assertIn('transition_probs', payload)
        self.assertIn('expected_returns', payload)

    def test_staff_refresh_saves_prediction(self):
        user = User.objects.create_user(
            username='basecalc-world-model-staff',
            password='test-password',
            is_staff=True,
        )
        self.client.force_login(user)
        closes = [40000 + index * 50 for index in range(80)]
        snapshot = {
            'symbol': 'NIY=F',
            'source': 'yahoo',
            'price': closes[-1],
            'previous_close': closes[-2],
            'change_pct': 0.2,
            'fetched_at': timezone.now(),
            'opens': [close - 20 for close in closes],
            'highs': [close + 80 for close in closes],
            'lows': [close - 80 for close in closes],
            'closes': closes,
            'volumes': [1000 for _ in closes],
            'timestamps': [1700000000 + index * 86400 for index in range(80)],
        }

        with (
            patch(
                'basecalc.views.get_nikkei_per_values',
                return_value={'index_based': 18.5, 'dividend_yield_index_based': 1.8},
            ),
            patch('basecalc.views.get_jgb10y_yield_percent', return_value=1.2),
            patch('basecalc.views.get_stale_futures_snapshot', return_value=snapshot),
        ):
            response = self.client.post(
                reverse('basecalc:index'),
                {'action': 'update', 'price': str(closes[-1])},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(WorldModelPrediction.objects.count(), 1)

    def test_recent_duplicate_prediction_is_not_saved(self):
        world_model = {
            'price': 41000,
            'state_key': 'dip_buy',
            'state_label': '押し目買い',
            'direction': 'up',
            'sentiment_score': 42,
            'continuation_score': 62,
            'shock_score': 25,
            'confidence': 'Middle',
            'main_scenario': 'test',
            'sub_scenario': '',
            'invalidation_price': 40600,
            'upside_targets': [{'price': 41400}, {'price': 41800}],
            'downside_targets': [{'price': 40600}, {'price': 40200}],
            'evidence': [],
            'features': {
                'symbol': 'NIY=F',
                'source': 'test',
                'close': 41000,
            },
        }

        first = save_prediction(world_model)
        second = save_prediction(dict(world_model))

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(WorldModelPrediction.objects.count(), 1)

    def test_save_prediction_persists_world_model_v2_fields(self):
        world_model = {
            'model_version': 'wm_v2.0.0',
            'price': 41000,
            'state_key': 'dip_buy',
            'state_label': '押し目買い',
            'direction': 'up',
            'sentiment_score': 42,
            'continuation_score': 62,
            'shock_score': 25,
            'confidence': 'Middle',
            'confidence_score': 68,
            'data_quality_score': 91,
            'main_scenario': 'test',
            'sub_scenario': '',
            'invalidation_price': 40600,
            'upside_targets': [{'price': 41400, 'probability': 0.62}],
            'downside_targets': [{'price': 40600, 'probability': 0.45}],
            'evidence': [],
            'features': {'symbol': 'NIY=F', 'source': 'test', 'close': 41000},
            'transition_probs': [{'state_key': 'range_neutral', 'label': 'レンジ中立', 'probability': 1.0}],
            'expected_returns': {'1d': 0.4},
            'market_context': {'risk_score': 20, 'risk_label': 'risk_on'},
        }

        prediction = save_prediction(world_model)
        prediction.refresh_from_db()

        self.assertEqual(prediction.model_version, 'wm_v2.0.0')
        self.assertEqual(prediction.confidence_score, 68)
        self.assertEqual(prediction.data_quality_score, 91)
        self.assertEqual(prediction.transition_probs[0]['state_key'], 'range_neutral')
        self.assertEqual(prediction.expected_returns['1d'], 0.4)

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
            patch('basecalc.views.get_stale_futures_snapshot', return_value=None),
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
            confidence_score=55,
            main_scenario='test',
            invalidation_price=41400,
            upside_targets=[{'price': 41400}, {'price': 41800}],
            downside_targets=[{'price': 40600}, {'price': 40200}],
            evidence=[],
            features={'symbol': 'NIY=F'},
            instrument_key='cme_nikkei_futures',
            instrument_type='futures',
            source_symbol='NIY=F',
            source_name='yahoo',
            readiness_level='ready',
            directional_allowed=True,
            bar_counts={'1d': 80},
            indicator_validity={'ema20': True, 'ema60': True, 'rsi14': True, 'atr14': True},
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
                instrument_key='cme_nikkei_futures',
                instrument_type='futures',
                source_symbol='NIY=F',
                source_name='yahoo',
                readiness_level='ready',
                directional_allowed=True,
                bar_counts={'1d': 80},
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

    def test_weak_state_lowers_confidence(self):
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
                instrument_key='cme_nikkei_futures',
                instrument_type='futures',
                source_symbol='NIY=F',
                source_name='yahoo',
                readiness_level='ready',
                directional_allowed=True,
                bar_counts={'1d': 80},
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

        adjustment = confidence_adjustment_for_state('dip_buy')

        self.assertIsNotNone(adjustment)
        self.assertEqual(apply_confidence_adjustment('Middle', adjustment), 'Low')
        self.assertLess(apply_sentiment_score_adjustment(42, adjustment), 42)

    def test_history_page_links_to_saved_validation_report(self):
        response = self.client.get(reverse('basecalc:history'), {'horizon': '1d'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '検証・分析')
        self.assertContains(response, '検証レポートを開く')
        self.assertNotContains(response, '改善候補')

    def test_outcome_evaluation_uses_daily_horizons(self):
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
        bar_specs = (
            ('1d', timezone.timedelta(days=1), '1d', 41450),
            ('3d', timezone.timedelta(days=3), '1d', 41650),
            ('5d', timezone.timedelta(days=5), '1d', 41900),
        )
        for _, delta, timeframe, close in bar_specs:
            MarketBar.objects.create(
                symbol='NIY=F',
                timeframe=timeframe,
                timestamp=created_at + delta,
                open=close - 80,
                high=close + 120,
                low=close - 140,
                close=close,
                source='test',
            )

        evaluate_due_predictions(50000, now=timezone.now())

        horizons = set(
            PredictionOutcome.objects.filter(prediction=prediction).values_list(
                'horizon',
                flat=True,
            )
        )
        self.assertTrue({'1d', '3d', '5d'}.issubset(horizons))
        one_day = PredictionOutcome.objects.get(prediction=prediction, horizon='1d')
        self.assertEqual(one_day.price_at_evaluation, 41450)

    def test_persistence_export_import_is_idempotent(self):
        prediction = WorldModelPrediction.objects.create(
            model_version='wm_v2.0.0',
            price=41000,
            state_key='dip_buy',
            state_label='押し目買い',
            direction='up',
            sentiment_score=42,
            continuation_score=62,
            shock_score=25,
            confidence='Middle',
            confidence_score=68,
            data_quality_score=90,
            main_scenario='test',
            invalidation_price=40600,
            upside_targets=[{'price': 41400, 'probability': 0.62}],
            downside_targets=[{'price': 40600, 'probability': 0.45}],
            evidence=[],
            features={'symbol': 'NIY=F'},
            transition_probs=[{'state_key': 'range_neutral', 'label': 'レンジ中立', 'probability': 1.0}],
            expected_returns={'1d': 0.4},
            context={'risk_score': 20},
        )
        PredictionOutcome.objects.create(
            prediction=prediction,
            horizon='1d',
            evaluated_at=timezone.now(),
            price_at_evaluation=41400,
            realized_return_pct=0.97,
            direction_hit=True,
            upside_t1_hit=True,
        )
        MarketBar.objects.create(
            symbol='NIY=F',
            timeframe='1d',
            timestamp=timezone.now(),
            close=41400,
            source='test',
        )

        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'basecalc_history.json'
            exported = export_basecalc_history(str(path))
            PredictionOutcome.objects.all().delete()
            WorldModelPrediction.objects.all().delete()
            MarketBar.objects.all().delete()

            first_import = import_basecalc_history(str(path))
            second_import = import_basecalc_history(str(path))

        self.assertEqual(exported['predictions'], 1)
        self.assertEqual(first_import['predictions_created'], 1)
        self.assertEqual(second_import['predictions_created'], 0)
        self.assertEqual(WorldModelPrediction.objects.count(), 1)
        self.assertEqual(PredictionOutcome.objects.count(), 1)
        self.assertEqual(MarketBar.objects.count(), 1)

    def test_performance_t1_rates_do_not_exceed_one_when_both_sides_hit(self):
        prediction = WorldModelPrediction.objects.create(
            model_version='wm_v2.0.0',
            price=41000,
            state_key='range_neutral',
            state_label='レンジ中立',
            direction='neutral',
            sentiment_score=0,
            continuation_score=20,
            shock_score=20,
            confidence='Low',
            confidence_score=20,
            data_quality_score=90,
            main_scenario='test',
            upside_targets=[{'price': 41400, 'probability': None}],
            downside_targets=[{'price': 40600, 'probability': None}],
            evidence=[],
            expected_returns={'1d': {'value': 0.5}},
            features={'symbol': 'NIY=F', 'previous_close': 40900, 'close': 41000},
            instrument_key='cme_nikkei_futures',
            readiness_level='ready',
        )
        PredictionOutcome.objects.create(
            prediction=prediction,
            horizon='1d',
            evaluated_at=timezone.now(),
            price_at_evaluation=41041,
            realized_return_pct=0.1,
            direction_hit=True,
            upside_t1_hit=True,
            downside_t1_hit=True,
        )

        summary = performance_summary()
        state_rows = state_performance_summary()

        self.assertEqual(summary['target_t1_hit_rate'], 1.0)
        self.assertEqual(summary['model_directional_accuracy'], 1.0)
        self.assertEqual(summary['continuation_directional_accuracy'], 1.0)
        self.assertEqual(summary['zero_prediction_mae'], 0.1)
        self.assertEqual(summary['model_mae'], 0.4)
        self.assertEqual(summary['mae_improvement_rate'], -3.0)
        self.assertEqual(state_rows[0]['target_t1_hit_rate'], 1.0)
        self.assertIn('baseline_comparison', summary)

    def test_baseline_comparison_reports_named_models(self):
        rows = []
        specs = (
            ('up', 0.8, 0.5, 0.9, 40800, 41200),
            ('down', -0.6, -0.4, -0.7, 41000, 40900),
        )
        for direction, realized, expected, previous_close, current_close, price_at_eval in specs:
            prediction = WorldModelPrediction.objects.create(
                model_version='wm_v2.0.0',
                price=41000,
                state_key='range_neutral',
                state_label='レンジ中立',
                direction=direction,
                sentiment_score=40 if direction == 'up' else -40,
                continuation_score=20,
                shock_score=20,
                confidence='Middle',
                confidence_score=65,
                data_quality_score=90,
                main_scenario='test',
                evidence=[],
                expected_returns={'1d': {'value': expected}},
                features={
                    'symbol': 'NIY=F',
                    'previous_close': previous_close,
                    'close': current_close,
                    'ema5': current_close + (10 if direction == 'up' else -10),
                    'ema20': current_close,
                    'vwap': current_close - (5 if direction == 'up' else -5),
                    'atr14': 400,
                },
                instrument_key='cme_nikkei_futures',
                readiness_level='ready',
            )
            rows.append(
                PredictionOutcome.objects.create(
                    prediction=prediction,
                    horizon='1d',
                    evaluated_at=timezone.now(),
                    price_at_evaluation=price_at_eval,
                    realized_return_pct=realized,
                    direction_hit=True,
                )
            )

        result = baseline_comparison_summary(rows, '1d')

        keys = {row['key'] for row in result['rows']}
        self.assertTrue({'always_up', 'always_neutral', 'continuation', 'ema_cross', 'vwap_side', 'model'}.issubset(keys))
        self.assertEqual(result['sample_count'], 2)
        self.assertGreaterEqual(result['best_baseline']['directional_accuracy'], 0)

    def test_atr_range_baseline_does_not_use_realized_return_direction(self):
        rows = []
        for realized in (2.0, -2.0):
            prediction = WorldModelPrediction.objects.create(
                model_version='wm_v2.0.0',
                price=41000,
                state_key='range_neutral',
                state_label='レンジ中立',
                direction='neutral',
                sentiment_score=0,
                continuation_score=20,
                shock_score=20,
                confidence='Middle',
                confidence_score=65,
                data_quality_score=90,
                main_scenario='test',
                evidence=[],
                expected_returns={'1d': {'value': 0.0}},
                features={
                    'symbol': 'NIY=F',
                    'previous_close': 41000,
                    'close': 41000,
                    'ema5': 41000,
                    'ema20': 41000,
                    'vwap': 41000,
                    'atr14': 400,
                },
                instrument_key='cme_nikkei_futures',
                readiness_level='ready',
            )
            rows.append(
                PredictionOutcome.objects.create(
                    prediction=prediction,
                    horizon='1d',
                    evaluated_at=timezone.now(),
                    price_at_evaluation=41000,
                    realized_return_pct=realized,
                    direction_hit=False,
                )
            )

        result = baseline_comparison_summary(rows, '1d')
        atr_row = next(row for row in result['rows'] if row['key'] == 'atr_range')

        self.assertEqual(atr_row['directional_accuracy'], 0.0)
        self.assertLessEqual(atr_row['avg_strategy_return_pct'], 0.0)

    def test_model_baseline_treats_small_expected_return_as_neutral(self):
        prediction = WorldModelPrediction.objects.create(
            model_version='wm_v2.0.0',
            price=41000,
            state_key='range_neutral',
            state_label='レンジ中立',
            direction='up',
            sentiment_score=40,
            continuation_score=20,
            shock_score=20,
            confidence='Middle',
            confidence_score=65,
            data_quality_score=90,
            main_scenario='test',
            evidence=[],
            expected_returns={'1d': {'value': 0.2}},
            features={'symbol': 'NIY=F'},
            instrument_key='cme_nikkei_futures',
            readiness_level='ready',
        )
        outcome = PredictionOutcome.objects.create(
            prediction=prediction,
            horizon='1d',
            evaluated_at=timezone.now(),
            price_at_evaluation=41400,
            realized_return_pct=1.0,
            direction_hit=True,
        )

        result = baseline_comparison_summary([outcome], '1d')
        model_row = next(row for row in result['rows'] if row['key'] == 'model')

        self.assertEqual(model_row['directional_accuracy'], 0.0)
        self.assertLessEqual(model_row['avg_strategy_return_pct'], 0.0)

    def test_prediction_gate_accepts_model_when_it_beats_atr_baseline(self):
        world_model = {
            'readiness_level': 'ready',
            'confidence_score': 60,
            'similar_summary': {
                'case_count': 30,
                'is_statistically_valid': True,
            },
            'data_quality': {
                'level': 'good',
                'fallback_used': False,
            },
        }
        performance = {
            'baseline_comparison': {
                'sample_count': 30,
                'best_baseline': {'key': 'always_up'},
                'rows': [
                    {'key': 'model', 'risk_adjusted_return_pct': 0.12, 'balanced_accuracy': 0.55, 'directional_accuracy': 0.56},
                    {'key': 'atr_range', 'risk_adjusted_return_pct': 0.02, 'balanced_accuracy': 0.48, 'directional_accuracy': 0.50},
                    {'key': 'always_up', 'risk_adjusted_return_pct': 0.20, 'balanced_accuracy': 0.40, 'directional_accuracy': 0.45},
                ],
            },
        }

        self.assertTrue(can_show_prediction(world_model, performance))

    def test_prediction_stop_reasons_are_empty_when_prediction_can_show(self):
        from .services.decision_context import prediction_stop_reasons

        world_model = {
            'readiness_level': 'ready',
            'confidence_score': 60,
            'similar_summary': {
                'case_count': 30,
                'is_statistically_valid': True,
            },
            'data_quality': {
                'level': 'good',
                'fallback_used': False,
            },
        }
        performance = {
            'baseline_comparison': {
                'sample_count': 30,
                'rows': [
                    {'key': 'model', 'risk_adjusted_return_pct': 0.12, 'balanced_accuracy': 0.55, 'directional_accuracy': 0.56},
                    {'key': 'atr_range', 'risk_adjusted_return_pct': 0.02, 'balanced_accuracy': 0.48, 'directional_accuracy': 0.50},
                ],
            },
        }

        self.assertEqual(prediction_stop_reasons(world_model, performance), [])

    def test_prediction_gate_blocks_when_atr_baseline_beats_model(self):
        world_model = {
            'readiness_level': 'ready',
            'confidence_score': 60,
            'similar_summary': {
                'case_count': 30,
                'is_statistically_valid': True,
            },
            'data_quality': {
                'level': 'good',
                'fallback_used': False,
            },
        }
        performance = {
            'baseline_comparison': {
                'sample_count': 30,
                'best_baseline': {'key': 'atr_range'},
                'rows': [
                    {'key': 'model', 'risk_adjusted_return_pct': 0.01, 'balanced_accuracy': 0.45, 'directional_accuracy': 0.48},
                    {'key': 'atr_range', 'risk_adjusted_return_pct': 0.08, 'balanced_accuracy': 0.55, 'directional_accuracy': 0.58},
                ],
            },
        }

        self.assertFalse(can_show_prediction(world_model, performance))

    def test_calibration_summary_compares_expected_and_realized_returns(self):
        prediction = WorldModelPrediction.objects.create(
            model_version='wm_v2.0.0',
            price=41000,
            state_key='range_neutral',
            state_label='レンジ中立',
            direction='neutral',
            sentiment_score=0,
            continuation_score=20,
            shock_score=20,
            confidence='Low',
            confidence_score=20,
            data_quality_score=90,
            main_scenario='test',
            evidence=[],
            features={'symbol': 'NIY=F'},
            expected_returns={'1d': {'value': 0.4, 'source': 'sentiment_fallback'}},
            instrument_key='cme_nikkei_futures',
            readiness_level='ready',
        )
        PredictionOutcome.objects.create(
            prediction=prediction,
            horizon='1d',
            evaluated_at=timezone.now(),
            price_at_evaluation=41100,
            realized_return_pct=0.2,
            direction_hit=True,
        )

        rows = calibration_summary('1d')

        self.assertEqual(rows[0]['bucket'], '0.0%〜0.5%')
        self.assertEqual(rows[0]['sample_count'], 1)
        self.assertEqual(rows[0]['avg_expected_pct'], 0.4)
        self.assertEqual(rows[0]['avg_realized_pct'], 0.2)

    def test_confidence_calibration_buckets_actual_results(self):
        for confidence_score, realized, direction_hit, t1_hit in (
            (55, 0.3, True, True),
            (68, -0.4, False, False),
            (82, 0.9, True, True),
        ):
            prediction = WorldModelPrediction.objects.create(
                model_version='wm_v2.0.0',
                price=41000,
                state_key='range_neutral',
                state_label='レンジ中立',
                direction='up',
                sentiment_score=20,
                continuation_score=20,
                shock_score=20,
                confidence='Middle',
                confidence_score=confidence_score,
                data_quality_score=90,
                main_scenario='test',
                evidence=[],
                expected_returns={'1d': {'value': 0.4}},
                features={'symbol': 'NIY=F'},
                instrument_key='cme_nikkei_futures',
                readiness_level='ready',
            )
            PredictionOutcome.objects.create(
                prediction=prediction,
                horizon='1d',
                evaluated_at=timezone.now(),
                price_at_evaluation=41100,
                realized_return_pct=realized,
                direction_hit=direction_hit,
                upside_t1_hit=t1_hit,
            )

        rows = confidence_calibration_summary('1d')
        by_bucket = {row['bucket']: row for row in rows}

        self.assertEqual(by_bucket['50台']['directional_accuracy'], 1.0)
        self.assertEqual(by_bucket['60台']['directional_accuracy'], 0.0)
        self.assertEqual(by_bucket['80台']['target_t1_hit_rate'], 1.0)
        self.assertIn('avg_return_pct', by_bucket['80台'])

    def test_validation_design_summary_splits_periods_and_regimes(self):
        base_time = timezone.now() - timezone.timedelta(days=80)
        for index, (state_key, realized, hit, atr_ratio) in enumerate(
            (
                ('range_neutral', 0.4, True, 0.006),
                ('range_neutral', -0.5, False, 0.007),
                ('bull_trend_continuation', 0.9, True, 0.018),
                ('bear_trend_continuation', -0.8, True, 0.02),
            )
        ):
            prediction = WorldModelPrediction.objects.create(
                model_version='wm_v2.0.0',
                prediction_timestamp=base_time + timezone.timedelta(days=index * 20),
                price=41000,
                state_key=state_key,
                state_label=STATE_DEFINITIONS[state_key]['label'],
                direction='up' if realized > 0 else 'down',
                sentiment_score=30,
                continuation_score=20,
                shock_score=20,
                confidence='Middle',
                confidence_score=65,
                data_quality_score=90,
                main_scenario='test',
                evidence=[],
                expected_returns={'1d': {'value': 0.4}},
                features={'symbol': 'NIY=F', 'atr14': 41000 * atr_ratio},
                instrument_key='cme_nikkei_futures',
                readiness_level='ready',
            )
            PredictionOutcome.objects.create(
                prediction=prediction,
                horizon='1d',
                evaluated_at=timezone.now(),
                price_at_evaluation=41100,
                realized_return_pct=realized,
                direction_hit=hit,
            )

        summary = validation_design_summary('1d')

        self.assertTrue(summary['walk_forward'])
        self.assertTrue(summary['period_splits'])
        self.assertIn('volatility_regimes', summary)
        self.assertTrue(summary['market_regimes'])

    def test_build_validation_report_command_writes_saved_report_json(self):
        prediction = WorldModelPrediction.objects.create(
            model_version='wm_v2.0.0',
            price=41000,
            state_key='range_neutral',
            state_label='レンジ中立',
            direction='up',
            sentiment_score=20,
            continuation_score=20,
            shock_score=20,
            confidence='Middle',
            confidence_score=65,
            data_quality_score=90,
            main_scenario='test',
            evidence=[],
            expected_returns={'1d': {'value': 0.4}},
            features={'symbol': 'NIY=F', 'atr14': 400},
            instrument_key='cme_nikkei_futures',
            readiness_level='ready',
            is_backtest=True,
        )
        PredictionOutcome.objects.create(
            prediction=prediction,
            horizon='1d',
            evaluated_at=timezone.now(),
            price_at_evaluation=41200,
            realized_return_pct=0.5,
            direction_hit=True,
            upside_t1_hit=True,
        )

        with TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / 'validation_report.json'
            call_command(
                'build_basecalc_validation_report',
                output=str(output_path),
                horizons='1d',
            )
            payload = json.loads(output_path.read_text(encoding='utf-8'))

        self.assertEqual(payload['schema'], 'basecalc_validation_report_v1')
        self.assertEqual(payload['filters']['is_backtest'], True)
        self.assertEqual(payload['horizons']['1d']['summary']['total_predictions'], 1)
        self.assertIn('validation_design', payload['horizons']['1d'])

    def test_backtest_command_dry_run_uses_saved_market_bars(self):
        start = timezone.now() - timezone.timedelta(days=60)
        for index in range(40):
            close = 40000 + index * 25
            MarketBar.objects.create(
                symbol='NIY=F',
                timeframe='1d',
                timestamp=start + timezone.timedelta(days=index),
                open=close - 20,
                high=close + 80,
                low=close - 80,
                close=close,
                source='test',
            )
        out = StringIO()

        call_command('backtest_basecalc_model', '--symbol', 'NIY=F', '--limit', '40', '--dry-run', stdout=out)

        self.assertIn('basecalc backtest complete', out.getvalue())
