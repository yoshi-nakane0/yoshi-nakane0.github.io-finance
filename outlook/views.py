import csv
import os
import time
from datetime import date, datetime
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from django.conf import settings
from django.shortcuts import redirect, render
from django.urls import reverse

OUTLOOK_DATA_DIR = (
    Path(__file__).resolve().parent.parent / "static" / "outlook" / "data"
)
OUTLOOK_DATA_PATH = OUTLOOK_DATA_DIR / "data.csv"
OUTLOOK_CSV_FIELDS = ("tab", "created_at", "title", "body", "watch_until")
LEGACY_OUTLOOK_CSV_FIELDS = ("created_at", "title", "body", "watch_until")
TOKYO_TZ = ZoneInfo("Asia/Tokyo")
TAB_CHOICES = (
    ("tradeplan", "TradePlan"),
    ("watch", "Watch"),
    ("notes", "Notes"),
)
TAB_LABELS = dict(TAB_CHOICES)
TAB_META = {
    "tradeplan": {"eyebrow": "Trading Focus", "title": "TradePlan"},
    "watch": {"eyebrow": "Priority Monitor", "title": "Watch"},
    "notes": {"eyebrow": "", "title": "Notes"},
}
VALID_TABS = set(TAB_LABELS)
OUTLOOK_SYNC_URL = (os.getenv("OUTLOOK_SYNC_URL") or "").strip()
OUTLOOK_SYNC_INTERVAL_SEC = int(os.getenv("OUTLOOK_SYNC_INTERVAL_SEC", "60"))
OUTLOOK_SYNC_TIMEOUT_SEC = int(os.getenv("OUTLOOK_SYNC_TIMEOUT_SEC", "10"))
_SYNC_STATE = {"last_attempt": 0.0}


def _now_jst():
    return datetime.now(TOKYO_TZ).replace(second=0, microsecond=0)


def _format_datetime(value):
    return value.strftime("%Y-%m-%d %H:%M")


def _normalize_tab(value, default="tradeplan"):
    if value in VALID_TABS:
        return value
    return default


def _remote_csv_is_valid(csv_text):
    reader = csv.DictReader(StringIO(csv_text))
    fieldnames = tuple(reader.fieldnames or ())
    return fieldnames in (OUTLOOK_CSV_FIELDS, LEGACY_OUTLOOK_CSV_FIELDS)


def _sync_outlook_csv_from_remote():
    if not settings.DEBUG or not OUTLOOK_SYNC_URL:
        return

    now = time.monotonic()
    if now - _SYNC_STATE["last_attempt"] < OUTLOOK_SYNC_INTERVAL_SEC:
        return
    _SYNC_STATE["last_attempt"] = now

    try:
        response = requests.get(
            OUTLOOK_SYNC_URL,
            timeout=OUTLOOK_SYNC_TIMEOUT_SEC,
            headers={"Cache-Control": "no-cache"},
        )
        response.raise_for_status()
    except requests.RequestException:
        return

    remote_csv = response.text.lstrip("\ufeff")
    if not remote_csv.strip() or not _remote_csv_is_valid(remote_csv):
        return

    OUTLOOK_DATA_DIR.mkdir(parents=True, exist_ok=True)
    local_csv = ""
    if OUTLOOK_DATA_PATH.exists():
        local_csv = OUTLOOK_DATA_PATH.read_text(encoding="utf-8")

    if local_csv == remote_csv:
        return

    OUTLOOK_DATA_PATH.write_text(remote_csv, encoding="utf-8")


def _rewrite_outlook_csv(rows):
    OUTLOOK_DATA_DIR.mkdir(parents=True, exist_ok=True)
    with OUTLOOK_DATA_PATH.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=OUTLOOK_CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "tab": _normalize_tab(
                        (row.get("tab") or "").strip(),
                        default="notes",
                    ),
                    "created_at": (row.get("created_at") or "").strip(),
                    "title": (row.get("title") or "").strip(),
                    "body": (row.get("body") or "").strip(),
                    "watch_until": (row.get("watch_until") or "").strip(),
                }
            )


