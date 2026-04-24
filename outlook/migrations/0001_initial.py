import csv
import json
import uuid
from datetime import date
from pathlib import Path

from django.db import migrations, models


OUTLOOK_CSV_FIELDS = ("id", "tab", "created_at", "title", "body", "watch_until")
LEGACY_OUTLOOK_CSV_FIELDS = (
    ("tab", "created_at", "title", "body", "watch_until"),
    ("created_at", "title", "body", "watch_until"),
)
VALID_ITEM_TABS = {"watch", "notes"}
VALID_POSITION_TYPES = {"long", "short"}


def _parse_watch_until(value):
    try:
        return date.fromisoformat((value or "").strip())
    except (TypeError, ValueError, AttributeError):
        return None


def _parse_plan_date(value):
    try:
        return date.fromisoformat((value or "").strip())
    except (TypeError, ValueError, AttributeError):
        return None


def _normalize_item_row(row, default_tab="notes"):
    normalized_tab = (row.get("tab") or default_tab).strip()
    if normalized_tab not in VALID_ITEM_TABS:
        normalized_tab = default_tab

    return {
        "id": (row.get("id") or "").strip() or uuid.uuid4().hex,
        "tab": normalized_tab,
        "created_at": (row.get("created_at") or "").strip(),
        "title": (row.get("title") or "").strip(),
        "body": (row.get("body") or "").strip(),
        "watch_until": (
            (row.get("watch_until") or "").strip()
            if normalized_tab == "watch"
            else ""
        ),
    }


def _load_static_outlook_rows(data_dir):
    csv_path = data_dir / "data.csv"
    if not csv_path.exists():
        return []

    try:
        with csv_path.open("r", newline="", encoding="utf-8") as csv_file:
            reader = csv.DictReader(csv_file)
            fieldnames = tuple(reader.fieldnames or ())
            rows = list(reader)
    except OSError:
        return []

    if fieldnames == OUTLOOK_CSV_FIELDS:
        normalized_rows = [
            _normalize_item_row(row, default_tab="notes") for row in rows
        ]
    elif fieldnames == LEGACY_OUTLOOK_CSV_FIELDS[0]:
        normalized_rows = [
            _normalize_item_row(row, default_tab="notes") for row in rows
        ]
    elif fieldnames == LEGACY_OUTLOOK_CSV_FIELDS[1]:
        normalized_rows = [
            _normalize_item_row(
                {
                    "tab": "notes",
                    "created_at": row.get("created_at"),
                    "title": row.get("title"),
                    "body": row.get("body"),
                    "watch_until": row.get("watch_until"),
                },
                default_tab="notes",
            )
            for row in rows
        ]
    else:
        normalized_rows = [
            _normalize_item_row(
                {
                    "tab": row.get("tab") or "notes",
                    "created_at": row.get("created_at"),
                    "title": row.get("title"),
                    "body": row.get("body"),
                    "watch_until": row.get("watch_until"),
                },
                default_tab="notes",
            )
            for row in rows
        ]

    deduped_rows = []
    seen_ids = set()
    for row in normalized_rows:
        item_id = row["id"]
        while item_id in seen_ids:
            item_id = uuid.uuid4().hex
        row["id"] = item_id
        seen_ids.add(item_id)
        deduped_rows.append(row)
    return deduped_rows


def _load_static_json(data_path):
    if not data_path.exists():
        return []

    try:
        loaded = json.loads(data_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    if not isinstance(loaded, list):
        return []
    return loaded


def load_initial_outlook_data(apps, schema_editor):
    OutlookItem = apps.get_model("outlook", "OutlookItem")
    TradePlanEntry = apps.get_model("outlook", "TradePlanEntry")
    TradePlanPosition = apps.get_model("outlook", "TradePlanPosition")

    base_dir = Path(__file__).resolve().parents[2]
    data_dir = base_dir / "static" / "outlook" / "data"

    if not OutlookItem.objects.exists():
        for row in _load_static_outlook_rows(data_dir):
            OutlookItem.objects.update_or_create(
                id=row["id"],
                defaults={
                    "tab": row["tab"],
                    "created_at": row["created_at"],
                    "title": row["title"],
                    "body": row["body"],
                    "watch_until": _parse_watch_until(row["watch_until"]),
                },
            )

    if not TradePlanEntry.objects.exists():
        for entry in _load_static_json(data_dir / "tradeplan.json"):
            plan_date = _parse_plan_date(entry.get("date"))
            if plan_date is None:
                continue
            TradePlanEntry.objects.update_or_create(
                plan_date=plan_date,
                defaults={
                    "long_text": (entry.get("long") or "").strip(),
                    "long_continue": bool(entry.get("long_continue")),
                    "short_text": (entry.get("short") or "").strip(),
                    "short_continue": bool(entry.get("short_continue")),
                    "square_text": (entry.get("square") or "").strip(),
                    "square_continue": bool(entry.get("square_continue")),
                },
            )

    if not TradePlanPosition.objects.exists():
        seen_ids = set()
        for position in _load_static_json(data_dir / "tradeplan_positions.json"):
            start_date = _parse_plan_date(
                position.get("start_date") or position.get("date")
            )
            end_date = _parse_plan_date(
                position.get("end_date") or position.get("start_date") or position.get("date")
            )
            if start_date is None:
                continue
            if end_date is None:
                end_date = start_date
            if end_date < start_date:
                start_date, end_date = end_date, start_date

            position_type = str(position.get("type") or "").strip().lower()
            if position_type not in VALID_POSITION_TYPES:
                position_type = "long"

            position_id = (position.get("id") or "").strip() or uuid.uuid4().hex
            while position_id in seen_ids:
                position_id = uuid.uuid4().hex
            seen_ids.add(position_id)

            TradePlanPosition.objects.update_or_create(
                id=position_id,
                defaults={
                    "position_type": position_type,
                    "start_date": start_date,
                    "end_date": end_date,
                },
            )


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="OutlookItem",
            fields=[
                ("id", models.CharField(editable=False, max_length=32, primary_key=True, serialize=False)),
                ("tab", models.CharField(choices=[("watch", "Watch"), ("notes", "Notes")], max_length=10)),
                ("created_at", models.CharField(max_length=16)),
                ("title", models.CharField(max_length=255)),
                ("body", models.TextField(blank=True)),
                ("watch_until", models.DateField(blank=True, null=True)),
            ],
            options={"ordering": ("-created_at", "-id")},
        ),
        migrations.CreateModel(
            name="TradePlanEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("plan_date", models.DateField(unique=True)),
                ("long_text", models.TextField(blank=True)),
                ("long_continue", models.BooleanField(default=False)),
                ("short_text", models.TextField(blank=True)),
                ("short_continue", models.BooleanField(default=False)),
                ("square_text", models.TextField(blank=True)),
                ("square_continue", models.BooleanField(default=False)),
            ],
            options={"ordering": ("plan_date",)},
        ),
        migrations.CreateModel(
            name="TradePlanPosition",
            fields=[
                ("id", models.CharField(editable=False, max_length=32, primary_key=True, serialize=False)),
                ("position_type", models.CharField(choices=[("long", "Long"), ("short", "Short")], max_length=10)),
                ("start_date", models.DateField()),
                ("end_date", models.DateField()),
            ],
            options={"ordering": ("start_date", "end_date", "position_type", "id")},
        ),
        migrations.RunPython(load_initial_outlook_data, migrations.RunPython.noop),
    ]
