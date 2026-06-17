"""仮定を入れたマクロシナリオ分析。"""

from copy import deepcopy
from typing import Dict, Optional

from django.utils import timezone

from ..models import Indicator, Observation, RegimeSnapshot
from .crash_alert import compute_crash_alert
from .regime import (
    build_regime_assessment_from_metrics,
    collect_key_metrics,
)
from .world_state import build_world_state_assessment_from_metrics

CUSTOM_SCENARIO_FIELDS = [
    {
        'name': 'scenario_rate_bp',
        'label': '米10年金利',
        'suffix': 'bp',
        'metric_overrides': {'yield_curve_2y10y': 0.01, 'yield_curve_3m10y': 0.01},
        'crash_overrides': {'T10Y2Y': 0.01, 'T10Y3M': 0.01},
    },
    {
        'name': 'scenario_vix',
        'label': 'VIX',
        'suffix': '',
        'metric_set': {'vix': 1.0},
        'crash_set': {'VIXCLS': 1.0},
    },
    {
        'name': 'scenario_hy_spread',
        'label': 'HYスプレッド',
        'suffix': 'pt',
        'metric_overrides': {'hy_spread': 1.0},
        'crash_overrides': {'BAMLH0A0HYM2': 1.0},
    },
    {
        'name': 'scenario_core_pce',
        'label': 'Core PCE前年比',
        'suffix': 'pt',
        'metric_overrides': {'core_pce_yoy': 1.0},
    },
    {
        'name': 'scenario_unrate_6m',
        'label': '失業率6カ月変化',
        'suffix': 'pt',
        'metric_overrides': {'unrate_6m_change': 1.0},
    },
    {
        'name': 'scenario_payems',
        'label': '雇用者数前月差',
        'suffix': '千人',
        'metric_set': {'payems_mom': 1.0},
    },
]


SCENARIOS = [
    {
        'key': 'rates_up',
        'title': '米長期金利 +50bp',
        'assumption': '10年金利が上がり、長短金利差が50bp上方向へ動く想定',
        'metric_overrides': {
            'yield_curve_2y10y': 0.50,
            'yield_curve_3m10y': 0.50,
            'breakeven_5y': 0.15,
        },
        'crash_overrides': {
            'T10Y2Y': 0.50,
            'T10Y3M': 0.50,
        },
    },
    {
        'key': 'stress_up',
        'title': 'VIX 25超・信用悪化',
        'assumption': 'VIXを最低25、HYスプレッドを+1.0ptとして市場ストレスを見る',
        'metric_minimums': {
            'vix': 25.0,
        },
        'metric_overrides': {
            'hy_spread': 1.0,
        },
        'crash_minimums': {
            'VIXCLS': 25.0,
        },
        'crash_overrides': {
            'BAMLH0A0HYM2': 1.0,
        },
    },
    {
        'key': 'inflation_reaccel',
        'title': 'Core PCE再加速',
        'assumption': '物価系指標が+0.3〜0.4pt上振れする想定',
        'metric_overrides': {
            'core_pce_yoy': 0.40,
            'pce_yoy': 0.30,
            'cpi_yoy': 0.30,
            'core_cpi_yoy': 0.30,
            'breakeven_5y': 0.25,
        },
        'crash_overrides': {},
    },
    {
        'key': 'labor_down',
        'title': '雇用が急速に悪化',
        'assumption': '失業率6カ月変化+0.5pt、雇用者数前月差-150千人の想定',
        'metric_overrides': {
            'unrate_6m_change': 0.50,
            'payems_mom': -150.0,
            'jolts_yoy': -10.0,
        },
        'crash_overrides': {},
    },
    {
        'key': 'rate_cut_delay',
        'title': '利下げ後退・米金利上昇',
        'assumption': '景気指標は強いが、利下げ織り込み後退と米金利上昇が株価先物に逆風となる想定',
        'metric_overrides': {
            'yield_curve_2y10y': -0.35,
            'yield_curve_3m10y': -0.25,
            'breakeven_5y': 0.20,
            'core_pce_yoy': 0.20,
        },
        'crash_overrides': {
            'T10Y2Y': -0.35,
            'T10Y3M': -0.25,
        },
    },
    {
        'key': 'rates_down_risk_on',
        'title': '金利低下・リスクオン',
        'assumption': '米金利低下、VIX低下、信用環境改善で株価先物に追い風となる想定',
        'metric_overrides': {
            'yield_curve_2y10y': 0.25,
            'yield_curve_3m10y': 0.25,
            'hy_spread': -0.40,
        },
        'metric_maximums': {
            'vix': 15.0,
        },
        'crash_overrides': {
            'T10Y2Y': 0.25,
            'T10Y3M': 0.25,
            'BAMLH0A0HYM2': -0.40,
        },
        'crash_maximums': {
            'VIXCLS': 15.0,
        },
    },
]


