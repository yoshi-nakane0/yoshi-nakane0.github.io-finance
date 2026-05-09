import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from earning.services.features import MODEL_PATH


JSON_OUTPUT_PATH = Path(settings.BASE_DIR) / 'static' / 'earning' / 'ml' / 'baseline-v1.json'


def _slim_node(node):
    if 'leaf_value' in node:
        return {'leaf_value': float(node['leaf_value'])}
    out = {
        'split_feature': int(node['split_feature']),
        'threshold': float(node['threshold']),
        'decision_type': node.get('decision_type', '<='),
        'default_left': bool(node.get('default_left', True)),
        'left_child': _slim_node(node['left_child']),
        'right_child': _slim_node(node['right_child']),
    }
    return out


class Command(BaseCommand):
    help = 'Export the trained LightGBM model to a JSON file for client-side inference.'

    def handle(self, *args, **options):
        import lightgbm as lgb
        import numpy as np

        from earning.services.lgb_walker import predict_from_json

        if not MODEL_PATH.exists():
            raise CommandError(
                f'Model file not found at {MODEL_PATH}. '
                f'Run `python manage.py earnings_train_model` first.'
            )

        booster = lgb.Booster(model_file=str(MODEL_PATH))
        dumped = booster.dump_model()

        feature_names = list(dumped.get('feature_names', []))
        trees_raw = dumped.get('tree_info', [])
        trees_slim = []
        for t in trees_raw:
            trees_slim.append({
                'shrinkage': 1.0,
                'root': _slim_node(t['tree_structure']),
            })

        # Empirical init_score: probe with all-zero features.
        probe = [0.0] * len(feature_names)
        partial_model = {
            'feature_names': feature_names,
            'init_score': 0.0,
            'trees': trees_slim,
        }
        tree_sum = predict_from_json(probe, partial_model)
        expected = float(booster.predict(np.array([probe]))[0])
        init_score = expected - tree_sum

        payload = {
            'feature_names': feature_names,
            'init_score': init_score,
            'trees': trees_slim,
        }

        JSON_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        JSON_OUTPUT_PATH.write_text(json.dumps(payload, separators=(',', ':')), encoding='utf-8')
        self.stdout.write(self.style.SUCCESS(
            f'Exported model JSON to {JSON_OUTPUT_PATH} ({JSON_OUTPUT_PATH.stat().st_size} bytes)'
        ))
