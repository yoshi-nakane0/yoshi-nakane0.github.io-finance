"""レジーム判定ロジックの簡易バックテスト。"""

import json
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

from django.core.management.base import BaseCommand

from macro.models import Observation, RegimeSnapshot
from macro.services.regime import (
    MODEL_VERSION,
    _latest_observation,
    classify_regime,
    collect_key_metrics,
)


def _add_month(d: date) -> date:
    year = d.year + (1 if d.month == 12 else 0)
    month = 1 if d.month == 12 else d.month + 1
    return date(year, month, 1)


def _month_starts(start: date, end: date) -> List[date]:
    current = date(start.year, start.month, 1)
    last = date(end.year, end.month, 1)
    months = []
    while current <= last:
        months.append(current)
        current = _add_month(current)
    return months


def _prediction_is_recession(label: str) -> bool:
    return label == RegimeSnapshot.Label.CONTRACTION


def _actual_recession(as_of: date) -> Optional[bool]:
    obs = _latest_observation('USREC', as_of=as_of)
    if obs is None or obs.value is None:
        return None
    return obs.value >= 0.5


def _classification_metrics(rows: List[Dict]) -> Dict:
    usable = [r for r in rows if r['actual_recession'] is not None]
    if not usable:
        return {
            'truth_source': 'USREC unavailable',
            'evaluated_count': 0,
            'accuracy': None,
            'precision': None,
            'recall': None,
            'false_positive_rate': None,
            'false_negative_rate': None,
        }

    tp = sum(1 for r in usable if r['predicted_recession'] and r['actual_recession'])
    tn = sum(1 for r in usable if not r['predicted_recession'] and not r['actual_recession'])
    fp = sum(1 for r in usable if r['predicted_recession'] and not r['actual_recession'])
    fn = sum(1 for r in usable if not r['predicted_recession'] and r['actual_recession'])

    def safe_div(num, den):
        return round(num / den, 4) if den else None

    return {
        'truth_source': 'USREC',
        'evaluated_count': len(usable),
        'accuracy': safe_div(tp + tn, len(usable)),
        'precision': safe_div(tp, tp + fp),
        'recall': safe_div(tp, tp + fn),
        'false_positive_rate': safe_div(fp, fp + tn),
        'false_negative_rate': safe_div(fn, fn + tp),
    }


class Command(BaseCommand):
    help = 'macro レジーム判定を月次で簡易バックテストする'

    def add_arguments(self, parser):
        parser.add_argument(
            '--years',
            type=int,
            default=10,
            help='直近何年分を検証するか',
        )
        parser.add_argument(
            '--output',
            type=str,
            default='',
            help='結果JSONの保存先。未指定なら保存しない',
        )

    def handle(self, *args, **options):
        years = max(options['years'], 1)
        latest = (
            Observation.objects
            .order_by('-observation_date')
            .values_list('observation_date', flat=True)
            .first()
        )
        if latest is None:
            result = {
                'model_version': MODEL_VERSION,
                'sample_count': 0,
                'message': 'Observation がないため検証対象がありません。',
                'metrics': _classification_metrics([]),
                'label_counts': {},
            }
            self._emit(result, options['output'])
            return

        start = date(max(latest.year - years, 1900), latest.month, 1)
        rows = []
        label_counts: Dict[str, int] = {}

        for month in _month_starts(start, latest):
            metrics = collect_key_metrics(as_of=month)
            label, strength = classify_regime(metrics)
            label_counts[label] = label_counts.get(label, 0) + 1
            actual = _actual_recession(month)
            rows.append({
                'month': month.isoformat(),
                'label': label,
                'rule_strength': strength,
                'predicted_recession': _prediction_is_recession(label),
                'actual_recession': actual,
            })

        result = {
            'model_version': MODEL_VERSION,
            'sample_count': len(rows),
            'metrics': _classification_metrics(rows),
            'label_counts': label_counts,
        }
        self._emit(result, options['output'])

    def _emit(self, result: Dict, output_path: str) -> None:
        text = json.dumps(result, ensure_ascii=False, indent=2)
        if output_path:
            Path(output_path).write_text(text + '\n', encoding='utf-8')
        self.stdout.write(text)