def _label_display(value: str) -> str:
    try:
        return RegimeSnapshot.Label(value).label
    except ValueError:
        return value


def _probability_display(value: Optional[float]) -> str:
    if value is None:
        return '—'
    return f'{value * 100:.0f}%'


def _regime_view_display(label: str) -> str:
    if label in ('—', ''):
        return '景気の見方: 判定保留'
    if label == RegimeSnapshot.Label.UNKNOWN.label:
        return '景気の見方: 判定保留'
    return f'景気の見方: {label}寄り'


def _regime_fit_display(value: Optional[float]) -> str:
    return f'近さの目安 {_probability_display(value)}'


def _signed_points_display(value: Optional[float]) -> str:
    if value is None:
        return '—'
    sign = '+' if value > 0 else ''
    return f'{sign}{value:.0f}pt'


def _top_regime_probability(probabilities: Dict[str, float]) -> tuple[str, float]:
    if not probabilities:
        return '—', 0.0
    key, value = max(probabilities.items(), key=lambda item: item[1])
    return _label_display(key), value


def _apply_metric_scenario(base_metrics: Dict, scenario: Dict) -> Dict:
    metrics = deepcopy(base_metrics)
    for key, delta in scenario.get('metric_overrides', {}).items():
        current = metrics.get(key)
        metrics[key] = delta if current is None else current + delta
    for key, minimum in scenario.get('metric_minimums', {}).items():
        current = metrics.get(key)
        metrics[key] = minimum if current is None else max(current, minimum)
    for key, maximum in scenario.get('metric_maximums', {}).items():
        current = metrics.get(key)
        metrics[key] = maximum if current is None else min(current, maximum)
    return metrics


def scenario_overrides_from_query(query) -> Optional[Dict]:
    metric_overrides = {}
    metric_sets = {}
    crash_overrides = {}
    crash_sets = {}
    active = []
    for field in CUSTOM_SCENARIO_FIELDS:
        raw_value = query.get(field['name']) if hasattr(query, 'get') else None
        if raw_value in (None, ''):
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        active.append({
            'label': field['label'],
            'value': value,
            'suffix': field['suffix'],
        })
        for key, multiplier in field.get('metric_overrides', {}).items():
            metric_overrides[key] = metric_overrides.get(key, 0.0) + value * multiplier
        for key, multiplier in field.get('metric_set', {}).items():
            metric_sets[key] = value * multiplier
        for key, multiplier in field.get('crash_overrides', {}).items():
            crash_overrides[key] = crash_overrides.get(key, 0.0) + value * multiplier
        for key, multiplier in field.get('crash_set', {}).items():
            crash_sets[key] = value * multiplier

    if not active:
        return None
    assumption = ' / '.join(
        f"{item['label']} {item['value']:+g}{item['suffix']}"
        for item in active
    )
    return {
        'key': 'custom',
        'title': 'カスタム',
        'assumption': assumption,
        'metric_overrides': metric_overrides,
        'metric_sets': metric_sets,
        'crash_overrides': crash_overrides,
        'crash_sets': crash_sets,
        'is_custom': True,
        'active_inputs': active,
        'input_values': {
            field['name']: query.get(field['name'], '')
            for field in CUSTOM_SCENARIO_FIELDS
        },
    }


def _latest_meta(series_id: str):
    obs = (
        Observation.objects
        .filter(indicator__fred_series_id=series_id)
        .select_related('indicator')
        .order_by('-observation_date')
        .first()
    )
    if obs is None:
        indicator = Indicator.objects.filter(fred_series_id=series_id).first()
        return {
            'value': None,
            'observation_date': timezone.localdate(),
            'frequency': indicator.frequency if indicator else None,
        }
    return {
        'value': obs.value,
        'observation_date': obs.observation_date,
        'frequency': obs.indicator.frequency,
    }


