import json
from datetime import date, datetime
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .anchor_snapshot import load_anchor_snapshot
from .confidence import calculate_confidence_score
from .data_quality import evaluate_snapshot_quality
from .futures_sentiment import calculate_futures_sentiment
from .indicators import calculate_atr, calculate_ema, calculate_macd, calculate_rsi
from . import market_shock
from .backtesting import run_basecalc_backtest
from .market_context import calculate_context_score
from .market_bars import prune_market_bars
from .models import MarketBar, MarketSnapshot, PredictionOutcome, WorldModelPrediction
from .outcomes import (
    apply_confidence_adjustment,
    apply_sentiment_score_adjustment,
    confidence_adjustment_for_state,
    evaluate_due_predictions,
    improvement_insights,
    performance_summary,
    save_prediction,
)
from .persistence import export_basecalc_history, import_basecalc_history
from .readiness import evaluate_world_model_readiness
from .similarity import find_similar_cases
from .status import status_display_rows
from .state_machine import STATE_DEFINITIONS, estimate_transition_probabilities
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
        response = self.client.get(reverse('basecalc:index'), {'price': '41000'})

        self.assertNotContains(response, 'id="price-refresh"')

    def test_get_without_price_uses_cached_manual_price_only(self):
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
            patch('basecalc.views.get_stale_futures_snapshot', return_value=None) as futures_snapshot,
        ):
            response = self.client.post(
                reverse('basecalc:index'),
                {'action': 'update', 'price': '40000'},
            )

        self.assertEqual(response.status_code, 200)
        per_values.assert_called_once()
        jgb_yield.assert_called_once()
        self.assertGreaterEqual(futures_snapshot.call_count, 1)
        self.assertEqual(cache.get('nikkei_forward_per'), 18.5)
        self.assertEqual(cache.get('nikkei_jgb10y_yield_percent'), 1.2)
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
            start=timezone.make_aware(datetime(2026, 3, 1)),
        )
        MarketBar.objects.filter(
            symbol='NIY=F',
            timestamp__date__gte=date(2026, 6, 1),
        ).delete()
        rows = [
            (date(2026, 6, 1), 66250, 67240, 66240, 67080),
            (date(2026, 6, 2), 67070, 67220, 65580, 66750),
            (date(2026, 6, 3), 67220, 68800, 67190, 68560),
            (date(2026, 6, 4), 67650, 67910, 66950, 67640),
            (date(2026, 6, 5), 67350, 67410, 65890, 66670),
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
            fetched_at=timezone.make_aware(datetime(2026, 6, 5)),
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
            patch('basecalc.operations.get_nikkei_per_values_for_update', return_value={}),
            patch('basecalc.operations.get_jgb10y_yield_for_update', return_value=None),
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
        )
        self.assertIn('exported=True', out.getvalue())


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
        self.assertContains(response, '市場ストレス・急落予測')
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

    def test_state_machine_definitions_and_probabilities(self):
        required = {'label', 'phase_label', 'base_bias', 'next_states'}
        for definition in STATE_DEFINITIONS.values():
            self.assertTrue(required.issubset(definition))

        transitions = estimate_transition_probabilities(
            'dip_buy',
            {'sentiment_score': 45, 'continuation_score': 70, 'shock_score': 20},
        )

        self.assertAlmostEqual(sum(row['probability'] for row in transitions), 1.0, places=2)

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
                'per': {
                    'age_minutes': 1440,
                    'age_days': 3,
                    'source': 'nikkei',
                    'fallback_used': False,
                    'decision_level': 'limited',
                    'decision_label': '参考',
                },
            }
        )

        self.assertEqual(rows[0]['age_display'], '12分前')
        self.assertEqual(rows[0]['fallback_display'], 'なし')
        self.assertEqual(rows[0]['decision_label'], '判定可能')
        self.assertEqual(rows[1]['age_display'], '3日前')
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
        self.assertTrue(all(0 <= target['probability'] <= 1 for target in targets['upside']))
        self.assertIn('source', targets['upside'][0])
        self.assertIn('bullish_reason', targets['invalidation'])

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
