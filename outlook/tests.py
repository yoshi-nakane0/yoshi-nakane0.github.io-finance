import json
import os
import re
import shutil
import sqlite3
import tempfile
from unittest.mock import patch
from datetime import date
from pathlib import Path

from django.test import TestCase, override_settings
from django.urls import reverse

from myproject import settings as project_settings

from . import views
from .models import OutlookItem, TradePlanEntry, TradePlanPosition


class OutlookViewTests(TestCase):
    def setUp(self):
        OutlookItem.objects.all().delete()
        TradePlanEntry.objects.all().delete()
        TradePlanPosition.objects.all().delete()

    def test_index_renders_with_database_storage(self):
        response = self.client.get(reverse("outlook:index"))

        self.assertEqual(response.status_code, 200)

    def test_create_watch_item_persists_to_database(self):
        response = self.client.post(
            reverse("outlook:index"),
            {
                "active_tab": "watch",
                "tab": "watch",
                "created_at": "2026-03-09 08:30",
                "title": "NVIDIA",
                "body": "決算ガイダンスを監視する",
                "watch_until": "2026-03-14",
            },
        )

        self.assertEqual(response.status_code, 302)
        created_item = OutlookItem.objects.get()
        self.assertEqual(created_item.tab, "watch")
        self.assertEqual(created_item.title, "NVIDIA")
        self.assertEqual(created_item.watch_until, date(2026, 3, 14))

    def test_edit_item_updates_database_record(self):
        item = OutlookItem.objects.create(
            id="a" * 32,
            tab="watch",
            created_at="2026-03-09 08:30",
            title="Before",
            body="Before body",
            watch_until=date(2026, 3, 14),
        )

        response = self.client.post(
            reverse("outlook:index"),
            {
                "active_tab": "watch",
                "edit_id": item.id,
                "tab": "notes",
                "created_at": "2026-03-09 09:00",
                "title": "After",
                "body": "After body",
                "watch_until": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.tab, "notes")
        self.assertEqual(item.title, "After")
        self.assertIsNone(item.watch_until)

    def test_delete_selected_items_removes_records(self):
        first_item = OutlookItem.objects.create(
            id="b" * 32,
            tab="watch",
            created_at="2026-03-09 08:30",
            title="First",
            body="First body",
            watch_until=date(2026, 3, 14),
        )
        second_item = OutlookItem.objects.create(
            id="c" * 32,
            tab="notes",
            created_at="2026-03-09 09:30",
            title="Second",
            body="Second body",
        )

        response = self.client.post(
            reverse("outlook:index"),
            {
                "active_tab": "watch",
                "action": "delete_selected",
                "selected_ids": [first_item.id, second_item.id],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(OutlookItem.objects.filter(id=first_item.id).exists())
        self.assertFalse(OutlookItem.objects.filter(id=second_item.id).exists())

    def test_tradeplan_save_persists_to_database(self):
        response = self.client.post(
            reverse("outlook:index"),
            {
                "tradeplan_action": "save",
                "plan_date": "2026-03-09",
                "long": "押し目買いを検討",
                "long_continue": "on",
                "short": "",
                "square": "",
                "calendar_year": "2026",
                "calendar_month": "3",
            },
        )

        self.assertEqual(response.status_code, 302)
        tradeplan_entry = TradePlanEntry.objects.get(plan_date=date(2026, 3, 9))
        self.assertEqual(tradeplan_entry.long_text, "押し目買いを検討")
        self.assertTrue(tradeplan_entry.long_continue)

    def test_tradeplan_position_api_crud_uses_database(self):
        create_response = self.client.post(
            reverse("outlook:tradeplan_positions"),
            data=json.dumps(
                {
                    "action": "create",
                    "type": "long",
                    "start_date": "2026-03-09",
                    "end_date": "2026-03-11",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(create_response.status_code, 200)
        created_payload = create_response.json()
        position_id = created_payload["position"]["id"]
        self.assertTrue(TradePlanPosition.objects.filter(id=position_id).exists())

        update_response = self.client.post(
            reverse("outlook:tradeplan_positions"),
            data=json.dumps(
                {
                    "action": "update",
                    "id": position_id,
                    "type": "short",
                    "start_date": "2026-03-10",
                    "end_date": "2026-03-12",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(update_response.status_code, 200)
        position = TradePlanPosition.objects.get(id=position_id)
        self.assertEqual(position.position_type, "short")
        self.assertEqual(position.start_date, date(2026, 3, 10))
        self.assertEqual(position.end_date, date(2026, 3, 12))

        delete_response = self.client.post(
            reverse("outlook:tradeplan_positions"),
            data=json.dumps({"action": "delete", "id": position_id}),
            content_type="application/json",
        )

        self.assertEqual(delete_response.status_code, 200)
        self.assertFalse(TradePlanPosition.objects.filter(id=position_id).exists())

    def test_tradeplan_page_renders_csrf_field_for_fetch_requests(self):
        response = self.client.get(reverse("outlook:index") + "?tab=tradeplan")
        html = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="tradeplan-csrf-form"')
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertIn(
            f'data-tradeplan-storage-mode="{views._tradeplan_position_storage_mode()}"',
            html,
        )

    def test_tradeplan_position_api_accepts_csrf_token_rendered_in_page(self):
        client = self.client_class(enforce_csrf_checks=True, HTTP_HOST="localhost")
        response = client.get(reverse("outlook:index") + "?tab=tradeplan")

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        match = re.search(
            r'name="csrfmiddlewaretoken" value="([^"]+)"',
            html,
        )
        self.assertIsNotNone(match)
        csrf_token = match.group(1)

        create_response = client.post(
            reverse("outlook:tradeplan_positions"),
            data=json.dumps(
                {
                    "action": "create",
                    "type": "long",
                    "start_date": "2026-03-09",
                    "end_date": "2026-03-09",
                }
            ),
            content_type="application/json",
            HTTP_HOST="localhost",
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(create_response.status_code, 200)
        self.assertEqual(TradePlanPosition.objects.count(), 1)

    @override_settings(DEBUG=False)
    def test_tradeplan_storage_mode_uses_browser_without_database_url(self):
        with patch.dict(os.environ, {"DATABASE_URL": ""}, clear=False):
            self.assertEqual(views._tradeplan_position_storage_mode(), "browser")
            self.assertIn(
                "このブラウザに保存",
                views._tradeplan_position_storage_notice(),
            )

    @override_settings(DEBUG=False)
    def test_tradeplan_storage_mode_uses_database_with_database_url(self):
        with patch.dict(
            os.environ,
            {"DATABASE_URL": "postgresql://user:pass@example.com:5432/app"},
            clear=False,
        ):
            self.assertEqual(views._tradeplan_position_storage_mode(), "database")
            self.assertEqual(views._tradeplan_position_storage_notice(), "")


class SqliteBootstrapTests(TestCase):
    def test_bootstrap_replaces_stale_sqlite_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bundled_db = Path(temp_dir) / "bundled.sqlite3"
            stale_db = Path(temp_dir) / "stale.sqlite3"

            shutil.copy2(project_settings.BASE_DIR / "db.sqlite3", bundled_db)

            with sqlite3.connect(stale_db) as connection:
                connection.execute(
                    "CREATE TABLE stale_only (id INTEGER PRIMARY KEY)"
                )
                connection.commit()

            project_settings.bootstrap_sqlite_database(
                stale_db,
                source_path=bundled_db,
            )

            with sqlite3.connect(stale_db) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }

            self.assertIn("outlook_outlookitem", tables)
            self.assertIn("outlook_tradeplanentry", tables)
            self.assertIn("outlook_tradeplanposition", tables)
            self.assertNotIn("stale_only", tables)
