from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse


from earning.models import Stock


class StockModelTests(TestCase):
    def test_create_stock_with_required_fields(self):
        stock = Stock.objects.create(
            symbol='AAPL',
            market='NASDAQ',
            company='Apple Inc.',
            industry='Consumer Electronics',
        )
        self.assertEqual(stock.symbol, 'AAPL')
        self.assertEqual(stock.market, 'NASDAQ')
        self.assertEqual(str(stock), 'AAPL (Apple Inc.)')

    def test_symbol_market_pair_is_unique(self):
        Stock.objects.create(symbol='AAPL', market='NASDAQ', company='Apple Inc.', industry='Tech')
        with self.assertRaises(Exception):
            Stock.objects.create(symbol='AAPL', market='NASDAQ', company='Dup', industry='Tech')

    def test_peer_symbols_defaults_to_empty_list(self):
        stock = Stock.objects.create(symbol='MSFT', market='NASDAQ', company='Microsoft', industry='Tech')
        self.assertEqual(stock.peer_symbols, [])

    def test_peer_symbols_stores_list(self):
        stock = Stock.objects.create(
            symbol='NVDA',
            market='NASDAQ',
            company='NVIDIA',
            industry='Semiconductors',
            peer_symbols=['AMD', 'INTC', 'TSM'],
        )
        stock.refresh_from_db()
        self.assertEqual(stock.peer_symbols, ['AMD', 'INTC', 'TSM'])


from datetime import date as date_cls
from earning.models import EarningsEvent


class EarningsEventModelTests(TestCase):
    def setUp(self):
        self.stock = Stock.objects.create(
            symbol='AAPL', market='NASDAQ', company='Apple Inc.', industry='Tech',
        )

    def test_create_event_with_minimum_fields(self):
        event = EarningsEvent.objects.create(
            stock=self.stock,
            fiscal_period="Q1 '26",
            event_date=date_cls(2026, 1, 30),
        )
        self.assertEqual(event.stock, self.stock)
        self.assertEqual(event.fiscal_period, "Q1 '26")

    def test_stock_and_fiscal_period_are_unique_together(self):
        EarningsEvent.objects.create(stock=self.stock, fiscal_period="Q1 '26", event_date=date_cls(2026, 1, 30))
        with self.assertRaises(Exception):
            EarningsEvent.objects.create(stock=self.stock, fiscal_period="Q1 '26", event_date=date_cls(2026, 1, 31))

    def test_default_text_columns_are_blank(self):
        event = EarningsEvent.objects.create(
            stock=self.stock, fiscal_period="Q2 '26", event_date=date_cls(2026, 4, 30),
        )
        self.assertEqual(event.summary, '')
        self.assertEqual(event.fundamental, 'flat')
        self.assertEqual(event.guidance_revision, '')

    def test_past_reactions_default_empty_list(self):
        event = EarningsEvent.objects.create(
            stock=self.stock, fiscal_period="Q3 '26", event_date=date_cls(2026, 7, 30),
        )
        self.assertEqual(event.past_reactions, [])

    def test_event_ordering_descending_by_date(self):
        EarningsEvent.objects.create(stock=self.stock, fiscal_period="Q1 '26", event_date=date_cls(2026, 1, 30))
        EarningsEvent.objects.create(stock=self.stock, fiscal_period="Q2 '26", event_date=date_cls(2026, 4, 30))
        EarningsEvent.objects.create(stock=self.stock, fiscal_period="Q3 '26", event_date=date_cls(2026, 7, 30))
        dates = list(EarningsEvent.objects.values_list('event_date', flat=True))
        self.assertEqual(dates, [date_cls(2026, 7, 30), date_cls(2026, 4, 30), date_cls(2026, 1, 30)])


from django.db import transaction
from earning.models import EarningsPrediction


