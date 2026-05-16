import csv
import hashlib
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_GET

TOKYO_TZ = ZoneInfo("Asia/Tokyo")
EVENT_TEXT_LIMIT = 30
EVENTS_CACHE_TIMEOUT = 60 * 60
INITIAL_MONTHS = 1
MORE_EVENTS_LIMIT = 30


def _events_csv_path():
    return Path(settings.BASE_DIR) / "static" / "events" / "data.csv"


def _events_csv_signature():
    csv_path = _events_csv_path()
    try:
        stat = csv_path.stat()
    except FileNotFoundError:
        return str(csv_path), 0, 0
    return str(csv_path), stat.st_mtime_ns, stat.st_size


def _events_cache_key(prefix, *parts):
    key_source = "|".join(str(part) for part in parts)
    key_hash = hashlib.md5(key_source.encode("utf-8")).hexdigest()
    return f"events:{prefix}:{key_hash}"


def _today_iso():
    return datetime.now(TOKYO_TZ).date().isoformat()


def _parse_iso_date(value):
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _add_months(value, months):
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    month_days = [
        31,
        29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ]
    return value.replace(year=year, month=month, day=min(value.day, month_days[month - 1]))


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
    effective_today = today_iso or _today_iso()
    cache_key = _events_cache_key("grouped", _events_csv_signature(), effective_today)
    cached_groups = cache.get(cache_key)
    if cached_groups is not None:
        return cached_groups

    future_items = {}
    past_items = {}
    csv_path = _events_csv_path()

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

    grouped_events = _build_grouped_entries(future_items), _build_grouped_entries(past_items)
    cache.set(cache_key, grouped_events, EVENTS_CACHE_TIMEOUT)
    return grouped_events


def _split_initial_future_groups(future_groups, today_iso=None):
    today_value = _parse_iso_date(today_iso or _today_iso())
    if today_value is None:
        return future_groups, []

    end_value = _add_months(today_value, INITIAL_MONTHS)
    initial_groups = []
    remaining_groups = []

    for group in future_groups:
        group_date = _parse_iso_date(group["date"])
        if group_date is not None and group_date <= end_value:
            initial_groups.append(group)
        else:
            remaining_groups.append(group)

    return initial_groups, remaining_groups


def _count_group_items(grouped_entries):
    return sum(len(group["items"]) for group in grouped_entries)


def _slice_group_items(grouped_entries, offset=0, limit=MORE_EVENTS_LIMIT):
    sliced_groups = []
    seen_items = 0
    remaining = limit

    for group in grouped_entries:
        group_items = group["items"]
        group_size = len(group_items)

        if seen_items + group_size <= offset:
            seen_items += group_size
            continue

        start_index = max(0, offset - seen_items)
        selected_items = group_items[start_index:start_index + remaining]
        if selected_items:
            sliced_groups.append({"date": group["date"], "items": selected_items})
            remaining -= len(selected_items)

        if remaining <= 0:
            break

        seen_items += group_size

    return sliced_groups


@require_GET
def index(request):
    today_iso = _today_iso()
    cache_key = _events_cache_key("index-html", _events_csv_signature(), today_iso)
    cached_html = cache.get(cache_key)
    if cached_html is not None:
        return HttpResponse(cached_html)

    future_groups, past_groups = _load_grouped_events(today_iso)
    initial_future_groups, remaining_future_groups = _split_initial_future_groups(future_groups, today_iso)
    html = render_to_string(
        "events/index.html",
        {
            "future_groups": initial_future_groups,
            "has_future_more_events": bool(remaining_future_groups),
            "future_more_url": reverse("events:future_more"),
            "has_past_events": bool(past_groups),
            "past_events_url": reverse("events:past_events"),
        },
        request=request,
    )
    cache.set(cache_key, html, EVENTS_CACHE_TIMEOUT)
    return HttpResponse(html)


@require_GET
def future_more_events(request):
    try:
        offset = max(0, int(request.GET.get("offset", "0")))
    except ValueError:
        offset = 0

    today_iso = _today_iso()
    cache_key = _events_cache_key("future-more", _events_csv_signature(), today_iso, offset, MORE_EVENTS_LIMIT)
    cached_payload = cache.get(cache_key)
    if cached_payload is not None:
        return JsonResponse(cached_payload)

    future_groups, _ = _load_grouped_events(today_iso)
    _, remaining_future_groups = _split_initial_future_groups(future_groups, today_iso)
    selected_groups = _slice_group_items(remaining_future_groups, offset, MORE_EVENTS_LIMIT)
    next_offset = offset + _count_group_items(selected_groups)
    total_remaining = _count_group_items(remaining_future_groups)
    payload = {
        "html": render_to_string("events/_event_sections.html", {"grouped_entries": selected_groups}, request=request),
        "nextOffset": next_offset,
        "hasMore": next_offset < total_remaining,
    }
    cache.set(cache_key, payload, EVENTS_CACHE_TIMEOUT)
    return JsonResponse(payload)


@require_GET
def past_events(request):
    today_iso = _today_iso()
    cache_key = _events_cache_key("past-html", _events_csv_signature(), today_iso)
    cached_html = cache.get(cache_key)
    if cached_html is not None:
        return HttpResponse(cached_html)

    html = render_to_string(
        "events/_event_sections.html",
        {"grouped_entries": _load_grouped_events(today_iso)[1]},
        request=request,
    )
    cache.set(cache_key, html, EVENTS_CACHE_TIMEOUT)
    return HttpResponse(html)
