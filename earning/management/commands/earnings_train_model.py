from django.core.management.base import BaseCommand, CommandError

from earning.models import EarningsEvent
from earning.services.features import (
    FEATURE_COLUMNS,
    MODEL_PATH,
    build_feature_matrix,
)


LGB_PARAMS = {
    'objective': 'regression',
    'metric': 'rmse',
    'num_leaves': 4,
    'min_data_in_leaf': 2,
    'learning_rate': 0.05,
    'feature_fraction': 0.9,
    'bagging_fraction': 0.9,
    'bagging_freq': 5,
    'lambda_l2': 1.0,
    'verbose': -1,
}
NUM_BOOST_ROUNDS = 30
MIN_TRAINABLE = 10
KFOLD_K = 5
KFOLD_SEED = 42


def _make_kfold_splits(n, k, seed):
    import numpy as np

    rng = np.random.default_rng(seed)
    indices = np.arange(n)
    rng.shuffle(indices)
    folds = np.array_split(indices, k)
    splits = []
    for i in range(k):
        val_idx = folds[i]
        train_idx = np.concatenate([folds[j] for j in range(k) if j != i])
        splits.append((train_idx, val_idx))
    return splits


def _directional_hit_rate(y_true, y_pred):
    if len(y_true) == 0:
        return 0.0
    matches = sum(1 for t, p in zip(y_true, y_pred) if (t > 0) == (p > 0))
    return matches / len(y_true)


class Command(BaseCommand):
    help = 'Train baseline LightGBM regressor for reaction_close prediction.'

    def add_arguments(self, parser):
        parser.add_argument('--no-save', action='store_true',
                            help='Do not write the trained model to disk (used in tests).')

    def handle(self, *args, **options):
        import lightgbm as lgb
        import numpy as np

        events = list(
            EarningsEvent.objects
            .filter(reaction_close__isnull=False)
            .select_related('stock')
        )
        X, y, feat_names = build_feature_matrix(events)
        n = X.shape[0]
        if n < MIN_TRAINABLE:
            raise CommandError(
                f'Only {n} trainable events; need at least {MIN_TRAINABLE}. '
                f'Run earnings_fetch_prices and earnings_attach_macro backfills first.'
            )
        if n < 50:
            self.stdout.write(self.style.WARNING(
                f'n={n} is below the 10x-features rule of thumb; CV metrics are highly noisy.'
            ))

        splits = _make_kfold_splits(n, KFOLD_K, KFOLD_SEED)
        rmses, maes, hits = [], [], []
        for fold_idx, (train_idx, val_idx) in enumerate(splits, start=1):
            X_tr, y_tr = X[train_idx], y[train_idx]
            X_va, y_va = X[val_idx], y[val_idx]
            train_set = lgb.Dataset(X_tr, label=y_tr, feature_name=feat_names)
            booster = lgb.train(LGB_PARAMS, train_set, num_boost_round=NUM_BOOST_ROUNDS)
            y_pred = booster.predict(X_va)
            rmse = float(np.sqrt(np.mean((y_va - y_pred) ** 2)))
            mae = float(np.mean(np.abs(y_va - y_pred)))
            hit = _directional_hit_rate(y_va.tolist(), y_pred.tolist())
            rmses.append(rmse)
            maes.append(mae)
            hits.append(hit)
            self.stdout.write(f'  Fold {fold_idx}: RMSE={rmse:.3f} MAE={mae:.3f} Hit={hit:.2%}')

        self.stdout.write(self.style.SUCCESS(
            f'CV mean: RMSE={np.mean(rmses):.3f} MAE={np.mean(maes):.3f} Hit rate={np.mean(hits):.2%}'
        ))

        train_set = lgb.Dataset(X, label=y, feature_name=feat_names)
        booster = lgb.train(LGB_PARAMS, train_set, num_boost_round=NUM_BOOST_ROUNDS)

        self.stdout.write(f'Trained on {n} events with {len(feat_names)} features.')
        self.stdout.write('Feature importance (gain):')
        importances = booster.feature_importance(importance_type='gain')
        for name, gain in sorted(zip(feat_names, importances), key=lambda p: -p[1]):
            self.stdout.write(f'  {name}: {gain:.2f}')

        if options['no_save']:
            self.stdout.write('--no-save specified; skipping model write.')
            return

        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        booster.save_model(str(MODEL_PATH))
        self.stdout.write(self.style.SUCCESS(f'Saved model to {MODEL_PATH}'))
