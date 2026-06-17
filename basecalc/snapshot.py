import json
import logging
from datetime import date, datetime
from pathlib import Path

from django.conf import settings

DEFAULT_BASECALC_SNAPSHOT_PATH = Path('basecalc/data/latest_snapshot.json')

logger = logging.getLogger(__name__)


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f'Object of type {type(value).__name__} is not JSON serializable')


def _snapshot_path(path=None):
    return Path(path) if path else settings.BASE_DIR / DEFAULT_BASECALC_SNAPSHOT_PATH


def load_basecalc_snapshot(path=None):
    payload_path = _snapshot_path(path)
    if not payload_path.exists():
        return None
    try:
        with payload_path.open(encoding='utf-8') as fp:
            payload = json.load(fp)
    except (OSError, json.JSONDecodeError):
        logger.exception('failed to read basecalc snapshot: %s', payload_path)
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def write_basecalc_snapshot(payload, path=None):
    payload_path = _snapshot_path(path)
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.loads(json.dumps(payload, default=_json_default))
    payload_path.write_text(
        json.dumps(serialized, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
