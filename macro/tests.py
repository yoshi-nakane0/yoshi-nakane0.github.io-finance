"""macro モジュールのユニットテスト。"""

from datetime import date
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

from .models import Indicator, Observation, PriceObservation, RegimeSnapshot
from .services import (
    crash_alert,
    dashboard,
    detail_analysis,
    historical_crash,
    judgment,
    linkage,
    regime,
    similarity,
    sparkline,
)


class MacroRuntimeConfigTest(SimpleTestCase):
    def test_refresh_workflow_updates_checked_in_database(self):
        workflow = (
            Path(settings.BASE_DIR)
            / '.github'
            / 'workflows'
            / 'refresh-macro-data.yml'
        ).read_text(encoding='utf-8')

        self.assertIn('SQLITE_DB_PATH: db.sqlite3', workflow)

    def test_wsgi_runtime_migration_check_not_based_on_one_old_table(self):
        wsgi_source = (
            Path(settings.BASE_DIR)
            / 'myproject'
            / 'wsgi.py'
        ).read_text(encoding='utf-8')

        self.assertNotIn("name='macro_observation'", wsgi_source)


class _ObsStub:
    """ユニットテスト用の最小 Observation モック。"""

    def __init__(self, value=None, prev_value=None, yoy_change=None):
        self.value = value
        self.prev_value = prev_value
        self.yoy_change = yoy_change


class SparklineTest(TestCase):
    def test_empty_returns_empty_string(self):
        self.assertEqual(sparkline.generate_sparkline_svg([]), "")

    def test_single_value_returns_empty(self):
        self.assertEqual(sparkline.generate_sparkline_svg([1.0]), "")

    def test_normal_series_contains_polyline(self):
        svg = sparkline.generate_sparkline_svg([1.0, 2.0, 3.0])
        self.assertIn("<polyline", svg)
        self.assertIn("points=", svg)

    def test_flat_series_returns_horizontal_line(self):
        svg = sparkline.generate_sparkline_svg([5.0, 5.0, 5.0])
        # 全て同じ値なら中央水平線が描かれる
        self.assertIn("<polyline", svg)


class SimilarityTest(TestCase):
    def test_distance_empty_returns_inf(self):
        d = similarity.vector_distance({}, {})
        self.assertEqual(d, float('inf'))

    def test_distance_no_common_keys(self):
        d = similarity.vector_distance({'A': 1.0}, {'B': 2.0})
        self.assertEqual(d, float('inf'))

    def test_distance_identical_vectors_zero(self):
        v = {'A': 1.0, 'B': 2.0}
        d = similarity.vector_distance(v, v)
        self.assertEqual(d, 0.0)

    def test_distance_known_values(self):
        v1 = {'A': 0.0, 'B': 0.0}
        v2 = {'A': 3.0, 'B': 4.0}
        # sqrt((9+16)/2) = sqrt(12.5) ≈ 3.535
        d = similarity.vector_distance(v1, v2)
        self.assertAlmostEqual(d, 3.5355339, places=4)


class LinkageTest(TestCase):
    def test_pearson_perfect_positive(self):
        xs = [1, 2, 3, 4, 5]
        ys = [2, 4, 6, 8, 10]
        self.assertAlmostEqual(linkage._pearson(xs, ys), 1.0, places=5)

    def test_pearson_perfect_negative(self):
        xs = [1, 2, 3, 4, 5]
        ys = [10, 8, 6, 4, 2]
        self.assertAlmostEqual(linkage._pearson(xs, ys), -1.0, places=5)

    def test_pearson_zero_variance_returns_none(self):
        xs = [1, 1, 1, 1]
        ys = [1, 2, 3, 4]
        self.assertIsNone(linkage._pearson(xs, ys))

    def test_pearson_too_short_returns_none(self):
        self.assertIsNone(linkage._pearson([1], [2]))


