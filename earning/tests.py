from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse


from earning.models import Stock
from scripts.earning import _normalize_earnings_date_after_keyword, _sort_eps_sales_targets


class EarningScriptDateParseTests(TestCase):
    def test_uses_date_after_earnings_label(self):
        text = (
            '市場クローズ\n'
            '5月8日 23:59 GMT に取引終了\n'
            '最新の決算報告日\n'
            '2026年4月30日\n'
            '決算期間\n'
            '2026 第2四半期'
        )
        self.assertEqual(
            _normalize_earnings_date_after_keyword(text, '最新の決算報告日'),
            '2026-04-30',
        )

    def test_uses_inline_date_after_earnings_label(self):
        text = '最新の決算報告日 2026年4月30日 決算期間 2026 第2四半期'
        self.assertEqual(
            _normalize_earnings_date_after_keyword(text, '最新の決算報告日'),
            '2026-04-30',
        )

    def test_eps_sales_blank_dates_are_first(self):
        df = pd.DataFrame([
            {'date': '2026-05-01', 'market': 'NASDAQ', 'symbol': 'AAPL'},
            {'date': None, 'market': 'TSE', 'symbol': '4519'},
            {'date': '2026-04-30', 'market': 'NYSE', 'symbol': 'MRK'},
        ])
        sorted_df = _sort_eps_sales_targets(df)
        self.assertEqual(list(sorted_df['symbol']), ['4519', 'MRK', 'AAPL'])


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
            'eps_forecast': '2.10',
            'sales_forecast': '120.0',
            'surp_current': '+4.0',
            'surp_eps_current': '+0.30',
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
            'summary': 'first', 'eps_forecast': '2.40',
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


from earning.services.macro import attach_macro_snapshot


class AttachMacroSnapshotTests(TestCase):
    def setUp(self):
        self.stock = Stock.objects.create(symbol='AAPL', market='NASDAQ', company='Apple Inc.', industry='Tech')
        self.event = EarningsEvent.objects.create(
            stock=self.stock, fiscal_period="Q1 '26", event_date=date_cls(2026, 1, 30),
        )
        # Pre-populate all 5 indicators with one observation each, on or before event_date
        self._make_indicator('VIXCLS', date_cls(2026, 1, 30), 18.5)
        self._make_indicator('BAMLH0A0HYM2', date_cls(2026, 1, 29), 3.2)
        self._make_indicator('CBOE_SKEW', date_cls(2026, 1, 28), 140.0)
        self._make_indicator('T5YIE', date_cls(2026, 1, 30), 2.4, category='inflation')
        self._make_indicator('RUT_INDEX', date_cls(2026, 1, 27), 2100.5)

    def _make_indicator(self, series_id, obs_date, value, category='market'):
        ind, _ = Indicator.objects.get_or_create(
            fred_series_id=series_id,
            defaults={'name_ja': series_id, 'category': category},
        )
        Observation.objects.create(indicator=ind, observation_date=obs_date, value=value)

    def test_fills_all_five_columns(self):
        n = attach_macro_snapshot(self.event)
        self.assertEqual(n, 5)
        self.event.refresh_from_db()
        self.assertAlmostEqual(self.event.vix_at_event, 18.5)
        self.assertAlmostEqual(self.event.hy_spread_at_event, 3.2)
        self.assertAlmostEqual(self.event.skew_at_event, 140.0)
        self.assertAlmostEqual(self.event.t5yie_at_event, 2.4)
        self.assertAlmostEqual(self.event.rut_at_event, 2100.5)

    def test_skips_event_without_date(self):
        self.event.event_date = None
        self.event.save()
        n = attach_macro_snapshot(self.event)
        self.assertEqual(n, 0)
        self.event.refresh_from_db()
        self.assertIsNone(self.event.vix_at_event)

    def test_partial_when_some_indicators_missing(self):
        # Remove the indicator rows for SKEW and RUT to simulate missing series
        Indicator.objects.filter(fred_series_id__in=['CBOE_SKEW', 'RUT_INDEX']).delete()
        n = attach_macro_snapshot(self.event)
        self.assertEqual(n, 3)
        self.event.refresh_from_db()
        self.assertAlmostEqual(self.event.vix_at_event, 18.5)
        self.assertIsNone(self.event.skew_at_event)
        self.assertIsNone(self.event.rut_at_event)

    def test_idempotent_on_rerun(self):
        attach_macro_snapshot(self.event)
        n = attach_macro_snapshot(self.event)
        self.assertEqual(n, 5)
        self.event.refresh_from_db()
        self.assertAlmostEqual(self.event.vix_at_event, 18.5)