class EarningsPredictionModelTests(TestCase):
    def setUp(self):
        self.stock = Stock.objects.create(symbol='AAPL', market='NASDAQ', company='Apple Inc.', industry='Tech')
        self.event = EarningsEvent.objects.create(
            stock=self.stock, fiscal_period="Q1 '26", event_date=date_cls(2026, 1, 30),
        )

    def test_create_prediction(self):
        pred = EarningsPrediction.objects.create(
            event=self.event,
            predicted_reaction=2.5,
            confidence=0.7,
            model_version='baseline-v0',
        )
        self.assertEqual(pred.event, self.event)
        self.assertAlmostEqual(pred.predicted_reaction, 2.5)
        self.assertEqual(pred.model_version, 'baseline-v0')

    def test_one_prediction_per_event_per_model_version(self):
        EarningsPrediction.objects.create(event=self.event, predicted_reaction=1.0, model_version='baseline-v0')
        with self.assertRaises(Exception):
            with transaction.atomic():
                EarningsPrediction.objects.create(event=self.event, predicted_reaction=2.0, model_version='baseline-v0')
        EarningsPrediction.objects.create(event=self.event, predicted_reaction=2.0, model_version='baseline-v1')
        self.assertEqual(self.event.predictions.count(), 2)


import csv as _csv
from io import StringIO
from django.core.management import call_command


class ImportEarningsCsvTests(TestCase):
    def _write_csv(self, path, rows):
        headers = list(rows[0].keys())
        with path.open('w', encoding='utf-8', newline='') as f:
            writer = _csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.csv_path = Path(self.temp_dir.name) / 'data.csv'

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_imports_a_single_row_into_stock_and_event(self):
        rows = [{
            'date': '2026-01-30', 'market': 'NASDAQ', 'symbol': 'AAPL', 'company': 'Apple Inc.',
            'fiscal_period': "Q1 '26", 'industry': 'Tech',
            'Fundamental': 'up', 'Direction': 'up', 'Sentiment': 'flat', 'Risk': '70',
            'summary': 'great quarter',
            'eps_forecast': '2.10', 'eps_4q_ago': '1.88', 'eps_current': '2.40', 'eps_4q_prior_period': '1.95',
            'sales_forecast': '120.0', 'sales_4q_ago': '110.0', 'sales_current': '125.0', 'sales_4q_prior_period': '108.0',
            'surp_4q_ago': '+2.0', 'surp_current': '+4.0', 'surp_4q_prior_period': '+1.0',
            'surp_eps_4q_ago': '+0.05', 'surp_eps_current': '+0.30', 'surp_eps_4q_prior_period': '+0.10',
            'theme': 'AI', 'theme_score': '80', 'watch_tier': '最重要', 'watch_role': '主要',
            'nikkei_weight': '5.5', 'gross_margin': '45', 'operating_margin': '30',
            'guidance_revision': 'up', 'relative_strength': '78',
            'reaction_close': '2.5', 'reaction_next_day': '1.2', 'market_interpretation': 'bullish',
            'past_q1': '1.0', 'past_q2': '-0.5', 'past_q3': '2.0', 'past_q4': '0.5',
        }]
        self._write_csv(self.csv_path, rows)

        out = StringIO()
        call_command('import_earnings_csv', str(self.csv_path), stdout=out)

        stock = Stock.objects.get(symbol='AAPL', market='NASDAQ')
        self.assertEqual(stock.industry, 'Tech')
        self.assertEqual(stock.theme, 'AI')

        event = EarningsEvent.objects.get(stock=stock, fiscal_period="Q1 '26")
        self.assertEqual(event.event_date, date_cls(2026, 1, 30))
        self.assertEqual(event.fundamental, 'up')
        self.assertAlmostEqual(event.risk_value, 70.0)
        self.assertAlmostEqual(event.gross_margin, 45.0)
        self.assertEqual(event.guidance_revision, 'up')
        self.assertAlmostEqual(event.reaction_close, 2.5)
        self.assertEqual(event.market_interpretation, 'bullish')
        self.assertEqual(event.past_reactions, [1.0, -0.5, 2.0, 0.5])

    def test_re_running_does_not_duplicate(self):
        rows = [{
            'date': '2026-01-30', 'market': 'NASDAQ', 'symbol': 'AAPL', 'company': 'Apple Inc.',
            'fiscal_period': "Q1 '26", 'industry': 'Tech',
            'Fundamental': 'up', 'Direction': 'up', 'Sentiment': 'flat', 'Risk': '70',
            'summary': 'first', 'eps_current': '2.40',
        }]
        self._write_csv(self.csv_path, rows)
        call_command('import_earnings_csv', str(self.csv_path), stdout=StringIO())

        rows[0]['summary'] = 'second'
        self._write_csv(self.csv_path, rows)
        call_command('import_earnings_csv', str(self.csv_path), stdout=StringIO())

        self.assertEqual(Stock.objects.count(), 1)
        self.assertEqual(EarningsEvent.objects.count(), 1)
        self.assertEqual(EarningsEvent.objects.get().summary, 'second')

    def test_invalid_date_falls_back_to_null(self):
        rows = [{
            'date': '決算日未定', 'market': 'NASDAQ', 'symbol': 'NVDA', 'company': 'NVIDIA',
            'fiscal_period': "Q1 '26", 'industry': 'Tech',
            'Fundamental': 'flat',
        }]
        self._write_csv(self.csv_path, rows)
        call_command('import_earnings_csv', str(self.csv_path), stdout=StringIO())
        self.assertIsNone(EarningsEvent.objects.get().event_date)