def _ensure_outlook_csv():
    OUTLOOK_DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not OUTLOOK_DATA_PATH.exists():
        _rewrite_outlook_csv([])
        return

    with OUTLOOK_DATA_PATH.open("r", newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        fieldnames = tuple(reader.fieldnames or ())
        rows = list(reader)

    if fieldnames == OUTLOOK_CSV_FIELDS:
        return

    if fieldnames == LEGACY_OUTLOOK_CSV_FIELDS:
        _rewrite_outlook_csv(
            [
                {
                    "tab": "notes",
                    "created_at": row.get("created_at"),
                    "title": row.get("title"),
                    "body": row.get("body"),
                    "watch_until": row.get("watch_until"),
                }
                for row in rows
            ]
        )
        return

    normalized_rows = []
    for row in rows:
        normalized_rows.append(
            {
                "tab": row.get("tab") or "notes",
                "created_at": row.get("created_at"),
                "title": row.get("title"),
                "body": row.get("body"),
                "watch_until": row.get("watch_until"),
            }
        )
    _rewrite_outlook_csv(normalized_rows)


def _parse_created_at(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return None


def _parse_watch_until(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _append_item(item_row):
    _ensure_outlook_csv()
    with OUTLOOK_DATA_PATH.open("a", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=OUTLOOK_CSV_FIELDS)
        writer.writerow(item_row)


def _item_status(watch_until_value):
    watch_until_date = _parse_watch_until(watch_until_value)
    if watch_until_date is None:
        return "期限未設定", "status-chip-muted"

    today = _now_jst().date()
    if watch_until_date < today:
        return "期限切れ", "status-chip-overdue"
    if watch_until_date == today:
        return "本日期限", "status-chip-today"
    return "監視中", "status-chip-active"


def _load_items_by_tab():
    _ensure_outlook_csv()
    items_by_tab = {tab: [] for tab in VALID_TABS}

    with OUTLOOK_DATA_PATH.open("r", newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        for row in reader:
            title = (row.get("title") or "").strip()
            body = (row.get("body") or "").strip()
            if not title and not body:
                continue

            tab = _normalize_tab((row.get("tab") or "").strip(), default="notes")
            created_at = (row.get("created_at") or "").strip()
            watch_until = (row.get("watch_until") or "").strip()
            status_label, status_class = _item_status(watch_until)

            items_by_tab[tab].append(
                {
                    "tab": tab,
                    "created_at": created_at,
                    "title": title,
                    "body": body,
                    "watch_until": watch_until,
                    "status_label": status_label,
                    "status_class": status_class,
                    "created_at_sort": _parse_created_at(created_at)
                    or datetime.min,
                }
            )

    for items in items_by_tab.values():
        items.sort(key=lambda item: item["created_at_sort"], reverse=True)

    return items_by_tab


def _build_item_form(post_data=None, default_tab="notes"):
    now_text = _format_datetime(_now_jst())
    if not post_data:
        return {
            "tab": default_tab,
            "created_at": now_text,
            "title": "",
            "body": "",
            "watch_until": (
                ""
                if default_tab == "tradeplan"
                else _now_jst().date().isoformat()
            ),
        }

    return {
        "tab": _normalize_tab(
            (post_data.get("tab") or "").strip(),
            default=default_tab,
        ),
        "created_at": (post_data.get("created_at") or now_text).strip(),
        "title": (post_data.get("title") or "").strip(),
        "body": (post_data.get("body") or "").strip(),
        "watch_until": (post_data.get("watch_until") or "").strip(),
    }


def index(request):
    _sync_outlook_csv_from_remote()
    active_tab = _normalize_tab(request.GET.get("tab"))
    show_item_form = request.GET.get("compose") == "1"
    item_form = _build_item_form(default_tab=active_tab)
    item_errors = {}

    if request.method == "POST":
        item_form = _build_item_form(request.POST, default_tab=active_tab)
        active_tab = item_form["tab"]
        show_item_form = True

        created_at = _parse_created_at(item_form["created_at"])
        watch_until = _parse_watch_until(item_form["watch_until"])
        watch_until_required = item_form["tab"] != "tradeplan"

        if not item_form["title"]:
            item_errors["title"] = "タイトルを入力してください。"
        if not item_form["body"]:
            item_errors["body"] = "本文を入力してください。"
        if watch_until_required and watch_until is None:
            item_errors["watch_until"] = "監視期限を入力してください。"

        if not item_errors:
            item_row = {
                "tab": item_form["tab"],
                "created_at": _format_datetime(created_at or _now_jst()),
                "title": item_form["title"],
                "body": item_form["body"],
                "watch_until": watch_until.isoformat() if watch_until else "",
            }
            _append_item(item_row)
            return redirect(
                f"{reverse('outlook:index')}?tab={item_form['tab']}&saved=1"
            )

    items_by_tab = _load_items_by_tab()
    active_items = items_by_tab[active_tab]
    active_meta = TAB_META[active_tab]

    context = {
        "active_tab": active_tab,
        "active_items": active_items,
        "active_count": len(active_items),
        "active_tab_eyebrow": active_meta["eyebrow"],
        "active_tab_title": active_meta["title"],
        "show_item_form": show_item_form,
        "saved": request.GET.get("saved") == "1",
        "item_form": item_form,
        "item_errors": item_errors,
        "tab_choices": TAB_CHOICES,
    }
    return render(request, "outlook/index.html", context)