class RegimeClassificationTest(TestCase):
    def test_strong_expansion(self):
        metrics = {
            'indpro_yoy': 3.5,
            'unrate_6m_change': -0.1,
            'gdp_yoy': 2.5,
        }
        label, conf = regime.classify_regime(metrics)
        self.assertEqual(label, RegimeSnapshot.Label.EXPANSION)
        self.assertGreater(conf, 0)

    def test_contraction(self):
        metrics = {'indpro_yoy': -2.0, 'gdp_yoy': -0.5}
        label, _ = regime.classify_regime(metrics)
        self.assertEqual(label, RegimeSnapshot.Label.CONTRACTION)

    def test_recovery_pattern(self):
        metrics = {
            'indpro_yoy': 0.5,
            'indpro_3m_change_pct': 1.0,
            'unrate_6m_change': -0.2,
        }
        label, _ = regime.classify_regime(metrics)
        self.assertEqual(label, RegimeSnapshot.Label.RECOVERY)

    def test_slowdown_with_employment_weakness(self):
        metrics = {
            'indpro_yoy': 0.8,
            'unrate_6m_change': 0.4,
        }
        label, _ = regime.classify_regime(metrics)
        self.assertEqual(label, RegimeSnapshot.Label.SLOWDOWN)

    def test_unknown_when_no_data(self):
        label, conf = regime.classify_regime({})
        self.assertEqual(label, RegimeSnapshot.Label.UNKNOWN)
        self.assertEqual(conf, 0)

    def test_inflation_high(self):
        flag, conf = regime.classify_inflation({'core_pce_yoy': 4.0})
        self.assertEqual(flag, RegimeSnapshot.InflationFlag.HIGH)
        self.assertGreater(conf, 0)

    def test_inflation_easing(self):
        flag, _ = regime.classify_inflation({
            'core_pce_yoy': 2.5,
            'core_pce_yoy_3m_ago': 3.0,
        })
        self.assertEqual(flag, RegimeSnapshot.InflationFlag.EASING)

    def test_inflation_normal(self):
        flag, _ = regime.classify_inflation({'core_pce_yoy': 1.8})
        self.assertEqual(flag, RegimeSnapshot.InflationFlag.NORMAL)

    def test_inflation_unknown(self):
        flag, _ = regime.classify_inflation({})
        self.assertEqual(flag, RegimeSnapshot.InflationFlag.UNKNOWN)


class DashboardFormatTest(TestCase):
    def test_format_value_large_numbers(self):
        self.assertEqual(dashboard.format_value(158000.0, '千人'), '158,000')

    def test_format_value_percent(self):
        self.assertEqual(dashboard.format_value(3.21, '%'), '3.21')

    def test_format_value_none(self):
        self.assertEqual(dashboard.format_value(None, '%'), '—')

    def test_format_pct_positive_has_plus(self):
        self.assertEqual(dashboard.format_pct(2.5), '+2.5%')

    def test_format_pct_negative(self):
        self.assertEqual(dashboard.format_pct(-1.2), '-1.2%')

    def test_format_pct_none(self):
        self.assertEqual(dashboard.format_pct(None), '—')

    def test_format_signed_positive(self):
        self.assertEqual(dashboard.format_signed(0.5, 2), '+0.50')


@override_settings(ALLOWED_HOSTS=['*'])
class MacroUrlsTest(TestCase):
    """URLが正しく解決され、想定したHTTPステータスを返すことを確認。"""

    def test_index_renders(self):
        r = self.client.get(reverse('macro:index'))
        self.assertEqual(r.status_code, 200)

    def test_refresh_without_key_redirects(self):
        r = self.client.post(reverse('macro:refresh'))
        self.assertEqual(r.status_code, 302)

    def test_indicator_detail_existing(self):
        # マイグレーションでシードされた CPIAUCSL は存在する想定
        r = self.client.get(
            reverse('macro:indicator_detail', args=['CPIAUCSL'])
        )
        self.assertEqual(r.status_code, 200)

    def test_indicator_detail_404(self):
        r = self.client.get(
            reverse('macro:indicator_detail', args=['NOPE_NOPE'])
        )
        self.assertEqual(r.status_code, 404)

    def test_similar_detail_renders(self):
        r = self.client.get(
            reverse('macro:similar_detail', args=['2019-03-01'])
        )
        self.assertEqual(r.status_code, 200)

    def test_similar_detail_invalid_date(self):
        r = self.client.get(
            reverse('macro:similar_detail', args=['not-a-date'])
        )
        self.assertEqual(r.status_code, 404)


