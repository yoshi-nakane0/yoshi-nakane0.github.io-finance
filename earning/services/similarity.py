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
