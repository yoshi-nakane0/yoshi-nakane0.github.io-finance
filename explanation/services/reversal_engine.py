from typing import Any, Dict

from .contracts import BasecalcSignal, MacroSignal


WATCH_THRESHOLD = 60
ENTRY_THRESHOLD = 82


def evaluate_reversal(macro: MacroSignal, basecalc: BasecalcSignal) -> Dict[str, Any]:
    if basecalc.primary_direction == 'up' or basecalc.bias == 'bullish':
        side = 'short'
        score = int(basecalc.reversal_risk_score or _counter_score(basecalc))
        macro_support = macro.bias in {'negative', 'neutral_inflation_risk'}
        label = '上昇中の反落警戒'
    elif basecalc.primary_direction == 'down' or basecalc.bias == 'bearish':
        side = 'long'
        score = int(basecalc.rebound_improvement_score or _counter_score(basecalc))
        macro_support = macro.bias == 'positive'
        label = '下落中の反発警戒'
    else:
        return {
            'side': '',
            'status': 'none',
            'score': 0,
            'label': '逆張り条件なし',
            'entry_allowed': False,
            'reasons': [],
        }

    reasons = list((basecalc.counter_bias or {}).get('reasons') or [])
    if not reasons and score >= WATCH_THRESHOLD:
        reasons.append(label)
    status = 'entry' if score >= ENTRY_THRESHOLD and macro_support else 'watch' if score >= WATCH_THRESHOLD else 'none'
    return {
        'side': side if status != 'none' else '',
        'status': status,
        'score': score,
        'label': (basecalc.counter_bias or {}).get('label') or label,
        'entry_allowed': status == 'entry',
        'reasons': reasons[:4],
    }


def _counter_score(basecalc):
    try:
        return int((basecalc.counter_bias or {}).get('score') or 0)
    except (TypeError, ValueError):
        return 0
