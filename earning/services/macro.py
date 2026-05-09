import logging

logger = logging.getLogger(__name__)


MACRO_FIELD_MAP = {
    'vix_at_event': 'VIXCLS',
    'hy_spread_at_event': 'BAMLH0A0HYM2',
    'skew_at_event': 'CBOE_SKEW',
    't5yie_at_event': 'T5YIE',
    'rut_at_event': 'RUT_INDEX',
}


def get_latest_value_on_or_before(series_id, event_date):
    from macro.models import Indicator, Observation

    if event_date is None:
        return None
    try:
        indicator = Indicator.objects.get(fred_series_id=series_id)
    except Indicator.DoesNotExist:
        logger.warning('Macro indicator not found: %s', series_id)
        return None

    obs = (
        Observation.objects
        .filter(indicator=indicator, observation_date__lte=event_date)
        .order_by('-observation_date')
        .first()
    )
    if obs is None:
        return None
    return obs.value


def attach_macro_snapshot(event):
    if event.event_date is None:
        return 0

    update_fields = []
    for column_name, series_id in MACRO_FIELD_MAP.items():
        value = get_latest_value_on_or_before(series_id, event.event_date)
        if value is None:
            logger.info('No macro value for %s on or before %s', series_id, event.event_date)
            continue
        setattr(event, column_name, value)
        update_fields.append(column_name)

    if update_fields:
        event.save(update_fields=update_fields)
    return len(update_fields)
