from datetime import timedelta
from typing import Dict, Optional

from django.utils import timezone

from basecalc.market_bars import market_bars_between, nearest_bar_for_horizon

from ..models import ExplanationSnapshot, ExplanationTradeOutcome
from .static_snapshot import load_static_trade_outcomes


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
            'is_actionable': metrics['is_actionable'],
            'outcome_kind': metrics['outcome_kind'],
            'missed_opportunity': metrics['missed_opportunity'],
            'horizon_return_pct': metrics['horizon_return_pct'],
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


def build_trade_validation_summary(include_static=True) -> Dict[str, object]:
    db_rows = list(ExplanationTradeOutcome.objects.select_related('explanation').all()[:1000])
    static_rows = load_static_trade_outcomes() if include_static else []
    rows = _normalize_and_merge_outcomes(db_rows, static_rows)
    return _summarize_trade_outcomes(rows)


def build_static_trade_validation_summary(path=None) -> Dict[str, object]:
    return _summarize_trade_outcomes(_normalize_and_merge_outcomes([], load_static_trade_outcomes(path)))


def _summarize_trade_outcomes(rows):
    actionable_rows = [row for row in rows if row['is_actionable']]
    wait_rows = [row for row in rows if not row['is_actionable']]
    wait_after_large_move_count = sum(1 for row in wait_rows if abs(row.get('horizon_return_pct') or 0) >= 1)
    return {
        'available': bool(rows),
        'total_count': len(rows),
        'actionable_count': len(actionable_rows),
        'wait_count': len(wait_rows),
        'wait_after_large_move_count': wait_after_large_move_count,
        'missed_opportunity_count': sum(1 for row in wait_rows if row.get('missed_opportunity')),
        'horizon_rows': _group_rows(rows, 'horizon'),
        'side_rows': _group_rows(rows, 'selected_side'),
        'style_rows': _group_rows(rows, 'trend_or_reversal'),
        'confidence_rows': _group_rows(rows, 'confidence_bucket'),
        'wait_reason_rows': _group_rows(wait_rows, 'decision_type'),
        'one_line': f"検証 {len(rows)}件 / 売買候補 {len(actionable_rows)}件 / 待機観測 {len(wait_rows)}件",
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
            'direction_hit': None,
            'realized_rr': None,
            'exit_reason': 'wait_observed',
            'outcome_kind': 'wait_observed',
            'is_actionable': False,
            'missed_opportunity': False,
            'horizon_return_pct': _return_pct(entry, exit_price),
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
        'outcome_kind': 'actionable_observed',
        'is_actionable': True,
        'missed_opportunity': False,
        'horizon_return_pct': _return_pct(entry, exit_price),
    }


def _group_rows(rows, field_name):
    grouped = {}
    for row in rows:
        key = row.get(field_name) or 'unknown'
        item = grouped.setdefault(
            key,
            {
                'label': key,
                'sample_count': 0,
                'direction_sample_count': 0,
                'direction_hit_count': 0,
                'target_1_hit_count': 0,
                'stop_hit_count': 0,
            },
        )
        item['sample_count'] += 1
        if row.get('is_actionable'):
            item['direction_sample_count'] += 1
            item['direction_hit_count'] += 1 if row.get('direction_hit') else 0
        item['target_1_hit_count'] += 1 if row.get('target_1_hit') else 0
        item['stop_hit_count'] += 1 if row.get('stop_hit') else 0
    result = []
    for item in grouped.values():
        total = item['sample_count']
        item['direction_hit_rate'] = _rate(item['direction_hit_count'], item['direction_sample_count'])
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


def _return_pct(entry, exit_price):
    entry = _number(entry)
    exit_price = _number(exit_price)
    if entry is None or exit_price is None or entry == 0:
        return None
    return round((exit_price - entry) / entry * 100, 2)


def _normalize_and_merge_outcomes(db_rows, static_rows):
    merged = {}
    for row in db_rows:
        normalized = _normalize_db_outcome(row)
        merged[_outcome_key(normalized)] = normalized
    for row in static_rows:
        normalized = _normalize_static_outcome(row)
        if normalized is None:
            continue
        merged.setdefault(_outcome_key(normalized), normalized)
    return sorted(
        merged.values(),
        key=lambda row: (row.get('evaluated_at') or '', row.get('explanation_as_of') or '', row.get('horizon') or ''),
        reverse=True,
    )


def _normalize_db_outcome(row):
    selected_side = row.selected_side or 'no_trade'
    return {
        'explanation_as_of': row.explanation.as_of.isoformat() if row.explanation_id else '',
        'horizon': row.horizon,
        'evaluated_at': row.evaluated_at.isoformat() if row.evaluated_at else '',
        'selected_side': selected_side,
        'decision_type': row.decision_type or '',
        'trend_or_reversal': row.trend_or_reversal or 'no_trade',
        'direction_hit': row.direction_hit,
        'target_1_hit': row.target_1_hit,
        'stop_hit': row.stop_hit,
        'realized_rr': row.realized_rr,
        'expected_rr': row.expected_rr,
        'macro_regime': row.macro_regime,
        'technical_regime': row.technical_regime,
        'confidence_bucket': row.confidence_bucket,
        'outcome_kind': row.outcome_kind or _outcome_kind(selected_side),
        'is_actionable': selected_side in {'long', 'short'},
        'missed_opportunity': row.missed_opportunity,
        'horizon_return_pct': row.horizon_return_pct,
    }


def _normalize_static_outcome(row):
    if not isinstance(row, dict):
        return None
    selected_side = row.get('selected_side') or 'no_trade'
    return {
        'explanation_as_of': row.get('explanation_as_of') or '',
        'horizon': row.get('horizon') or '',
        'evaluated_at': row.get('evaluated_at') or '',
        'selected_side': selected_side,
        'decision_type': row.get('decision_type') or '',
        'trend_or_reversal': row.get('trend_or_reversal') or 'no_trade',
        'direction_hit': row.get('direction_hit') if selected_side in {'long', 'short'} else None,
        'target_1_hit': bool(row.get('target_1_hit')),
        'stop_hit': bool(row.get('stop_hit')),
        'realized_rr': _number(row.get('realized_rr')),
        'expected_rr': _number(row.get('expected_rr')),
        'macro_regime': row.get('macro_regime') or '',
        'technical_regime': row.get('technical_regime') or '',
        'confidence_bucket': row.get('confidence_bucket') or '',
        'outcome_kind': row.get('outcome_kind') or _outcome_kind(selected_side),
        'is_actionable': selected_side in {'long', 'short'},
        'missed_opportunity': bool(row.get('missed_opportunity')),
        'horizon_return_pct': _number(row.get('horizon_return_pct')),
    }


def _outcome_key(row):
    return '|'.join(
        str(row.get(key) or '')
        for key in ('explanation_as_of', 'horizon', 'selected_side', 'decision_type')
    )


def _outcome_kind(selected_side):
    return 'actionable_observed' if selected_side in {'long', 'short'} else 'wait_observed'
