from datetime import timedelta
from typing import Dict, Optional

from django.utils import timezone

from basecalc.market_bars import market_bars_between, nearest_bar_for_horizon
from basecalc.validation_report import load_validation_report

from ..models import ExplanationSnapshot, ExplanationTradeOutcome
from .static_snapshot import load_static_trade_outcomes, snapshot_from_payload, snapshot_key_for_payload


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
    metrics = _outcome_metrics(decision, bars, evaluation_bar.close, horizon=horizon)
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
            'target_1_hit': bool(metrics['target_1_hit']),
            'target_2_price': _target_price(decision, 'target_2'),
            'target_2_hit': bool(metrics['target_2_hit']),
            'stop_price': _number(decision.get('stop_price')),
            'stop_hit': bool(metrics['stop_hit']),
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


def build_pending_trade_outcomes(snapshot_rows, existing_rows, horizon: Optional[str] = None, now=None):
    now = now or timezone.now()
    horizons = [horizon] if horizon else list(HORIZON_DAYS)
    existing_keys = {
        _outcome_key(_normalize_static_outcome(row) or {})
        for row in existing_rows or []
        if isinstance(row, dict)
    }
    pending_rows = []
    for payload in snapshot_rows or []:
        if not isinstance(payload, dict):
            continue
        snapshot = snapshot_from_payload(payload)
        decision = snapshot.trade_decision or {}
        if not decision:
            continue
        snapshot_key = payload.get('snapshot_key') or snapshot_key_for_payload(payload)
        for item in horizons:
            if item not in HORIZON_DAYS:
                continue
            target_at = snapshot.as_of + timedelta(days=HORIZON_DAYS[item])
            if target_at > now:
                continue
            candidate_key = '|'.join(str(value or '') for value in (snapshot_key, item))
            if candidate_key in existing_keys:
                continue
            symbol, instrument_key = _symbol_context(snapshot)
            evaluation_bar = nearest_bar_for_horizon(symbol, item, target_at, instrument_key=instrument_key)
            if evaluation_bar is not None:
                continue
            pending_rows.append(_pending_outcome_payload(snapshot, snapshot_key, item))
    return pending_rows


def build_trade_validation_summary(include_static=True) -> Dict[str, object]:
    db_rows = list(ExplanationTradeOutcome.objects.select_related('explanation').all()[:1000])
    static_rows = load_static_trade_outcomes() if include_static else []
    rows = _normalize_and_merge_outcomes(db_rows, static_rows)
    return _summarize_trade_outcomes(rows)


def build_static_trade_validation_summary(path=None) -> Dict[str, object]:
    return _summarize_trade_outcomes(_normalize_and_merge_outcomes([], load_static_trade_outcomes(path)))


def build_basecalc_backtest_validation_summary(path=None) -> Dict[str, object]:
    report = load_validation_report(path) if path else load_validation_report()
    if not isinstance(report, dict):
        return {'available': False, 'rows': [], 'total_count': 0}
    rows = []
    for horizon in ('1d', '3d', '5d'):
        summary = ((report.get('horizons') or {}).get(horizon) or {}).get('summary') or {}
        sample_count = _safe_int(summary.get('total_predictions'))
        if sample_count <= 0:
            continue
        rows.append(
            {
                'horizon': horizon,
                'label': _horizon_label(horizon),
                'sample_count': sample_count,
                'sample_count_display': f'{sample_count:,}件',
                'directional_accuracy_display': _percent_display(summary.get('directional_accuracy')),
                'target_t1_hit_rate_display': _percent_display(summary.get('target_t1_hit_rate')),
                'avg_return_pct_display': _signed_percent_display(summary.get('avg_return_pct')),
            }
        )
    if not rows:
        return {
            'available': False,
            'rows': [],
            'total_count': 0,
            'generated_at': report.get('generated_at') or '',
        }
    total_count = sum(row['sample_count'] for row in rows)
    primary = rows[0]
    detail_line = (
        f"{total_count:,}件 / "
        f"{primary['label']} 方向一致 {primary['directional_accuracy_display']} / "
        f"T1到達 {primary['target_t1_hit_rate_display']}"
    )
    return {
        'available': True,
        'generated_at': report.get('generated_at') or '',
        'is_backtest': bool((report.get('filters') or {}).get('is_backtest')),
        'total_count': total_count,
        'total_count_display': f'{total_count:,}件',
        'rows': rows,
        'detail_line': detail_line,
        'one_line': f'過去データ検証 {detail_line}',
    }


