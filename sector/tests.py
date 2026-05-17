from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class SectorRefreshSecurityTests(TestCase):
    def test_anonymous_refresh_is_forbidden(self):
        response = self.client.post(
            reverse('sector:index'),
            data='{"action": "refresh"}',
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 403)

    def test_refresh_button_is_hidden_for_anonymous_users(self):
        response = self.client.get(reverse('sector:index'))

        self.assertNotContains(response, 'id="refresh-btn"')

    def test_refresh_button_is_hidden_for_staff_users(self):
        user = User.objects.create_user(
            username='staff-user',
            password='test-password',
            is_staff=True,
        )
        self.client.force_login(user)

        response = self.client.get(reverse('sector:index'))

        self.assertNotContains(response, 'id="refresh-btn"')

    def test_staff_refresh_post_is_forbidden(self):
        user = User.objects.create_user(
            username='staff-user',
            password='test-password',
            is_staff=True,
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse('sector:index'),
            data='{"action": "refresh"}',
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 403)

    def test_refresh_button_is_visible_for_superusers(self):
        user = User.objects.create_superuser(
            username='creator',
            email='creator@example.com',
            password='test-password',
        )
        self.client.force_login(user)

        response = self.client.get(reverse('sector:index'))

        self.assertContains(response, 'id="refresh-btn"')

    def test_superuser_refresh_post_reaches_action_validation(self):
        user = User.objects.create_superuser(
            username='creator',
            email='creator@example.com',
            password='test-password',
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse('sector:index'),
            data='{"action": "unknown"}',
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