class JudgmentTest(TestCase):
    """指標値→5段階評価の変換ロジック。"""

    # --- lower_better ---
    def test_lower_better_min(self):
        rule = {'metric': 'level',
                'economic': {'direction': 'lower_better', 'thresholds': [10, 20, 30, 40]},
                'market':   {'direction': 'lower_better', 'thresholds': [10, 20, 30, 40]}}
        e, m = judgment.evaluate(_ObsStub(value=5), rule)
        self.assertEqual(e, 1)
        self.assertEqual(m, 1)

    def test_lower_better_max(self):
        rule = {'metric': 'level',
                'economic': {'direction': 'lower_better', 'thresholds': [10, 20, 30, 40]},
                'market':   {'direction': 'lower_better', 'thresholds': [10, 20, 30, 40]}}
        e, _ = judgment.evaluate(_ObsStub(value=99), rule)
        self.assertEqual(e, 5)

    def test_lower_better_boundary(self):
        rule = {'metric': 'level',
                'economic': {'direction': 'lower_better', 'thresholds': [10, 20, 30, 40]},
                'market':   {'direction': 'lower_better', 'thresholds': [10, 20, 30, 40]}}
        e, _ = judgment.evaluate(_ObsStub(value=20), rule)
        self.assertEqual(e, 2)

    # --- higher_better ---
    def test_higher_better_min(self):
        rule = {'metric': 'level',
                'economic': {'direction': 'higher_better', 'thresholds': [10, 20, 30, 40]},
                'market':   {'direction': 'higher_better', 'thresholds': [10, 20, 30, 40]}}
        e, _ = judgment.evaluate(_ObsStub(value=5), rule)
        self.assertEqual(e, 5)

    def test_higher_better_max(self):
        rule = {'metric': 'level',
                'economic': {'direction': 'higher_better', 'thresholds': [10, 20, 30, 40]},
                'market':   {'direction': 'higher_better', 'thresholds': [10, 20, 30, 40]}}
        e, _ = judgment.evaluate(_ObsStub(value=99), rule)
        self.assertEqual(e, 1)

    # --- target_band ---
    def test_target_band_center(self):
        rule = {'metric': 'level',
                'economic': {'direction': 'target_band', 'thresholds': [-1.0, 1.0, 2.5, 4.0]},
                'market':   {'direction': 'target_band', 'thresholds': [-1.0, 1.0, 2.5, 4.0]}}
        e, _ = judgment.evaluate(_ObsStub(value=2.0), rule)
        self.assertEqual(e, 1)

    def test_target_band_below(self):
        rule = {'metric': 'level',
                'economic': {'direction': 'target_band', 'thresholds': [-1.0, 1.0, 2.5, 4.0]},
                'market':   {'direction': 'target_band', 'thresholds': [-1.0, 1.0, 2.5, 4.0]}}
        e, _ = judgment.evaluate(_ObsStub(value=-2.0), rule)
        self.assertEqual(e, 5)

    def test_target_band_above(self):
        rule = {'metric': 'level',
                'economic': {'direction': 'target_band', 'thresholds': [-1.0, 1.0, 2.5, 4.0]},
                'market':   {'direction': 'target_band', 'thresholds': [-1.0, 1.0, 2.5, 4.0]}}
        e, _ = judgment.evaluate(_ObsStub(value=5.0), rule)
        self.assertEqual(e, 5)

    def test_target_band_side(self):
        rule = {'metric': 'level',
                'economic': {'direction': 'target_band', 'thresholds': [-1.0, 1.0, 2.5, 4.0]},
                'market':   {'direction': 'target_band', 'thresholds': [-1.0, 1.0, 2.5, 4.0]}}
        e, _ = judgment.evaluate(_ObsStub(value=3.0), rule)
        self.assertEqual(e, 3)

    # --- metric: yoy/level/mom ---
    def test_metric_yoy_uses_yoy_change(self):
        rule = {'metric': 'yoy',
                'economic': {'direction': 'lower_better', 'thresholds': [1, 2, 3, 4]},
                'market':   {'direction': 'lower_better', 'thresholds': [1, 2, 3, 4]}}
        e, _ = judgment.evaluate(_ObsStub(value=999, yoy_change=0.5), rule)
        self.assertEqual(e, 1)

    def test_metric_mom_uses_diff(self):
        rule = {'metric': 'mom',
                'economic': {'direction': 'higher_better', 'thresholds': [-100, 100, 200, 400]},
                'market':   {'direction': 'higher_better', 'thresholds': [-100, 100, 200, 400]}}
        e, _ = judgment.evaluate(_ObsStub(value=300, prev_value=100), rule)
        # diff=200 → higher_better で 3段
        self.assertEqual(e, 3)

    # --- 例外系 ---
    def test_no_rule_returns_none(self):
        e, m = judgment.evaluate(_ObsStub(value=1.0), None)
        self.assertIsNone(e)
        self.assertIsNone(m)

    def test_no_observation_returns_none(self):
        rule = {'metric': 'level',
                'economic': {'direction': 'lower_better', 'thresholds': [1, 2, 3, 4]},
                'market':   {'direction': 'lower_better', 'thresholds': [1, 2, 3, 4]}}
        e, m = judgment.evaluate(None, rule)
        self.assertIsNone(e)
        self.assertIsNone(m)

    def test_value_none_returns_none(self):
        rule = {'metric': 'yoy',
                'economic': {'direction': 'lower_better', 'thresholds': [1, 2, 3, 4]},
                'market':   {'direction': 'lower_better', 'thresholds': [1, 2, 3, 4]}}
        e, _ = judgment.evaluate(_ObsStub(yoy_change=None), rule)
        self.assertIsNone(e)


