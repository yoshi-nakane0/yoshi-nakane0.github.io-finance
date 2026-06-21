from datetime import timedelta
from typing import Dict, Optional

from django.utils import timezone

from basecalc.market_bars import market_bars_between, nearest_bar_for_horizon

from ..models import ExplanationSnapshot, ExplanationTradeOutcome


HORIZON_DAYS = {
    '1d': 1,
    '3d': 3,
    '5d': 5,
}


def evaluate_trade_outcome(snapshot: ExplanationSnapshot, horizon: str) -> Optional[ExplanationTradeOutcome]:
    if horizon not in HORIZON_DAYS:
        return None
    decision = snapshot.trade_decision or {}
    if not decision:
        return None
    symbol, instrument_key = _symbol_context(snapshot)
    target_at = snapshot.as_of + timedelta(days=HORIZON_DAYS[horizon])
    evaluation_bar = nearest_bar_for_horizon(symbol, horizon, target_at, instrument_key=instrument_key)
    if evaluation_bar is None:
        return None
    bars = market_bars_between(
        symbol,
        '1d',
        snapshot.as_of,
        evaluation_bar.timestamp,
        instrument_key=instrument_key,
    )
    if evaluation_bar not in bars:
        bars.append(evaluation_bar)
    bars = sorted(bars, key=lambda bar: bar.timestamp)
    metrics = _outcome_metrics(decision, bars, evaluation_bar.close)
    outcome, _created = ExplanationTradeOutcome.objects.update_or_create(
        explanation=snapshot,
        horizon=horizon,
        defaults={
            'evaluated_at': evaluation_bar.timestamp,
            'selected_side': decision.get('selected_side') or 'no_trade',
            'decision_type': decision.get('decision_type') or '',
            'trend_or_reversal': _trend_or_reversal(decision),
            'entry_price': _number(decision.get('entry_price') or decision.get('current_price')),
            'target_1_price': _target_price(decision, 'target_1'),
            'target_1_hit': metrics['target_1_hit'],
            'target_2_price': _target_price(decision, 'target_2'),
            'target_2_hit': metrics['target_2_hit'],
            'stop_price': _number(decision.get('stop_price')),
            'stop_hit': metrics['stop_hit'],
            'max_favorable_excursion': metrics['mfe_pct'],
            'max_adverse_excursion': metrics['mae_pct'],
            'exit_price': evaluation_bar.close,
            'exit_reason': metrics['exit_reason'],
            'realized_rr': metrics['realized_rr'],
            'expected_rr': _number(decision.get('reward_risk')),
            'direction_hit': metrics['direction_hit'],
            'macro_regime': snapshot.macro_bias,
            'technical_regime': snapshot.basecalc_bias,
            'confidence_bucket': _confidence_bucket(decision.get('confidence_score')),
            'sample_count_at_decision': _sample_count(snapshot),
        },
    )
    return outcome


def evaluate_due_trade_outcomes(horizon: Optional[str] = None) -> Dict[str, int]:
    horizons = [horizon] if horizon else list(HORIZON_DAYS)
    counts = {item: 0 for item in horizons if item in HORIZON_DAYS}
    now = timezone.now()
    for item in list(counts):
        due_at = now - timedelta(days=HORIZON_DAYS[item])
        snapshots = ExplanationSnapshot.objects.filter(as_of__lte=due_at).exclude(trade_decision={})
        for snapshot in snapshots.iterator():
            if evaluate_trade_outcome(snapshot, item) is not None:
                counts[item] += 1
    return counts


def build_trade_validation_summary() -> Dict[str, object]:
    rows = list(ExplanationTradeOutcome.objects.all()[:1000])
    return {
        'available': bool(rows),
        'side_rows': _group_rows(rows, 'selected_side'),
        'style_rows': _group_rows(rows, 'trend_or_reversal'),
        'confidence_rows': _group_rows(rows, 'confidence_bucket'),
    }


