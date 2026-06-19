from datetime import datetime

from django.utils import timezone

from basecalc.snapshot import load_basecalc_snapshot
from macro.services.dashboard_cache import load_static_macro_payload


def build_explanation_refresh_status(
    snapshot,
    *,
    macro_payload=None,
    basecalc_snapshot=None,
):
    macro_payload = load_static_macro_payload() if macro_payload is None else macro_payload
    basecalc_snapshot = load_basecalc_snapshot() if basecalc_snapshot is None else basecalc_snapshot
    source_snapshots = snapshot.source_snapshots or {}

    sources = [
        _source_status(
            'Macro',
            _macro_current_time(macro_payload or {}),
            _stored_time(source_snapshots.get('macro'), snapshot.as_of),
        ),
        _source_status(
            'Basecalc',
            _basecalc_current_time(basecalc_snapshot or {}),
            _stored_time(source_snapshots.get('basecalc'), snapshot.as_of),
        ),
    ]
    stale_sources = [source for source in sources if source['needs_refresh']]
    latest_source = _latest_source(stale_sources)

    return {
        'needs_refresh': bool(stale_sources),
        'message': (
            'Macro / Basecalc が更新されています。Explanation の再作成が必要です。'
            if stale_sources
            else ''
        ),
        'latest_source_label': latest_source['label'] if latest_source else '',
        'sources': sources,
    }


def _source_status(label, current_at, saved_at):
    return {
        'label': label,
        'current_at': current_at,
        'saved_at': saved_at,
        'needs_refresh': bool(current_at and saved_at and current_at > saved_at),
    }


def _latest_source(sources):
    dated_sources = [source for source in sources if source['current_at']]
    if not dated_sources:
        return None
    return max(dated_sources, key=lambda source: source['current_at'])


def _macro_current_time(payload):
    return (
        _parse_datetime(payload.get('generated_at'))
        or _parse_datetime((payload.get('house_view') or {}).get('generated_at'))
        or _parse_datetime((payload.get('house_view') or {}).get('as_of'))
        or _parse_datetime((payload.get('data_quality_report') or {}).get('as_of'))
    )


def _basecalc_current_time(payload):
    return (
        _parse_datetime(payload.get('generated_at'))
        or _parse_datetime((payload.get('world_model') or {}).get('as_of'))
    )


def _stored_time(source, fallback):
    raw = ((source or {}).get('raw') or {})
    return (
        _parse_datetime(raw.get('generated_at'))
        or _parse_datetime(raw.get('as_of'))
        or _parse_datetime((raw.get('world_model') or {}).get('as_of'))
        or _parse_datetime(fallback)
    )


def _parse_datetime(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        except ValueError:
            return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed
