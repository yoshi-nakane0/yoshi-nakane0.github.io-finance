from django.core.management.base import BaseCommand

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
            'target': report.target,
            'horizon': report.horizon,
            'display_policy': display_policy,
            'display_label': POLICY_LABELS.get(display_policy, display_policy),
            'display_reason': reason,
            'sample_count': report.sample_count,
            'event_count': report.event_count,
            'validation_method': report.validation_method,
            'key_metrics': {
                'mae': metrics.get('mae'),
                'baseline_mae': metrics.get('baseline_mae'),
                'skill_score': metrics.get('skill_score'),
                'direction_accuracy': metrics.get('direction_accuracy'),
                'roc_auc': metrics.get('roc_auc'),
                'pr_auc': metrics.get('pr_auc'),
            },
            'limitations': report.warnings or [],
            'evaluated_at': report.evaluated_at.isoformat(),
        })
    return {'model_cards': cards}


class Command(BaseCommand):
    help = 'モデルカードを static JSON として出力する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output',
            default='static/macro/model_cards.json',
            help='出力先JSONパス',
        )
        parser.add_argument('--limit', type=int, default=100)

    def handle(self, *args, **options):
        payload = build_model_cards(limit=options['limit'])
        write_static_macro_payload(payload, options['output'])
        self.stdout.write(self.style.SUCCESS(f"exported model cards: {options['output']}"))