from earning.models import EarningsPriceWindow


class EarningsPriceWindowModelTests(TestCase):
    def setUp(self):
        self.stock = Stock.objects.create(symbol='AAPL', market='NASDAQ', company='Apple Inc.', industry='Tech')
        self.event = EarningsEvent.objects.create(
            stock=self.stock, fiscal_period="Q1 '26", event_date=date_cls(2026, 1, 30),
        )

    def test_create_row_with_required_fields(self):
        row = EarningsPriceWindow.objects.create(
            event=self.event,
            trade_date=date_cls(2026, 1, 28),
            offset_days=-2,
            open=100.0, high=101.0, low=99.0, close=100.5, volume=12345,
        )
        self.assertEqual(row.event, self.event)
        self.assertEqual(row.offset_days, -2)
        self.assertAlmostEqual(row.close, 100.5)

    def test_event_and_trade_date_are_unique_together(self):
        EarningsPriceWindow.objects.create(
            event=self.event, trade_date=date_cls(2026, 1, 28), offset_days=-2,
        )
        with transaction.atomic():
            with self.assertRaises(Exception):
                EarningsPriceWindow.objects.create(
                    event=self.event, trade_date=date_cls(2026, 1, 28), offset_days=-2,
                )

    def test_ordering_by_event_then_trade_date(self):
        EarningsPriceWindow.objects.create(event=self.event, trade_date=date_cls(2026, 1, 30), offset_days=0)
        EarningsPriceWindow.objects.create(event=self.event, trade_date=date_cls(2026, 1, 28), offset_days=-2)
        EarningsPriceWindow.objects.create(event=self.event, trade_date=date_cls(2026, 1, 29), offset_days=-1)
        dates = list(EarningsPriceWindow.objects.values_list('trade_date', flat=True))
        self.assertEqual(dates, [date_cls(2026, 1, 28), date_cls(2026, 1, 29), date_cls(2026, 1, 30)])

    def test_str_includes_offset_with_sign(self):
        row = EarningsPriceWindow.objects.create(
            event=self.event, trade_date=date_cls(2026, 2, 5), offset_days=4,
        )
        self.assertIn('T+4', str(row))
        row2 = EarningsPriceWindow.objects.create(
            event=self.event, trade_date=date_cls(2026, 1, 28), offset_days=-2,
        )
        self.assertIn('T-2', str(row2))


from earning.services.yfinance import build_yahoo_symbol


class EarningsEventMacroColumnsTests(TestCase):
    def setUp(self):
        self.stock = Stock.objects.create(symbol='AAPL', market='NASDAQ', company='Apple Inc.', industry='Tech')

    def test_macro_columns_default_to_none(self):
        event = EarningsEvent.objects.create(
            stock=self.stock, fiscal_period="Q1 '26", event_date=date_cls(2026, 1, 30),
        )
        self.assertIsNone(event.vix_at_event)
        self.assertIsNone(event.hy_spread_at_event)
        self.assertIsNone(event.skew_at_event)
        self.assertIsNone(event.t5yie_at_event)
        self.assertIsNone(event.rut_at_event)

    def test_macro_columns_can_be_set_and_saved(self):
        event = EarningsEvent.objects.create(
            stock=self.stock, fiscal_period="Q1 '26", event_date=date_cls(2026, 1, 30),
            vix_at_event=18.5, hy_spread_at_event=3.2, skew_at_event=140.0,
            t5yie_at_event=2.4, rut_at_event=2100.5,
        )
        event.refresh_from_db()
        self.assertAlmostEqual(event.vix_at_event, 18.5)
        self.assertAlmostEqual(event.rut_at_event, 2100.5)


