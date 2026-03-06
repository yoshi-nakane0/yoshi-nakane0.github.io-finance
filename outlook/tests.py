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
                    "id": "trade-1",
                    "tab": "tradeplan",
                    "created_at": "2026-03-06 09:00",
                    "title": "USD/JPY follow-through",
                    "body": "押し目は短いかを確認する。",
                    "watch_until": "2026-03-10",
                },
                {
                    "id": "trade-2",
                    "tab": "tradeplan",
                    "created_at": "2026-03-06 08:40",
                    "title": "Nikkei pullback",
                    "body": "押し目買いの戻りを監視する。",
                    "watch_until": "",
                },
                {
                    "id": "watch-1",
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

    def _read_rows(self):
        with views.OUTLOOK_DATA_PATH.open("r", newline="", encoding="utf-8") as csv_file:
            return list(csv.DictReader(csv_file))

    def test_watch_tab_renders(self):
        response = self.client.get(reverse("outlook:index"), {"tab": "watch"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "NVIDIA")
        self.assertContains(response, "Watch")
        self.assertNotContains(response, "カードを選択してまとめて削除できます。")

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

        saved_row = next(
            row for row in self._read_rows() if row["title"] == "USD/JPY watch"
        )
        self.assertEqual(saved_row["tab"], "notes")
        self.assertEqual(saved_row["watch_until"], "2026-03-10")
        self.assertTrue(saved_row["id"])

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

        saved_row = next(row for row in self._read_rows() if row["title"] == "Rates bias")
        self.assertEqual(saved_row["tab"], "tradeplan")
        self.assertEqual(saved_row["watch_until"], "")
        self.assertTrue(saved_row["id"])

    def test_single_delete_removes_one_card(self):
        response = self.client.post(
            reverse("outlook:index"),
            {
                "active_tab": "tradeplan",
                "delete_id": "trade-1",
            },
        )

        self.assertRedirects(
            response,
            f"{reverse('outlook:index')}?tab=tradeplan",
        )

        remaining_ids = {row["id"] for row in self._read_rows()}
        self.assertNotIn("trade-1", remaining_ids)
        self.assertIn("trade-2", remaining_ids)
        self.assertIn("watch-1", remaining_ids)

    def test_bulk_delete_removes_multiple_cards(self):
        response = self.client.post(
            reverse("outlook:index"),
            {
                "active_tab": "tradeplan",
                "action": "delete_selected",
                "selected_ids": ["trade-1", "trade-2"],
            },
        )

        self.assertRedirects(
            response,
            f"{reverse('outlook:index')}?tab=tradeplan",
        )

        remaining_rows = self._read_rows()
        self.assertEqual(len(remaining_rows), 1)
        self.assertEqual(remaining_rows[0]["id"], "watch-1")

    def test_legacy_csv_without_ids_is_migrated(self):
        views.OUTLOOK_DATA_PATH.write_text(
            "\n".join(
                [
                    "tab,created_at,title,body,watch_until",
                    "notes,2026-03-06 11:00,Legacy Item,旧形式の行,2026-03-15",
                ]
            ),
            encoding="utf-8",
        )

        response = self.client.get(reverse("outlook:index"), {"tab": "notes"})

        self.assertEqual(response.status_code, 200)
        migrated_rows = self._read_rows()
        self.assertEqual(migrated_rows[0]["title"], "Legacy Item")
        self.assertTrue(migrated_rows[0]["id"])

    @override_settings(DEBUG=True)
    def test_local_debug_syncs_from_remote_csv_when_url_configured(self):
        views.OUTLOOK_SYNC_URL = "https://example.com/outlook-data.csv"
        views.OUTLOOK_SYNC_INTERVAL_SEC = 0
        views._SYNC_STATE["last_attempt"] = 0.0

        remote_csv = "\n".join(
            [
                "id,tab,created_at,title,body,watch_until",
                "remote-1,watch,2026-03-06 11:00,Remote Watch,本番の最新データ,2026-03-20",
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