def _scenario_value_lookup(scenario: Dict):
    overrides = scenario.get('crash_overrides', {})
    sets = scenario.get('crash_sets', {})
    minimums = scenario.get('crash_minimums', {})

    def lookup(series_id: str):
        meta = _latest_meta(series_id)
        value = meta.get('value')
        if series_id in sets:
            value = sets[series_id]
        if series_id in overrides:
            value = overrides[series_id] if value is None else value + overrides[series_id]
        if series_id in minimums:
            value = minimums[series_id] if value is None else max(value, minimums[series_id])
        if series_id in scenario.get('crash_maximums', {}):
            maximum = scenario['crash_maximums'][series_id]
            value = maximum if value is None else min(value, maximum)
        return {**meta, 'value': value}

    return lookup


def _scenario_crash_alert(scenario: Dict) -> Dict:
    return compute_crash_alert(value_lookup=_scenario_value_lookup(scenario))


def _market_stress_score_from_alert(alert: Dict) -> Optional[int]:
    return alert.get('market_stress_score')


def _world_state_delta_rows(base_world: Dict, scenario_world: Dict) -> list[Dict]:
    labels = {
        'growth_score': '成長',
        'inflation_score': '物価',
        'policy_pressure_score': '政策圧力',
        'market_stress_score': '市場ストレス',
        'credit_score': '信用',
        'risk_appetite_score': 'リスク選好',
    }
    rows = []
    for key, label in labels.items():
        base = base_world.get(key)
        scenario = scenario_world.get(key)
        if base is None or scenario is None:
            continue
        delta = scenario - base
        if abs(delta) < 0.5:
            continue
        rows.append({
            'key': key,
            'label': label,
            'delta': round(delta, 1),
            'delta_display': f'{delta:+.1f}',
            'is_negative': delta < 0,
        })
    return rows


def _score_value(payload: Dict, key: str, default: float = 50.0) -> float:
    value = payload.get(key)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_most_likely_scenario(metrics: Dict, base_world: Dict, base_assessment: Dict) -> Dict:
    label, probability = _top_regime_probability(
        base_assessment.get('regime_probabilities', {})
    )
    return {
        'key': 'most_likely',
        'title': '最も近い基本シナリオ',
        'assumption': (
            f'現在の状態が大きく変わらない場合。景気の見方は'
            f'{_regime_view_display(label)}、近さは{_probability_display(probability)}です。'
        ),
        'metric_overrides': {},
        'crash_overrides': {},
    }


def build_top_risk_scenario(metrics: Dict, base_world: Dict) -> Dict:
    risk_candidates = {
        'market_stress_score': _score_value(base_world, 'market_stress_score'),
        'credit_score': 100 - _score_value(base_world, 'credit_score'),
        'policy_pressure_score': _score_value(base_world, 'policy_pressure_score'),
        'inflation_score': _score_value(base_world, 'inflation_score'),
        'risk_appetite_score': 100 - _score_value(base_world, 'risk_appetite_score'),
    }
    top_key = max(risk_candidates.items(), key=lambda item: item[1])[0]
    labels = {
        'market_stress_score': '市場ストレス',
        'credit_score': '信用悪化',
        'policy_pressure_score': '政策圧力',
        'inflation_score': '物価圧力',
        'risk_appetite_score': 'リスク選好低下',
    }
    return {
        'key': 'top_risk',
        'title': '悪化シナリオ',
        'assumption': f'{labels[top_key]}が一段悪化する想定',
        'metric_minimums': {'vix': 24.0},
        'metric_overrides': {
            'hy_spread': 0.75,
            'core_pce_yoy': 0.20,
            'unrate_6m_change': 0.25,
        },
        'crash_minimums': {'VIXCLS': 24.0},
        'crash_overrides': {'BAMLH0A0HYM2': 0.75},
    }


def build_improvement_scenario(metrics: Dict, base_world: Dict) -> Dict:
    return {
        'key': 'improvement',
        'title': '改善シナリオ',
        'assumption': 'VIX低下、信用スプレッド縮小、金利圧力低下、リスク選好改善を想定',
        'metric_overrides': {
            'hy_spread': -0.50,
            'core_pce_yoy': -0.20,
            'yield_curve_2y10y': 0.20,
            'yield_curve_3m10y': 0.20,
        },
        'metric_maximums': {'vix': 16.0},
        'crash_overrides': {'BAMLH0A0HYM2': -0.50},
        'crash_maximums': {'VIXCLS': 16.0},
    }


