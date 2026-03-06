import csv
import json
import os
import time
from datetime import date, datetime, timedelta
from io import StringIO
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import requests
from django.conf import settings
from django.shortcuts import redirect, render
from django.urls import reverse

OUTLOOK_DATA_DIR = (
    Path(__file__).resolve().parent.parent / "static" / "outlook" / "data"
)
OUTLOOK_DATA_PATH = OUTLOOK_DATA_DIR / "data.csv"
TRADEPLAN_DATA_PATH = OUTLOOK_DATA_DIR / "tradeplan.json"
OUTLOOK_CSV_FIELDS = ("id", "tab", "created_at", "title", "body", "watch_until")
LEGACY_OUTLOOK_CSV_FIELDS = (
    ("tab", "created_at", "title", "body", "watch_until"),
    ("created_at", "title", "body", "watch_until"),
)
TOKYO_TZ = ZoneInfo("Asia/Tokyo")
TAB_CHOICES = (
    ("tradeplan", "TradePlan"),
    ("watch", "Watch"),
    ("notes", "Notes"),
)
ITEM_TAB_CHOICES = (
    ("watch", "Watch"),
    ("notes", "Notes"),
)
TRADEPLAN_LANE_CHOICES = (
    ("long", "Long", "tradeplan-dot-long", "上昇シナリオを入力"),
    ("short", "Short", "tradeplan-dot-short", "下落シナリオを入力"),
    ("square", "Square", "tradeplan-dot-square", "様子見 / 手仕舞いを入力"),
)
TAB_LABELS = dict(TAB_CHOICES)
VALID_TABS = set(TAB_LABELS)
VALID_ITEM_TABS = {value for value, _label in ITEM_TAB_CHOICES}
TAB_META = {
    "tradeplan": {"eyebrow": "Timeline Matrix", "title": "TradePlan"},
    "watch": {"eyebrow": "Priority Monitor", "title": "Watch"},
    "notes": {"eyebrow": "", "title": "Notes"},
}
WEEKDAY_LABELS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
OUTLOOK_SYNC_URL = (os.getenv("OUTLOOK_SYNC_URL") or "").strip()
OUTLOOK_SYNC_INTERVAL_SEC = int(os.getenv("OUTLOOK_SYNC_INTERVAL_SEC", "60"))
OUTLOOK_SYNC_TIMEOUT_SEC = int(os.getenv("OUTLOOK_SYNC_TIMEOUT_SEC", "10"))
_SYNC_STATE = {"last_attempt": 0.0}
CARD_PREVIEW_LIMIT = 30


def _now_jst():
    return datetime.now(TOKYO_TZ).replace(second=0, microsecond=0)


def _today_jst():
    return _now_jst().date()


def _format_datetime(value):
    return value.strftime("%Y-%m-%d %H:%M")


def _normalize_tab(value, default="tradeplan"):
    if value in VALID_TABS:
        return value
    return default


def _normalize_item_tab(value, default="watch"):
    if value in VALID_ITEM_TABS:
        return value
    return default


def _generate_item_id():
    return uuid4().hex


def _as_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "on", "yes"}


def _normalize_item_row(row, default_tab="notes"):
    return {
        "id": (row.get("id") or "").strip() or _generate_item_id(),
        "tab": _normalize_tab(
            (row.get("tab") or "").strip(),
            default=default_tab,
        ),
        "created_at": (row.get("created_at") or "").strip(),
        "title": (row.get("title") or "").strip(),
        "body": (row.get("body") or "").strip(),
        "watch_until": (row.get("watch_until") or "").strip(),
    }


def _normalize_tradeplan_entry(entry):
    plan_date = _parse_plan_date(entry.get("date")) or _today_jst()
    normalized_entry = {"date": plan_date.isoformat()}
    for lane_key, _label, _dot_class, _placeholder in TRADEPLAN_LANE_CHOICES:
        normalized_entry[lane_key] = (entry.get(lane_key) or "").strip()
        normalized_entry[f"{lane_key}_continue"] = _as_bool(
            entry.get(f"{lane_key}_continue")
        )
    return normalized_entry


