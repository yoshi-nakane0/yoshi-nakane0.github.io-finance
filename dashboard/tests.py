from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class DashboardPageTests(TestCase):
    def test_dashboard_page_renders_performance_markup(self):
        response = self.client.get(reverse("dashboard:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="dashboard-page"', html=False)
        self.assertContains(response, "dashboard-section--deferred", count=3)
        self.assertContains(response, "/static/common/css/layout.css", html=False)
        self.assertContains(response, "/static/dashboard/css/style.css", html=False)

    def test_dashboard_has_hidden_admin_route_script(self):
        response = self.client.get(reverse("dashboard:index"))

        self.assertContains(response, reverse("dashboard:admin_panel"))
        self.assertContains(response, "tapCount >= 5")
        self.assertNotContains(response, '".dashboard-card"')

    def test_live_stock_trends_pairs_dashboard_cards(self):
        response = self.client.get(reverse("dashboard:index"))
        content = response.content.decode()
        live_stock_start = content.index("Live Stock Trends")
        market_forecast_start = content.index("Market Forecast")
        live_stock_section = content[live_stock_start:market_forecast_start]

        for card_type in ("type-macro", "type-basecalc", "type-sector", "type-prediction"):
            self.assertIn(card_type, live_stock_section)

        macro_position = live_stock_section.index("type-macro")
        basecalc_position = live_stock_section.index("type-basecalc")
        sector_position = live_stock_section.index("type-sector")
        prediction_position = live_stock_section.index("type-prediction")

        self.assertLess(macro_position, basecalc_position)
        self.assertLess(basecalc_position, sector_position)
        self.assertLess(sector_position, prediction_position)
        self.assertNotIn("type-macro grid-span-2", live_stock_section)

    def test_admin_panel_shows_login_for_anonymous_user(self):
        response = self.client.get(reverse("dashboard:admin_panel"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "管理者ログイン")
        self.assertContains(response, "name=\"username\"", html=False)

    def test_admin_panel_denies_non_superuser(self):
        user = User.objects.create_user(
            username="normal-user",
            password="test-password",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("dashboard:admin_panel"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "権限がありません")
        self.assertNotContains(response, "Sectorへ移動")

    def test_admin_panel_allows_superuser(self):
        user = User.objects.create_superuser(
            username="creator",
            email="creator@example.com",
            password="test-password",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("dashboard:admin_panel"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ログアウト")
        self.assertNotContains(response, "管理者ログイン")
        self.assertNotContains(response, "Sectorへ移動")

    def test_admin_panel_login_redirects_to_dashboard(self):
        User.objects.create_superuser(
            username="creator",
            email="creator@example.com",
            password="test-password",
        )

        response = self.client.post(
            reverse("dashboard:admin_panel"),
            {"action": "login", "username": "creator", "password": "test-password"},
            follow=True,
        )

        self.assertRedirects(response, reverse("dashboard:index"))
        self.assertContains(response, 'class="dashboard-page"', html=False)
        self.assertNotContains(response, "管理者ログイン")
