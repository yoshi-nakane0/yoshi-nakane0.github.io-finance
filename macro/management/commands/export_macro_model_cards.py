from django.core.management.base import BaseCommand, CommandError

from macro.services.dashboard_cache import write_static_macro_payload
from macro.services.model_validation import latest_validation_reports, model_display_grade


POLICY_LABELS = {
    'show': 'トップ表示可',
    'reference': '参考値',
    'hidden': '監査ページのみ',
    'blocked': '使用不可',
}


def build_model_cards(limit=100):
    cards = []
    for report in latest_validation_reports(limit=limit):
        display_policy, reason = model_display_grade(report)
        metrics = report.metrics or {}
        cards.append({
            'model_version': report.model_version,
            'purpose': f'{report.target}の{report.horizon}先を予測する',
            'target': report.target,
            'horizon': report.horizon,
            'feature_set': (report.metrics or {}).get('feature_set') or [],
            'training_period': (report.metrics or {}).get('training_period'),
            'validation_period': (report.metrics or {}).get('validation_period'),
            'display_policy': display_policy,
            'display_label': POLICY_LABELS.get(display_policy, display_policy),
            'display_reason': reason,
            'sample_count': report.sample_count,
            'event_count': report.event_count,
            'baseline': (report.metrics or {}).get('baseline') or 'zero_or_last_value',
            'validation_method': report.validation_method,
            'key_metrics': {
                'mae': metrics.get('mae'),
                'rmse': metrics.get('rmse'),
                'baseline_mae': metrics.get('baseline_mae'),
                'skill_score': metrics.get('skill_score'),
                'direction_accuracy': metrics.get('direction_accuracy'),
                'roc_auc': metrics.get('roc_auc'),
                'pr_auc': metrics.get('pr_auc'),
                'brier_score': metrics.get('brier_score'),
            },
            'known_weaknesses': report.warnings or [],
            'limitations': report.warnings or [],
            'leakage_check': metrics.get('leakage_check') or 'not_flagged',
            'publication_lag_check': metrics.get('publication_lag_check') or 'not_flagged',
            'last_validated_at': report.evaluated_at.isoformat(),
            'evaluated_at': report.evaluated_at.isoformat(),
        })
    warnings = []
    status = 'ok'
    if not cards:
        status = 'blocked'
        warnings.append('model cards = 0')
    return {'model_cards': cards, 'status': status, 'warnings': warnings}


class Command(BaseCommand):
    help = 'モデルカードを static JSON として出力する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output',
            default='static/macro/model_cards.json',
            help='出力先JSONパス',
        )
        parser.add_argument('--limit', type=int, default=100)
        parser.add_argument(
            '--allow-empty',
            action='store_true',
            help='モデルカードが空でもJSONを書き出す（手元の診断用途のみ）',
        )

    def handle(self, *args, **options):
        payload = build_model_cards(limit=options['limit'])
        if payload['status'] == 'blocked' and not options['allow_empty']:
            raise CommandError('model_cards is blocked: model cards = 0')
        write_static_macro_payload(payload, options['output'])
        self.stdout.write(self.style.SUCCESS(f"exported model cards: {options['output']}"))