def _remote_csv_is_valid(csv_text):
    reader = csv.DictReader(StringIO(csv_text))
    fieldnames = tuple(reader.fieldnames or ())
    return fieldnames in (OUTLOOK_CSV_FIELDS, *LEGACY_OUTLOOK_CSV_FIELDS)


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
            writer.writerow(_normalize_item_row(row, default_tab="notes"))


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
        if any(not (row.get("id") or "").strip() for row in rows):
            _rewrite_outlook_csv(rows)
        return

    if fieldnames == LEGACY_OUTLOOK_CSV_FIELDS[0]:
        _rewrite_outlook_csv(rows)
        return

    if fieldnames == LEGACY_OUTLOOK_CSV_FIELDS[1]:
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


def _ensure_tradeplan_data():
    OUTLOOK_DATA_DIR.mkdir(parents=True, exist_ok=True)
    if TRADEPLAN_DATA_PATH.exists():
        return
    TRADEPLAN_DATA_PATH.write_text("[]\n", encoding="utf-8")


def _rewrite_tradeplan_data(entries):
    _ensure_tradeplan_data()
    normalized_entries = [_normalize_tradeplan_entry(entry) for entry in entries]
    normalized_entries.sort(key=lambda entry: entry["date"])
    TRADEPLAN_DATA_PATH.write_text(
        json.dumps(normalized_entries, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _load_tradeplan_entries():
    _ensure_tradeplan_data()
    try:
        loaded = json.loads(TRADEPLAN_DATA_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        loaded = []

    if not isinstance(loaded, list):
        loaded = []

    entries = []
    for entry in loaded:
        if isinstance(entry, dict):
            entries.append(_normalize_tradeplan_entry(entry))

    entries.sort(key=lambda entry: entry["date"])
    return entries


def _save_tradeplan_entry(tradeplan_form):
    entries_by_date = {
        entry["date"]: entry for entry in _load_tradeplan_entries()
    }
    normalized_entry = _normalize_tradeplan_entry(
        {
            "date": tradeplan_form["date"],
            "long": tradeplan_form["long"],
            "long_continue": tradeplan_form["long_continue"],
            "short": tradeplan_form["short"],
            "short_continue": tradeplan_form["short_continue"],
            "square": tradeplan_form["square"],
            "square_continue": tradeplan_form["square_continue"],
        }
    )
    entries_by_date[normalized_entry["date"]] = normalized_entry
    _rewrite_tradeplan_data(entries_by_date.values())


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


def _parse_plan_date(value):
    try:
        return date.fromisoformat((value or "").strip())
    except (TypeError, ValueError, AttributeError):
        return None


def _append_item(item_row):
    _ensure_outlook_csv()
    with OUTLOOK_DATA_PATH.open("a", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=OUTLOOK_CSV_FIELDS)
        writer.writerow(_normalize_item_row(item_row, default_tab="notes"))


def _read_outlook_rows():
    _ensure_outlook_csv()
    with OUTLOOK_DATA_PATH.open("r", newline="", encoding="utf-8") as csv_file:
        return list(csv.DictReader(csv_file))


def _get_item_by_id(item_id):
    for row in _read_outlook_rows():
        normalized_row = _normalize_item_row(row, default_tab="notes")
        if normalized_row["id"] != item_id:
            continue
        if normalized_row["tab"] not in VALID_ITEM_TABS:
            return None
        return normalized_row
    return None


def _update_item(item_id, item_row):
    updated_rows = []
    item_updated = False

    for row in _read_outlook_rows():
        normalized_row = _normalize_item_row(row, default_tab="notes")
        if normalized_row["id"] == item_id:
            updated_rows.append(
                {
                    "id": item_id,
                    "tab": item_row["tab"],
                    "created_at": item_row["created_at"],
                    "title": item_row["title"],
                    "body": item_row["body"],
                    "watch_until": item_row["watch_until"],
                }
            )
            item_updated = True
            continue
        updated_rows.append(normalized_row)

    if item_updated:
        _rewrite_outlook_csv(updated_rows)

    return item_updated


def _delete_items_by_ids(item_ids):
    target_ids = {item_id.strip() for item_id in item_ids if item_id.strip()}
    if not target_ids:
        return 0

    remaining_rows = []
    deleted_count = 0
    for row in _read_outlook_rows():
        if (row.get("id") or "").strip() in target_ids:
            deleted_count += 1
            continue
        remaining_rows.append(row)

    if deleted_count:
        _rewrite_outlook_csv(remaining_rows)
    return deleted_count


def _build_text_display(text, limit=CARD_PREVIEW_LIMIT):
    normalized_text = (text or "").strip()
    return {
        "full": normalized_text,
        "preview": normalized_text[:limit],
        "is_truncated": len(normalized_text) > limit,
    }


def _item_status(watch_until_value):
    watch_until_date = _parse_watch_until(watch_until_value)
    if watch_until_date is None:
        return "期限未設定", "status-chip-muted"

    today = _today_jst()
    if watch_until_date < today:
        return "期限切れ", "status-chip-overdue"
    if watch_until_date == today:
        return "本日期限", "status-chip-today"
    return "監視中", "status-chip-active"


def _load_items_by_tab():
    _ensure_outlook_csv()
    items_by_tab = {tab: [] for tab in VALID_ITEM_TABS}

    for row in _read_outlook_rows():
        title = (row.get("title") or "").strip()
        body = (row.get("body") or "").strip()
        if not title and not body:
            continue

        tab = _normalize_tab((row.get("tab") or "").strip(), default="notes")
        if tab not in VALID_ITEM_TABS:
            continue

        created_at = (row.get("created_at") or "").strip()
        watch_until = (row.get("watch_until") or "").strip()
        status_label, status_class = _item_status(watch_until)

        items_by_tab[tab].append(
            {
                "id": (row.get("id") or "").strip(),
                "tab": tab,
                "created_at": created_at,
                "title": title,
                "title_display": _build_text_display(title),
                "body": body,
                "body_display": _build_text_display(body),
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


def _build_item_form(post_data=None, default_tab="watch"):
    now_text = _format_datetime(_now_jst())
    normalized_default_tab = _normalize_item_tab(default_tab, default="watch")
    if not post_data:
        return {
            "edit_id": "",
            "tab": normalized_default_tab,
            "created_at": now_text,
            "title": "",
            "body": "",
            "watch_until": _today_jst().isoformat(),
        }

    return {
        "edit_id": (post_data.get("edit_id") or "").strip(),
        "tab": _normalize_item_tab(
            (post_data.get("tab") or "").strip(),
            default=normalized_default_tab,
        ),
        "created_at": (post_data.get("created_at") or now_text).strip(),
        "title": (post_data.get("title") or "").strip(),
        "body": (post_data.get("body") or "").strip(),
        "watch_until": (post_data.get("watch_until") or "").strip(),
    }


def _build_tradeplan_form(post_data=None, default_date=None):
    parsed_default_date = _parse_plan_date(default_date) or _today_jst()
    tradeplan_form = {"date": parsed_default_date.isoformat()}
    for lane_key, _label, _dot_class, _placeholder in TRADEPLAN_LANE_CHOICES:
        tradeplan_form[lane_key] = ""
        tradeplan_form[f"{lane_key}_continue"] = False

    if not post_data:
        return tradeplan_form

    tradeplan_form["date"] = (post_data.get("plan_date") or "").strip()
    for lane_key, _label, _dot_class, _placeholder in TRADEPLAN_LANE_CHOICES:
        tradeplan_form[lane_key] = (post_data.get(lane_key) or "").strip()
        tradeplan_form[f"{lane_key}_continue"] = _as_bool(
            post_data.get(f"{lane_key}_continue")
        )
    return tradeplan_form


def _build_tradeplan_form_lanes(tradeplan_form):
    lanes = []
    for lane_key, label, dot_class, placeholder in TRADEPLAN_LANE_CHOICES:
        lanes.append(
            {
                "key": lane_key,
                "label": label,
                "dot_class": dot_class,
                "placeholder": placeholder,
                "text": tradeplan_form[lane_key],
                "continues": tradeplan_form[f"{lane_key}_continue"],
            }
        )
    return lanes


def _count_tradeplan_signals(entries):
    signal_count = 0
    for entry in entries:
        for lane_key, _label, _dot_class, _placeholder in TRADEPLAN_LANE_CHOICES:
            if entry.get(lane_key):
                signal_count += 1
    return signal_count


def _build_tradeplan_timeline(entries, focus_date=None):
    focus_date = focus_date or _today_jst()
    entry_dates = [
        _parse_plan_date(entry.get("date"))
        for entry in entries
        if _parse_plan_date(entry.get("date")) is not None
    ]
    anchor_dates = entry_dates + [focus_date, _today_jst()]
    start_date = min(anchor_dates) - timedelta(days=1)
    end_date = max(anchor_dates)
    minimum_end_date = focus_date + timedelta(days=8)
    if end_date < minimum_end_date:
        end_date = minimum_end_date

    days = []
    current_date = start_date
    while current_date <= end_date:
        days.append(
            {
                "iso": current_date.isoformat(),
                "label": current_date.strftime("%m/%d"),
                "weekday": WEEKDAY_LABELS[current_date.weekday()],
                "is_today": current_date == _today_jst(),
                "is_focus": current_date == focus_date,
            }
        )
        current_date += timedelta(days=1)

    entries_by_date = {entry["date"]: entry for entry in entries}
    rows = []
    for lane_key, label, dot_class, _placeholder in TRADEPLAN_LANE_CHOICES:
        cells = []
        for day in days:
            entry = entries_by_date.get(day["iso"], {})
            text = (entry.get(lane_key) or "").strip()
            cells.append(
                {
                    "has_value": bool(text),
                    "text": text,
                    "continues": _as_bool(entry.get(f"{lane_key}_continue")),
                    "dot_class": dot_class,
                }
            )
        rows.append(
            {
                "key": lane_key,
                "label": label,
                "dot_class": dot_class,
                "cells": cells,
            }
        )
    return days, rows


def index(request):
    _sync_outlook_csv_from_remote()
    active_tab = _normalize_tab(request.GET.get("tab"))
    default_item_tab = active_tab if active_tab in VALID_ITEM_TABS else "watch"
    editing_item_id = ""
    show_item_form = (
        active_tab in VALID_ITEM_TABS and request.GET.get("compose") == "1"
    )
    item_form = _build_item_form(default_tab=default_item_tab)
    item_errors = {}
    tradeplan_form = _build_tradeplan_form(default_date=request.GET.get("plan_date"))
    tradeplan_errors = {}

    edit_item_id = (request.GET.get("edit") or "").strip()
    if request.method != "POST" and active_tab in VALID_ITEM_TABS and edit_item_id:
        edit_item = _get_item_by_id(edit_item_id)
        if edit_item and edit_item["tab"] == active_tab:
            show_item_form = True
            editing_item_id = edit_item["id"]
            item_form = _build_item_form(edit_item, default_tab=edit_item["tab"])
            item_form["edit_id"] = edit_item["id"]

    if request.method == "POST":
        if request.POST.get("tradeplan_action") == "save":
            active_tab = "tradeplan"
            tradeplan_form = _build_tradeplan_form(request.POST)
            plan_date = _parse_plan_date(tradeplan_form["date"])
            if plan_date is None:
                tradeplan_errors["date"] = "日付を入力してください。"
            if not any(
                tradeplan_form[lane_key]
                for lane_key, _label, _dot_class, _placeholder in TRADEPLAN_LANE_CHOICES
            ):
                tradeplan_errors["non_field"] = (
                    "Long / Short / Square のいずれかを入力してください。"
                )

            if not tradeplan_errors:
                _save_tradeplan_entry(tradeplan_form)
                return redirect(
                    f"{reverse('outlook:index')}?tab=tradeplan&tradeplan_saved=1&plan_date={tradeplan_form['date']}"
                )

        else:
            delete_tab = _normalize_tab(
                request.POST.get("active_tab"),
                default=active_tab,
            )
            delete_id = (request.POST.get("delete_id") or "").strip()
            selected_ids = [
                item_id.strip()
                for item_id in request.POST.getlist("selected_ids")
                if item_id.strip()
            ]

            if delete_id:
                _delete_items_by_ids([delete_id])
                return redirect(f"{reverse('outlook:index')}?tab={delete_tab}")

            if request.POST.get("action") == "delete_selected":
                _delete_items_by_ids(selected_ids)
                return redirect(f"{reverse('outlook:index')}?tab={delete_tab}")

            item_form = _build_item_form(
                request.POST,
                default_tab=default_item_tab,
            )
            active_tab = item_form["tab"]
            show_item_form = True
            editing_item_id = item_form["edit_id"]

            created_at = _parse_created_at(item_form["created_at"])
            watch_until = _parse_watch_until(item_form["watch_until"])

            if not item_form["title"]:
                item_errors["title"] = "タイトルを入力してください。"
            if not item_form["body"]:
                item_errors["body"] = "本文を入力してください。"
            if watch_until is None:
                item_errors["watch_until"] = "監視期限を入力してください。"

            if not item_errors:
                item_row = {
                    "tab": item_form["tab"],
                    "created_at": _format_datetime(created_at or _now_jst()),
                    "title": item_form["title"],
                    "body": item_form["body"],
                    "watch_until": watch_until.isoformat(),
                }
                if editing_item_id:
                    _update_item(editing_item_id, item_row)
                else:
                    _append_item(item_row)
                return redirect(
                    f"{reverse('outlook:index')}?tab={item_form['tab']}&saved=1"
                )

    items_by_tab = _load_items_by_tab()
    active_items = items_by_tab.get(active_tab, [])
    active_meta = TAB_META[active_tab]
    tradeplan_entries = _load_tradeplan_entries()
    focus_plan_date = _parse_plan_date(tradeplan_form["date"]) or _today_jst()
    tradeplan_days, tradeplan_rows = _build_tradeplan_timeline(
        tradeplan_entries,
        focus_date=focus_plan_date,
    )
    active_count = (
        _count_tradeplan_signals(tradeplan_entries)
        if active_tab == "tradeplan"
        else len(active_items)
    )

    context = {
        "active_tab": active_tab,
        "active_items": active_items,
        "active_count": active_count,
        "active_count_label": "signals" if active_tab == "tradeplan" else "items",
        "active_tab_eyebrow": active_meta["eyebrow"],
        "active_tab_title": active_meta["title"],
        "show_compose": active_tab in VALID_ITEM_TABS,
        "show_item_form": show_item_form,
        "saved": request.GET.get("saved") == "1",
        "tradeplan_saved": request.GET.get("tradeplan_saved") == "1",
        "editing_item": bool(editing_item_id),
        "item_form": item_form,
        "item_errors": item_errors,
        "tab_choices": ITEM_TAB_CHOICES,
        "tradeplan_form": tradeplan_form,
        "tradeplan_form_lanes": _build_tradeplan_form_lanes(tradeplan_form),
        "tradeplan_errors": tradeplan_errors,
        "tradeplan_days": tradeplan_days,
        "tradeplan_rows": tradeplan_rows,
    }
    return render(request, "outlook/index.html", context)
