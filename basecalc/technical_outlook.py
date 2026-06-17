from .world_model import build_world_model


def build_technical_outlook(price, market_snapshot=None, intermarket_context=None, as_of=None):
    return build_world_model(
        price=price,
        market_snapshot=market_snapshot,
        intermarket_context=intermarket_context,
        as_of=as_of,
    )
