"""macro モジュールのユニットテスト。"""

from datetime import date

from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

from .models import Indicator, Observation, PriceObservation, RegimeSnapshot
from .services import dashboard, judgment, linkage, regime, similarity, sparkline


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


class IndicatorSeedingTest(TestCase):
    """マイグレーションが23系列を登録していることを確認。"""

    def test_seeded_count(self):
        self.assertEqual(Indicator.objects.count(), 23)

    def test_importance_a_count(self):
        self.assertEqual(
            Indicator.objects.filter(importance='A').count(), 11
        )

    def test_categories_present(self):
        for cat in ['inflation', 'employment', 'growth', 'rates', 'market']:
            self.assertTrue(
                Indicator.objects.filter(category=cat).exists(),
                f"missing category: {cat}",
            )
