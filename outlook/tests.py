import csv
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.urls import reverse

from . import views


class OutlookViewTests(TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.original_dir = views.OUTLOOK_DATA_DIR
        self.original_path = views.OUTLOOK_DATA_PATH
        self.original_sync_url = views.OUTLOOK_SYNC_URL
        self.original_sync_interval = views.OUTLOOK_SYNC_INTERVAL_SEC
        views.OUTLOOK_DATA_DIR = Path(self.temp_dir.name)
        views.OUTLOOK_DATA_PATH = views.OUTLOOK_DATA_DIR / "data.csv"
        self.addCleanup(self._restore_paths)
        self._write_csv(
            [
                {
                    "tab": "tradeplan",
                    "created_at": "2026-03-06 09:00",
                    "title": "USD/JPY follow-through",
                    "body": "押し目は短いかを確認する。",
                    "watch_until": "2026-03-10",
                },
                {
                    "tab": "watch",
                    "created_at": "2026-03-06 08:20",
                    "title": "NVIDIA",
                    "body": "ガイダンスを監視する。",
                    "watch_until": "2026-03-14",
                },
            ]
        )

    def _restore_paths(self):
        views.OUTLOOK_DATA_DIR = self.original_dir
        views.OUTLOOK_DATA_PATH = self.original_path
        views.OUTLOOK_SYNC_URL = self.original_sync_url
        views.OUTLOOK_SYNC_INTERVAL_SEC = self.original_sync_interval
        views._SYNC_STATE["last_attempt"] = 0.0

    def _write_csv(self, rows):
        views.OUTLOOK_DATA_DIR.mkdir(parents=True, exist_ok=True)
        with views.OUTLOOK_DATA_PATH.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=views.OUTLOOK_CSV_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def test_watch_tab_renders(self):
        response = self.client.get(reverse("outlook:index"), {"tab": "watch"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "NVIDIA")
        self.assertContains(response, "Watch")

    def test_note_post_saves_csv_and_redirects(self):
        response = self.client.post(
            reverse("outlook:index"),
            {
                "tab": "notes",
                "created_at": "2026-03-06 09:30",
                "title": "USD/JPY watch",
                "body": "150円台の定着を確認する。",
                "watch_until": "2026-03-10",
            },
        )

        self.assertRedirects(response, f"{reverse('outlook:index')}?tab=notes&saved=1")

        csv_text = views.OUTLOOK_DATA_PATH.read_text(encoding="utf-8")
        self.assertIn("tab,created_at,title,body,watch_until", csv_text)
        self.assertIn("USD/JPY watch", csv_text)
        self.assertIn("2026-03-10", csv_text)

    def test_tradeplan_post_allows_blank_watch_until(self):
        response = self.client.post(
            reverse("outlook:index"),
            {
                "tab": "tradeplan",
                "created_at": "2026-03-06 10:00",
                "title": "Rates bias",
                "body": "金利主導の強さを確認する。",
                "watch_until": "",
            },
        )

        self.assertRedirects(
            response,
            f"{reverse('outlook:index')}?tab=tradeplan&saved=1",
        )

        csv_text = views.OUTLOOK_DATA_PATH.read_text(encoding="utf-8")
        self.assertIn("tradeplan,2026-03-06 10:00,Rates bias,金利主導の強さを確認する。,", csv_text)

    @override_settings(DEBUG=True)
    def test_local_debug_syncs_from_remote_csv_when_url_configured(self):
        views.OUTLOOK_SYNC_URL = "https://example.com/outlook-data.csv"
        views.OUTLOOK_SYNC_INTERVAL_SEC = 0
        views._SYNC_STATE["last_attempt"] = 0.0

        remote_csv = "\n".join(
            [
                "tab,created_at,title,body,watch_until",
                "watch,2026-03-06 11:00,Remote Watch,本番の最新データ,2026-03-20",
            ]
        )
        mock_response = Mock()
        mock_response.text = remote_csv
        mock_response.raise_for_status.return_value = None

        with patch("outlook.views.requests.get", return_value=mock_response) as mock_get:
            response = self.client.get(reverse("outlook:index"), {"tab": "watch"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Remote Watch")
        self.assertEqual(
            views.OUTLOOK_DATA_PATH.read_text(encoding="utf-8"),
            remote_csv,
        )
        mock_get.assert_called_once()