def _pending_outcome_payload(snapshot, snapshot_key, horizon):
    decision = snapshot.trade_decision or {}
    selected_side = decision.get('selected_side') or 'no_trade'
    return {
        'snapshot_key': snapshot_key,
        'explanation_as_of': snapshot.as_of.isoformat() if snapshot.as_of else '',
        'horizon': horizon,
        'evaluated_at': None,
        'selected_side': selected_side,
        'decision_type': decision.get('decision_type') or '',
        'trend_or_reversal': _trend_or_reversal(decision),
        'entry_price': _number(decision.get('entry_price') or decision.get('current_price')),
        'target_1_price': None,
        'target_1_hit': None,
        'target_2_price': None,
        'target_2_hit': None,
        'stop_price': None,
        'stop_hit': None,
        'max_favorable_excursion': None,
        'max_adverse_excursion': None,
        'exit_price': None,
        'exit_reason': 'pending_market_data',
        'realized_rr': None,
        'expected_rr': None,
        'direction_hit': None,
        'is_actionable': selected_side in {'long', 'short'},
        'outcome_kind': 'pending',
        'missed_opportunity': False,
        'horizon_return_pct': None,
        'macro_regime': snapshot.macro_bias,
        'technical_regime': snapshot.basecalc_bias,
        'confidence_bucket': _confidence_bucket(decision.get('confidence_score')),
        'sample_count_at_decision': _sample_count(snapshot),
    }


def _summarize_trade_outcomes(rows):
    actionable_rows = [row for row in rows if row['is_actionable']]
    wait_rows = [row for row in rows if not row['is_actionable']]
    pending_rows = [row for row in rows if row.get('outcome_kind') == 'pending']
    wait_after_large_move_count = sum(1 for row in wait_rows if abs(row.get('horizon_return_pct') or 0) >= 1)
    risk_avoided_count = sum(1 for row in wait_rows if row.get('outcome_kind') == 'risk_avoided')
    wait_valid_count = sum(1 for row in wait_rows if row.get('outcome_kind') in {'wait_valid', 'noise_avoided', 'wait_observed'})
    evaluated_at = [row.get('evaluated_at') for row in rows if row.get('evaluated_at')]
    snapshot_as_of = [row.get('explanation_as_of') for row in rows if row.get('explanation_as_of')]
    return {
        'available': bool(rows),
        'total_count': len(rows),
        'actionable_count': len(actionable_rows),
        'wait_count': len(wait_rows),
        'pending_count': len(pending_rows),
        'wait_after_large_move_count': wait_after_large_move_count,
        'missed_opportunity_count': sum(1 for row in wait_rows if row.get('missed_opportunity')),
        'risk_avoided_count': risk_avoided_count,
        'wait_valid_count': wait_valid_count,
        'last_evaluated_at': max(evaluated_at) if evaluated_at else None,
        'latest_snapshot_as_of': max(snapshot_as_of) if snapshot_as_of else None,
        'horizon_rows': _group_rows(rows, 'horizon'),
        'side_rows': _group_rows(rows, 'selected_side'),
        'style_rows': _group_rows(rows, 'trend_or_reversal'),
        'confidence_rows': _group_rows(rows, 'confidence_bucket'),
        'wait_reason_rows': _group_rows(wait_rows, 'decision_type'),
        'wait_quality_rows': _group_rows(wait_rows, 'outcome_kind'),
        'one_line': (
            f"検証 {len(rows)}件 / 売買候補 {len(actionable_rows)}件 / "
            f"待機観測 {len(wait_rows)}件 / 機会損失候補 {sum(1 for row in wait_rows if row.get('missed_opportunity'))}件"
        ),
    }


def _outcome_metrics(decision, bars, exit_price, horizon='1d'):
    side = decision.get('selected_side') or 'no_trade'
    entry = _number(decision.get('entry_price') or decision.get('current_price'))
    target_1 = _target_price(decision, 'target_1')
    target_2 = _target_price(decision, 'target_2')
    stop = _number(decision.get('stop_price'))
    if side == 'no_trade' or entry is None:
        horizon_return_pct = _return_pct(entry, exit_price)
        return {
            'target_1_hit': None,
            'target_2_hit': None,
            'stop_hit': None,
            'mfe_pct': None,
            'mae_pct': None,
            'direction_hit': None,
            'realized_rr': None,
            'exit_reason': 'wait_observed',
            'outcome_kind': _wait_outcome_kind(decision, horizon_return_pct, horizon),
            'is_actionable': False,
            'missed_opportunity': _missed_opportunity(decision, horizon_return_pct, horizon),
            'horizon_return_pct': horizon_return_pct,
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
                'target_sample_count': 0,
                'target_1_hit_count': 0,
                'stop_sample_count': 0,
                'stop_hit_count': 0,
            },
        )
        item['sample_count'] += 1
        if row.get('is_actionable'):
            item['direction_sample_count'] += 1
            item['direction_hit_count'] += 1 if row.get('direction_hit') else 0
            item['target_sample_count'] += 1
            item['target_1_hit_count'] += 1 if row.get('target_1_hit') else 0
            item['stop_sample_count'] += 1
            item['stop_hit_count'] += 1 if row.get('stop_hit') else 0
    result = []
    for item in grouped.values():
        total = item['sample_count']
        item['direction_hit_rate'] = _rate(item['direction_hit_count'], item['direction_sample_count'])
        item['target_1_hit_rate'] = _rate(item['target_1_hit_count'], item['target_sample_count'])
        item['stop_hit_rate'] = _rate(item['stop_hit_count'], item['stop_sample_count'])
        result.append(item)
    return sorted(result, key=lambda item: item['label'])