from unittest.mock import patch


class EarningsAttachMacroCommandTests(TestCase):
    def setUp(self):
        self.stock = Stock.objects.create(symbol='AAPL', market='NASDAQ', company='Apple Inc.', industry='Tech')
        self.recent_event = EarningsEvent.objects.create(
            stock=self.stock, fiscal_period="Q1 '26",
            event_date=date.today() - timedelta(days=10),
        )
        self.old_event = EarningsEvent.objects.create(
            stock=self.stock, fiscal_period="Q4 '24",
            event_date=date.today() - timedelta(days=500),
        )

    @patch('earning.management.commands.earnings_attach_macro.attach_macro_snapshot')
    def test_iterates_only_events_within_window(self, mock_attach):
        mock_attach.return_value = 5
        out = StringIO()
        call_command('earnings_attach_macro', '--days', '90', stdout=out)
        self.assertEqual(mock_attach.call_count, 1)
        called_event = mock_attach.call_args[0][0]
        self.assertEqual(called_event.id, self.recent_event.id)

    @patch('earning.management.commands.earnings_attach_macro.attach_macro_snapshot')
    def test_symbol_flag_filters_to_one_stock(self, mock_attach):
        other_stock = Stock.objects.create(symbol='MSFT', market='NASDAQ', company='Microsoft', industry='Tech')
        EarningsEvent.objects.create(
            stock=other_stock, fiscal_period="Q1 '26",
            event_date=date.today() - timedelta(days=5),
        )
        mock_attach.return_value = 5
        out = StringIO()
        call_command('earnings_attach_macro', '--symbol', 'AAPL', stdout=out)
        self.assertEqual(mock_attach.call_count, 1)
        called_event = mock_attach.call_args[0][0]
        self.assertEqual(called_event.stock.symbol, 'AAPL')


import math
from earning.services.features import (
    FEATURE_COLUMNS,
    MODEL_VERSION,
    _guidance_to_numeric,
    _compute_pre_short_return,
    _compute_pre_hv_20,
)


class FeatureConstantsTests(TestCase):
    def test_feature_columns_have_canonical_order(self):
        self.assertEqual(FEATURE_COLUMNS, [
            'gross_margin',
            'operating_margin',
            'relative_strength',
            'guidance_revision_numeric',
            'vix_at_event',
            'hy_spread_at_event',
            'skew_at_event',
            't5yie_at_event',
            'rut_at_event',
            'pre_short_return',
            'pre_hv_20',
        ])

    def test_model_version_is_baseline_v1(self):
        self.assertEqual(MODEL_VERSION, 'baseline-v1')


class GuidanceToNumericTests(TestCase):
    def test_up_returns_one(self):
        self.assertEqual(_guidance_to_numeric('up'), 1.0)

    def test_flat_returns_zero(self):
        self.assertEqual(_guidance_to_numeric('flat'), 0.0)

    def test_down_returns_negative_one(self):
        self.assertEqual(_guidance_to_numeric('down'), -1.0)

    def test_empty_or_unknown_returns_zero(self):
        self.assertEqual(_guidance_to_numeric(''), 0.0)
        self.assertEqual(_guidance_to_numeric(None), 0.0)
        self.assertEqual(_guidance_to_numeric('something_else'), 0.0)


class DerivedFeatureTests(TestCase):
    def setUp(self):
        from earning.models import EarningsPriceWindow
        self.stock = Stock.objects.create(symbol='AAPL', market='NASDAQ', company='Apple Inc.', industry='Tech')
        self.event = EarningsEvent.objects.create(
            stock=self.stock, fiscal_period="Q1 '26", event_date=date_cls(2026, 1, 30),
        )
        # Seed 21 daily price window rows: offset_days from -20 to 0
        for offset in range(-20, 1):
            EarningsPriceWindow.objects.create(
                event=self.event,
                trade_date=date_cls(2026, 1, 30) + timedelta(days=offset),
                offset_days=offset,
                close=100.0 + offset,
                volume=1_000_000,
            )

    def test_compute_pre_short_return_uses_t_minus_1_over_t_minus_6(self):
        # offset_days=-1 close = 99.0, offset_days=-6 close = 94.0
        # return = (99.0 / 94.0 - 1) * 100 ≈ 5.319%
        result = _compute_pre_short_return(self.event)
        self.assertAlmostEqual(result, (99.0 / 94.0 - 1) * 100, places=4)

    def test_compute_pre_short_return_returns_none_when_data_insufficient(self):
        from earning.models import EarningsPriceWindow
        EarningsPriceWindow.objects.filter(event=self.event, offset_days=-6).delete()
        self.assertIsNone(_compute_pre_short_return(self.event))

    def test_compute_pre_hv_20_returns_positive_float(self):
        result = _compute_pre_hv_20(self.event)
        self.assertIsNotNone(result)
        self.assertGreater(result, 0)

    def test_compute_pre_hv_20_returns_none_when_insufficient_data(self):
        from earning.models import EarningsPriceWindow
        EarningsPriceWindow.objects.filter(event=self.event, offset_days__lte=-11).delete()
        self.assertIsNone(_compute_pre_hv_20(self.event))


