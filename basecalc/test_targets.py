from django.test import SimpleTestCase

from .services.decision_context import _top_lines
from .targets import build_targets
from .views import _practical_lines_match_latest_price


class BasecalcTargetSelectionTests(SimpleTestCase):
    def test_structural_resistance_ranks_before_atr_projection(self):
        features = {
            "price": 40000,
            "atr14": 300,
            "previous_high": 40500,
            "recent_high": 40600,
            "high_5d": 40700,
            "previous_low": 39600,
            "recent_low": 39400,
            "low_5d": 39300,
            "vwap": 39950,
            "ema20": 39880,
            "indicator_validity": {"pivot": True},
            "pivots": {"r1": 40510, "s1": 39520},
            "readiness_level": "ready",
        }

        targets = build_targets(
            features,
            {
                "case_count": 35,
                "is_statistically_valid": True,
                "target_t1_hit_rate": 0.52,
            },
        )

        first_upside = targets["upside"][0]
        self.assertNotEqual(first_upside["source"], "atr_1")
        self.assertNotEqual(first_upside.get("line_role"), "atr_projection")
        self.assertIn(
            first_upside.get("line_role"),
            {"structural", "psychological", "similar_projection"},
        )

    def test_confluent_levels_keep_multiple_sources(self):
        features = {
            "price": 40000,
            "atr14": 300,
            "previous_high": 40500,
            "recent_high": 40520,
            "previous_low": 39600,
            "recent_low": 39580,
            "indicator_validity": {"pivot": True},
            "pivots": {"r1": 40510, "s1": 39590},
            "readiness_level": "ready",
        }

        targets = build_targets(
            features,
            {
                "case_count": 40,
                "is_statistically_valid": True,
                "target_t1_hit_rate": 0.50,
            },
        )

        first_upside = targets["upside"][0]
        self.assertGreaterEqual(first_upside.get("confluence_count") or 1, 2)
        self.assertGreaterEqual(len(first_upside.get("sources") or []), 2)

    def test_probability_uses_final_rank_after_sort(self):
        features = {
            "price": 40000,
            "atr14": 300,
            "previous_high": 40500,
            "recent_high": 40600,
            "previous_low": 39600,
            "recent_low": 39400,
            "indicator_validity": {"pivot": True},
            "pivots": {"r1": 40510, "s1": 39520},
            "readiness_level": "ready",
        }

        targets = build_targets(
            features,
            {
                "case_count": 35,
                "is_statistically_valid": True,
                "target_t1_hit_rate": 0.60,
            },
        )

        labels = [row["label"] for row in targets["upside"][:3]]
        self.assertEqual(labels, ["T1", "T2", "T3"])
        self.assertTrue(
            all("probability_display" in row for row in targets["upside"][:3])
        )


class BasecalcPracticalLineTests(SimpleTestCase):
    def test_practical_line_cache_does_not_require_near_levels(self):
        world_model = {
            "practical_lines": {
                "target_model_version": "targets_v2",
                "current_price": 40000,
                "upside_resistance": 40500,
                "downside_support": 39500,
                "near_upside": None,
                "near_downside": None,
            }
        }

        self.assertTrue(_practical_lines_match_latest_price(world_model, 40000))

    def test_top_lines_does_not_restore_lines_when_contract_error(self):
        world_model = {
            "price": 40000,
            "output_contract": {"contract_status": "error"},
            "practical_lines": {
                "current_price": 40000,
                "upside_resistance": 40500,
                "downside_support": 39500,
            },
        }

        result = _top_lines(world_model, {"price": 40000})

        self.assertIsNone(result["upside_resistance"])
        self.assertIsNone(result["downside_support"])
        self.assertIsNone(result["first_target"])
        self.assertEqual(result["line_status"], "stopped")
