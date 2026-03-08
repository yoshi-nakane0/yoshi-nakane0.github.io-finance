import calendar
import csv
import json
import os
import time
from datetime import date, datetime, timedelta
from io import StringIO
from urllib.parse import urljoin
from uuid import uuid4
from zoneinfo import ZoneInfo

import requests
from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from .models import OutlookItem, TradePlanEntry, TradePlanPosition

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
TRADEPLAN_POSITION_TYPES = {"long", "short"}
TAB_LABELS = dict(TAB_CHOICES)
VALID_TABS = set(TAB_LABELS)
VALID_ITEM_TABS = {value for value, _label in ITEM_TAB_CHOICES}
TAB_META = {
    "tradeplan": {"eyebrow": "", "title": "TradePlan"},
    "watch": {"eyebrow": "Priority Monitor", "title": "Watch"},
    "notes": {"eyebrow": "", "title": "Notes"},
}
CALENDAR_WEEKDAY_LABELS = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
TRADEPLAN_MIN_DATE = date(2026, 1, 1)
TRADEPLAN_MAX_DATE = date(2028, 12, 31)
TRADEPLAN_YEAR_CHOICES = tuple(
    range(TRADEPLAN_MIN_DATE.year, TRADEPLAN_MAX_DATE.year + 1)
)
TRADEPLAN_MONTH_CHOICES = tuple((month, f"{month}月") for month in range(1, 13))
TRADEPLAN_TEXT_PREVIEW_LIMIT = 22
OUTLOOK_SYNC_BASE_URL = (os.getenv("OUTLOOK_SYNC_BASE_URL") or "").strip()
OUTLOOK_SYNC_URL = (os.getenv("OUTLOOK_SYNC_URL") or "").strip()
OUTLOOK_SYNC_DATA_URL = (
    os.getenv("OUTLOOK_SYNC_DATA_URL") or OUTLOOK_SYNC_URL
).strip()
OUTLOOK_SYNC_TRADEPLAN_URL = (os.getenv("OUTLOOK_SYNC_TRADEPLAN_URL") or "").strip()
OUTLOOK_SYNC_TRADEPLAN_POSITIONS_URL = (
    os.getenv("OUTLOOK_SYNC_TRADEPLAN_POSITIONS_URL") or ""
).strip()
OUTLOOK_SYNC_INTERVAL_SEC = int(os.getenv("OUTLOOK_SYNC_INTERVAL_SEC", "60"))
OUTLOOK_SYNC_TIMEOUT_SEC = int(os.getenv("OUTLOOK_SYNC_TIMEOUT_SEC", "10"))
_SYNC_STATE = {"last_attempt": 0.0}
CARD_PREVIEW_LIMIT = 30
TRADEPLAN_POSITION_STORAGE_KEY = "outlook:tradeplan:positions:v1"


def _now_jst():
    return datetime.now(TOKYO_TZ).replace(second=0, microsecond=0)


def _today_jst():
    return _now_jst().date()


def _format_datetime(value):
    return value.strftime("%Y-%m-%d %H:%M")


def _tradeplan_position_storage_mode():
    if settings.DEBUG:
        return "database"
    if (os.getenv("DATABASE_URL") or "").strip():
        return "database"
    return "browser"


def _tradeplan_position_storage_notice():
    if _tradeplan_position_storage_mode() != "browser":
        return ""
    return (
        "TradePlan の Long / Short はこのブラウザに保存されます。"
        "別端末・別ブラウザには同期されません。"
    )


def _is_tradeplan_date_in_range(value):
    return value is not None and TRADEPLAN_MIN_DATE <= value <= TRADEPLAN_MAX_DATE


def _clamp_tradeplan_date(value):
    if value is None:
        return None
    if value < TRADEPLAN_MIN_DATE:
        return TRADEPLAN_MIN_DATE
    if value > TRADEPLAN_MAX_DATE:
        return TRADEPLAN_MAX_DATE
    return value


def _resolve_tradeplan_date(value=None):
    parsed_value = _parse_plan_date(value) or _today_jst()
    return _clamp_tradeplan_date(parsed_value)


def _month_start(value):
    return date(value.year, value.month, 1)


