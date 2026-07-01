import json
from datetime import date, datetime
from pathlib import Path

from django.conf import settings
from django.utils import timezone


DEFAULT_MANIFEST_PATH = Path('static/finance_data_manifest.json')


def build_finance_data_manifest(base_dir=None):
    root = Path(base_dir or settings.BASE_DIR)
    macro = _load_json(root / 'static' / 'macro' / 'latest_dashboard.json')
    basecalc = _load_json(root / 'basecalc' / 'data' / 'latest_snapshot.json')
    explanation = _load_json(root / 'explanation' / 'data' / 'latest_snapshot.json')

    macro_status = _macro_status(macro)
    basecalc_status = _basecalc_status(basecalc)
    explanation_status = _explanation_status(explanation)
    blocking_reasons = []
    blocking_reasons.extend(_list((macro or {}).get('warnings')))
    blocking_reasons.extend(
        _list((((basecalc or {}).get('world_model') or {}).get('output_contract') or {}).get('stop_reasons'))
    )
    blocking_reasons.extend(_list(((explanation or {}).get('audit') or {}).get('items')))

    return {
        'schema': 'finance_data_manifest_v1',
        'generated_at': timezone.now().isoformat(),
        'macro_as_of': _macro_as_of(macro),
        'basecalc_as_of': _basecalc_as_of(basecalc),
        'explanation_as_of': (explanation or {}).get('as_of'),
        'explanation_generated_at': (explanation or {}).get('generated_at'),
        'git_sha': (explanation or {}).get('git_sha') or '',
        'workflow_run_id': (explanation or {}).get('workflow_run_id') or '',
        'macro_status': macro_status,
        'basecalc_status': basecalc_status,
        'explanation_status': explanation_status,
        'blocking_reasons': _dedupe(blocking_reasons),
        'data_freshness': {
            'macro': _freshness_label(macro_status),
            'basecalc': _freshness_label(basecalc_status),
            'explanation': _freshness_label(explanation_status),
        },
        'source_versions': {
            'macro': (macro or {}).get('model_version') or '',
            'basecalc': (((basecalc or {}).get('world_model') or {}).get('model_version')) or '',
            'explanation': (explanation or {}).get('version') or '',
        },
    }


def write_finance_data_manifest(manifest, path=None):
    output_path = Path(path) if path else settings.BASE_DIR / DEFAULT_MANIFEST_PATH
    output_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.loads(json.dumps(manifest, default=_json_default))
    output_path.write_text(
        json.dumps(serialized, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    return serialized


def _load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None


def _macro_as_of(payload):
    payload = payload or {}
    return (
        payload.get('generated_at')
        or ((payload.get('house_view') or {}).get('generated_at'))
        or ((payload.get('house_view') or {}).get('as_of'))
    )


def _basecalc_as_of(payload):
    payload = payload or {}
    return (
        payload.get('decision_price_as_of')
        or ((payload.get('decision_price') or {}).get('as_of'))
        or payload.get('generated_at')
    )


def _macro_status(payload):
    if not payload:
        return 'blocked'
    if payload.get('stale') is True:
        return 'limited'
    warnings = _list(payload.get('warnings'))
    return 'reference' if warnings else 'ok'


def _basecalc_status(payload):
    if not payload:
        return 'blocked'
    contract = (((payload.get('world_model') or {}).get('output_contract')) or {})
    status = contract.get('contract_status')
    if status == 'ok':
        return 'ok'
    if status == 'limited':
        return 'limited'
    if status == 'error':
        return 'blocked'
    return 'reference'


def _explanation_status(payload):
    if not payload:
        return 'blocked'
    status = ((payload.get('final') or {}).get('status')) or ''
    if status in {'ok', 'reference', 'limited', 'blocked'}:
        return status
    if status == 'valid':
        return 'ok'
    return 'reference'


def _freshness_label(status):
    if status == 'ok':
        return 'current'
    if status in {'reference', 'limited'}:
        return 'usable_with_warning'
    return 'blocked'


def _list(value):
    if not value:
        return []
    return value if isinstance(value, list) else [str(value)]


def _dedupe(values):
    result = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f'Object of type {type(value).__name__} is not JSON serializable')