from macro.models import Indicator, Observation
from earning.services.macro import (
    MACRO_FIELD_MAP,
    get_latest_value_on_or_before,
)


class MacroFieldMapTests(TestCase):
    def test_map_has_five_entries(self):
        self.assertEqual(len(MACRO_FIELD_MAP), 5)
        for column, series in MACRO_FIELD_MAP.items():
            self.assertTrue(column.endswith('_at_event'))
            self.assertIsInstance(series, str)
            self.assertTrue(series)

    def test_map_covers_expected_series(self):
        self.assertEqual(MACRO_FIELD_MAP['vix_at_event'], 'VIXCLS')
        self.assertEqual(MACRO_FIELD_MAP['hy_spread_at_event'], 'BAMLH0A0HYM2')
        self.assertEqual(MACRO_FIELD_MAP['skew_at_event'], 'CBOE_SKEW')
        self.assertEqual(MACRO_FIELD_MAP['t5yie_at_event'], 'T5YIE')
        self.assertEqual(MACRO_FIELD_MAP['rut_at_event'], 'RUT_INDEX')


class GetLatestValueOnOrBeforeTests(TestCase):
    def setUp(self):
        self.indicator, _ = Indicator.objects.get_or_create(
            fred_series_id='VIXCLS',
            defaults={'name_ja': 'VIX', 'category': 'market'},
        )

    def test_returns_value_on_event_date(self):
        Observation.objects.create(indicator=self.indicator, observation_date=date_cls(2026, 1, 28), value=15.0)
        Observation.objects.create(indicator=self.indicator, observation_date=date_cls(2026, 1, 30), value=18.5)
        Observation.objects.create(indicator=self.indicator, observation_date=date_cls(2026, 2, 2), value=20.0)
        result = get_latest_value_on_or_before('VIXCLS', date_cls(2026, 1, 30))
        self.assertAlmostEqual(result, 18.5)

    def test_returns_prior_value_when_event_on_holiday(self):
        Observation.objects.create(indicator=self.indicator, observation_date=date_cls(2026, 1, 30), value=18.5)
        # event on 2026-01-31 (Saturday) — no obs that day
        result = get_latest_value_on_or_before('VIXCLS', date_cls(2026, 1, 31))
        self.assertAlmostEqual(result, 18.5)

    def test_returns_none_when_indicator_missing(self):
        result = get_latest_value_on_or_before('NOSUCHSERIES', date_cls(2026, 1, 30))
        self.assertIsNone(result)

    def test_returns_none_when_no_observations(self):
        result = get_latest_value_on_or_before('VIXCLS', date_cls(2026, 1, 30))
        self.assertIsNone(result)

    def test_returns_none_when_event_date_is_none(self):
        Observation.objects.create(indicator=self.indicator, observation_date=date_cls(2026, 1, 30), value=18.5)
        result = get_latest_value_on_or_before('VIXCLS', None)
        self.assertIsNone(result)


class BuildYahooSymbolTests(TestCase):
    def test_tse_appends_dot_t(self):
        self.assertEqual(build_yahoo_symbol('TSE', '4519'), '4519.T')

    def test_tse_lowercase_market_still_works(self):
        self.assertEqual(build_yahoo_symbol('tse', '7203'), '7203.T')

    def test_nasdaq_passes_symbol_through(self):
        self.assertEqual(build_yahoo_symbol('NASDAQ', 'AAPL'), 'AAPL')

    def test_nyse_passes_symbol_through(self):
        self.assertEqual(build_yahoo_symbol('NYSE', 'UNH'), 'UNH')

    def test_unknown_market_returns_none(self):
        self.assertIsNone(build_yahoo_symbol('LSE', 'BP'))

    def test_empty_symbol_returns_none(self):
        self.assertIsNone(build_yahoo_symbol('NASDAQ', ''))
        self.assertIsNone(build_yahoo_symbol('NASDAQ', '   '))


from unittest.mock import patch, MagicMock
import requests as _requests_mod
from earning.services.yfinance import _fetch_chart_json, YahooFetchError


class FetchChartJsonTests(TestCase):
    @patch('earning.services.yfinance.requests.get')
    def test_returns_payload_on_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'chart': {'result': [{'foo': 'bar'}], 'error': None}}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = _fetch_chart_json('http://example.com/AAPL')
        self.assertEqual(result, {'chart': {'result': [{'foo': 'bar'}], 'error': None}})
        self.assertEqual(mock_get.call_count, 1)

    @patch('earning.services.yfinance.time.sleep')
    @patch('earning.services.yfinance.requests.get')
    def test_retries_transient_then_succeeds(self, mock_get, mock_sleep):
        ok_response = MagicMock()
        ok_response.status_code = 200
        ok_response.json.return_value = {'chart': {'result': [{}], 'error': None}}
        ok_response.raise_for_status = MagicMock()
        mock_get.side_effect = [
            _requests_mod.Timeout(),
            _requests_mod.ConnectionError(),
            ok_response,
        ]

        result = _fetch_chart_json('http://example.com/AAPL')
        self.assertEqual(mock_get.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)
        self.assertIn('chart', result)

    @patch('earning.services.yfinance.time.sleep')
    @patch('earning.services.yfinance.requests.get')
    def test_raises_after_three_transient_failures(self, mock_get, mock_sleep):
        mock_get.side_effect = [
            _requests_mod.Timeout(),
            _requests_mod.Timeout(),
            _requests_mod.Timeout(),
        ]
        with self.assertRaises(YahooFetchError):
            _fetch_chart_json('http://example.com/AAPL')
        self.assertEqual(mock_get.call_count, 3)

    @patch('earning.services.yfinance.requests.get')
    def test_does_not_retry_on_404(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = _requests_mod.HTTPError(response=mock_response)
        mock_get.return_value = mock_response

        with self.assertRaises(YahooFetchError):
            _fetch_chart_json('http://example.com/UNKNOWN')
        self.assertEqual(mock_get.call_count, 1)

    @patch('earning.services.yfinance.time.sleep')
    @patch('earning.services.yfinance.requests.get')
    def test_retries_on_500(self, mock_get, mock_sleep):
        bad_response = MagicMock()
        bad_response.status_code = 503
        bad_response.raise_for_status.side_effect = _requests_mod.HTTPError(response=bad_response)
        ok_response = MagicMock()
        ok_response.status_code = 200
        ok_response.json.return_value = {'chart': {'result': [{}], 'error': None}}
        ok_response.raise_for_status = MagicMock()
        mock_get.side_effect = [bad_response, ok_response]

        _fetch_chart_json('http://example.com/AAPL')
        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(mock_sleep.call_count, 1)


from datetime import datetime, timezone as dt_timezone
from earning.services.yfinance import fetch_daily_history


def _to_ts(d):
    return int(datetime(d.year, d.month, d.day, tzinfo=dt_timezone.utc).timestamp())


class FetchDailyHistoryTests(TestCase):
    @patch('earning.services.yfinance._fetch_chart_json')
    def test_parses_chart_payload(self, mock_fetch):
        d1 = date_cls(2026, 1, 28)
        d2 = date_cls(2026, 1, 29)
        d3 = date_cls(2026, 1, 30)
        mock_fetch.return_value = {
            'chart': {
                'result': [{
                    'timestamp': [_to_ts(d1), _to_ts(d2), _to_ts(d3)],
                    'indicators': {
                        'quote': [{
                            'open': [100.0, 101.0, 102.0],
                            'high': [101.0, 102.0, 103.0],
                            'low': [99.0, 100.0, 101.0],
                            'close': [100.5, 101.5, 102.5],
                            'volume': [1000, 2000, 3000],
                        }],
                        'adjclose': [{'adjclose': [100.4, 101.4, 102.4]}],
                    },
                }],
                'error': None,
            }
        }
        result = fetch_daily_history('AAPL', d1, d3)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]['date'], d1)
        self.assertAlmostEqual(result[0]['open'], 100.0)
        self.assertAlmostEqual(result[0]['close'], 100.4)  # adjclose preferred
        self.assertEqual(result[0]['volume'], 1000)
        self.assertEqual(result[2]['date'], d3)

    @patch('earning.services.yfinance._fetch_chart_json')
    def test_returns_empty_when_chart_error_set(self, mock_fetch):
        mock_fetch.return_value = {'chart': {'result': None, 'error': {'code': 'Not Found'}}}
        result = fetch_daily_history('XXXNONEXIST', date_cls(2026, 1, 1), date_cls(2026, 1, 5))
        self.assertEqual(result, [])

    @patch('earning.services.yfinance._fetch_chart_json')
    def test_returns_empty_when_result_is_empty(self, mock_fetch):
        mock_fetch.return_value = {'chart': {'result': [], 'error': None}}
        result = fetch_daily_history('AAPL', date_cls(2026, 1, 1), date_cls(2026, 1, 5))
        self.assertEqual(result, [])

    @patch('earning.services.yfinance._fetch_chart_json')
    def test_handles_missing_adjclose_falls_back_to_close(self, mock_fetch):
        d1 = date_cls(2026, 1, 28)
        mock_fetch.return_value = {
            'chart': {
                'result': [{
                    'timestamp': [_to_ts(d1)],
                    'indicators': {
                        'quote': [{
                            'open': [100.0], 'high': [101.0], 'low': [99.0],
                            'close': [100.5], 'volume': [1000],
                        }],
                    },
                }],
                'error': None,
            }
        }
        result = fetch_daily_history('AAPL', d1, d1)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0]['close'], 100.5)

    @patch('earning.services.yfinance._fetch_chart_json')
    def test_handles_null_values_in_quote_arrays(self, mock_fetch):
        d1 = date_cls(2026, 1, 28)
        d2 = date_cls(2026, 1, 29)
        mock_fetch.return_value = {
            'chart': {
                'result': [{
                    'timestamp': [_to_ts(d1), _to_ts(d2)],
                    'indicators': {
                        'quote': [{
                            'open': [100.0, None],
                            'high': [101.0, None],
                            'low': [99.0, None],
                            'close': [100.5, None],
                            'volume': [1000, None],
                        }],
                        'adjclose': [{'adjclose': [100.4, None]}],
                    },
                }],
                'error': None,
            }
        }
        result = fetch_daily_history('AAPL', d1, d2)
        self.assertEqual(len(result), 2)
        self.assertIsNone(result[1]['open'])
        self.assertIsNone(result[1]['close'])
        self.assertIsNone(result[1]['volume'])

    @patch('earning.services.yfinance._fetch_chart_json')
    def test_returns_empty_on_yahoo_fetch_error(self, mock_fetch):
        mock_fetch.side_effect = YahooFetchError('boom')
        result = fetch_daily_history('AAPL', date_cls(2026, 1, 1), date_cls(2026, 1, 5))
        self.assertEqual(result, [])


