import csv
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse


CSV_HEADERS = [
    'date',
    'market',
    'symbol',
    'company',
    'industry',
    'Fundamental',
    'Risk',
    'Direction',
    'Sentiment',
    'sales_current',
    'sales_forecast',
    'sales_4q_ago',
    'sales_4q_prior_period',
    'eps_current',
    'eps_forecast',
    'eps_4q_ago',
    'eps_4q_prior_period',
    'surp_4q_ago',
    'surp_current',
    'surp_4q_prior_period',
    'surp_eps_4q_ago',
    'surp_eps_current',
    'surp_eps_4q_prior_period',
    'fiscal_period',
    'summary',
]


def build_row(target_date, company, market, symbol, summary):
    return {
        'date': target_date.isoformat(),
        'market': market,
        'symbol': symbol,
        'company': company,
        'industry': 'Software',
        'Fundamental': 'up',
        'Risk': '82',
        'Direction': 'flat',
        'Sentiment': 'down',
        'sales_current': '120',
        'sales_forecast': '130',
        'sales_4q_ago': '100',
        'sales_4q_prior_period': '95',
        'eps_current': '3.2',
        'eps_forecast': '3.4',
        'eps_4q_ago': '2.9',
        'eps_4q_prior_period': '2.4',
        'surp_4q_ago': '4%',
        'surp_current': '6%',
        'surp_4q_prior_period': '2%',
        'surp_eps_4q_ago': '3%',
        'surp_eps_current': '5%',
        'surp_eps_4q_prior_period': '1%',
        'fiscal_period': 'Q4',
        'summary': summary,
    }


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class EarningsViewTests(TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.temp_dir = TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        data_dir = self.base_dir / 'static' / 'earning' / 'data'
        data_dir.mkdir(parents=True, exist_ok=True)

        today = date.today()
        rows = [
            build_row(today + timedelta(days=1), 'Future Corp', 'NYSE', 'FUT', 'upcoming summary'),
            build_row(today - timedelta(days=1), 'Past Corp', 'TSE', 'PST', 'completed summary'),
        ]

        with (data_dir / 'data.csv').open('w', encoding='utf-8', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=CSV_HEADERS)
            writer.writeheader()
            writer.writerows(rows)

        self.base_dir_override = override_settings(BASE_DIR=self.base_dir)
        self.base_dir_override.enable()

    def tearDown(self):
        self.base_dir_override.disable()
        self.temp_dir.cleanup()
        cache.clear()
        super().tearDown()

    def test_index_renders_only_upcoming_groups_initially(self):
        response = self.client.get(reverse('earning:index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Future Corp')
        self.assertNotContains(response, 'Past Corp')
        self.assertContains(response, '決算済みを表示（1日分）')
        self.assertNotContains(response, 'cdn.jsdelivr.net')
        self.assertContains(response, '/static/dashboard/vendor/bootstrap-icons/bootstrap-icons.css')

    def test_completed_endpoint_returns_only_completed_groups(self):
        response = self.client.get(reverse('earning:completed'))
        content = response.content.decode('utf-8')

        self.assertEqual(response.status_code, 200)
        self.assertIn('Past Corp', content)
        self.assertNotIn('Future Corp', content)
        self.assertIn('data-period="completed"', content)