def _outcome_metrics(decision, bars, exit_price):
    side = decision.get('selected_side') or 'no_trade'
    entry = _number(decision.get('entry_price') or decision.get('current_price'))
    target_1 = _target_price(decision, 'target_1')
    target_2 = _target_price(decision, 'target_2')
    stop = _number(decision.get('stop_price'))
    if side == 'no_trade' or entry is None:
        return {
            'target_1_hit': False,
            'target_2_hit': False,
            'stop_hit': False,
            'mfe_pct': None,
            'mae_pct': None,
            'direction_hit': False,
            'realized_rr': None,
            'exit_reason': 'no_trade_observed',
        }
    highs = [_number(bar.high) or bar.close for bar in bars]
    lows = [_number(bar.low) or bar.close for bar in bars]
    high = max(highs or [exit_price])
    low = min(lows or [exit_price])
    if side == 'long':
        target_1_hit = target_1 is not None and high >= target_1
        target_2_hit = target_2 is not None and high >= target_2
        stop_hit = stop is not None and low <= stop
        mfe = (high - entry) / entry * 100
        mae = (low - entry) / entry * 100
        direction_hit = exit_price > entry
        realized = exit_price - entry
        risk = entry - stop if stop is not None else None
    else:
        target_1_hit = target_1 is not None and low <= target_1
        target_2_hit = target_2 is not None and low <= target_2
        stop_hit = stop is not None and high >= stop
        mfe = (entry - low) / entry * 100
        mae = (entry - high) / entry * 100
        direction_hit = exit_price < entry
        realized = entry - exit_price
        risk = stop - entry if stop is not None else None
    realized_rr = round(realized / risk, 2) if risk and risk > 0 else None
    return {
        'target_1_hit': target_1_hit,
        'target_2_hit': target_2_hit,
        'stop_hit': stop_hit,
        'mfe_pct': round(mfe, 2),
        'mae_pct': round(mae, 2),
        'direction_hit': direction_hit,
        'realized_rr': realized_rr,
        'exit_reason': 'stop' if stop_hit else 'target_1' if target_1_hit else 'horizon',
    }


def _group_rows(rows, field_name):
    grouped = {}
    for row in rows:
        key = getattr(row, field_name) or 'unknown'
        item = grouped.setdefault(
            key,
            {
                'label': key,
                'sample_count': 0,
                'direction_hit_count': 0,
                'target_1_hit_count': 0,
                'stop_hit_count': 0,
            },
        )
        item['sample_count'] += 1
        item['direction_hit_count'] += 1 if row.direction_hit else 0
        item['target_1_hit_count'] += 1 if row.target_1_hit else 0
        item['stop_hit_count'] += 1 if row.stop_hit else 0
    result = []
    for item in grouped.values():
        total = item['sample_count']
        item['direction_hit_rate'] = _rate(item['direction_hit_count'], total)
        item['target_1_hit_rate'] = _rate(item['target_1_hit_count'], total)
        item['stop_hit_rate'] = _rate(item['stop_hit_count'], total)
        result.append(item)
    return sorted(result, key=lambda item: item['label'])


def _rate(hit, total):
    if not total:
        return 'N/A'
    return f'{hit / total * 100:.0f}%'


def _symbol_context(snapshot):
    basecalc = ((snapshot.source_snapshots or {}).get('basecalc') or {}).get('raw') or {}
    world_model = basecalc.get('world_model') or (basecalc.get('data') or {}).get('world_model') or {}
    features = world_model.get('features') or {}
    symbol = features.get('source_symbol') or world_model.get('source_symbol') or 'NIY=F'
    instrument_key = features.get('instrument_key') or world_model.get('instrument_key') or None
    return symbol, instrument_key


def _target_price(decision, key):
    target = decision.get(key) or {}
    return _number(target.get('price')) if isinstance(target, dict) else None


def _trend_or_reversal(decision):
    value = decision.get('decision_type') or ''
    return 'reversal' if 'reversal' in value else 'trend' if value in {'trend_follow', 'pullback', 'rally_sell'} else 'no_trade'


def _confidence_bucket(score):
    score = _number(score)
    if score is None:
        return ''
    if score >= 70:
        return 'high'
    if score >= 50:
        return 'middle'
    return 'low'


def _sample_count(snapshot):
    basecalc = ((snapshot.source_snapshots or {}).get('basecalc') or {}).get('raw') or {}
    world_model = basecalc.get('world_model') or (basecalc.get('data') or {}).get('world_model') or {}
    similar = world_model.get('similar_summary') or {}
    try:
        return int(similar.get('case_count')) if similar.get('case_count') is not None else None
    except (TypeError, ValueError):
        return None


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
