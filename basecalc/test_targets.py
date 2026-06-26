from django.test import SimpleTestCase

from .services.decision_context import _top_lines
from .targets import build_targets
from .views import _first_level_row, _practical_lines_match_latest_price


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
            {"structural", "similar_projection"},
        )

    def test_round_number_levels_are_not_used_as_target_or_near_level_sources(self):
        features = {
            "price": 69570,
            "atr14": 3500,
            "previous_high": 72720,
            "previous_low": 68920,
            "ema20": 69790,
            "vwap": 50140,
            "indicator_validity": {"pivot": True},
            "pivots": {"r1": 69890, "s1": 68800},
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

        rows = (
            targets["upside"]
            + targets["downside"]
            + targets["near_levels"]["upside"]
            + targets["near_levels"]["downside"]
        )
        self.assertFalse(
            [row for row in rows if str(row.get("source") or "").startswith("round_")]
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

    def test_top_lines_uses_real_market_levels_for_near_lines(self):
        world_model = {
            "price": 69400,
            "output_contract": {"contract_status": "ok"},
            "near_levels": {
                "upside": [
                    {
                        "price": 69500,
                        "reason": "100円刻み",
                        "source": "round_100",
                        "line_role": "psychological",
                        "distance_abs": 100,
                        "distance_pct": 0.14,
                    },
                    {
                        "price": 69820,
                        "reason": "EMA20",
                        "source": "ema20",
                        "line_role": "structural",
                        "distance_abs": 420,
                        "distance_pct": 0.61,
                    },
                    {
                        "price": 69890,
                        "reason": "Pivot R1",
                        "source": "pivot_r1",
                        "line_role": "structural",
                        "distance_abs": 490,
                        "distance_pct": 0.71,
                    },
                ],
                "downside": [
                    {
                        "price": 69140,
                        "reason": "前日安値",
                        "source": "previous_low",
                        "line_role": "structural",
                        "distance_abs": 260,
                        "distance_pct": -0.37,
                    },
                    {
                        "price": 68920,
                        "reason": "Pivot S1",
                        "source": "pivot_s1",
                        "line_role": "structural",
                        "distance_abs": 480,
                        "distance_pct": -0.69,
                    },
                ],
            },
            "practical_lines": {
                "current_price": 69400,
                "upside_resistance": 72720,
                "downside_support": 63870,
                "near_upside": 69500,
                "near_upside_detail": {
                    "price": 69500,
                    "reason": "100円刻み",
                    "source": "round_100",
                    "line_role": "psychological",
                    "distance_abs": 100,
                    "distance_pct": 0.14,
                },
                "near_downside": 69140,
                "near_downside_detail": {
                    "price": 69140,
                    "reason": "前日安値",
                    "source": "previous_low",
                    "line_role": "structural",
                    "distance_abs": 260,
                    "distance_pct": -0.37,
                },
            },
        }

        result = _top_lines(world_model, {"price": 69400})

        self.assertEqual(result["near_upside"], 69820)
        self.assertEqual(result["near_upside_detail"]["source"], "ema20")
        self.assertEqual(result["near_downside"], 68920)
        self.assertEqual(result["near_downside_detail"]["source"], "pivot_s1")

    def test_top_lines_hides_near_level_when_only_round_numbers_are_available(self):
        world_model = {
            "price": 69570,
            "output_contract": {"contract_status": "ok"},
            "near_levels": {
                "upside": [
                    {
                        "price": 69600,
                        "reason": "100円刻み",
                        "source": "round_100",
                        "line_role": "psychological",
                        "distance_abs": 30,
                    },
                    {
                        "price": 70000,
                        "reason": "500円刻み",
                        "source": "round_500",
                        "line_role": "psychological",
                        "distance_abs": 430,
                    },
                ],
                "downside": [
                    {
                        "price": 69500,
                        "reason": "100円刻み",
                        "source": "round_100",
                        "line_role": "psychological",
                        "distance_abs": 70,
                    }
                ],
            },
            "practical_lines": {
                "current_price": 69570,
                "upside_resistance": 72720,
                "downside_support": 63870,
                "near_upside_detail": {
                    "price": 70000,
                    "reason": "500円刻み",
                    "source": "round_500",
                    "line_role": "psychological",
                    "distance_abs": 430,
                },
            },
        }

        result = _top_lines(world_model, {"price": 69570})

        self.assertIsNone(result["near_upside"])
        self.assertIsNone(result["near_downside"])
        self.assertIsNone(result["near_upside_detail"])

    def test_practical_line_builder_skips_too_close_round_number_near_level(self):
        row = _first_level_row(
            [
                {
                    "price": 69500,
                    "reason": "100円刻み",
                    "source": "round_100",
                    "line_role": "psychological",
                    "distance_abs": 100,
                    "distance_pct": 0.14,
                },
                {
                    "price": 69820,
                    "reason": "EMA20",
                    "source": "ema20",
                    "line_role": "structural",
                    "distance_abs": 420,
                    "distance_pct": 0.61,
                },
            ],
            current_price=69400,
        )

        self.assertEqual(row["price"], 69820)
        self.assertEqual(row["source"], "ema20")

    def test_practical_line_builder_returns_none_when_only_round_numbers_are_available(self):
        row = _first_level_row(
            [
                {
                    "price": 70000,
                    "reason": "500円刻み",
                    "source": "round_500",
                    "line_role": "psychological",
                    "distance_abs": 430,
                    "distance_pct": 0.62,
                }
            ],
            current_price=69570,
        )

        self.assertIsNone(row)