import numpy as np
from earning.services.features import build_feature_row, build_feature_matrix


class BuildFeatureRowTests(TestCase):
    def setUp(self):
        from earning.models import EarningsPriceWindow
        self.stock = Stock.objects.create(symbol='AAPL', market='NASDAQ', company='Apple Inc.', industry='Tech')
        self.event = EarningsEvent.objects.create(
            stock=self.stock, fiscal_period="Q1 '26", event_date=date_cls(2026, 1, 30),
            gross_margin=45.0, operating_margin=30.0, relative_strength=78.0,
            guidance_revision='up',
            vix_at_event=18.5, hy_spread_at_event=3.2, skew_at_event=140.0,
            t5yie_at_event=2.4, rut_at_event=2100.5,
        )
        for offset in range(-20, 1):
            EarningsPriceWindow.objects.create(
                event=self.event,
                trade_date=date_cls(2026, 1, 30) + timedelta(days=offset),
                offset_days=offset,
                close=100.0 + offset,
                volume=1_000_000,
            )

    def test_returns_dict_with_all_eleven_columns(self):
        row = build_feature_row(self.event)
        self.assertIsNotNone(row)
        self.assertEqual(set(row.keys()), set(FEATURE_COLUMNS))

    def test_returns_none_when_event_has_no_features_at_all(self):
        empty_stock = Stock.objects.create(symbol='ZZZ', market='NASDAQ', company='Empty', industry='X')
        empty_event = EarningsEvent.objects.create(
            stock=empty_stock, fiscal_period="Q1 '26", event_date=None,
        )
        # No fundamentals, no macro, no price window, no event_date → all features None
        row = build_feature_row(empty_event)
        self.assertIsNone(row)


class BuildFeatureMatrixTests(TestCase):
    def setUp(self):
        from earning.models import EarningsPriceWindow
        self.stock = Stock.objects.create(symbol='AAPL', market='NASDAQ', company='Apple Inc.', industry='Tech')

    def _make_event_with_label(self, fiscal_period, label):
        event = EarningsEvent.objects.create(
            stock=self.stock, fiscal_period=fiscal_period, event_date=date_cls(2026, 1, 30),
            gross_margin=45.0, operating_margin=30.0, relative_strength=78.0,
            guidance_revision='up',
            vix_at_event=18.5, hy_spread_at_event=3.2, skew_at_event=140.0,
            t5yie_at_event=2.4, rut_at_event=2100.5,
            reaction_close=label,
        )
        return event

    def test_skips_events_without_label(self):
        labeled = self._make_event_with_label("Q1 '26", 2.5)
        unlabeled = EarningsEvent.objects.create(
            stock=self.stock, fiscal_period="Q2 '26", event_date=date_cls(2026, 4, 30),
            gross_margin=45.0,  # has at least one feature
        )
        # unlabeled has no reaction_close → should be filtered out
        events = list(EarningsEvent.objects.all())
        X, y, names = build_feature_matrix(events)
        self.assertEqual(X.shape[0], 1)  # only the labeled one
        self.assertEqual(y[0], 2.5)

    def test_returns_correct_shapes_and_feature_names(self):
        self._make_event_with_label("Q1 '26", 2.5)
        self._make_event_with_label("Q2 '26", -1.0)
        events = list(EarningsEvent.objects.all())
        X, y, names = build_feature_matrix(events)
        self.assertEqual(X.shape, (2, 11))
        self.assertEqual(y.shape, (2,))
        self.assertEqual(names, FEATURE_COLUMNS)