from earning.services.yfinance import _business_day_offset, fetch_price_window
from earning.models import EarningsPriceWindow as PriceWindow


class BusinessDayOffsetTests(TestCase):
    def test_event_lands_on_a_trading_day(self):
        days = [date_cls(2026, 1, 28), date_cls(2026, 1, 29), date_cls(2026, 1, 30),
                date_cls(2026, 2, 2), date_cls(2026, 2, 3)]
        event = date_cls(2026, 1, 30)
        self.assertEqual(_business_day_offset(date_cls(2026, 1, 28), event, days), -2)
        self.assertEqual(_business_day_offset(date_cls(2026, 1, 30), event, days), 0)
        self.assertEqual(_business_day_offset(date_cls(2026, 2, 2), event, days), 1)

    def test_event_falls_on_weekend(self):
        days = [date_cls(2026, 1, 28), date_cls(2026, 1, 29), date_cls(2026, 1, 30),
                date_cls(2026, 2, 2), date_cls(2026, 2, 3)]
        event = date_cls(2026, 1, 31)  # Saturday
        # First trading day on or after 2026-01-31 is 2026-02-02 → offset 0
        self.assertEqual(_business_day_offset(date_cls(2026, 1, 30), event, days), -1)
        self.assertEqual(_business_day_offset(date_cls(2026, 2, 2), event, days), 0)
        self.assertEqual(_business_day_offset(date_cls(2026, 2, 3), event, days), 1)


