import math


class _Vector(list):
    def __eq__(self, other):
        if isinstance(other, (int, float)):
            return [value == other for value in self]
        return super().__eq__(other)


class _Matrix(list):
    def __getitem__(self, key):
        if isinstance(key, tuple):
            row_key, col_key = key
            if isinstance(row_key, slice):
                return _Vector(row[col_key] for row in self[row_key])
            return super().__getitem__(row_key)[col_key]
        return super().__getitem__(key)


def _is_nan(value):
    try:
        return math.isnan(float(value))
    except (TypeError, ValueError):
        return False


def _as_float(value):
    return float(value)


def _as_rows(matrix):
    return [[_as_float(value) for value in row] for row in matrix]


def _zscore_normalize(matrix):
    rows = _as_rows(matrix)
    if not rows:
        return _Matrix(), [], []

    width = len(rows[0])
    mean = []
    std = []
    for col in range(width):
        values = [row[col] for row in rows if not _is_nan(row[col])]
        if values:
            col_mean = sum(values) / len(values)
            variance = sum((value - col_mean) ** 2 for value in values) / len(values)
            col_std = math.sqrt(variance)
        else:
            col_mean = float('nan')
            col_std = float('nan')
        mean.append(col_mean)
        std.append(col_std)

    normalized = _Matrix()
    for row in rows:
        normalized_row = []
        for value, col_mean, col_std in zip(row, mean, std):
            if _is_nan(value):
                normalized_row.append(float('nan'))
            elif col_std == 0.0:
                normalized_row.append(0.0)
            elif _is_nan(col_mean) or _is_nan(col_std):
                normalized_row.append(float('nan'))
            else:
                normalized_row.append((value - col_mean) / col_std)
        normalized.append(_Vector(normalized_row))
    return normalized, mean, std


def _nan_safe_euclidean(a, b):
    a_values = [_as_float(value) for value in a]
    b_values = [_as_float(value) for value in b]
    pairs = [
        (av, bv)
        for av, bv in zip(a_values, b_values)
        if not _is_nan(av) and not _is_nan(bv)
    ]
    if not pairs:
        return float('inf')
    dist_sq = sum((av - bv) ** 2 for av, bv in pairs)
    total_dims = len(a_values)
    return math.sqrt(dist_sq * total_dims / len(pairs))


def build_similarity_pool(events):
    from earning.services.features import FEATURE_COLUMNS, build_feature_row

    rows = []
    for ev in events:
        if ev.reaction_close is None:
            continue
        feat = build_feature_row(ev)
        if feat is None:
            continue
        vector = [feat[c] if feat[c] is not None else float('nan') for c in FEATURE_COLUMNS]
        rows.append({'event': ev, 'vector_raw': vector, 'reaction_close': ev.reaction_close})

    if not rows:
        return {'entries': [], 'mean': None, 'std': None}

    matrix = [r['vector_raw'] for r in rows]
    normalized, mean, std = _zscore_normalize(matrix)
    for i, r in enumerate(rows):
        r['vector'] = normalized[i]

    return {'entries': rows, 'mean': mean, 'std': std}


def find_similar_events(target_event, pool, top_n=3):
    from earning.services.features import FEATURE_COLUMNS, build_feature_row

    if not pool.get('entries') or pool.get('mean') is None:
        return []

    feat = build_feature_row(target_event)
    if feat is None:
        return []

    raw_vector = [
        feat[c] if feat[c] is not None else float('nan')
        for c in FEATURE_COLUMNS
    ]
    target_normalized = []
    for value, col_mean, col_std in zip(raw_vector, pool['mean'], pool['std']):
        if _is_nan(value):
            target_normalized.append(float('nan'))
        elif col_std == 0.0:
            target_normalized.append(0.0)
        elif _is_nan(col_mean) or _is_nan(col_std):
            target_normalized.append(float('nan'))
        else:
            target_normalized.append((value - col_mean) / col_std)

    scored = []
    for entry in pool['entries']:
        if entry['event'].id == target_event.id:
            continue
        dist = _nan_safe_euclidean(target_normalized, entry['vector'])
        if dist == float('inf'):
            continue
        scored.append((dist, entry))

    scored.sort(key=lambda p: p[0])
    results = []
    for dist, entry in scored[:top_n]:
        ev = entry['event']
        rc = entry['reaction_close']
        sign = '+' if rc > 0 else ''
        results.append({
            'symbol': ev.stock.symbol,
            'fiscal_period': ev.fiscal_period,
            'reaction_display': f'{sign}{rc:.1f}%',
            'reaction_class': 'reaction-positive' if rc > 1.0 else ('reaction-negative' if rc < -1.0 else 'reaction-neutral'),
        })
    return results
