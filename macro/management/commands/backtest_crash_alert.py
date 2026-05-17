"""市場ストレス・急落警戒スコアを月次終値ベースで検証する。"""

import csv
import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean, median

from dateutil.relativedelta import relativedelta
from django.core.management.base import BaseCommand, CommandError

from macro.models import Observation, PriceObservation
from macro.services.crash_alert import compute_crash_alert


DEFAULT_TARGETS = [
    ('2023-03-13', 'SVB 銀行危機'),
    ('2023-10-27', '長期金利急上昇局面'),
    ('2024-04-19', '中東情勢悪化の下落'),
    ('2024-08-05', '円キャリー巻き戻し'),
    ('2025-04-07', '関税ショック'),
    ('2026-05-08', '現在参照'),
]

TARGET_TICKERS = {
    'GSPC': PriceObservation.Ticker.SP500,
    'IXIC': PriceObservation.Ticker.NASDAQ,
    'N225': PriceObservation.Ticker.NIKKEI,
    'DJI': PriceObservation.Ticker.NYDOW,
}

THRESHOLDS = (25, 50, 70, 85)


def _month_end(month_start: date) -> date:
    return month_start.replace(day=1) + relativedelta(months=1) - timedelta(days=1)


def _make_lookup_for_date(target_date: date):
    """target_date 時点で利用可能だった各 series の最新値を返す。"""
    cache = {}

    def lookup(series_id: str):
        if series_id in cache:
            return cache[series_id]
        obs = (
            Observation.objects
            .filter(
                indicator__fred_series_id=series_id,
                observation_date__lte=target_date,
            )
            .select_related('indicator')
            .order_by('-observation_date')
            .first()
        )
        if obs is None:
            cache[series_id] = None
        else:
            cache[series_id] = {
                'value': obs.value,
                'observation_date': obs.observation_date,
                'frequency': obs.indicator.frequency,
            }
        return cache[series_id]

    return lookup


def _parse_dates(spec: str):
    out = []
    for item in spec.split(','):
        value = item.strip()
        if not value:
            continue
        try:
            datetime.strptime(value, '%Y-%m-%d')
        except ValueError as exc:
            raise CommandError(f"不正な日付指定: {value} ({exc})")
        out.append((value, ''))
    return out


def _load_price_series(ticker: str):
    rows = (
        PriceObservation.objects
        .filter(ticker=ticker)
        .order_by('observation_month')
        .values_list('observation_month', 'close_price')
    )
    return {month.replace(day=1): close for month, close in rows}


def _future_drawdown(
    prices,
    month_start: date,
    horizon_months: int,
    threshold: float,
):
    base = prices.get(month_start)
    if base in (None, 0):
        return None, None
    max_drawdown = None
    lead_month = None
    for offset in range(1, horizon_months + 1):
        target_month = month_start + relativedelta(months=offset)
        future = prices.get(target_month)
        if future is None:
            continue
        ret = (future - base) / base * 100.0
        if max_drawdown is None or ret < max_drawdown:
            max_drawdown = ret
        if lead_month is None and ret <= threshold:
            lead_month = offset
    if max_drawdown is None:
        return None, None
    lead_days = lead_month * 30 if lead_month is not None else None
    return max_drawdown, lead_days


def _roc_auc(records):
    positives = [r['score'] for r in records if r['event']]
    negatives = [r['score'] for r in records if not r['event']]
    if not positives or not negatives:
        return None
    wins = 0.0
    total = 0
    for pos in positives:
        for neg in negatives:
            total += 1
            if pos > neg:
                wins += 1
            elif pos == neg:
                wins += 0.5
    return wins / total if total else None


def _pr_auc(records):
    positive_total = sum(1 for r in records if r['event'])
    if positive_total == 0:
        return None
    tp = 0
    fp = 0
    previous_recall = 0.0
    area = 0.0
    for row in sorted(records, key=lambda r: r['score'], reverse=True):
        if row['event']:
            tp += 1
        else:
            fp += 1
        recall = tp / positive_total
        precision = tp / (tp + fp)
        area += (recall - previous_recall) * precision
        previous_recall = recall
    return area


