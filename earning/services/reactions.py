def compute_price_reactions(event):
    rows = list(
        event.price_window
        .filter(offset_days__in=[-1, 0, 1])
        .values_list('offset_days', 'close')
    )
    closes = {offset: close for offset, close in rows if close is not None}
    previous_close = closes.get(-1)
    if previous_close in (None, 0):
        return None, None

    reaction_close = None
    if closes.get(0) is not None:
        reaction_close = (closes[0] / previous_close - 1) * 100

    reaction_next_day = None
    if closes.get(1) is not None:
        reaction_next_day = (closes[1] / previous_close - 1) * 100

    return reaction_close, reaction_next_day


def update_price_reactions(event):
    reaction_close, reaction_next_day = compute_price_reactions(event)
    update_fields = []

    if reaction_close is not None:
        event.reaction_close = reaction_close
        update_fields.append('reaction_close')
    if reaction_next_day is not None:
        event.reaction_next_day = reaction_next_day
        update_fields.append('reaction_next_day')

    if update_fields:
        event.save(update_fields=update_fields)

    return reaction_close, reaction_next_day
