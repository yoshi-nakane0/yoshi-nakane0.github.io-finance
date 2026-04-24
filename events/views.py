import csv
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings
from django.shortcuts import render
from django.urls import reverse

TOKYO_TZ = ZoneInfo("Asia/Tokyo")
EVENT_TEXT_LIMIT = 30


def _events_csv_path():
    return Path(settings.BASE_DIR) / "static" / "events" / "data.csv"


def _today_iso():
    return datetime.now(TOKYO_TZ).date().isoformat()


def _truncate_event_text(value):
    text = (value or "").strip()
    if not text:
        return "-"
    if len(text) <= EVENT_TEXT_LIMIT:
        return text
    return f"{text[:EVENT_TEXT_LIMIT]}..."


def _build_grouped_entries(grouped_items):
    return [
        {"date": date_value, "items": grouped_items[date_value]}
        for date_value in sorted(grouped_items)
    ]


def _load_grouped_events(today_iso=None):
    future_items = {}
    past_items = {}
    csv_path = _events_csv_path()
    effective_today = today_iso or _today_iso()

    if not csv_path.exists():
        return [], []

    with csv_path.open("r", encoding="utf-8", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        for row in reader:
            date_value = (row.get("date") or "").strip()
            if not date_value:
                continue

            impact = (row.get("impact") or "").strip() or "-"
            event_text = (row.get("event") or "").strip()
            item = {
                "time": (row.get("time") or "").strip() or "-",
                "currency": (row.get("currency") or "").strip() or "-",
                "event": event_text or "-",
                "display_event": _truncate_event_text(event_text),
                "impact": impact,
                "is_important": impact == "★★★",
            }

            target = future_items if date_value >= effective_today else past_items
            target.setdefault(date_value, []).append(item)

    return _build_grouped_entries(future_items), _build_grouped_entries(past_items)


def index(request):
    future_groups, past_groups = _load_grouped_events()
    return render(
        request,
        "events/index.html",
        {
            "future_groups": future_groups,
            "has_past_events": bool(past_groups),
            "past_events_url": reverse("events:past_events"),
        },
    )


def past_events(request):
    _, past_groups = _load_grouped_events()
    return render(
        request,
        "events/_event_sections.html",
        {"grouped_entries": past_groups},
    )