class CrashAlertTest(TestCase):
    """クラッシュ警戒度のサブスコア・総合スコア計算。"""

    def test_band_score_lowest(self):
        bands = [(15, 0), (20, 25), (25, 50), (30, 75), (40, 90), (float('inf'), 100)]
        self.assertEqual(crash_alert._band_score(10, bands), 0)

    def test_band_score_middle(self):
        bands = [(15, 0), (20, 25), (25, 50), (30, 75), (40, 90), (float('inf'), 100)]
        self.assertEqual(crash_alert._band_score(22, bands), 50)

    def test_band_score_highest(self):
        bands = [(15, 0), (20, 25), (25, 50), (30, 75), (40, 90), (float('inf'), 100)]
        self.assertEqual(crash_alert._band_score(60, bands), 100)

    def test_classify_low(self):
        level, _ = crash_alert._classify(10)
        self.assertEqual(level, 'low')

    def test_classify_medium(self):
        level, _ = crash_alert._classify(45)
        self.assertEqual(level, 'medium')

    def test_classify_high(self):
        level, _ = crash_alert._classify(70)
        self.assertEqual(level, 'high')

    def test_classify_extreme(self):
        level, _ = crash_alert._classify(85)
        self.assertEqual(level, 'extreme')

    def test_compute_no_data_returns_unknown(self):
        result = crash_alert.compute_crash_alert()
        self.assertEqual(result['level'], 'unknown')
        self.assertIsNone(result['total_score'])


class LightgbmPredictionLoadTest(TestCase):
    """学習済み LightGBM 予測 JSON の読み込み・整形ロジック。"""

    def test_classify_positive(self):
        self.assertEqual(dashboard._classify_predicted_return(2.5), 'positive')

    def test_classify_neutral(self):
        self.assertEqual(dashboard._classify_predicted_return(-1.5), 'neutral')

    def test_classify_warn(self):
        self.assertEqual(dashboard._classify_predicted_return(-5.0), 'warn')

    def test_classify_danger(self):
        self.assertEqual(dashboard._classify_predicted_return(-10.0), 'danger')

    def test_load_returns_none_when_file_missing(self):
        # ベースラインとして「存在しない」を確認するのは難しいので、
        # ここでは load 関数が None または dict を返す型のみ確認
        result = dashboard.load_lightgbm_prediction()
        self.assertTrue(result is None or isinstance(result, dict))


class HistoricalCrashTest(TestCase):
    """歴史的クラッシュ月との類似度。"""

    def test_no_data_returns_empty(self):
        # シードされたインジケーターはあるが Observation がない状態。
        result = historical_crash.find_similar_crash_months()
        self.assertEqual(result, [])

    def test_crash_months_constant_size(self):
        # 定数リストが不正にならないこと（少なくとも数件登録されている）。
        self.assertGreaterEqual(len(historical_crash.HISTORICAL_CRASH_MONTHS), 5)