from unittest.mock import patch, MagicMock
from earning.services.predict import load_model, predict_event


class PredictionPipelineTests(TestCase):
    def setUp(self):
        from earning.models import EarningsPriceWindow, EarningsPrediction
        self.stock = Stock.objects.create(symbol='AAPL', market='NASDAQ', company='Apple Inc.', industry='Tech')
        self.event = EarningsEvent.objects.create(
            stock=self.stock, fiscal_period="Q1 '26", event_date=date_cls(2026, 1, 30),
            gross_margin=45.0, operating_margin=30.0, relative_strength=78.0,
            guidance_revision='up',
            vix_at_event=18.5, hy_spread_at_event=3.2, skew_at_event=140.0,
            t5yie_at_event=2.4, rut_at_event=2100.5,
        )
        for offset in range(-20, 1):
            EarningsPriceWindow.objects.create(
                event=self.event,
                trade_date=date_cls(2026, 1, 30) + timedelta(days=offset),
                offset_days=offset,
                close=100.0 + offset,
                volume=1_000_000,
            )

    def test_predict_event_writes_prediction_to_db(self):
        from earning.models import EarningsPrediction
        mock_model = MagicMock()
        mock_model.predict.return_value = [2.45]
        result = predict_event(self.event, mock_model)
        self.assertAlmostEqual(result, 2.45)
        pred = EarningsPrediction.objects.get(event=self.event, model_version='baseline-v1')
        self.assertAlmostEqual(pred.predicted_reaction, 2.45)
        self.assertIsNone(pred.confidence)

    def test_predict_event_returns_none_when_features_unavailable(self):
        empty_stock = Stock.objects.create(symbol='ZZZ', market='NASDAQ', company='Empty', industry='X')
        empty_event = EarningsEvent.objects.create(
            stock=empty_stock, fiscal_period="Q1 '26", event_date=None,
        )
        mock_model = MagicMock()
        result = predict_event(empty_event, mock_model)
        self.assertIsNone(result)
        mock_model.predict.assert_not_called()

    @patch('earning.services.predict.MODEL_PATH')
    def test_load_model_raises_when_file_missing(self, mock_path):
        mock_path.exists.return_value = False
        with self.assertRaises(FileNotFoundError):
            load_model()


class EarningsTrainModelCommandTests(TestCase):
    def setUp(self):
        from earning.models import EarningsPriceWindow
        self.stock = Stock.objects.create(symbol='AAPL', market='NASDAQ', company='Apple Inc.', industry='Tech')
        # Create 12 events with full features + labels (just enough to clear the < 10 guard)
        for i in range(12):
            event = EarningsEvent.objects.create(
                stock=self.stock, fiscal_period=f"Q{i % 4 + 1} '{20 + i // 4}",
                event_date=date_cls(2024, 1, 1) + timedelta(days=i * 30),
                gross_margin=40.0 + i, operating_margin=20.0 + i, relative_strength=70.0 + i,
                guidance_revision='up' if i % 2 == 0 else 'flat',
                vix_at_event=15.0 + i * 0.5,
                hy_spread_at_event=3.0 + i * 0.1,
                skew_at_event=140.0 + i,
                t5yie_at_event=2.0 + i * 0.05,
                rut_at_event=2000.0 + i * 10,
                reaction_close=float(i % 5 - 2) * 0.5,  # mix of pos / neg / zero
            )
            for offset in range(-20, 1):
                EarningsPriceWindow.objects.create(
                    event=event,
                    trade_date=date_cls(2024, 1, 1) + timedelta(days=i * 30 + offset),
                    offset_days=offset,
                    close=100.0 + offset + i,
                    volume=1_000_000,
                )

    def test_train_command_completes_with_no_save(self):
        out = StringIO()
        call_command('earnings_train_model', '--no-save', stdout=out)
        output = out.getvalue()
        self.assertIn('Trained on', output)
        self.assertIn('RMSE', output)
        self.assertIn('Hit rate', output)


from unittest.mock import patch, MagicMock


