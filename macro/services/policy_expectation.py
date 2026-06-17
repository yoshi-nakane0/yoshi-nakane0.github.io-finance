"""政策金利見通しと金利市場の株価向け逆風/追い風を作る。"""

from __future__ import annotations

from datetime import date
from typing import Optional

from django.utils import timezone

from ..models import Observation, PolicyExpectationSnapshot


MODEL_VERSION = 'policy_expectation_v1'


SERIES_LABELS = {
    'FEDFUNDS': '実効FF金利',
    'DFEDTARL': 'FF金利目標下限',
    'DFEDTARU': 'FF金利目標上限',
    'SOFR': 'SOFR',
    'DGS2': '米2年金利',
    'DGS10': '米10年金利',
    'T5YIE': '5年期待インフレ',
    'MOVE_INDEX': 'MOVE指数',
    'DEXJPUS': 'ドル円',
    'BOJ_POLICY_RATE': 'BOJ政策金利',
    'JPN10Y': '日本10年金利',
}


def _latest_observation(series_id: str, *, as_of: Optional[date] = None):
    qs = Observation.objects.filter(indicator__fred_series_id=series_id)
    if as_of is not None:
        qs = qs.filter(observation_date__lte=as_of)
    return qs.order_by('-observation_date').first()


def _latest_value(series_id: str, *, as_of: Optional[date] = None):
    obs = _latest_observation(series_id, as_of=as_of)
    return obs.value if obs else None


def _change_bp(series_id: str, days: int, *, as_of: Optional[date] = None):
    latest = _latest_observation(series_id, as_of=as_of)
    if latest is None:
        return None
    older = (
        Observation.objects
        .filter(
            indicator__fred_series_id=series_id,
            observation_date__lte=latest.observation_date - timezone.timedelta(days=days),
        )
        .order_by('-observation_date')
        .first()
    )
    if older is None:
        return None
    return (latest.value - older.value) * 100.0


def _data_quality(values: dict) -> float:
    required = ('FEDFUNDS', 'DFEDTARL', 'DFEDTARU', 'DGS2', 'DGS10', 'T5YIE')
    present = sum(1 for key in required if values.get(key) is not None)
    return round(present / len(required) * 100, 1)


def _policy_bias(*, us2y_5d_bp, us10y_5d_bp, breakeven_5y, move_index) -> str:
    if us2y_5d_bp is not None and us2y_5d_bp >= 5:
        return 'hawkish_headwind'
    if us10y_5d_bp is not None and us10y_5d_bp >= 8:
        return 'rate_up_headwind'
    if breakeven_5y is not None and breakeven_5y >= 2.6:
        return 'inflation_headwind'
    if move_index is not None and move_index >= 130:
        return 'rates_volatility_headwind'
    if us2y_5d_bp is not None and us2y_5d_bp <= -5:
        return 'dovish_tailwind'
    return 'neutral'


def _bias_label(policy_bias: str) -> str:
    return {
        'hawkish_headwind': '利下げ織り込み後退',
        'rate_up_headwind': '長期金利上昇が逆風',
        'inflation_headwind': '物価見通しが逆風',
        'rates_volatility_headwind': '金利変動が逆風',
        'dovish_tailwind': '金利低下が追い風',
        'neutral': '中立',
    }.get(policy_bias, policy_bias)