def _threshold_metrics(records):
    metrics = []
    for threshold in THRESHOLDS:
        tp = sum(1 for r in records if r['score'] >= threshold and r['event'])
        fp = sum(1 for r in records if r['score'] >= threshold and not r['event'])
        tn = sum(1 for r in records if r['score'] < threshold and not r['event'])
        fn = sum(1 for r in records if r['score'] < threshold and r['event'])
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        fpr = fp / (fp + tn) if fp + tn else None
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision is not None and recall is not None and precision + recall > 0
            else None
        )
        metrics.append({
            'threshold': threshold,
            'precision': precision,
            'recall': recall,
            'f1': f1,
            'false_positive_rate': fpr,
            'tp': tp,
            'fp': fp,
            'tn': tn,
            'fn': fn,
        })
    return metrics


def _quantiles(values):
    if not values:
        return None
    values = sorted(values)
    return {
        'min': values[0],
        'median': median(values),
        'mean': mean(values),
        'max': values[-1],
    }


def _round_float(value, digits=4):
    if value is None:
        return None
    return round(float(value), digits)


def _rounded_payload(payload):
    def convert(value):
        if isinstance(value, float):
            if math.isnan(value) or math.isinf(value):
                return None
            return _round_float(value)
        if isinstance(value, dict):
            return {k: convert(v) for k, v in value.items()}
        if isinstance(value, list):
            return [convert(v) for v in value]
        if isinstance(value, date):
            return value.isoformat()
        return value

    return convert(payload)


def _write_json(path: str, payload: dict):
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(_rounded_payload(payload), ensure_ascii=False, indent=2),
        encoding='utf-8',
    )


def _write_csv(path: str, rows):
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        'month',
        'score',
        'level',
        'level_label',
        'data_quality_pct',
        'rule_agreement_pct',
        'event',
        'max_drawdown_pct',
        'lead_time_days',
    ]
    with output_path.open('w', encoding='utf-8', newline='') as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