class FetchPriceWindowTests(TestCase):
    def setUp(self):
        self.stock = Stock.objects.create(symbol='AAPL', market='NASDAQ', company='Apple Inc.', industry='Tech')
        self.event = EarningsEvent.objects.create(
            stock=self.stock, fiscal_period="Q1 '26", event_date=date_cls(2026, 1, 30),
        )

    def _payload(self, dates_with_close):
        # dates_with_close: list of (date, close_value)
        return [
            {
                'date': d,
                'open': close - 1,
                'high': close + 1,
                'low': close - 2,
                'close': close,
                'volume': 1000,
            }
            for d, close in dates_with_close
        ]

    @patch('earning.services.yfinance.fetch_daily_history')
    def test_creates_rows_with_correct_offsets(self, mock_fetch):
        mock_fetch.return_value = self._payload([
            (date_cls(2026, 1, 28), 100.0),
            (date_cls(2026, 1, 29), 101.0),
            (date_cls(2026, 1, 30), 102.0),
            (date_cls(2026, 2, 2), 103.0),
        ])
        n = fetch_price_window(self.event)
        self.assertEqual(n, 4)
        rows = list(PriceWindow.objects.filter(event=self.event).order_by('trade_date'))
        self.assertEqual(rows[0].offset_days, -2)
        self.assertEqual(rows[2].offset_days, 0)
        self.assertEqual(rows[3].offset_days, 1)
        self.assertAlmostEqual(rows[2].close, 102.0)

    @patch('earning.services.yfinance.fetch_daily_history')
    def test_idempotent_on_rerun(self, mock_fetch):
        mock_fetch.return_value = self._payload([
            (date_cls(2026, 1, 28), 100.0),
            (date_cls(2026, 1, 30), 102.0),
        ])
        fetch_price_window(self.event)
        # Second run: same data, same row count
        n = fetch_price_window(self.event)
        self.assertEqual(n, 2)
        self.assertEqual(PriceWindow.objects.filter(event=self.event).count(), 2)

    @patch('earning.services.yfinance.fetch_daily_history')
    def test_skips_unsupported_market(self, mock_fetch):
        self.stock.market = 'LSE'
        self.stock.save()
        n = fetch_price_window(self.event)
        self.assertEqual(n, 0)
        self.assertFalse(mock_fetch.called)

    @patch('earning.services.yfinance.fetch_daily_history')
    def test_skips_event_without_date(self, mock_fetch):
        self.event.event_date = None
        self.event.save()
        n = fetch_price_window(self.event)
        self.assertEqual(n, 0)
        self.assertFalse(mock_fetch.called)

    @patch('earning.services.yfinance.fetch_daily_history')
    def test_returns_zero_when_history_empty(self, mock_fetch):
        mock_fetch.return_value = []
        n = fetch_price_window(self.event)
        self.assertEqual(n, 0)
        self.assertEqual(PriceWindow.objects.filter(event=self.event).count(), 0)