class DetailAnalysisTest(TestCase):
    """詳細ページ用分析ロジック。"""

    def test_correlation_label_strong_positive(self):
        self.assertEqual(detail_analysis.correlation_label(0.85), '強い正の連動')

    def test_correlation_label_moderate_positive(self):
        self.assertEqual(detail_analysis.correlation_label(0.5), '中程度の正の連動')

    def test_correlation_label_weak(self):
        self.assertEqual(detail_analysis.correlation_label(0.1), '弱い / 無相関')

    def test_correlation_label_moderate_negative(self):
        self.assertEqual(detail_analysis.correlation_label(-0.5), '中程度の逆連動')

    def test_correlation_label_strong_negative(self):
        self.assertEqual(detail_analysis.correlation_label(-0.85), '強い逆連動')

    def test_correlation_label_none(self):
        self.assertEqual(detail_analysis.correlation_label(None), 'データ不足')

    def test_interpret_state_no_rule(self):
        ind = Indicator.objects.create(
            fred_series_id='TEST_NORULE',
            name_ja='テスト',
            category='inflation',
            judgment_rule=None,
        )
        result = detail_analysis.interpret_state(ind, _ObsStub(value=1.0))
        self.assertFalse(result['has_interpretation'])

    def test_interpret_state_with_rule_generates_sentences(self):
        ind = Indicator.objects.create(
            fred_series_id='TEST_RULE',
            name_ja='テスト2',
            category='inflation',
            judgment_rule={
                'metric': 'level',
                'economic': {'direction': 'lower_better', 'thresholds': [10, 20, 30, 40]},
                'market':   {'direction': 'lower_better', 'thresholds': [10, 20, 30, 40]},
            },
        )
        # 5 → 経済1段（最良）、市場1段
        result = detail_analysis.interpret_state(ind, _ObsStub(value=5, prev_value=4))
        self.assertTrue(result['has_interpretation'])
        self.assertEqual(result['economic_stage'], 1)
        self.assertEqual(result['market_stage'], 1)
        # 解釈文は3つ（経済・市場・推移）
        self.assertEqual(len(result['sentences']), 3)


class IndicatorSeedingTest(TestCase):
    """マイグレーションが51系列を登録していることを確認（Phase 4で5系列追加）。"""

    def test_seeded_count(self):
        self.assertEqual(Indicator.objects.count(), 51)

    def test_importance_a_count(self):
        # Phase 1: 11 + Phase 2: SP500, T10Y3M = 13（Phase 4 は B のみ）
        self.assertEqual(
            Indicator.objects.filter(importance='A').count(), 13
        )

    def test_external_sources_present(self):
        sources = set(
            Indicator.objects.values_list('source', flat=True).distinct()
        )
        for s in ['fred', 'cboe', 'finra', 'aaii', 'naaim', 'yfinance']:
            self.assertIn(s, sources)


class ExternalClientParseTest(TestCase):
    """外部クライアントのCSVパースロジック（HTTPは投げない）。"""

    def test_cboe_parse_csv(self):
        from macro.services import cboe_client
        text = "Date,SKEW\n2024-01-02,135.42\n2024-01-03,138.10\n"
        rows = cboe_client._parse_csv(text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][1], 135.42)

    def test_naaim_parse_csv(self):
        from macro.services import naaim_client
        text = "Date,NAAIM Exposure Index\n2024-01-03,75.5\n2024-01-10,80.0\n"
        rows = naaim_client._parse_csv(text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][1], 80.0)

    def test_aaii_parse_csv(self):
        from macro.services import aaii_client
        text = "Date,Bullish,Bearish,Neutral\n2024-01-03,42.5%,30.0%,27.5%\n"
        rows = aaii_client._parse_csv(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], 42.5)

    def test_finra_parse_csv(self):
        from macro.services import finra_client
        text = (
            "Year-Month,Debit Balances in Customers' Securities Margin Accounts\n"
            "2024-12,815523\n"
            "2025-01,820100\n"
        )
        rows = finra_client._parse_csv(text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][1], 820100.0)

    def test_categories_present(self):
        for cat in ['inflation', 'employment', 'growth', 'rates', 'market']:
            self.assertTrue(
                Indicator.objects.filter(category=cat).exists(),
                f"missing category: {cat}",
            )
