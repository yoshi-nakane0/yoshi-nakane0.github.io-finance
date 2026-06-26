from django.test import SimpleTestCase

from .similarity import _is_downside_t1_hit, _is_upside_t1_hit


class SimilarityT1HitDefinitionTests(SimpleTestCase):
    def test_upside_t1_hit_uses_mfe(self):
        self.assertTrue(_is_upside_t1_hit(1.10, 0.80))
        self.assertFalse(_is_upside_t1_hit(0.40, 0.80))

    def test_downside_t1_hit_uses_mae(self):
        self.assertTrue(_is_downside_t1_hit(-1.10, -0.80))
        self.assertFalse(_is_downside_t1_hit(-0.40, -0.80))

    def test_none_values_are_not_hits(self):
        self.assertFalse(_is_upside_t1_hit(None, 0.80))
        self.assertFalse(_is_downside_t1_hit(None, -0.80))