class EarningsFetchPricesCommandTests(TestCase):
    def setUp(self):
        self.stock = Stock.objects.create(symbol='AAPL', market='NASDAQ', company='Apple Inc.', industry='Tech')
        # one event within 90-day window, one event 200 days old
        self.recent_event = EarningsEvent.objects.create(
            stock=self.stock, fiscal_period="Q1 '26",
            event_date=date.today() - timedelta(days=10),
        )
        self.old_event = EarningsEvent.objects.create(
            stock=self.stock, fiscal_period="Q4 '25",
            event_date=date.today() - timedelta(days=200),
        )

    @patch('earning.management.commands.earnings_fetch_prices.fetch_price_window')
    @patch('earning.management.commands.earnings_fetch_prices.time.sleep')
    def test_iterates_only_recent_events_by_default(self, mock_sleep, mock_fetch):
        mock_fetch.return_value = 5
        out = StringIO()
        call_command('earnings_fetch_prices', stdout=out)
        # only the recent event (within 90 days) should be processed
        self.assertEqual(mock_fetch.call_count, 1)
        called_event = mock_fetch.call_args[0][0]
        self.assertEqual(called_event.id, self.recent_event.id)

    @patch('earning.management.commands.earnings_fetch_prices.fetch_price_window')
    @patch('earning.management.commands.earnings_fetch_prices.time.sleep')
    def test_days_flag_widens_window(self, mock_sleep, mock_fetch):
        mock_fetch.return_value = 5
        out = StringIO()
        call_command('earnings_fetch_prices', '--days', '365', stdout=out)
        self.assertEqual(mock_fetch.call_count, 2)

    @patch('earning.management.commands.earnings_fetch_prices.fetch_price_window')
    @patch('earning.management.commands.earnings_fetch_prices.time.sleep')
    def test_symbol_flag_filters_to_one_stock(self, mock_sleep, mock_fetch):
        other_stock = Stock.objects.create(symbol='MSFT', market='NASDAQ', company='Microsoft', industry='Tech')
        EarningsEvent.objects.create(
            stock=other_stock, fiscal_period="Q1 '26",
            event_date=date.today() - timedelta(days=5),
        )
        mock_fetch.return_value = 3
        out = StringIO()
        call_command('earnings_fetch_prices', '--symbol', 'AAPL', stdout=out)
        self.assertEqual(mock_fetch.call_count, 1)
        called_event = mock_fetch.call_args[0][0]
        self.assertEqual(called_event.stock.symbol, 'AAPL')


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class EarningsViewTests(TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()

        today = date.today()
        future_stock = Stock.objects.create(
            symbol='FUT', market='NYSE', company='Future Corp', industry='Software',
        )
        past_stock = Stock.objects.create(
            symbol='PST', market='TSE', company='Past Corp', industry='Software',
        )
        EarningsEvent.objects.create(
            stock=future_stock, fiscal_period='Q4',
            event_date=today + timedelta(days=1),
            fundamental='up', direction='flat', sentiment='down', risk_value=82,
            eps_current='3.2', eps_forecast='3.4', eps_4q_ago='2.9', eps_4q_prior_period='2.4',
            sales_current='120', sales_forecast='130', sales_4q_ago='100', sales_4q_prior_period='95',
            surp_4q_ago='4%', surp_current='6%', surp_4q_prior_period='2%',
            surp_eps_4q_ago='3%', surp_eps_current='5%', surp_eps_4q_prior_period='1%',
            summary='upcoming summary',
        )
        EarningsEvent.objects.create(
            stock=past_stock, fiscal_period='Q4',
            event_date=today - timedelta(days=1),
            fundamental='up', direction='flat', sentiment='down', risk_value=82,
            eps_current='3.2', eps_forecast='3.4', eps_4q_ago='2.9', eps_4q_prior_period='2.4',
            sales_current='120', sales_forecast='130', sales_4q_ago='100', sales_4q_prior_period='95',
            surp_4q_ago='4%', surp_current='6%', surp_4q_prior_period='2%',
            surp_eps_4q_ago='3%', surp_eps_current='5%', surp_eps_4q_prior_period='1%',
            summary='completed summary',
        )

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def test_index_renders_only_upcoming_groups_initially(self):
        response = self.client.get(reverse('earning:index'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Future Corp')
        self.assertNotContains(response, 'Past Corp')
        self.assertContains(response, '決算済みを表示（1日分）')
        self.assertNotContains(response, 'cdn.jsdelivr.net')
        self.assertContains(response, '/static/dashboard/vendor/bootstrap-icons/bootstrap-icons.css')

    def test_completed_endpoint_returns_only_completed_groups(self):
        response = self.client.get(reverse('earning:completed'))
        content = response.content.decode('utf-8')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Past Corp', content)
        self.assertNotIn('Future Corp', content)
        self.assertIn('data-period="completed"', content)
