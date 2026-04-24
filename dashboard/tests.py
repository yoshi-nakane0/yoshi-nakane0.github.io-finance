from django.test import SimpleTestCase
from django.urls import reverse


class DashboardPageTests(SimpleTestCase):
    def test_dashboard_page_renders_performance_markup(self):
        response = self.client.get(reverse("dashboard:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="dashboard-page"', html=False)
        self.assertContains(response, "dashboard-section--deferred", count=3)
        self.assertContains(response, "/static/common/css/layout.css", html=False)
        self.assertContains(response, "/static/dashboard/css/style.css", html=False)
