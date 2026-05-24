"""急落確率モデル v1。

外部の学習ライブラリを使わず、月次データから軽量なロジスティック回帰を学習する。
目的変数は「指定期間内に対象指数が指定率以上下落したか」。
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from statistics import mean
from typing import Dict, List, Optional, Sequence, Tuple

from dateutil.relativedelta import relativedelta

from macro.models import Observation, PriceObservation
from macro.services.crash_alert import compute_crash_alert


TARGET_TICKERS = {
    'GSPC': PriceObservation.Ticker.SP500,
    'IXIC': PriceObservation.Ticker.NASDAQ,
    'N225': PriceObservation.Ticker.NIKKEI,
    'DJI': PriceObservation.Ticker.NYDOW,
}

FEATURE_NAMES = [
    'market_stress_score',
    'forward_risk_score',
    'volatility_sentiment_score',
    'credit_liquidity_score',
    'macro_cycle_score',
    'price_action_score',
    'data_quality_pct',
    'rule_agreement_pct',
]

MODEL_VERSION = 'crash_probability_logistic_v1'


def month_end(month_start: date) -> date:
    return month_start.replace(day=1) + relativedelta(months=1) - timedelta(days=1)


def load_price_series(ticker: str) -> Dict[date, float]:
    rows = (
        PriceObservation.objects
        .filter(ticker=ticker)
        .order_by('observation_month')
        .values_list('observation_month', 'close_price')
    )
    return {month.replace(day=1): close for month, close in rows}


def make_lookup_for_date(target_date: date):
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


def future_drawdown(
    prices: Dict[date, float],
    month_start: date,
    horizon_months: int,
    threshold: float,
) -> Tuple[Optional[float], Optional[int]]:
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
    return max_drawdown, lead_month * 30 if lead_month is not None else None


def _category_score(alert: Dict, category: str) -> Optional[float]:
    for row in alert.get('category_summary', []):
        if row.get('category') == category:
            return row.get('avg_score')
    return None


def _features_from_alert(alert: Dict) -> Dict[str, float]:
    return {
        'market_stress_score': alert.get('market_stress_score') or 0.0,
        'forward_risk_score': alert.get('forward_risk_score') or 0.0,
        'volatility_sentiment_score': _category_score(alert, 'volatility_sentiment') or 0.0,
        'credit_liquidity_score': _category_score(alert, 'credit_liquidity') or 0.0,
        'macro_cycle_score': _category_score(alert, 'macro_cycle') or 0.0,
        'price_action_score': _category_score(alert, 'price_action') or 0.0,
        'data_quality_pct': alert.get('data_quality_pct') or 0.0,
        'rule_agreement_pct': alert.get('rule_agreement_pct') or 0.0,
    }


def build_dataset(
    *,
    target: str,
    horizon_days: int,
    drawdown_threshold: float,
) -> List[Dict]:
    ticker = TARGET_TICKERS[target]
    horizon_months = max(1, math.ceil(horizon_days / 30.4375))
    prices = load_price_series(ticker)
    rows: List[Dict] = []
    for month_start in sorted(prices):
        max_drawdown, lead_time_days = future_drawdown(
            prices,
            month_start,
            horizon_months,
            drawdown_threshold,
        )
        if max_drawdown is None:
            continue
        as_of = month_end(month_start)
        alert = compute_crash_alert(
            value_lookup=make_lookup_for_date(as_of),
            as_of=as_of,
        )
        if alert.get('market_stress_score') is None:
            continue
        rows.append({
            'month': month_start.isoformat(),
            'event': max_drawdown <= drawdown_threshold,
            'max_drawdown_pct': max_drawdown,
            'lead_time_days': lead_time_days,
            'features': _features_from_alert(alert),
        })
    return rows


def current_features() -> Dict[str, float]:
    return _features_from_alert(compute_crash_alert())


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def _standardize_train(
    matrix: Sequence[Sequence[float]],
) -> Tuple[List[List[float]], List[float], List[float]]:
    columns = list(zip(*matrix))
    means = [mean(col) for col in columns]
    scales = []
    for col, col_mean in zip(columns, means):
        variance = mean([(value - col_mean) ** 2 for value in col])
        scales.append(math.sqrt(variance) or 1.0)
    standardized = [
        [(value - means[idx]) / scales[idx] for idx, value in enumerate(row)]
        for row in matrix
    ]
    return standardized, means, scales


def _standardize_row(
    values: Sequence[float],
    means: Sequence[float],
    scales: Sequence[float],
) -> List[float]:
    return [
        (value - means[idx]) / (scales[idx] or 1.0)
        for idx, value in enumerate(values)
    ]


def _feature_row(features: Dict[str, float]) -> List[float]:
    return [(features.get(name) or 0.0) / 100.0 for name in FEATURE_NAMES]


def train_logistic_model(
    train_rows: List[Dict],
    *,
    iterations: int = 1800,
    learning_rate: float = 0.08,
    l2: float = 0.06,
) -> Dict:
    matrix = [_feature_row(row['features']) for row in train_rows]
    labels = [1.0 if row['event'] else 0.0 for row in train_rows]
    x_scaled, means, scales = _standardize_train(matrix)
    x = [[1.0, *row] for row in x_scaled]
    weights = [0.0 for _ in x[0]]

    positive_count = sum(labels)
    negative_count = len(labels) - positive_count
    if positive_count <= 0 or negative_count <= 0:
        raise ValueError('positive and negative samples are required')
    positive_weight = negative_count / positive_count

    for _ in range(iterations):
        gradients = [0.0 for _ in weights]
        for row, label in zip(x, labels):
            pred = _sigmoid(sum(w * value for w, value in zip(weights, row)))
            sample_weight = positive_weight if label == 1.0 else 1.0
            error = (pred - label) * sample_weight
            for idx, value in enumerate(row):
                gradients[idx] += error * value
        count = len(x)
        for idx in range(len(weights)):
            penalty = l2 * weights[idx] if idx > 0 else 0.0
            weights[idx] -= learning_rate * (gradients[idx] / count + penalty)

    return {
        'feature_names': FEATURE_NAMES,
        'weights': weights,
        'means': means,
        'scales': scales,
        'positive_weight': positive_weight,
        'training_samples': len(train_rows),
        'training_event_count': int(positive_count),
    }


def predict_probability(model: Dict, features: Dict[str, float]) -> float:
    row = _feature_row(features)
    scaled = _standardize_row(row, model['means'], model['scales'])
    values = [1.0, *scaled]
    return _sigmoid(sum(w * value for w, value in zip(model['weights'], values)))


def roc_auc(records: List[Dict], score_key: str = 'probability') -> Optional[float]:
    positives = [r[score_key] for r in records if r['event']]
    negatives = [r[score_key] for r in records if not r['event']]
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


def pr_auc(records: List[Dict], score_key: str = 'probability') -> Optional[float]:
    positive_total = sum(1 for r in records if r['event'])
    if positive_total == 0:
        return None
    tp = 0
    fp = 0
    previous_recall = 0.0
    area = 0.0
    for row in sorted(records, key=lambda r: r[score_key], reverse=True):
        if row['event']:
            tp += 1
        else:
            fp += 1
        recall = tp / positive_total
        precision = tp / (tp + fp)
        area += (recall - previous_recall) * precision
        previous_recall = recall
    return area


def brier_score(records: List[Dict]) -> Optional[float]:
    if not records:
        return None
    return mean([
        (row['probability'] - (1.0 if row['event'] else 0.0)) ** 2
        for row in records
    ])


def wilson_interval(
    event_count: int,
    sample_count: int,
    *,
    z_value: float = 1.96,
) -> Optional[Tuple[float, float]]:
    """少数イベントでも極端になりにくい実現率の目安範囲。"""
    if sample_count <= 0 or event_count < 0 or event_count > sample_count:
        return None
    p_hat = event_count / sample_count
    z2 = z_value ** 2
    denominator = 1 + z2 / sample_count
    center = (p_hat + z2 / (2 * sample_count)) / denominator
    spread = (
        z_value
        * math.sqrt(
            (p_hat * (1 - p_hat) / sample_count)
            + z2 / (4 * sample_count ** 2)
        )
        / denominator
    )
    return max(0.0, center - spread), min(1.0, center + spread)


def threshold_metrics(records: List[Dict], thresholds=(0.1, 0.2, 0.3, 0.5)) -> List[Dict]:
    out = []
    for threshold in thresholds:
        tp = sum(1 for r in records if r['probability'] >= threshold and r['event'])
        fp = sum(1 for r in records if r['probability'] >= threshold and not r['event'])
        tn = sum(1 for r in records if r['probability'] < threshold and not r['event'])
        fn = sum(1 for r in records if r['probability'] < threshold and r['event'])
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        fpr = fp / (fp + tn) if fp + tn else None
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision is not None and recall is not None and precision + recall > 0
            else None
        )
        out.append({
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
    return out


def calibration_bins(records: List[Dict], bins: int = 5) -> List[Dict]:
    if not records:
        return []
    out = []
    for idx in range(bins):
        lower = idx / bins
        upper = (idx + 1) / bins
        bucket = [
            row for row in records
            if lower <= row['probability'] < upper
            or (idx == bins - 1 and row['probability'] == 1.0)
        ]
        if not bucket:
            out.append({
                'lower': lower,
                'upper': upper,
                'count': 0,
                'event_count': 0,
                'avg_probability': None,
                'event_rate': None,
                'smoothed_event_rate': None,
            })
            continue
        event_count = sum(1 for row in bucket if row['event'])
        out.append({
            'lower': lower,
            'upper': upper,
            'count': len(bucket),
            'event_count': event_count,
            'avg_probability': mean(row['probability'] for row in bucket),
            'event_rate': event_count / len(bucket),
            'smoothed_event_rate': (event_count + 1) / (len(bucket) + 2),
        })
    return out


def calibrated_probability(raw_probability: float, bins: List[Dict]) -> float:
    """検証期間の確率帯別実現率で、表示用の確率を保守的に校正する。"""
    fallback_rates = [
        row['smoothed_event_rate'] for row in bins
        if row.get('smoothed_event_rate') is not None
    ]
    fallback = mean(fallback_rates) if fallback_rates else raw_probability
    for row in bins:
        lower = row['lower']
        upper = row['upper']
        in_bin = (
            lower <= raw_probability < upper
            or (upper == 1.0 and raw_probability == 1.0)
        )
        if in_bin and row.get('smoothed_event_rate') is not None:
            return row['smoothed_event_rate']
    return fallback


def coefficient_rows(model: Dict) -> List[Dict]:
    rows = []
    for name, weight in zip(model['feature_names'], model['weights'][1:]):
        rows.append({
            'feature': name,
            'coefficient': weight,
            'direction': 'raises_risk' if weight >= 0 else 'lowers_risk',
        })
    return sorted(rows, key=lambda r: abs(r['coefficient']), reverse=True)