class Command(BaseCommand):
    help = '市場ストレス・急落警戒スコアを月次終値ベースで検証する'

    def add_arguments(self, parser):
        parser.add_argument('--dates', default='', help='カンマ区切りの YYYY-MM-DD 日付。指定時は簡易表示のみ。')
        parser.add_argument('--details', action='store_true', help='簡易表示で各コンポーネントも表示する。')
        parser.add_argument('--target', default='GSPC', choices=sorted(TARGET_TICKERS), help='検証対象指数。')
        parser.add_argument('--horizon-days', type=int, default=63, help='将来下落を確認する期間。')
        parser.add_argument('--drawdown-threshold', type=float, default=-10.0, help='イベント判定する最大下落率。例: -10')
        parser.add_argument('--output', default='', help='検証サマリJSONの出力先。')
        parser.add_argument('--csv-output', default='', help='月次行CSVの出力先。')

    def handle(self, *args, **options):
        if options['dates']:
            self._handle_point_in_time(options)
            return
        self._handle_backtest(options)

    def _handle_point_in_time(self, options):
        targets = _parse_dates(options['dates']) if options['dates'] else DEFAULT_TARGETS
        self.stdout.write('=' * 80)
        self.stdout.write('市場ストレス・急落警戒スコア 簡易確認')
        self.stdout.write('=' * 80)
        self.stdout.write(f"{'日付':<12} {'レベル':<8} {'点':>4} {'品質':>5} {'強度':>5} 局面")
        self.stdout.write('-' * 80)
        for date_str, label in targets:
            target = datetime.strptime(date_str, '%Y-%m-%d').date()
            result = compute_crash_alert(
                value_lookup=_make_lookup_for_date(target),
                as_of=target,
            )
            score = result['total_score']
            score_str = f'{score:>4}' if score is not None else '   -'
            self.stdout.write(
                f"{date_str:<12} {result['level_label']:<8} {score_str}"
                f" {result['data_quality_pct']:>4}% {result['rule_agreement_pct']:>4}% {label}"
            )
            if options['details']:
                for component in sorted(
                    result['components'],
                    key=lambda x: (x['category'], -(x['score'] or -1)),
                ):
                    self.stdout.write(
                        f"  {component['category']:<22} {component['label']:<20}"
                        f" score={component['score'] if component['score'] is not None else '-':>3}"
                        f" date={component['observation_date'] or '-'}"
                        f" fresh={'Y' if component['is_fresh'] else 'N'}"
                    )
        self.stdout.write('=' * 80)

    def _handle_backtest(self, options):
        target = TARGET_TICKERS[options['target']]
        horizon_months = max(1, math.ceil(options['horizon_days'] / 30.4375))
        threshold = options['drawdown_threshold']
        prices = _load_price_series(target)
        if len(prices) < horizon_months + 2:
            raise CommandError('価格データが不足しています。先に価格データを更新してください。')

        rows = []
        months = sorted(prices)
        for month_start in months:
            max_drawdown, lead_time_days = _future_drawdown(
                prices,
                month_start,
                horizon_months,
                threshold,
            )
            if max_drawdown is None:
                continue
            as_of = _month_end(month_start)
            result = compute_crash_alert(
                value_lookup=_make_lookup_for_date(as_of),
                as_of=as_of,
            )
            if result['total_score'] is None:
                continue
            rows.append({
                'month': month_start.isoformat(),
                'score': result['total_score'],
                'level': result['level'],
                'level_label': result['level_label'],
                'data_quality_pct': result['data_quality_pct'],
                'rule_agreement_pct': result['rule_agreement_pct'],
                'event': max_drawdown <= threshold,
                'max_drawdown_pct': max_drawdown,
                'lead_time_days': lead_time_days,
            })

        if not rows:
            raise CommandError('検証に使える月次行がありません。')

        event_count = sum(1 for row in rows if row['event'])
        lead_times = [row['lead_time_days'] for row in rows if row['lead_time_days'] is not None]
        danger_drawdowns = [
            row['max_drawdown_pct'] for row in rows
            if row['level'] == 'danger'
        ]
        calm_misses = sum(
            1 for row in rows
            if row['level'] == 'calm' and row['event']
        )
        payload = {
            'target': options['target'],
            'horizon_days': options['horizon_days'],
            'horizon_months_used': horizon_months,
            'drawdown_threshold_pct': threshold,
            'sample_count': len(rows),
            'event_count': event_count,
            'roc_auc': _roc_auc(rows),
            'pr_auc': _pr_auc(rows),
            'thresholds': _threshold_metrics(rows),
            'lead_time_days': _quantiles(lead_times),
            'danger_drawdown_pct': _quantiles(danger_drawdowns),
            'calm_miss_count': calm_misses,
            'note': '月次終値ベースの検証です。月中の最大下落は反映していません。',
            'rows': rows,
        }

        self.stdout.write('市場ストレス・急落警戒スコア 月次バックテスト')
        self.stdout.write(f"対象: {options['target']} / 期間: {options['horizon_days']}日相当 / 下落: {threshold:.1f}%")
        self.stdout.write(f"サンプル: {len(rows)} / イベント: {event_count}")
        self.stdout.write(f"ROC-AUC: {_round_float(payload['roc_auc'])} / PR-AUC: {_round_float(payload['pr_auc'])}")
        self.stdout.write(f"平常表示時の取り逃し: {calm_misses}件")

        if options['output']:
            _write_json(options['output'], payload)
            self.stdout.write(f"JSON出力: {options['output']}")
        if options['csv_output']:
            _write_csv(options['csv_output'], rows)
            self.stdout.write(f"CSV出力: {options['csv_output']}")