def _shift_month(value, month_delta):
    month_index = (value.year * 12 + value.month - 1) + month_delta
    year, zero_based_month = divmod(month_index, 12)
    return date(year, zero_based_month + 1, 1)


def _resolve_tradeplan_calendar_month(
    year_value=None,
    month_value=None,
    focus_date=None,
):
    fallback_month = _month_start(_resolve_tradeplan_date(focus_date))
    try:
        selected_month = date(int(year_value), int(month_value), 1)
    except (TypeError, ValueError):
        return fallback_month

    minimum_month = _month_start(TRADEPLAN_MIN_DATE)
    maximum_month = _month_start(TRADEPLAN_MAX_DATE)
    if selected_month < minimum_month:
        return minimum_month
    if selected_month > maximum_month:
        return maximum_month
    return selected_month


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
    normalized_tab = _normalize_tab(
        (row.get("tab") or "").strip(),
        default=default_tab,
    )
    return {
        "id": (row.get("id") or "").strip() or _generate_item_id(),
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


def _normalize_tradeplan_entry(entry):
    plan_date = _parse_plan_date(entry.get("date")) or _today_jst()
    normalized_entry = {"date": plan_date.isoformat()}
    for lane_key, _label, _dot_class, _placeholder in TRADEPLAN_LANE_CHOICES:
        normalized_entry[lane_key] = (entry.get(lane_key) or "").strip()
        normalized_entry[f"{lane_key}_continue"] = _as_bool(
            entry.get(f"{lane_key}_continue")
        )
    return normalized_entry


def _normalize_tradeplan_position(position):
    raw_start_date = position.get("start_date") or position.get("date")
    raw_end_date = position.get("end_date") or raw_start_date
    start_date = _clamp_tradeplan_date(
        _parse_plan_date(raw_start_date) or _today_jst()
    )
    end_date = _clamp_tradeplan_date(_parse_plan_date(raw_end_date) or start_date)
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    normalized_type = str(position.get("type") or "").strip().lower()
    if normalized_type not in TRADEPLAN_POSITION_TYPES:
        normalized_type = "long"

    return {
        "id": (position.get("id") or "").strip() or _generate_item_id(),
        "type": normalized_type,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }


def _remote_csv_is_valid(csv_text):
    reader = csv.DictReader(StringIO(csv_text))
    fieldnames = tuple(reader.fieldnames or ())
    return fieldnames in (OUTLOOK_CSV_FIELDS, *LEGACY_OUTLOOK_CSV_FIELDS)


def _normalize_tradeplan_entries(entries):
    normalized_entries = []
    for entry in entries:
        if isinstance(entry, dict):
            normalized_entries.append(_normalize_tradeplan_entry(entry))
    normalized_entries.sort(key=lambda entry: entry["date"])
    return normalized_entries


def _normalize_tradeplan_positions_list(positions):
    normalized_positions = []
    for position in positions:
        if isinstance(position, dict):
            normalized_positions.append(_normalize_tradeplan_position(position))
    normalized_positions.sort(
        key=lambda position: (
            position["start_date"],
            position["end_date"],
            position["type"],
            position["id"],
        )
    )
    return normalized_positions


def _normalize_outlook_rows(rows):
    normalized_rows = []
    seen_ids = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        normalized_row = _normalize_item_row(row, default_tab="notes")
        if normalized_row["tab"] not in VALID_ITEM_TABS:
            continue

        item_id = normalized_row["id"]
        while item_id in seen_ids:
            item_id = _generate_item_id()
        normalized_row["id"] = item_id
        seen_ids.add(item_id)
        normalized_rows.append(normalized_row)

    normalized_rows.sort(
        key=lambda row: (
            row["created_at"],
            row["id"],
            row["tab"],
            row["title"],
            row["body"],
            row["watch_until"],
        )
    )
    return normalized_rows


def _serialize_outlook_item(item):
    return {
        "id": item.id,
        "tab": item.tab,
        "created_at": item.created_at,
        "title": item.title,
        "body": item.body,
        "watch_until": item.watch_until.isoformat() if item.watch_until else "",
    }


def _serialize_tradeplan_entry(entry):
    return {
        "date": entry.plan_date.isoformat(),
        "long": entry.long_text,
        "long_continue": entry.long_continue,
        "short": entry.short_text,
        "short_continue": entry.short_continue,
        "square": entry.square_text,
        "square_continue": entry.square_continue,
    }


def _serialize_tradeplan_position(position):
    return {
        "id": position.id,
        "type": position.position_type,
        "start_date": position.start_date.isoformat(),
        "end_date": position.end_date.isoformat(),
    }


def _resolve_outlook_sync_url(explicit_url, relative_path, sibling_name):
    if explicit_url:
        return explicit_url
    if OUTLOOK_SYNC_BASE_URL:
        return urljoin(
            OUTLOOK_SYNC_BASE_URL.rstrip("/") + "/",
            relative_path,
        )
    if OUTLOOK_SYNC_DATA_URL:
        return urljoin(OUTLOOK_SYNC_DATA_URL, sibling_name)
    return ""


def _build_outlook_sync_targets():
    targets = (
        {
            "remote_url": _resolve_outlook_sync_url(
                OUTLOOK_SYNC_DATA_URL,
                "static/outlook/data/data.csv",
                "data.csv",
            ),
            "sync": _sync_remote_outlook_csv,
        },
        {
            "remote_url": _resolve_outlook_sync_url(
                OUTLOOK_SYNC_TRADEPLAN_URL,
                "static/outlook/data/tradeplan.json",
                "tradeplan.json",
            ),
            "sync": _sync_remote_tradeplan_entries,
        },
        {
            "remote_url": _resolve_outlook_sync_url(
                OUTLOOK_SYNC_TRADEPLAN_POSITIONS_URL,
                "static/outlook/data/tradeplan_positions.json",
                "tradeplan_positions.json",
            ),
            "sync": _sync_remote_tradeplan_positions,
        },
    )
    return [target for target in targets if target["remote_url"]]


def _fetch_remote_text(remote_url):
    if not remote_url:
        return None

    try:
        response = requests.get(
            remote_url,
            timeout=OUTLOOK_SYNC_TIMEOUT_SEC,
            headers={"Cache-Control": "no-cache"},
        )
        response.raise_for_status()
    except requests.RequestException:
        return None
    return response.text.lstrip("\ufeff")


def _sync_remote_outlook_csv(remote_url):
    remote_csv = _fetch_remote_text(remote_url)
    if not remote_csv or not _remote_csv_is_valid(remote_csv):
        return

    remote_rows = _normalize_outlook_rows(list(csv.DictReader(StringIO(remote_csv))))
    if _read_outlook_rows() == remote_rows:
        return
    _replace_outlook_items(remote_rows)


def _load_remote_json_list(remote_url):
    remote_text = _fetch_remote_text(remote_url)
    if not remote_text:
        return None

    try:
        loaded = json.loads(remote_text)
    except json.JSONDecodeError:
        return None

    if not isinstance(loaded, list):
        return None
    return loaded


def _sync_remote_tradeplan_entries(remote_url):
    remote_entries = _load_remote_json_list(remote_url)
    if remote_entries is None:
        return

    normalized_remote_entries = _normalize_tradeplan_entries(remote_entries)
    if _load_tradeplan_entries() == normalized_remote_entries:
        return
    _rewrite_tradeplan_data(normalized_remote_entries)


def _sync_remote_tradeplan_positions(remote_url):
    remote_positions = _load_remote_json_list(remote_url)
    if remote_positions is None:
        return

    normalized_remote_positions = _normalize_tradeplan_positions_list(
        remote_positions
    )
    if _load_tradeplan_positions() == normalized_remote_positions:
        return
    _rewrite_tradeplan_positions(normalized_remote_positions)


def sync_local_outlook_data_from_remote(force=False):
    if not settings.DEBUG:
        return

    sync_targets = _build_outlook_sync_targets()
    if not sync_targets:
        return

    now = time.monotonic()
    if not force and now - _SYNC_STATE["last_attempt"] < OUTLOOK_SYNC_INTERVAL_SEC:
        return
    _SYNC_STATE["last_attempt"] = now

    for target in sync_targets:
        target["sync"](target["remote_url"])


def _replace_outlook_items(rows):
    normalized_rows = _normalize_outlook_rows(rows)
    replacement_items = [
        OutlookItem(
            id=row["id"],
            tab=row["tab"],
            created_at=row["created_at"],
            title=row["title"],
            body=row["body"],
            watch_until=_parse_watch_until(row["watch_until"]),
        )
        for row in normalized_rows
    ]

    with transaction.atomic():
        OutlookItem.objects.all().delete()
        OutlookItem.objects.bulk_create(replacement_items)


def _rewrite_tradeplan_data(entries):
    normalized_entries = _normalize_tradeplan_entries(entries)
    replacement_entries = [
        TradePlanEntry(
            plan_date=_parse_plan_date(entry["date"]),
            long_text=entry["long"],
            long_continue=entry["long_continue"],
            short_text=entry["short"],
            short_continue=entry["short_continue"],
            square_text=entry["square"],
            square_continue=entry["square_continue"],
        )
        for entry in normalized_entries
    ]

    with transaction.atomic():
        TradePlanEntry.objects.all().delete()
        TradePlanEntry.objects.bulk_create(replacement_entries)


def _load_tradeplan_entries():
    return [
        _serialize_tradeplan_entry(entry)
        for entry in TradePlanEntry.objects.all().order_by("plan_date")
    ]


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
    plan_date = _parse_plan_date(normalized_entry["date"])
    if plan_date is None:
        return
    TradePlanEntry.objects.update_or_create(
        plan_date=plan_date,
        defaults={
            "long_text": normalized_entry["long"],
            "long_continue": normalized_entry["long_continue"],
            "short_text": normalized_entry["short"],
            "short_continue": normalized_entry["short_continue"],
            "square_text": normalized_entry["square"],
            "square_continue": normalized_entry["square_continue"],
        },
    )


def _rewrite_tradeplan_positions(positions):
    normalized_positions = _normalize_tradeplan_positions_list(positions)
    replacement_positions = [
        TradePlanPosition(
            id=position["id"],
            position_type=position["type"],
            start_date=_parse_plan_date(position["start_date"]),
            end_date=_parse_plan_date(position["end_date"]),
        )
        for position in normalized_positions
    ]

    with transaction.atomic():
        TradePlanPosition.objects.all().delete()
        TradePlanPosition.objects.bulk_create(replacement_positions)


def _load_tradeplan_positions():
    return [
        _serialize_tradeplan_position(position)
        for position in TradePlanPosition.objects.all().order_by(
            "start_date",
            "end_date",
            "position_type",
            "id",
        )
    ]


def _position_overlaps_range(position, start_date, end_date):
    position_start = _parse_plan_date(position.get("start_date"))
    position_end = _parse_plan_date(position.get("end_date"))
    if position_start is None or position_end is None:
        return False
    return position_start <= end_date and position_end >= start_date


def _visible_tradeplan_positions(positions, start_date, end_date):
    return [
        position
        for position in positions
        if _position_overlaps_range(position, start_date, end_date)
    ]


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
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat((value or "").strip())
    except (TypeError, ValueError, AttributeError):
        return None


def _append_item(item_row):
    normalized_row = _normalize_item_row(item_row, default_tab="notes")
    if normalized_row["tab"] not in VALID_ITEM_TABS:
        return
    OutlookItem.objects.create(
        id=normalized_row["id"],
        tab=normalized_row["tab"],
        created_at=normalized_row["created_at"],
        title=normalized_row["title"],
        body=normalized_row["body"],
        watch_until=_parse_watch_until(normalized_row["watch_until"]),
    )


def _read_outlook_rows():
    return _normalize_outlook_rows(
        [
            _serialize_outlook_item(item)
            for item in OutlookItem.objects.all().order_by(
                "created_at",
                "id",
                "tab",
            )
        ]
    )


def _get_item_by_id(item_id):
    try:
        item = OutlookItem.objects.get(id=item_id)
    except OutlookItem.DoesNotExist:
        return None

    if item.tab not in VALID_ITEM_TABS:
        return None
    return _serialize_outlook_item(item)


def _update_item(item_id, item_row):
    return bool(
        OutlookItem.objects.filter(id=item_id).update(
            tab=item_row["tab"],
            created_at=item_row["created_at"],
            title=item_row["title"],
            body=item_row["body"],
            watch_until=_parse_watch_until(item_row["watch_until"]),
        )
    )


def _delete_items_by_ids(item_ids):
    target_ids = {item_id.strip() for item_id in item_ids if item_id.strip()}
    if not target_ids:
        return 0

    deleted_count, _details = OutlookItem.objects.filter(id__in=target_ids).delete()
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
    items_by_tab = {tab: [] for tab in VALID_ITEM_TABS}

    for item in OutlookItem.objects.filter(tab__in=VALID_ITEM_TABS).order_by(
        "-created_at",
        "-id",
    ):
        title = item.title.strip()
        body = item.body.strip()
        if not title and not body:
            continue

        tab = item.tab
        created_at = item.created_at
        watch_until = item.watch_until.isoformat() if item.watch_until else ""
        status_label, status_class = _item_status(watch_until)

        items_by_tab[tab].append(
            {
                "id": item.id,
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
            "watch_until": (
                _today_jst().isoformat()
                if normalized_default_tab == "watch"
                else ""
            ),
        }

    normalized_tab = _normalize_item_tab(
        (post_data.get("tab") or "").strip(),
        default=normalized_default_tab,
    )
    return {
        "edit_id": (post_data.get("edit_id") or "").strip(),
        "tab": normalized_tab,
        "created_at": (post_data.get("created_at") or now_text).strip(),
        "title": (post_data.get("title") or "").strip(),
        "body": (post_data.get("body") or "").strip(),
        "watch_until": (
            (post_data.get("watch_until") or "").strip()
            if normalized_tab == "watch"
            else ""
        ),
    }


def _find_tradeplan_entry(entries, plan_date):
    if plan_date is None:
        return None
    target_date = plan_date.isoformat()
    for entry in entries:
        if entry.get("date") == target_date:
            return entry
    return None


def _build_tradeplan_form(post_data=None, default_date=None, entry=None):
    parsed_default_date = _resolve_tradeplan_date(default_date)
    tradeplan_form = {"date": parsed_default_date.isoformat()}
    for lane_key, _label, _dot_class, _placeholder in TRADEPLAN_LANE_CHOICES:
        tradeplan_form[lane_key] = (entry or {}).get(lane_key, "").strip()
        tradeplan_form[f"{lane_key}_continue"] = _as_bool(
            (entry or {}).get(f"{lane_key}_continue")
        )

    if not post_data:
        return tradeplan_form

    tradeplan_form["date"] = (post_data.get("plan_date") or "").strip()
    for lane_key, _label, _dot_class, _placeholder in TRADEPLAN_LANE_CHOICES:
        tradeplan_form[lane_key] = (post_data.get(lane_key) or "").strip()
        tradeplan_form[f"{lane_key}_continue"] = _as_bool(
            post_data.get(f"{lane_key}_continue")
        )
    return tradeplan_form


def _build_tradeplan_calendar_signals(entry):
    signals = []
    if not entry:
        return signals

    for lane_key, label, dot_class, _placeholder in TRADEPLAN_LANE_CHOICES:
        text = (entry.get(lane_key) or "").strip()
        if not text:
            continue
        text_display = _build_text_display(text, limit=TRADEPLAN_TEXT_PREVIEW_LIMIT)
        signals.append(
            {
                "key": lane_key,
                "label": label,
                "dot_class": dot_class,
                "text": text_display["preview"],
                "full_text": text_display["full"],
                "is_truncated": text_display["is_truncated"],
                "continues": _as_bool(entry.get(f"{lane_key}_continue")),
            }
        )
    return signals


def _build_tradeplan_calendar(entries, displayed_month, focus_date=None):
    displayed_month = _month_start(displayed_month)
    first_day = displayed_month
    last_day = date(
        displayed_month.year,
        displayed_month.month,
        calendar.monthrange(displayed_month.year, displayed_month.month)[1],
    )
    grid_start = first_day - timedelta(days=(first_day.weekday() + 1) % 7)
    grid_end = last_day + timedelta(days=(5 - last_day.weekday()) % 7)
    entries_by_date = {entry["date"]: entry for entry in entries}
    focus_date = _parse_plan_date(focus_date)
    calendar_days = []
    current_date = grid_start

    while current_date <= grid_end:
        signals = _build_tradeplan_calendar_signals(
            entries_by_date.get(current_date.isoformat())
        )
        is_in_range = _is_tradeplan_date_in_range(current_date)
        calendar_days.append(
            {
                "iso": current_date.isoformat(),
                "day": current_date.day,
                "year": current_date.year,
                "month": current_date.month,
                "is_current_month": current_date.month == displayed_month.month
                and current_date.year == displayed_month.year,
                "is_today": current_date == _today_jst(),
                "is_focus": current_date == focus_date,
                "is_in_range": is_in_range,
                "signals": signals if is_in_range else [],
            }
        )
        current_date += timedelta(days=1)

    previous_month = _shift_month(displayed_month, -1)
    next_month = _shift_month(displayed_month, 1)
    minimum_month = _month_start(TRADEPLAN_MIN_DATE)
    maximum_month = _month_start(TRADEPLAN_MAX_DATE)

    return {
        "year": displayed_month.year,
        "month": displayed_month.month,
        "month_title": displayed_month.strftime("%b %Y"),
        "days": calendar_days,
        "grid_start": grid_start.isoformat(),
        "grid_end": grid_end.isoformat(),
        "weekday_labels": CALENDAR_WEEKDAY_LABELS,
        "previous_month": previous_month if previous_month >= minimum_month else None,
        "next_month": next_month if next_month <= maximum_month else None,
    }


@require_http_methods(["POST"])
def tradeplan_positions(request):
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "invalid_json"}, status=400)

    action = str(payload.get("action") or "").strip().lower()
    positions = _load_tradeplan_positions()

    if action == "create":
        if str(payload.get("type") or "").strip().lower() not in TRADEPLAN_POSITION_TYPES:
            return JsonResponse({"ok": False, "error": "invalid_type"}, status=400)

        created_position = _normalize_tradeplan_position(payload)
        positions.append(created_position)
        _rewrite_tradeplan_positions(positions)
        return JsonResponse(
            {
                "ok": True,
                "position": created_position,
                "positions": _load_tradeplan_positions(),
            }
        )

    if action == "update":
        position_id = str(payload.get("id") or "").strip()
        if not position_id:
            return JsonResponse({"ok": False, "error": "missing_id"}, status=400)

        updated_position = None
        for index, position in enumerate(positions):
            if position["id"] != position_id:
                continue
            merged_position = {
                "id": position_id,
                "type": payload.get("type", position["type"]),
                "start_date": payload.get("start_date", position["start_date"]),
                "end_date": payload.get("end_date", position["end_date"]),
            }
            updated_position = _normalize_tradeplan_position(merged_position)
            positions[index] = updated_position
            break

        if updated_position is None:
            return JsonResponse({"ok": False, "error": "not_found"}, status=404)

        _rewrite_tradeplan_positions(positions)
        return JsonResponse(
            {
                "ok": True,
                "position": updated_position,
                "positions": _load_tradeplan_positions(),
            }
        )

    if action == "delete":
        position_id = str(payload.get("id") or "").strip()
        if not position_id:
            return JsonResponse({"ok": False, "error": "missing_id"}, status=400)

        remaining_positions = [
            position for position in positions if position["id"] != position_id
        ]
        if len(remaining_positions) == len(positions):
            return JsonResponse({"ok": False, "error": "not_found"}, status=404)

        _rewrite_tradeplan_positions(remaining_positions)
        return JsonResponse(
            {"ok": True, "positions": _load_tradeplan_positions()}
        )

    return JsonResponse({"ok": False, "error": "invalid_action"}, status=400)