class EarningsPredictCommandTests(TestCase):
    def setUp(self):
        from earning.models import EarningsPriceWindow
        self.stock = Stock.objects.create(symbol='AAPL', market='NASDAQ', company='Apple Inc.', industry='Tech')
        self.event = EarningsEvent.objects.create(
            stock=self.stock, fiscal_period="Q1 '26", event_date=date_cls(2026, 1, 30),
            gross_margin=45.0, operating_margin=30.0, relative_strength=78.0,
            guidance_revision='up',
            vix_at_event=18.5, hy_spread_at_event=3.2, skew_at_event=140.0,
            t5yie_at_event=2.4, rut_at_event=2100.5,
        )
        for offset in range(-20, 1):
            EarningsPriceWindow.objects.create(
                event=self.event,
                trade_date=date_cls(2026, 1, 30) + timedelta(days=offset),
                offset_days=offset,
                close=100.0 + offset,
                volume=1_000_000,
            )

    @patch('earning.management.commands.earnings_predict.load_model')
    def test_predict_command_writes_predictions(self, mock_load):
        from earning.models import EarningsPrediction
        mock_model = MagicMock()
        mock_model.predict.return_value = [1.5]
        mock_load.return_value = mock_model

        out = StringIO()
        call_command('earnings_predict', '--symbol', 'AAPL', stdout=out)

        pred = EarningsPrediction.objects.get(event=self.event, model_version='baseline-v1')
        self.assertAlmostEqual(pred.predicted_reaction, 1.5)
        self.assertIn('Wrote', out.getvalue())


import numpy as np
from earning.services.similarity import _zscore_normalize, _nan_safe_euclidean


class ZScoreNormalizeTests(TestCase):
    def test_constant_column_becomes_zero(self):
        m = np.array([[5.0, 1.0], [5.0, 2.0], [5.0, 3.0]])
        normalized, mean, std = _zscore_normalize(m)
        # column 0 has std==0 → all zeros
        self.assertTrue(np.all(normalized[:, 0] == 0.0))
        # column 1 normalizes to mean 0 std 1
        self.assertAlmostEqual(float(np.mean(normalized[:, 1])), 0.0, places=10)

    def test_preserves_nan_in_value(self):
        m = np.array([[1.0, 10.0], [2.0, np.nan], [3.0, 30.0]])
        normalized, mean, std = _zscore_normalize(m)
        # mean of column 1 computed over non-NaN entries only (10, 30 → 20)
        self.assertAlmostEqual(mean[1], 20.0)
        # NaN stays NaN in normalized output
        self.assertTrue(np.isnan(normalized[1, 1]))


class NanSafeEuclideanTests(TestCase):
    def test_returns_basic_distance_when_no_nan(self):
        a = np.array([0.0, 0.0])
        b = np.array([3.0, 4.0])
        # all dims valid → straight Euclidean = 5
        result = _nan_safe_euclidean(a, b)
        self.assertAlmostEqual(result, 5.0)

    def test_returns_inf_when_no_overlap(self):
        a = np.array([np.nan, 1.0])
        b = np.array([2.0, np.nan])
        self.assertEqual(_nan_safe_euclidean(a, b), float('inf'))


from earning.services.similarity import build_similarity_pool, find_similar_events


class SimilarityPoolTests(TestCase):
    def setUp(self):
        from earning.models import EarningsPriceWindow
        self.stock = Stock.objects.create(symbol='AAPL', market='NASDAQ', company='Apple Inc.', industry='Tech')

    def _make_event(self, fiscal_period, label, base=0.0):
        event = EarningsEvent.objects.create(
            stock=self.stock, fiscal_period=fiscal_period, event_date=date_cls(2026, 1, 30),
            gross_margin=45.0 + base, operating_margin=30.0 + base, relative_strength=78.0 + base,
            guidance_revision='up',
            vix_at_event=18.5 + base, hy_spread_at_event=3.2 + base, skew_at_event=140.0 + base,
            t5yie_at_event=2.4 + base, rut_at_event=2100.5 + base,
            reaction_close=label,
        )
        from earning.models import EarningsPriceWindow as PW
        for offset in range(-20, 1):
            PW.objects.create(
                event=event,
                trade_date=date_cls(2026, 1, 30) + timedelta(days=offset),
                offset_days=offset,
                close=100.0 + offset + base,
                volume=1_000_000,
            )
        return event

    def test_find_similar_returns_top_n_excluding_self(self):
        target = self._make_event("Q1 '26", 2.5, base=0.0)
        n1 = self._make_event("Q2 '26", 1.0, base=0.5)
        n2 = self._make_event("Q3 '26", -1.5, base=2.0)
        self._make_event("Q4 '26", 0.0, base=10.0)
        self._make_event("Q1 '27", 3.0, base=20.0)
        events = list(EarningsEvent.objects.all())
        pool = build_similarity_pool(events)
        result = find_similar_events(target, pool, top_n=3)
        self.assertEqual(len(result), 3)
        symbols = [r['fiscal_period'] for r in result]
        # closest should include Q2 (base 0.5) and Q3 (base 2.0); target Q1 must be excluded
        self.assertNotIn("Q1 '26", symbols)
        self.assertIn("Q2 '26", symbols)

    def test_find_similar_returns_empty_when_pool_empty(self):
        target = self._make_event("Q1 '26", 2.5, base=0.0)
        # Build pool from no events
        pool = build_similarity_pool([])
        self.assertEqual(find_similar_events(target, pool, top_n=3), [])

    def test_similarity_pool_does_not_require_numpy_at_runtime(self):
        import builtins
        from unittest.mock import patch

        target = self._make_event("Q1 '26", 2.5, base=0.0)
        self._make_event("Q2 '26", 1.0, base=0.5)
        real_import = builtins.__import__

        def block_numpy(name, *args, **kwargs):
            if name == 'numpy':
                raise ModuleNotFoundError("No module named 'numpy'")
            return real_import(name, *args, **kwargs)

        with patch('builtins.__import__', side_effect=block_numpy):
            pool = build_similarity_pool(EarningsEvent.objects.all())
            result = find_similar_events(target, pool, top_n=1)

        self.assertEqual(len(result), 1)


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