def _rate(hit, total):
    if not total:
        return 'N/A'
    return f'{hit / total * 100:.0f}%'


def _percent_display(value):
    number = _number(value)
    if number is None:
        return 'N/A'
    return f'{number * 100:.0f}%'


def _signed_percent_display(value):
    number = _number(value)
    if number is None:
        return 'N/A'
    return f'{number:.2f}%'


def _safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _horizon_label(horizon):
    return {'1d': '1日', '3d': '3日', '5d': '5日'}.get(horizon, horizon)


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
        merged[_outcome_key(normalized)] = normalized
    return sorted(
        merged.values(),
        key=lambda row: (row.get('evaluated_at') or '', row.get('explanation_as_of') or '', row.get('horizon') or ''),
        reverse=True,
    )


def _normalize_db_outcome(row):
    selected_side = row.selected_side or 'no_trade'
    return {
        'snapshot_key': '',
        'explanation_as_of': row.explanation.as_of.isoformat() if row.explanation_id else '',
        'horizon': row.horizon,
        'evaluated_at': row.evaluated_at.isoformat() if row.evaluated_at else '',
        'selected_side': selected_side,
        'decision_type': row.decision_type or '',
        'trend_or_reversal': row.trend_or_reversal or 'no_trade',
        'direction_hit': row.direction_hit,
        'target_1_hit': row.target_1_hit if selected_side in {'long', 'short'} else None,
        'stop_hit': row.stop_hit if selected_side in {'long', 'short'} else None,
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
        'snapshot_key': row.get('snapshot_key') or '',
        'explanation_as_of': row.get('explanation_as_of') or '',
        'horizon': row.get('horizon') or '',
        'evaluated_at': row.get('evaluated_at') or '',
        'selected_side': selected_side,
        'decision_type': row.get('decision_type') or '',
        'trend_or_reversal': row.get('trend_or_reversal') or 'no_trade',
        'direction_hit': row.get('direction_hit') if selected_side in {'long', 'short'} else None,
        'target_1_hit': bool(row.get('target_1_hit')) if selected_side in {'long', 'short'} else None,
        'stop_hit': bool(row.get('stop_hit')) if selected_side in {'long', 'short'} else None,
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
    if row.get('snapshot_key'):
        return '|'.join(str(row.get(key) or '') for key in ('snapshot_key', 'horizon'))
    return '|'.join(
        str(row.get(key) or '')
        for key in ('explanation_as_of', 'horizon', 'selected_side', 'decision_type')
    )


def _outcome_kind(selected_side):
    return 'actionable_observed' if selected_side in {'long', 'short'} else 'wait_observed'


def _wait_outcome_kind(decision, horizon_return_pct, horizon='1d'):
    if horizon_return_pct is None:
        return 'wait_valid'
    if _missed_opportunity(decision, horizon_return_pct, horizon):
        direction = _decision_direction(decision)
        return 'missed_opportunity_down' if direction == 'down' else 'missed_opportunity_up'
    direction = _decision_direction(decision)
    if direction == 'up' and horizon_return_pct <= -0.7:
        return 'risk_avoided'
    if direction == 'down' and horizon_return_pct >= 0.7:
        return 'risk_avoided'
    if abs(horizon_return_pct) < 0.3:
        return 'noise_avoided'
    return 'wait_valid'


def _missed_opportunity(decision, horizon_return_pct, horizon='1d'):
    if horizon_return_pct is None:
        return False
    direction = _decision_direction(decision)
    threshold = {'1d': 0.7, '3d': 1.2, '5d': 1.5}.get(horizon, 0.7)
    if direction == 'up':
        return horizon_return_pct >= threshold
    if direction == 'down':
        return horizon_return_pct <= -threshold
    return False


def _decision_direction(decision):
    selected = decision.get('selected_side')
    if selected == 'long':
        return 'up'
    if selected == 'short':
        return 'down'
    counter = decision.get('counter_scenario') or {}
    if counter.get('direction') in {'up', 'down'}:
        return counter.get('direction')
    return ''
