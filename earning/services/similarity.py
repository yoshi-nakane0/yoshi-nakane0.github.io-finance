def _zscore_normalize(matrix):
    import numpy as np

    arr = np.asarray(matrix, dtype=float)
    mean = np.nanmean(arr, axis=0)
    std = np.nanstd(arr, axis=0)

    safe_std = np.where(std == 0.0, 1.0, std)
    normalized = (arr - mean) / safe_std
    normalized[:, std == 0.0] = 0.0
    return normalized, mean, std


def _nan_safe_euclidean(a, b):
    import numpy as np

    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    valid_mask = ~(np.isnan(a) | np.isnan(b))
    valid_count = int(np.sum(valid_mask))
    if valid_count == 0:
        return float('inf')
    diff = a[valid_mask] - b[valid_mask]
    dist_sq = float(np.sum(diff * diff))
    total_dims = a.size
    return float(np.sqrt(dist_sq * total_dims / valid_count))


def build_similarity_pool(events):
    import numpy as np

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

    matrix = np.array([r['vector_raw'] for r in rows], dtype=float)
    normalized, mean, std = _zscore_normalize(matrix)
    for i, r in enumerate(rows):
        r['vector'] = normalized[i]

    return {'entries': rows, 'mean': mean, 'std': std}


def find_similar_events(target_event, pool, top_n=3):
    import numpy as np

    from earning.services.features import FEATURE_COLUMNS, build_feature_row

    if not pool.get('entries') or pool.get('mean') is None:
        return []

    feat = build_feature_row(target_event)
    if feat is None:
        return []

    raw_vector = np.array(
        [feat[c] if feat[c] is not None else float('nan') for c in FEATURE_COLUMNS],
        dtype=float,
    )
    safe_std = np.where(pool['std'] == 0.0, 1.0, pool['std'])
    target_normalized = (raw_vector - pool['mean']) / safe_std
    target_normalized = np.where(pool['std'] == 0.0, 0.0, target_normalized)

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
