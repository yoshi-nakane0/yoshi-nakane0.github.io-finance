from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from . import views


class OutlookItemFormTests(TestCase):
    def setUp(self):
        super().setUp()
        self.temporary_directory = TemporaryDirectory()
        self.data_dir = Path(self.temporary_directory.name)
        self.path_patches = [
            patch.object(views, "OUTLOOK_DATA_DIR", self.data_dir),
            patch.object(views, "OUTLOOK_DATA_PATH", self.data_dir / "data.csv"),
            patch.object(views, "TRADEPLAN_DATA_PATH", self.data_dir / "tradeplan.json"),
            patch.object(
                views,
                "TRADEPLAN_POSITION_DATA_PATH",
                self.data_dir / "tradeplan_positions.json",
            ),
            patch.object(views, "OUTLOOK_SYNC_BASE_URL", ""),
            patch.object(views, "OUTLOOK_SYNC_URL", ""),
            patch.object(views, "OUTLOOK_SYNC_DATA_URL", ""),
            patch.object(views, "OUTLOOK_SYNC_TRADEPLAN_URL", ""),
            patch.object(views, "OUTLOOK_SYNC_TRADEPLAN_POSITIONS_URL", ""),
        ]

        for path_patch in self.path_patches:
            path_patch.start()

        self.addCleanup(self._cleanup_patches)
        self.addCleanup(self.temporary_directory.cleanup)

    def _cleanup_patches(self):
        for path_patch in reversed(self.path_patches):
            path_patch.stop()

    def test_notes_edit_can_update_without_watch_until(self):
        views._rewrite_outlook_csv(
            [
                {
                    "id": "note-1",
                    "tab": "notes",
                    "created_at": "2026-03-09 10:00",
                    "title": "旧タイトル",
                    "body": "旧本文",
                    "watch_until": "2026-03-31",
                }
            ]
        )

        response = self.client.post(
            reverse("outlook:index"),
            {
                "edit_id": "note-1",
                "tab": "notes",
                "created_at": "2026-03-09 10:00",
                "title": "更新タイトル",
                "body": "更新本文",
                "watch_until": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"{reverse('outlook:index')}?tab=notes&saved=1")

        rows = views._read_outlook_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "note-1")
        self.assertEqual(rows[0]["tab"], "notes")
        self.assertEqual(rows[0]["title"], "更新タイトル")
        self.assertEqual(rows[0]["body"], "更新本文")
        self.assertEqual(rows[0]["watch_until"], "")

    def test_watch_still_requires_watch_until(self):
        response = self.client.post(
            reverse("outlook:index"),
            {
                "tab": "watch",
                "created_at": "2026-03-09 11:00",
                "title": "監視対象",
                "body": "本文",
                "watch_until": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "監視期限を入力してください。")
        self.assertEqual(views._read_outlook_rows(), [])
