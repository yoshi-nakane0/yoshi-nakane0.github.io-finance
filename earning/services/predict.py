import logging

import numpy as np

from earning.services.features import (
    FEATURE_COLUMNS,
    MODEL_PATH,
    MODEL_VERSION,
    build_feature_row,
)

logger = logging.getLogger(__name__)


def load_model():
    import lightgbm as lgb

    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f'Model file not found at {MODEL_PATH}. '
            f'Run `python manage.py earnings_train_model` first.'
        )
    return lgb.Booster(model_file=str(MODEL_PATH))


def predict_event(event, model):
    from earning.models import EarningsPrediction

    row = build_feature_row(event)
    if row is None:
        return None

    feature_vector = np.array(
        [[row[c] if row[c] is not None else float('nan') for c in FEATURE_COLUMNS]],
        dtype=float,
    )
    y_hat = float(model.predict(feature_vector)[0])

    EarningsPrediction.objects.update_or_create(
        event=event,
        model_version=MODEL_VERSION,
        defaults={'predicted_reaction': y_hat, 'confidence': None},
    )
    return y_hat
