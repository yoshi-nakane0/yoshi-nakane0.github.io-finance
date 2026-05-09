import math
from pathlib import Path

from django.conf import settings


MODEL_VERSION = 'baseline-v1'
MODEL_PATH = Path(settings.BASE_DIR) / 'earning' / 'ml' / 'models' / 'baseline-v1.lgb'

FEATURE_COLUMNS = [
    'gross_margin',
    'operating_margin',
    'relative_strength',
    'guidance_revision_numeric',
    'vix_at_event',
    'hy_spread_at_event',
    'skew_at_event',
    't5yie_at_event',
    'rut_at_event',
    'pre_short_return',
    'pre_hv_20',
]

_GUIDANCE_MAP = {'up': 1.0, 'flat': 0.0, 'down': -1.0}


def _guidance_to_numeric(value):
    if value is None:
        return 0.0
    return _GUIDANCE_MAP.get(value, 0.0)


def _compute_pre_short_return(event):
    rows = list(
        event.price_window
        .filter(offset_days__gte=-6, offset_days__lte=-1)
        .values_list('offset_days', 'close')
    )
    closes_by_offset = {off: c for off, c in rows if c is not None}
    if -1 not in closes_by_offset or -6 not in closes_by_offset:
        return None
    return (closes_by_offset[-1] / closes_by_offset[-6] - 1) * 100


def _compute_pre_hv_20(event):
    closes = list(
        event.price_window
        .filter(offset_days__gte=-21, offset_days__lte=-1)
        .order_by('offset_days')
        .values_list('close', flat=True)
    )
    closes = [c for c in closes if c is not None]
    if len(closes) < 11:
        return None
    log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    mean = sum(log_returns) / len(log_returns)
    var = sum((r - mean) ** 2 for r in log_returns) / len(log_returns)
    return math.sqrt(var) * math.sqrt(252) * 100


def build_feature_row(event):
    row = {
        'gross_margin': event.gross_margin,
        'operating_margin': event.operating_margin,
        'relative_strength': event.relative_strength,
        'guidance_revision_numeric': _guidance_to_numeric(event.guidance_revision),
        'vix_at_event': event.vix_at_event,
        'hy_spread_at_event': event.hy_spread_at_event,
        'skew_at_event': event.skew_at_event,
        't5yie_at_event': event.t5yie_at_event,
        'rut_at_event': event.rut_at_event,
        'pre_short_return': _compute_pre_short_return(event),
        'pre_hv_20': _compute_pre_hv_20(event),
    }
    if all(v is None for k, v in row.items() if k != 'guidance_revision_numeric'):
        return None
    return row


def build_feature_matrix(events):
    import numpy as np

    X_rows = []
    y_values = []
    for event in events:
        if event.reaction_close is None:
            continue
        row = build_feature_row(event)
        if row is None:
            continue
        X_rows.append([row[c] if row[c] is not None else float('nan') for c in FEATURE_COLUMNS])
        y_values.append(event.reaction_close)

    X = np.array(X_rows, dtype=float) if X_rows else np.empty((0, len(FEATURE_COLUMNS)))
    y = np.array(y_values, dtype=float)
    return X, y, list(FEATURE_COLUMNS)
