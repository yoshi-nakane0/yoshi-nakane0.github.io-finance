import datetime
import json
from pathlib import Path

from django.utils import timezone

BASECALC_STATUS_PATH = Path(__file__).resolve().parent / "data" / "basecalc_status.json"

STATUS_KEYS = ("price_data", "intermarket")

STATUS_LABELS = {
    "ready": "判定可能",
    "limited": "参考",
    "blocked": "停止",
}


def load_basecalc_status(path=BASECALC_STATUS_PATH):
    path = Path(path)
    if not path.exists():
        return _empty_status()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_status()
    if not isinstance(payload, dict):
        return _empty_status()
    return {**_empty_status(), **payload}


def write_basecalc_status(entries, path=BASECALC_STATUS_PATH, now=None):
    now = now or timezone.now()
    status = load_basecalc_status(path)
    for key, entry in (entries or {}).items():
        if key not in STATUS_KEYS or not isinstance(entry, dict):
            continue
        previous = status.get(key) if isinstance(status.get(key), dict) else {}
        merged = {**previous, **entry}
        if not merged.get("last_success_at"):
            merged["last_success_at"] = previous.get("last_success_at")
        if not merged.get("last_failed_at"):
            merged["last_failed_at"] = previous.get("last_failed_at")
        merged["age_minutes"] = _age_minutes(merged.get("last_success_at"), now)
        status[key] = merged
    status["updated_at"] = _iso(now)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    return status


def price_status_entry(snapshot, readiness_level="blocked", now=None):
    now = now or timezone.now()
    success = isinstance(snapshot, dict) and snapshot.get("price")
    source = (snapshot or {}).get("source") if isinstance(snapshot, dict) else None
    symbol = (snapshot or {}).get("symbol") if isinstance(snapshot, dict) else None
    fetched_at = _parse_datetime((snapshot or {}).get("fetched_at")) if success else None
    quality = (snapshot or {}).get("quality") if isinstance(snapshot, dict) else {}
    fallback_used = bool(
        (snapshot or {}).get("fallback_used")
        or (quality or {}).get("fallback_used")
        or source in {"stooq", "saved_snapshot", "last_good_cache"}
    )
    return {
        "last_success_at": _iso(fetched_at) if success else None,
        "last_failed_at": None if success else _iso(now),
        "source": _source_label(source, symbol),
        "age_minutes": _age_minutes(fetched_at, now),
        "fallback_used": fallback_used,
        "decision_level": readiness_level or "blocked",
        "decision_label": STATUS_LABELS.get(readiness_level, "停止"),
    }


def intermarket_status_entry(intermarket_context=None, now=None):
    now = now or timezone.now()
    context = intermarket_context if isinstance(intermarket_context, dict) else {}
    components = context.get("components") if isinstance(context.get("components"), dict) else {}
    readiness = context.get("readiness") if isinstance(context.get("readiness"), dict) else {}
    fetched_at = _parse_datetime(context.get("fetched_at"))
    success = bool(components)
    level = readiness.get("level") or ("ready" if success else "blocked")
    return {
        "last_success_at": _iso(fetched_at or now) if success else None,
        "last_failed_at": None if success else _iso(now),
        "source": "NQ=F / ES=F / YM=F",
        "age_minutes": _age_minutes(fetched_at or now, now) if success else None,
        "fallback_used": False,
        "asset_count": len(components),
        "decision_level": level,
        "decision_label": STATUS_LABELS.get(level, "停止"),
    }


def status_display_rows(status, world_model=None):
    status = status if isinstance(status, dict) else {}
    rows = []
    for key, label in (
        ("price_data", "価格データ"),
        ("intermarket", "米国3指数確認"),
    ):
        entry = status.get(key) if isinstance(status.get(key), dict) else {}
        if key == "price_data" and isinstance(world_model, dict):
            level = world_model.get("readiness_level") or entry.get("decision_level")
            source_status = world_model.get("source_status") or {}
            source = source_status.get("source") or entry.get("source")
            symbol = source_status.get("symbol")
            entry = {
                **entry,
                "source": _source_label(source, symbol) if source or symbol else entry.get("source"),
                "decision_level": level,
                "decision_label": STATUS_LABELS.get(level, entry.get("decision_label") or "停止"),
                "fallback_used": (world_model.get("data_quality") or {}).get(
                    "fallback_used",
                    entry.get("fallback_used"),
                ),
            }
            if source_status.get("source") and source_status.get("source") != "unknown":
                entry["age_minutes"] = world_model.get("stale_minutes")
        rows.append(
            {
                "key": key,
                "label": label,
                "age_display": _display_age(key, entry),
                "source": entry.get("source") or "N/A",
                "fallback_display": "あり" if entry.get("fallback_used") else "なし",
                "decision_label": entry.get("decision_label") or "停止",
                "decision_level": entry.get("decision_level") or "blocked",
                "last_success_at": entry.get("last_success_at") or "",
                "last_failed_at": entry.get("last_failed_at") or "",
            }
        )
    return rows


def _empty_status():
    return {
        "schema": "basecalc_status_v1",
        "updated_at": None,
        "price_data": {},
        "intermarket": {},
    }


def _source_label(source, symbol):
    if source and symbol:
        return f"{source}:{symbol}"
    return source or symbol or "N/A"


def _parse_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime.datetime):
        timestamp = value
    elif isinstance(value, str):
        try:
            timestamp = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if timezone.is_naive(timestamp):
        timestamp = timezone.make_aware(timestamp, timezone=datetime.timezone.utc)
    return timestamp


def _age_minutes(timestamp, now):
    timestamp = _parse_datetime(timestamp)
    if timestamp is None:
        return None
    if timezone.is_naive(now):
        now = timezone.make_aware(now, timezone=datetime.timezone.utc)
    return max(0, int((now - timestamp).total_seconds() // 60))


def _display_age(key, entry):
    minutes = entry.get("age_minutes")
    if minutes is None:
        return "不明"
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        return "不明"
    if minutes < 60:
        return f"{minutes}分前"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}時間前"
    return f"{hours // 24}日前"


def _iso(value):
    value = _parse_datetime(value)
    return value.isoformat() if value else None
