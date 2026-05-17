from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse


class BasecalcUpdateSecurityTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_get_update_true_does_not_fetch_external_data(self):
        with (
            patch('basecalc.views.get_nikkei_per_values') as per_values,
            patch('basecalc.views.get_jgb10y_yield_percent') as jgb_yield,
        ):
            response = self.client.get(
                reverse('basecalc:index'),
                {'update': 'true'},
            )

        self.assertEqual(response.status_code, 200)
        per_values.assert_not_called()
        jgb_yield.assert_not_called()

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
        ):
            response = self.client.post(
                reverse('basecalc:index'),
                {'action': 'update', 'price': '40000'},
            )

        self.assertEqual(response.status_code, 200)
        per_values.assert_called_once()
        jgb_yield.assert_called_once()
        self.assertEqual(cache.get('nikkei_forward_per'), 18.5)
        self.assertEqual(cache.get('nikkei_jgb10y_yield_percent'), 1.2)
