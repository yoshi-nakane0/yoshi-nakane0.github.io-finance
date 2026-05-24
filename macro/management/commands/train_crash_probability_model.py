"""急落確率モデル v1 を学習して JSON を出力する。"""

import hashlib
import json
import math
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from macro.models import ForecastSnapshot
from macro.services import crash_probability


OUTPUT_RELATIVE_PATH = Path('static') / 'macro' / 'crash_probability_model.json'


def _clean(value):
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, 6)
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    return value


def _fmt(value, digits=3):
    if value is None:
        return '—'
    return f'{value:.{digits}f}'


def _features_hash(features):
    normalized = json.dumps(features, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()


class Command(BaseCommand):
    help = '市場ストレス特徴量から今後の急落確率を推定するロジスティックモデルを学習する'

    def add_arguments(self, parser):
        parser.add_argument('--target', default='GSPC', choices=sorted(crash_probability.TARGET_TICKERS))
        parser.add_argument('--horizon-days', type=int, default=63)
        parser.add_argument('--drawdown-threshold', type=float, default=-10.0)
        parser.add_argument('--validation-months', type=int, default=84)
        parser.add_argument('--output', default=str(OUTPUT_RELATIVE_PATH))

    def handle(self, *args, **options):
        rows = crash_probability.build_dataset(
            target=options['target'],
            horizon_days=options['horizon_days'],
            drawdown_threshold=options['drawdown_threshold'],
        )
        if len(rows) < 80:
            raise CommandError('学習に使える月次サンプルが不足しています。')
        event_count = sum(1 for row in rows if row['event'])
        if event_count < 3:
            raise CommandError('急落イベントが少なすぎるため学習できません。')

        validation_months = min(options['validation_months'], max(12, len(rows) // 2))
        split = len(rows) - validation_months
        train_rows = rows[:split]
        validation_rows = rows[split:]
        if not any(row['event'] for row in train_rows):
            raise CommandError('訓練期間に急落イベントがありません。')
        if not any(row['event'] for row in validation_rows):
            raise CommandError('検証期間に急落イベントがありません。--validation-months を増やしてください。')

        model = crash_probability.train_logistic_model(train_rows)
        raw_scored_validation = []
        for row in validation_rows:
            probability = crash_probability.predict_probability(
                model,
                row['features'],
            )
            raw_scored_validation.append({**row, 'probability': probability})

        raw_calibration_bins = crash_probability.calibration_bins(raw_scored_validation)
        scored_validation = []
        for row in raw_scored_validation:
            calibrated = crash_probability.calibrated_probability(
                row['probability'],
                raw_calibration_bins,
            )
            scored_validation.append({
                **row,
                'raw_probability': row['probability'],
                'probability': calibrated,
            })

        current_features = crash_probability.current_features()
        current_raw_probability = crash_probability.predict_probability(
            model,
            current_features,
        )
        current_probability = crash_probability.calibrated_probability(
            current_raw_probability,
            raw_calibration_bins,
        )
        validation_event_count = sum(1 for row in scored_validation if row['event'])
        event_rate_interval = crash_probability.wilson_interval(
            validation_event_count,
            len(scored_validation),
        )

        payload = {
            'model_version': crash_probability.MODEL_VERSION,
            'trained_at': timezone.localdate().isoformat(),
            'target': options['target'],
            'horizon_days': options['horizon_days'],
            'drawdown_threshold_pct': options['drawdown_threshold'],
            'prediction_label': (
                f"今後{options['horizon_days']}日相当で"
                f"{options['target']}が{options['drawdown_threshold']:.0f}%以上下落する推定確率"
            ),
            'current_probability': current_probability,
            'current_raw_probability': current_raw_probability,
            'current_features': current_features,
            'sample_count': len(rows),
            'event_count': event_count,
            'training_samples': len(train_rows),
            'training_event_count': sum(1 for row in train_rows if row['event']),
            'validation_samples': len(scored_validation),
            'validation_event_count': validation_event_count,
            'validation_event_rate_interval': event_rate_interval,
            'validation': {
                'roc_auc': crash_probability.roc_auc(scored_validation),
                'pr_auc': crash_probability.pr_auc(scored_validation),
                'brier_score': crash_probability.brier_score(scored_validation),
                'thresholds': crash_probability.threshold_metrics(scored_validation),
                'calibration_bins': crash_probability.calibration_bins(scored_validation),
                'raw_roc_auc': crash_probability.roc_auc(raw_scored_validation),
                'raw_pr_auc': crash_probability.pr_auc(raw_scored_validation),
                'raw_brier_score': crash_probability.brier_score(raw_scored_validation),
                'raw_calibration_bins': raw_calibration_bins,
            },
            'coefficients': crash_probability.coefficient_rows(model),
            'model': model,
            'rows': scored_validation,
            'limitations': [
                '月次終値ベースの検証であり、月中の最大下落は反映していません。',
                '急落は発生回数が少ないため、確率は参考値です。',
                '投資助言や売買推奨ではありません。',
            ],
        }

        out_path = Path(settings.BASE_DIR) / options['output']
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(_clean(payload), ensure_ascii=False, indent=2),
            encoding='utf-8',
        )

        as_of_date = timezone.localdate()
        ForecastSnapshot.objects.update_or_create(
            as_of_date=as_of_date,
            model_version=crash_probability.MODEL_VERSION,
            target=options['target'],
            horizon=f"{options['horizon_days']}d",
            defaults={
                'prediction_value': current_probability,
                'prediction_interval': {
                    'type': 'validation_event_rate_wilson_95',
                    'lower': event_rate_interval[0] if event_rate_interval else None,
                    'upper': event_rate_interval[1] if event_rate_interval else None,
                    'horizon_days': options['horizon_days'],
                    'drawdown_threshold_pct': options['drawdown_threshold'],
                },
                'features_hash': _features_hash(current_features),
                'metadata': {
                    'prediction_kind': 'drawdown_event_probability',
                    'horizon_days': options['horizon_days'],
                    'drawdown_threshold_pct': options['drawdown_threshold'],
                    'raw_probability': current_raw_probability,
                    'validation_event_count': validation_event_count,
                    'validation_samples': len(scored_validation),
                },
            },
        )

        validation = payload['validation']
        self.stdout.write('急落確率モデル v1 学習完了')
        self.stdout.write(f"出力: {out_path.relative_to(settings.BASE_DIR)}")
        self.stdout.write(
            f"現在推定確率: {current_probability * 100:.1f}% / "
            f"raw {current_raw_probability * 100:.1f}% / "
            f"ROC-AUC: {_fmt(validation['roc_auc'])} / "
            f"PR-AUC: {_fmt(validation['pr_auc'])} / "
            f"Brier: {_fmt(validation['brier_score'])}"
        )