@ensure_csrf_cookie
def index(request):
    sync_local_outlook_data_from_remote()
    active_tab = _normalize_tab(request.GET.get("tab"))
    default_item_tab = active_tab if active_tab in VALID_ITEM_TABS else "watch"
    editing_item_id = ""
    show_item_form = (
        active_tab in VALID_ITEM_TABS and request.GET.get("compose") == "1"
    )
    item_form = _build_item_form(default_tab=default_item_tab)
    item_errors = {}
    tradeplan_entries = _load_tradeplan_entries()
    tradeplan_positions = _load_tradeplan_positions()
    requested_plan_date = _parse_plan_date(request.GET.get("plan_date"))
    requested_calendar_year = request.GET.get("calendar_year")
    requested_calendar_month = request.GET.get("calendar_month")
    displayed_month = _resolve_tradeplan_calendar_month(
        requested_calendar_year,
        requested_calendar_month,
        focus_date=requested_plan_date or _today_jst(),
    )
    if _is_tradeplan_date_in_range(requested_plan_date):
        focus_plan_date = requested_plan_date
    elif requested_calendar_year or requested_calendar_month:
        focus_plan_date = _resolve_tradeplan_date(displayed_month)
    else:
        focus_plan_date = _resolve_tradeplan_date(_today_jst())
    tradeplan_form = _build_tradeplan_form(
        default_date=focus_plan_date,
        entry=_find_tradeplan_entry(tradeplan_entries, focus_plan_date),
    )
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
            displayed_month = _resolve_tradeplan_calendar_month(
                request.POST.get("calendar_year"),
                request.POST.get("calendar_month"),
                focus_date=plan_date,
            )
            if plan_date is None:
                tradeplan_errors["date"] = "日付を入力してください。"
            elif not _is_tradeplan_date_in_range(plan_date):
                tradeplan_errors["date"] = (
                    "対象日は 2026-01-01 から 2028-12-31 の範囲で入力してください。"
                )
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
                    f"{reverse('outlook:index')}?tab=tradeplan"
                    f"&tradeplan_saved=1&plan_date={tradeplan_form['date']}"
                    f"&calendar_year={plan_date.year}&calendar_month={plan_date.month}"
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
            if item_form["tab"] == "watch" and watch_until is None:
                item_errors["watch_until"] = "監視期限を入力してください。"

            if not item_errors:
                item_row = {
                    "tab": item_form["tab"],
                    "created_at": _format_datetime(created_at or _now_jst()),
                    "title": item_form["title"],
                    "body": item_form["body"],
                    "watch_until": (
                        watch_until.isoformat()
                        if item_form["tab"] == "watch" and watch_until is not None
                        else ""
                    ),
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
    selected_tradeplan_date = _parse_plan_date(tradeplan_form["date"])
    if not _is_tradeplan_date_in_range(selected_tradeplan_date):
        selected_tradeplan_date = None
    active_count = len(active_items)
    tradeplan_calendar = _build_tradeplan_calendar(
        tradeplan_entries,
        displayed_month,
        focus_date=selected_tradeplan_date,
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
        "editing_item": bool(editing_item_id),
        "item_form": item_form,
        "item_errors": item_errors,
        "tab_choices": ITEM_TAB_CHOICES,
        "tradeplan_form": tradeplan_form,
        "tradeplan_calendar": tradeplan_calendar,
        "tradeplan_positions": _visible_tradeplan_positions(
            tradeplan_positions,
            _parse_plan_date(tradeplan_calendar["grid_start"]),
            _parse_plan_date(tradeplan_calendar["grid_end"]),
        ),
        "tradeplan_position_api_url": reverse("outlook:tradeplan_positions"),
        "tradeplan_position_min_date": TRADEPLAN_MIN_DATE.isoformat(),
        "tradeplan_position_max_date": TRADEPLAN_MAX_DATE.isoformat(),
        "tradeplan_position_storage_mode": _tradeplan_position_storage_mode(),
        "tradeplan_position_storage_key": TRADEPLAN_POSITION_STORAGE_KEY,
        "tradeplan_position_storage_notice": _tradeplan_position_storage_notice(),
        "tradeplan_year_choices": TRADEPLAN_YEAR_CHOICES,
        "tradeplan_month_choices": TRADEPLAN_MONTH_CHOICES,
    }
    return render(request, "outlook/index.html", context)