def build_policy_expectation_snapshot(*, as_of: Optional[date] = None) -> PolicyExpectationSnapshot:
    values = {
        series_id: _latest_value(series_id, as_of=as_of)
        for series_id in SERIES_LABELS
    }
    values['US_JP_10Y_DIFF'] = _rate_diff(values.get('DGS10'), values.get('JPN10Y'))
    us2y_1d_bp = _change_bp('DGS2', 1, as_of=as_of)
    us2y_5d_bp = _change_bp('DGS2', 5, as_of=as_of)
    us10y_5d_bp = _change_bp('DGS10', 5, as_of=as_of)
    policy_bias = _policy_bias(
        us2y_5d_bp=us2y_5d_bp,
        us10y_5d_bp=us10y_5d_bp,
        breakeven_5y=values.get('T5YIE'),
        move_index=values.get('MOVE_INDEX'),
    )
    drivers = []
    if us2y_5d_bp is not None:
        drivers.append('米2年金利')
    if us10y_5d_bp is not None:
        drivers.append('米10年金利')
    if values.get('T5YIE') is not None:
        drivers.append('期待インフレ')
    if values.get('MOVE_INDEX') is not None:
        drivers.append('MOVE指数')
    if values.get('DEXJPUS') is not None:
        drivers.append('ドル円')
    if values.get('JPN10Y') is not None:
        drivers.append('日本10年金利')
    if values.get('US_JP_10Y_DIFF') is not None:
        drivers.append('日米10年金利差')

    payload = {
        'model_version': MODEL_VERSION,
        'bias_label': _bias_label(policy_bias),
        'drivers': drivers,
        'values': values,
        'changes': {
            'us2y_1d_bp': us2y_1d_bp,
            'us2y_5d_bp': us2y_5d_bp,
            'us10y_5d_bp': us10y_5d_bp,
        },
        'summary': _summary(policy_bias, us2y_5d_bp),
    }
    snapshot = PolicyExpectationSnapshot.objects.create(
        as_of=timezone.now(),
        central_bank='FED',
        effective_rate=values.get('FEDFUNDS'),
        target_lower=values.get('DFEDTARL'),
        target_upper=values.get('DFEDTARU'),
        implied_3m_delta_bp=us2y_5d_bp,
        implied_6m_delta_bp=us10y_5d_bp,
        rate_shock_1d_bp=us2y_1d_bp,
        rate_shock_5d_bp=us2y_5d_bp,
        policy_bias=policy_bias,
        data_quality=_data_quality(values),
        payload=payload,
    )
    return snapshot


def _summary(policy_bias: str, us2y_5d_bp) -> str:
    label = _bias_label(policy_bias)
    if us2y_5d_bp is None:
        return f'{label}。米2年金利の変化は未取得です。'
    return f'{label}。米2年金利5日変化は{us2y_5d_bp:+.1f}bpです。'


def build_policy_expectation_context() -> dict:
    snapshot = PolicyExpectationSnapshot.objects.order_by('-as_of').first()
    if snapshot is None:
        return {
            'has_snapshot': False,
            'tone': 'warning',
            'title': '政策金利見通し',
            'summary': '政策金利見通しはまだ作成されていません。',
            'bias_label': '未作成',
            'rows': [],
            'alerts': [],
        }
    payload = snapshot.payload or {}
    values = payload.get('values') or {}
    changes = payload.get('changes') or {}
    rows = [
        {
            'label': SERIES_LABELS[key],
            'value': _number_display(values.get(key)),
        }
        for key in (
            'FEDFUNDS',
            'DFEDTARL',
            'DFEDTARU',
            'DGS2',
            'DGS10',
            'T5YIE',
            'MOVE_INDEX',
            'DEXJPUS',
            'BOJ_POLICY_RATE',
            'JPN10Y',
        )
        if values.get(key) is not None
    ]
    if values.get('US_JP_10Y_DIFF') is not None:
        rows.append({
            'label': '日米10年金利差',
            'value': _number_display(values.get('US_JP_10Y_DIFF')),
        })
    rows.extend([
        {'label': '米2年金利5日変化', 'value': _bp_display(changes.get('us2y_5d_bp'))},
        {'label': '米10年金利5日変化', 'value': _bp_display(changes.get('us10y_5d_bp'))},
    ])
    headwind = snapshot.policy_bias.endswith('headwind')
    return {
        'has_snapshot': True,
        'tone': 'warning' if headwind else 'good',
        'title': '政策金利見通し',
        'summary': payload.get('summary') or _bias_label(snapshot.policy_bias),
        'bias_label': payload.get('bias_label') or _bias_label(snapshot.policy_bias),
        'policy_bias': snapshot.policy_bias,
        'data_quality_display': f'{snapshot.data_quality:.0f}%',
        'rows': rows,
        'alerts': _alerts(snapshot),
    }


def _alerts(snapshot: PolicyExpectationSnapshot) -> list[str]:
    if snapshot.policy_bias == 'hawkish_headwind':
        return ['景気指標が強くても、利下げ後退・米金利上昇は株価先物に逆風です。']
    if snapshot.policy_bias == 'dovish_tailwind':
        return ['金利低下は株価先物に追い風です。']
    if snapshot.policy_bias.endswith('headwind'):
        return ['金利市場は株価先物に逆風です。']
    return ['政策金利見通しは中立です。']


def _number_display(value) -> str:
    if value is None:
        return '—'
    return f'{float(value):.2f}'


def _rate_diff(us_rate, jp_rate):
    if us_rate is None or jp_rate is None:
        return None
    return round(float(us_rate) - float(jp_rate), 4)


def _bp_display(value) -> str:
    if value is None:
        return '—'
    return f'{float(value):+.1f}bp'