def build_market_shock_scenario(metrics: Dict, base_world: Dict) -> Dict:
    return {
        'key': 'market_shock',
        'title': 'ショックシナリオ',
        'assumption': '外部ショックでVIX急騰と信用悪化が同時に起きる想定',
        'metric_minimums': {'vix': 35.0},
        'metric_overrides': {
            'hy_spread': 1.50,
            'unrate_6m_change': 0.35,
        },
        'crash_minimums': {'VIXCLS': 35.0},
        'crash_overrides': {'BAMLH0A0HYM2': 1.50},
    }


def build_auto_scenarios() -> Dict:
    metrics = collect_key_metrics()
    base_alert = compute_crash_alert()
    base_world = build_world_state_assessment_from_metrics(
        metrics,
        crash_alert_payload=base_alert,
    )
    base_assessment = build_regime_assessment_from_metrics(metrics)
    scenarios = [
        build_most_likely_scenario(metrics, base_world, base_assessment),
        build_top_risk_scenario(metrics, base_world),
        build_improvement_scenario(metrics, base_world),
        build_market_shock_scenario(metrics, base_world),
    ]
    scenarios.extend(
        scenario
        for scenario in SCENARIOS
        if scenario.get('key') in ('rate_cut_delay', 'rates_down_risk_on')
    )
    return build_scenario_analysis(scenario_list=scenarios)


def build_scenario_analysis(
    custom_scenario: Optional[Dict] = None,
    scenario_list: Optional[list[Dict]] = None,
) -> Dict:
    base_metrics = collect_key_metrics()
    base_assessment = build_regime_assessment_from_metrics(base_metrics)
    base_label, base_probability = _top_regime_probability(
        base_assessment.get('regime_probabilities', {})
    )
    base_alert = compute_crash_alert()
    base_market_stress = base_alert.get('market_stress_score')
    base_world = build_world_state_assessment_from_metrics(
        base_metrics,
        crash_alert_payload=base_alert,
    )

    scenarios = []
    scenario_list = list(scenario_list or SCENARIOS)
    if custom_scenario:
        scenario_list.insert(0, custom_scenario)

    for scenario in scenario_list:
        metrics = _apply_metric_scenario(base_metrics, scenario)
        for key, value in scenario.get('metric_sets', {}).items():
            metrics[key] = value
        assessment = build_regime_assessment_from_metrics(metrics)
        label, probability = _top_regime_probability(
            assessment.get('regime_probabilities', {})
        )
        scenario_alert = _scenario_crash_alert(scenario)
        market_stress = _market_stress_score_from_alert(scenario_alert)
        scenario_world = build_world_state_assessment_from_metrics(
            metrics,
            crash_alert_payload=scenario_alert,
        )
        world_state_delta_rows = _world_state_delta_rows(base_world, scenario_world)
        stress_delta = (
            market_stress - base_market_stress
            if market_stress is not None and base_market_stress is not None
            else None
        )
        risks = assessment.get('risk_probabilities', {})
        scenarios.append({
            'key': scenario['key'],
            'title': scenario['title'],
            'assumption': scenario['assumption'],
            'regime_label': label,
            'regime_probability_display': _probability_display(probability),
            'regime_view_display': _regime_view_display(label),
            'regime_fit_display': _regime_fit_display(probability),
            'recession_probability_display': _probability_display(risks.get('recession')),
            'inflation_probability_display': _probability_display(
                risks.get('inflation_reacceleration')
            ),
            'financial_stress_probability_display': _probability_display(
                risks.get('financial_stress')
            ),
            'market_stress_score': market_stress,
            'market_stress_delta_display': (
                _signed_points_display(stress_delta)
            ),
            'world_state_delta': {
                row['key']: row['delta']
                for row in world_state_delta_rows
            },
            'world_state_delta_rows': world_state_delta_rows,
            'is_custom': scenario.get('is_custom', False),
        })

    return {
        'model_version': base_assessment.get('model_version'),
        'base_regime_label': base_label,
        'base_regime_probability_display': _probability_display(base_probability),
        'base_regime_view_display': _regime_view_display(base_label),
        'base_regime_fit_display': _regime_fit_display(base_probability),
        'base_market_stress_score': base_market_stress,
        'scenarios': scenarios,
        'custom_fields': [
            {
                **field,
                'value': (
                    custom_scenario.get('input_values', {}).get(field['name'], '')
                    if custom_scenario else ''
                ),
            }
            for field in CUSTOM_SCENARIO_FIELDS
        ],
        'has_custom': bool(custom_scenario),
    }
