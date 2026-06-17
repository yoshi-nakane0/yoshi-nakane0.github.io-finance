"""政策スタンスの要約。"""

from __future__ import annotations

from .policy_expectation import build_policy_expectation_context


def summarize_policy_path() -> dict:
    context = build_policy_expectation_context()
    return {
        'label': context.get('label') or context.get('policy_bias') or 'neutral',
        'summary': context.get('summary') or '',
        'data_quality_display': context.get('data_quality_display') or '—',
    }
