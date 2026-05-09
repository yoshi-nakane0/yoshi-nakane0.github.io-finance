MACRO_KEYS = (
    'vix_at_event',
    'hy_spread_at_event',
    'skew_at_event',
    't5yie_at_event',
    'rut_at_event',
)


_RELATIVE_PERCENT_BANDS = {
    'vix_at_event': 0.5,
    'hy_spread_at_event': 0.5,
    'skew_at_event': 0.2,
    'rut_at_event': 0.2,
}
_ABSOLUTE_BANDS = {
    't5yie_at_event': 0.5,
}


def compute_feature_ranges(baseline_features):
    ranges = {}
    for key in MACRO_KEYS:
        value = baseline_features.get(key)
        if value is None:
            continue
        if key in _ABSOLUTE_BANDS:
            band = _ABSOLUTE_BANDS[key]
            ranges[key] = [value - band, value + band]
        else:
            band = _RELATIVE_PERCENT_BANDS[key]
            ranges[key] = [value * (1 - band), value * (1 + band)]
    return ranges
