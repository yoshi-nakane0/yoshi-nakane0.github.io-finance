"""主要指数の将来リターンを学習・予測する。"""

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from macro.services import forecast_models


OUTPUT_RELATIVE_PATH = Path('static') / 'macro' / 'return_forecast_model.json'
LEGACY_LIGHTGBM_OUTPUT = Path('static') / 'macro' / 'lightgbm_prediction.json'
RECENT_VALIDATION_MONTHS = 24
MIN_TRAINING_SAMPLES = 60


class Command(BaseCommand):
    help = 'マクロ特徴量で主要指数の将来リターンを学習・予測する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--target',
            choices=forecast_models.RETURN_TARGETS,
            default='GSPC',
        )
        parser.add_argument(
            '--horizon',
            choices=forecast_models.HORIZONS,
        )
        parser.add_argument('--all', action='store_true')
        parser.add_argument('--output', default=str(OUTPUT_RELATIVE_PATH))

    def handle(self, *args, **options):
        try:
            import numpy as np
        except ImportError as exc:
            raise CommandError(
                f'学習用依存が見つかりません: {exc}. '
                '`pip install -r requirements-train.txt` を実行してください。'
            )

        targets = (
            forecast_models.RETURN_TARGETS
            if options['all'] else (options['target'],)
        )
        horizons = (
            forecast_models.HORIZONS
            if options['horizon'] is None else (options['horizon'],)
        )
        results = []
        skipped = []

        for target in targets:
            for horizon in horizons:
                matrix = forecast_models.build_monthly_feature_matrix(
                    'return_forecast',
                    target,
                    horizon,
                )
                rows = matrix.get('rows') or []
                if len(rows) < MIN_TRAINING_SAMPLES:
                    skipped.append({
                        'target': target,
                        'horizon': horizon,
                        'reason': f'学習サンプル不足: {len(rows)}件',
                    })
                    continue

                split = max(1, len(rows) - RECENT_VALIDATION_MONTHS)
                train_rows = rows[:split]
                valid_rows = rows[split:]
                model = forecast_models.train_lightgbm_regressor(
                    np.array([row['x'] for row in train_rows], dtype='float64'),
                    np.array([row['target_value'] for row in train_rows], dtype='float64'),
                    np.array([row['x'] for row in valid_rows], dtype='float64'),
                    np.array([row['target_value'] for row in valid_rows], dtype='float64'),
                )
                latest = matrix['latest']
                prediction = float(model['booster'].predict([latest['x']])[0])
                horizon_months = forecast_models.parse_horizon_months(horizon)
                validation_mae = model.get('validation_mae')
                validation_rmse = model.get('validation_rmse')
                metadata = {
                    **(matrix.get('metadata') or {}),
                    'prediction_kind': 'return_pct',
                    'horizon_months': horizon_months,
                    'validation_mae_pct': validation_mae,
                    'validation_rmse_pct': validation_rmse,
                    'training_samples': len(train_rows),
                    'validation_samples': len(valid_rows),
                    'feature_count': len(matrix.get('feature_names') or []),
                }
                forecast_models.save_forecast_snapshot(
                    namespace='return_forecast',
                    model_version=forecast_models.RETURN_MODEL_VERSION,
                    target=target,
                    horizon=horizon,
                    prediction_value=prediction,
                    prediction_interval={
                        'type': 'validation_mae_pct',
                        'mae_pct': validation_mae,
                        'rmse_pct': validation_rmse,
                    },
                    feature_vector=latest['feature_vector'],
                    as_of=timezone.localdate(),
                    metadata=metadata,
                )
                results.append({
                    'target': target,
                    'horizon': horizon,
                    'horizon_months': horizon_months,
                    'predicted_return_pct': round(prediction, 4),
                    'validation_mae_pct': (
                        round(float(validation_mae), 4)
                        if validation_mae is not None else None
                    ),
                    'validation_rmse_pct': (
                        round(float(validation_rmse), 4)
                        if validation_rmse is not None else None
                    ),
                    'training_samples': len(train_rows),
                    'validation_samples': len(valid_rows),
                    'feature_count': len(matrix.get('feature_names') or []),
                })

        if not results:
            reason = skipped[0]['reason'] if skipped else '学習対象がありません。'
            raise CommandError(f'リターン予測を作成できませんでした: {reason}')

        payload = {
            'model_version': forecast_models.RETURN_MODEL_VERSION,
            'predicted_at': timezone.localdate().isoformat(),
            'results': results,
            'skipped': skipped,
        }
        out_path = Path(settings.BASE_DIR) / options['output']
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
        self._write_legacy_lightgbm_payload(results)
        self.stdout.write(self.style.SUCCESS(f'リターン予測 JSON 書き出し: {out_path}'))
        for row in results:
            self.stdout.write(
                f"  {row['target']} {row['horizon']}: "
                f"{row['predicted_return_pct']:+.2f}%"
            )
        for row in skipped:
            self.stdout.write(
                self.style.WARNING(
                    f"  skip {row['target']} {row['horizon']}: {row['reason']}"
                )
            )

    def _write_legacy_lightgbm_payload(self, results):
        gspc = [row for row in results if row['target'] == 'GSPC']
        if not gspc:
            return
        payload = {
            'predicted_at': timezone.localdate().isoformat(),
            'horizons': [
                {
                    'months': row['horizon_months'],
                    'predicted_return_pct': row['predicted_return_pct'],
                    'validation_mae_pct': row['validation_mae_pct'],
                }
                for row in gspc
            ],
            'training_samples': max(row['training_samples'] for row in gspc),
            'feature_count': max(row['feature_count'] for row in gspc),
            'model_version': forecast_models.RETURN_MODEL_VERSION,
        }
        out_path = Path(settings.BASE_DIR) / LEGACY_LIGHTGBM_OUTPUT
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