from earning.services.scenarios import MACRO_KEYS, compute_feature_ranges


class ScenariosFeatureRangesTests(TestCase):
    def test_compute_feature_ranges_uses_correct_bands(self):
        baseline = {
            'gross_margin': 45.0,  # ignored — not a macro key
            'vix_at_event': 18.5,
            'hy_spread_at_event': 3.2,
            'skew_at_event': 140.0,
            't5yie_at_event': 2.4,
            'rut_at_event': 2100.0,
        }
        ranges = compute_feature_ranges(baseline)
        # Only macro keys present
        self.assertEqual(set(ranges.keys()), set(MACRO_KEYS))
        # vix / hy / rut: ±50% / ±50% / ±20%
        self.assertAlmostEqual(ranges['vix_at_event'][0], 18.5 * 0.5)
        self.assertAlmostEqual(ranges['vix_at_event'][1], 18.5 * 1.5)
        self.assertAlmostEqual(ranges['hy_spread_at_event'][0], 3.2 * 0.5)
        self.assertAlmostEqual(ranges['hy_spread_at_event'][1], 3.2 * 1.5)
        self.assertAlmostEqual(ranges['rut_at_event'][0], 2100.0 * 0.8)
        self.assertAlmostEqual(ranges['rut_at_event'][1], 2100.0 * 1.2)
        # skew: ±20%
        self.assertAlmostEqual(ranges['skew_at_event'][0], 140.0 * 0.8)
        self.assertAlmostEqual(ranges['skew_at_event'][1], 140.0 * 1.2)
        # t5yie: absolute ±0.5
        self.assertAlmostEqual(ranges['t5yie_at_event'][0], 2.4 - 0.5)
        self.assertAlmostEqual(ranges['t5yie_at_event'][1], 2.4 + 0.5)

    def test_compute_feature_ranges_skips_none_values(self):
        baseline = {
            'vix_at_event': 18.5,
            'hy_spread_at_event': None,
            'skew_at_event': 140.0,
            't5yie_at_event': None,
            'rut_at_event': 2100.0,
        }
        ranges = compute_feature_ranges(baseline)
        # Only macro keys with non-None baseline are included
        self.assertEqual(set(ranges.keys()), {'vix_at_event', 'skew_at_event', 'rut_at_event'})


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
            eps_forecast='3.4', sales_forecast='130',
            surp_current='6%', surp_eps_current='5%',
            summary='upcoming summary',
        )
        EarningsEvent.objects.create(
            stock=past_stock, fiscal_period='Q4',
            event_date=today - timedelta(days=1),
            fundamental='up', direction='flat', sentiment='down', risk_value=82,
            eps_forecast='3.4', sales_forecast='130',
            surp_current='6%', surp_eps_current='5%',
            summary='completed summary',
        )
        from earning.models import EarningsPrediction
        future_event = EarningsEvent.objects.get(stock=future_stock)
        past_event = EarningsEvent.objects.get(stock=past_stock)
        past_event.reaction_close = 1.5
        past_event.save(update_fields=['reaction_close'])
        EarningsPrediction.objects.create(event=future_event, predicted_reaction=2.5, model_version='baseline-v1')
        EarningsPrediction.objects.create(event=past_event, predicted_reaction=0.5, model_version='baseline-v1')

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def test_index_renders_only_upcoming_groups_initially(self):
        response = self.client.get(reverse('earning:index'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'upcoming summary')
        self.assertNotContains(response, 'completed summary')
        self.assertContains(response, '決算済みを表示（1日分）')
        self.assertNotContains(response, 'cdn.jsdelivr.net')
        self.assertContains(response, '/static/dashboard/vendor/bootstrap-icons/bootstrap-icons.css')

    def test_index_does_not_enrich_completed_rows_initially(self):
        from unittest.mock import patch
        from earning import views

        touched_symbols = []
        original_enrich_item = views.enrich_item

        def record_enrich(item, *args, **kwargs):
            touched_symbols.append(item.get('symbol'))
            return original_enrich_item(item, *args, **kwargs)

        with patch('earning.views.enrich_item', side_effect=record_enrich):
            response = self.client.get(reverse('earning:index'))

        self.assertEqual(response.status_code, 200)
        self.assertIn('FUT', touched_symbols)
        self.assertNotIn('PST', touched_symbols)

    def test_completed_endpoint_returns_only_completed_groups(self):
        response = self.client.get(reverse('earning:completed'))
        content = response.content.decode('utf-8')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Past Corp', content)
        self.assertNotIn('Future Corp', content)
        self.assertIn('data-period="completed"', content)

    def test_card_renders_predicted_reaction_when_available(self):
        response = self.client.get(reverse('earning:index'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '事前予測')
        self.assertContains(response, '+2.5%')

    def test_card_renders_deviation_when_both_present(self):
        response = self.client.get(reverse('earning:completed'))
        content = response.content.decode('utf-8')
        self.assertEqual(response.status_code, 200)
        self.assertIn('乖離', content)
        # past_event: actual=1.5, predicted=0.5, deviation=+1.0
        self.assertIn('+1.0', content)

    def test_card_does_not_render_section_when_no_prediction(self):
        from earning.models import EarningsPrediction
        EarningsPrediction.objects.all().delete()
        cache.clear()
        response = self.client.get(reverse('earning:index'))
        self.assertNotContains(response, '事前予測')

    def test_card_embeds_baseline_features_json_when_complete(self):
        from earning.models import EarningsPriceWindow
        # Need full features for has_whatif: macro + reaction_close + price_window
        future_event = EarningsEvent.objects.get(stock__symbol='FUT')
        future_event.gross_margin = 45.0
        future_event.operating_margin = 30.0
        future_event.relative_strength = 78.0
        future_event.guidance_revision = 'up'
        future_event.vix_at_event = 18.5
        future_event.hy_spread_at_event = 3.2
        future_event.skew_at_event = 140.0
        future_event.t5yie_at_event = 2.4
        future_event.rut_at_event = 2100.0
        future_event.save()
        for offset in range(-20, 1):
            EarningsPriceWindow.objects.create(
                event=future_event,
                trade_date=future_event.event_date + timedelta(days=offset),
                offset_days=offset,
                close=100.0 + offset,
                volume=1_000_000,
            )
        cache.clear()
        response = self.client.get(reverse('earning:index'))
        content = response.content.decode('utf-8')
        self.assertIn('data-whatif-baseline', content)
        self.assertIn('"vix_at_event": 18.5', content)

    def test_card_omits_whatif_when_macro_missing(self):
        # Existing future_event has no macro snapshot; has_whatif must be False
        cache.clear()
        response = self.client.get(reverse('earning:index'))
        content = response.content.decode('utf-8')
        self.assertNotIn('data-whatif-baseline', content)


from earning.services.lgb_walker import predict_from_json, _walk_tree


class LgbWalkerTests(TestCase):
    def _single_leaf_tree(self, leaf_value):
        return {
            'shrinkage': 1.0,
            'root': {'leaf_value': leaf_value},
        }

    def _stump(self, split_feature, threshold, left_leaf, right_leaf, decision_type='<='):
        return {
            'shrinkage': 1.0,
            'root': {
                'split_feature': split_feature,
                'threshold': threshold,
                'decision_type': decision_type,
                'default_left': True,
                'left_child': {'leaf_value': left_leaf},
                'right_child': {'leaf_value': right_leaf},
            },
        }

    def test_walk_tree_returns_leaf_for_single_leaf(self):
        node = {'leaf_value': 1.5}
        self.assertAlmostEqual(_walk_tree(node, [0.0] * 11), 1.5)

    def test_walk_tree_follows_threshold_branch(self):
        tree = self._stump(0, 1.0, 0.5, 2.5)
        self.assertAlmostEqual(_walk_tree(tree['root'], [0.5] + [0.0]*10), 0.5)
        self.assertAlmostEqual(_walk_tree(tree['root'], [1.5] + [0.0]*10), 2.5)

    def test_walk_tree_follows_default_left_on_nan(self):
        tree = self._stump(0, 1.0, 0.5, 2.5)
        self.assertAlmostEqual(_walk_tree(tree['root'], [float('nan')] + [0.0]*10), 0.5)

    def test_predict_from_json_sums_trees_plus_init_score(self):
        model = {
            'feature_names': [f'f{i}' for i in range(11)],
            'init_score': 0.7,
            'trees': [
                self._single_leaf_tree(1.0),
                self._single_leaf_tree(2.0),
                self._stump(3, 0.5, 0.3, -0.2),
            ],
        }
        self.assertAlmostEqual(predict_from_json([0.0]*11, model), 4.0)


import json as _json
import tempfile as _tempfile
from pathlib import Path as _Path
from unittest.mock import patch as _patch


class ExportModelCommandTests(TestCase):
    def test_command_writes_json_with_round_trip_match(self):
        # Build a tiny LightGBM model in-process, save it, then export.
        import lightgbm as lgb
        import numpy as np

        rng = np.random.default_rng(42)
        X = rng.standard_normal((40, 11)).astype(float)
        y = rng.standard_normal(40).astype(float)
        train_set = lgb.Dataset(X, label=y)
        booster = lgb.train(
            {'objective': 'regression', 'num_leaves': 4, 'min_data_in_leaf': 2,
             'learning_rate': 0.1, 'verbose': -1},
            train_set,
            num_boost_round=10,
        )
        with _tempfile.TemporaryDirectory() as td:
            model_lgb = _Path(td) / 'baseline-v1.lgb'
            model_json = _Path(td) / 'baseline-v1.json'
            booster.save_model(str(model_lgb))

            with _patch('earning.management.commands.earnings_export_model_json.MODEL_PATH', model_lgb):
                with _patch('earning.management.commands.earnings_export_model_json.JSON_OUTPUT_PATH', model_json):
                    call_command('earnings_export_model_json', stdout=StringIO())

            self.assertTrue(model_json.exists())
            payload = _json.loads(model_json.read_text(encoding='utf-8'))
            self.assertIn('feature_names', payload)
            self.assertIn('init_score', payload)
            self.assertIn('trees', payload)
            self.assertEqual(len(payload['feature_names']), 11)

            # Round trip: predict_from_json on a sample row matches Booster.predict
            from earning.services.lgb_walker import predict_from_json
            sample = X[0].tolist()
            expected = float(booster.predict(np.array([sample]))[0])
            actual = predict_from_json(sample, payload)
            self.assertAlmostEqual(actual, expected, places=6)

    def test_command_round_trips_for_multiple_rows(self):
        import lightgbm as lgb
        import numpy as np

        rng = np.random.default_rng(7)
        X = rng.standard_normal((30, 11)).astype(float)
        y = rng.standard_normal(30).astype(float)
        train_set = lgb.Dataset(X, label=y)
        booster = lgb.train(
            {'objective': 'regression', 'num_leaves': 4, 'min_data_in_leaf': 2,
             'learning_rate': 0.1, 'verbose': -1},
            train_set,
            num_boost_round=15,
        )
        with _tempfile.TemporaryDirectory() as td:
            model_lgb = _Path(td) / 'baseline-v1.lgb'
            model_json = _Path(td) / 'baseline-v1.json'
            booster.save_model(str(model_lgb))
            with _patch('earning.management.commands.earnings_export_model_json.MODEL_PATH', model_lgb):
                with _patch('earning.management.commands.earnings_export_model_json.JSON_OUTPUT_PATH', model_json):
                    call_command('earnings_export_model_json', stdout=StringIO())
            payload = _json.loads(model_json.read_text(encoding='utf-8'))

            from earning.services.lgb_walker import predict_from_json
            preds_walker = [predict_from_json(X[i].tolist(), payload) for i in range(len(X))]
            preds_booster = booster.predict(X).tolist()
            for w, b in zip(preds_walker, preds_booster):
                self.assertAlmostEqual(w, b, places=6)
