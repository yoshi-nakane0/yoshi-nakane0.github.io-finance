"""市場ストレス・急落警戒スコアの計算。

この値は将来の暴落確率ではなく、現時点の市場ストレスを固定ルールで点数化した
参考スコアとして扱う。スコア、データ品質、判定強度、検証状態は分離して返す。
"""

from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from django.utils import timezone

from ..models import Indicator, Observation


PRICE_SYMBOLS = (
    ('GSPC', 'S&P500'),
    ('DJI', 'NYダウ'),
    ('IXIC', 'NASDAQ'),
    ('N225', '日経225'),
)

PRICE_INDEX_WEIGHTS = {
    'GSPC': 0.25,
    'DJI': 0.25,
    'IXIC': 0.25,
    'N225': 0.25,
}

NORMAL_COMPONENT_SPECS = [
    {
        'series_id': 'VIXCLS', 'label': 'VIX', 'category': 'volatility_sentiment',
        'bands': [(15, 0), (20, 25), (25, 50), (30, 75), (40, 90), (float('inf'), 100)],
    },
    {
        'series_id': 'CBOE_SKEW', 'label': 'SKEW（テール警戒）', 'category': 'volatility_sentiment',
        'bands': [(120, 0), (130, 25), (140, 50), (150, 75), (160, 90), (float('inf'), 100)],
    },
    {
        'series_id': 'NAAIM_EXPOSURE', 'label': 'NAAIMエクスポージャー', 'category': 'volatility_sentiment',
        'bands': [(50, 0), (70, 25), (85, 50), (95, 75), (105, 90), (float('inf'), 100)],
    },
    {
        'series_id': 'AAII_BULLISH', 'label': 'AAII強気%', 'category': 'volatility_sentiment',
        'bands': [(30, 0), (40, 25), (50, 50), (55, 75), (60, 90), (float('inf'), 100)],
    },
    {
        'series_id': 'MOVE_INDEX', 'label': 'MOVE指数', 'category': 'volatility_sentiment',
        'bands': [(80, 0), (100, 25), (130, 50), (160, 75), (200, 90), (float('inf'), 100)],
    },
    {
        'series_id': 'VIX_VIX3M_RATIO', 'label': 'VIX/VIX3M比', 'category': 'volatility_sentiment',
        'bands': [(0.9, 0), (0.95, 25), (1.0, 50), (1.1, 75), (1.2, 90), (float('inf'), 100)],
    },
    {
        'series_id': 'BAMLH0A0HYM2', 'label': 'HYスプレッド', 'category': 'credit_liquidity',
        'bands': [(3, 0), (4, 25), (5, 50), (7, 75), (10, 90), (float('inf'), 100)],
    },
    {
        'series_id': 'BAMLC0A0CM', 'label': 'IGスプレッド', 'category': 'credit_liquidity',
        'bands': [(1.0, 0), (1.3, 25), (1.7, 50), (2.2, 75), (3.0, 90), (float('inf'), 100)],
    },
    {
        'series_id': 'NFCI', 'label': '金融状況', 'category': 'credit_liquidity',
        'bands': [(-0.5, 0), (0.0, 25), (0.3, 50), (0.7, 75), (1.0, 90), (float('inf'), 100)],
    },
    {
        'series_id': 'STLFSI4', 'label': '金融ストレス', 'category': 'credit_liquidity',
        'bands': [(-1.0, 0), (0.0, 25), (0.5, 50), (1.5, 75), (2.5, 90), (float('inf'), 100)],
    },
    {
        'series_id': 'T10Y2Y', 'label': '2-10年スプレッド', 'category': 'macro_cycle',
        'bands': [(-0.5, 100), (0.0, 75), (0.5, 50), (1.5, 25), (float('inf'), 0)],
    },
    {
        'series_id': 'T10Y3M', 'label': '3M-10年スプレッド', 'category': 'macro_cycle',
        'bands': [(-0.5, 100), (0.0, 75), (0.5, 50), (1.5, 25), (float('inf'), 0)],
    },
]

PRICE_ACTION_SPECS = [
    {
        'series_id': f'PA_{sym}_DD200', 'label': f'{label} 200日線',
        'category': 'price_action', 'symbol': sym, 'metric': 'DD200',
        'bands': [(-15, 100), (-10, 85), (-5, 65), (0, 40), (5, 20), (float('inf'), 0)],
    }
    for sym, label in PRICE_SYMBOLS
] + [
    {
        'series_id': f'PA_{sym}_DD52W', 'label': f'{label} 52週高値',
        'category': 'price_action', 'symbol': sym, 'metric': 'DD52W',
        'bands': [(-25, 100), (-15, 85), (-10, 65), (-5, 40), (-1, 15), (float('inf'), 0)],
    }
    for sym, label in PRICE_SYMBOLS
] + [
    {
        'series_id': f'PA_{sym}_MOM20', 'label': f'{label} 20日',
        'category': 'price_action', 'symbol': sym, 'metric': 'MOM20',
        'bands': [(-15, 100), (-10, 85), (-5, 65), (-2, 40), (0, 20), (float('inf'), 0)],
    }
    for sym, label in PRICE_SYMBOLS
]

COMPONENT_SPECS = NORMAL_COMPONENT_SPECS + PRICE_ACTION_SPECS

CATEGORY_WEIGHTS = {
    'volatility_sentiment': 0.30,
    'credit_liquidity': 0.35,
    'macro_cycle': 0.15,
    'price_action': 0.20,
}

CATEGORY_LABELS = {
    'volatility_sentiment': 'ボラ・需給',
    'credit_liquidity': '信用・流動性',
    'macro_cycle': '景気・金利',
    'price_action': '価格アクション',
}

FRESHNESS_LIMIT_DAYS = {
    Indicator.Frequency.DAILY: 7,
    Indicator.Frequency.WEEKLY: 21,
    Indicator.Frequency.MONTHLY: 75,
    Indicator.Frequency.QUARTERLY: 150,
}

LEVEL_BANDS = [
    (25, 'calm', '平常'),
    (50, 'caution', '注意'),
    (70, 'alert', '警戒'),
    (85, 'high', '高警戒'),
    (10 ** 9, 'danger', '危険水準'),
]


def _band_score(value: float, bands) -> int:
    for upper, score in bands:
        if value <= upper:
            return score
    return bands[-1][1]


def _latest_observation_meta(series_id: str, as_of: Optional[date] = None) -> Optional[Dict]:
    qs = Observation.objects.filter(indicator__fred_series_id=series_id)
    if as_of is not None:
        qs = qs.filter(observation_date__lte=as_of)
    obs = qs.select_related('indicator').order_by('-observation_date').first()
    if obs is None:
        return None
    return {
        'value': obs.value,
        'observation_date': obs.observation_date,
        'frequency': obs.indicator.frequency,
    }


def _latest_value(series_id: str) -> Optional[float]:
    meta = _latest_observation_meta(series_id)
    return meta['value'] if meta else None


def _parse_observation_date(value) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _coerce_lookup_result(raw) -> Dict:
    if raw is None:
        return {'value': None}
    if isinstance(raw, dict):
        return {
            'value': raw.get('value'),
            'observation_date': _parse_observation_date(
                raw.get('observation_date') or raw.get('date')
            ),
            'frequency': raw.get('frequency'),
        }
    if isinstance(raw, tuple):
        value = raw[0] if len(raw) >= 1 else None
        obs_date = raw[1] if len(raw) >= 2 else None
        frequency = raw[2] if len(raw) >= 3 else None
        return {
            'value': value,
            'observation_date': _parse_observation_date(obs_date),
            'frequency': frequency,
        }
    if hasattr(raw, 'value'):
        return {
            'value': raw.value,
            'observation_date': _parse_observation_date(
                getattr(raw, 'observation_date', None)
            ),
            'frequency': getattr(getattr(raw, 'indicator', None), 'frequency', None),
        }
    return {'value': raw}


def _component_weight(spec: Dict) -> float:
    category = spec['category']
    category_weight = CATEGORY_WEIGHTS[category]
    if category != 'price_action':
        count = len([s for s in NORMAL_COMPONENT_SPECS if s['category'] == category])
        return category_weight / count
    symbol_weight = PRICE_INDEX_WEIGHTS[spec['symbol']]
    return category_weight * symbol_weight / 3


def _component_result(
    spec: Dict,
    raw,
    indicator: Optional[Indicator],
    as_of: date,
) -> Dict:
    meta = _coerce_lookup_result(raw)
    value = meta.get('value')
    frequency = meta.get('frequency') or (indicator.frequency if indicator else None)
    obs_date = meta.get('observation_date')
    age_days = max((as_of - obs_date).days, 0) if obs_date else None
    limit_days = FRESHNESS_LIMIT_DAYS.get(frequency)
    is_missing = value is None
    is_stale = (
        not is_missing
        and age_days is not None
        and limit_days is not None
        and age_days > limit_days
    )
    score = None if is_missing else _band_score(value, spec['bands'])

    warning = None
    if is_missing:
        warning = 'データなし'
    elif is_stale:
        warning = f'観測日が古い（目安{limit_days}日以内）'

    return {
        'series_id': spec['series_id'],
        'label': spec['label'],
        'category': spec['category'],
        'category_label': CATEGORY_LABELS.get(spec['category'], spec['category']),
        'symbol': spec.get('symbol'),
        'metric': spec.get('metric'),
        'value': value,
        'observation_date': obs_date.isoformat() if obs_date else None,
        'age_days': age_days,
        'is_missing': is_missing,
        'is_stale': is_stale,
        'is_fresh': not is_missing and not is_stale,
        'score': score,
        'weight': _component_weight(spec),
        'warning': warning,
    }


def _average(scores: List[float]) -> Optional[float]:
    if not scores:
        return None
    return sum(scores) / len(scores)


def _price_action_score(components: List[Dict]) -> Optional[float]:
    by_symbol: Dict[str, List[int]] = {}
    for component in components:
        if component['category'] != 'price_action' or not component['is_fresh']:
            continue
        by_symbol.setdefault(component['symbol'], []).append(component['score'])
    weighted_sum = 0.0
    available_weight = 0.0
    for symbol, scores in by_symbol.items():
        index_score = _average(scores)
        if index_score is None:
            continue
        weight = PRICE_INDEX_WEIGHTS[symbol]
        weighted_sum += index_score * weight
        available_weight += weight
    if available_weight <= 0:
        return None
    return weighted_sum / available_weight


def _category_scores(components: List[Dict]) -> Dict[str, Optional[float]]:
    scores: Dict[str, Optional[float]] = {}
    for category in CATEGORY_WEIGHTS:
        if category == 'price_action':
            scores[category] = _price_action_score(components)
            continue
        fresh_scores = [
            c['score'] for c in components
            if c['category'] == category and c['is_fresh'] and c['score'] is not None
        ]
        scores[category] = _average(fresh_scores)
    return scores


def _category_summary(
    components: List[Dict],
    cat_scores: Dict[str, Optional[float]],
) -> List[Dict]:
    out = []
    for category, weight in sorted(CATEGORY_WEIGHTS.items(), key=lambda x: -x[1]):
        items = [c for c in components if c['category'] == category]
        expected_weight = sum(c['weight'] for c in items)
        fresh_weight = sum(c['weight'] for c in items if c['is_fresh'])
        coverage = (
            round(fresh_weight / expected_weight * 100)
            if expected_weight > 0 else 0
        )
        score = cat_scores.get(category)
        out.append({
            'category': category,
            'category_label': CATEGORY_LABELS.get(category, category),
            'avg_score': round(score) if score is not None else None,
            'weight_pct': round(weight * 100),
            'coverage_pct': coverage,
            'count': len([c for c in items if not c['is_missing']]),
            'fresh_count': len([c for c in items if c['is_fresh']]),
            'expected_count': len(items),
            'missing_count': len([c for c in items if c['is_missing']]),
            'stale_count': len([c for c in items if c['is_stale']]),
        })
    return out


def _rule_agreement_pct(components: List[Dict], total_score: Optional[int]) -> int:
    fresh = [
        c for c in components
        if c['is_fresh'] and c['score'] is not None and c['weight'] > 0
    ]
    if not fresh or total_score is None:
        return 0
    stress_mode = total_score >= 50
    aligned_weight = 0.0
    total_weight = 0.0
    for component in fresh:
        total_weight += component['weight']
        if stress_mode and component['score'] >= 50:
            aligned_weight += component['weight']
        elif not stress_mode and component['score'] < 50:
            aligned_weight += component['weight']
    if total_weight <= 0:
        return 0
    return round(aligned_weight / total_weight * 100)


def _forward_risk_score(cat_scores: Dict[str, Optional[float]]) -> Optional[int]:
    categories = ('volatility_sentiment', 'credit_liquidity', 'macro_cycle')
    available = [
        (cat_scores[cat], CATEGORY_WEIGHTS[cat])
        for cat in categories
        if cat_scores.get(cat) is not None
    ]
    if not available:
        return None
    weight_sum = sum(weight for _, weight in available)
    return round(sum(score * weight for score, weight in available) / weight_sum)


def _classify(
    score: int,
    data_quality_pct: int = 100,
    low_coverage_categories: Optional[List[str]] = None,
    supporting_stress: bool = True,
) -> Tuple[str, str]:
    low_coverage_categories = low_coverage_categories or []
    if data_quality_pct < 70 or low_coverage_categories:
        return 'provisional', '参考表示'
    if score >= 85 and not (data_quality_pct >= 80 and supporting_stress):
        return 'high', '高警戒'
    if score >= 70 and data_quality_pct < 75:
        return 'provisional', '参考表示'
    for upper, level, label in LEVEL_BANDS:
        if score < upper:
            return level, label
    return LEVEL_BANDS[-1][1], LEVEL_BANDS[-1][2]


def _quality_warnings(
    components: List[Dict],
    category_summary: List[Dict],
    data_quality_pct: int,
) -> List[str]:
    warnings = []
    missing_components = [c for c in components if c['is_missing']]
    missing_count = len(missing_components)
    stale = [c for c in components if c['is_stale']]
    if missing_count:
        labels = '、'.join(c['label'] for c in missing_components[:4])
        suffix = ' など' if missing_count > 4 else ''
        warnings.append(
            f'未取得の指標: {labels}{suffix}。'
            f'この{missing_count}件は今回の点数に含めていません。'
        )
    if stale:
        labels = '、'.join(c['label'] for c in stale[:3])
        warnings.append(f'観測日が古い指標があります: {labels}')
    low_categories = [
        c['category_label'] for c in category_summary
        if c['coverage_pct'] < 50
    ]
    if low_categories:
        warnings.append('データ不足のカテゴリがあります: ' + '、'.join(low_categories))
    if data_quality_pct < 70:
        warnings.append('データ品質が低いため、警戒レベルは参考表示です。')
    return warnings


def compute_crash_alert(value_lookup=None, as_of: Optional[date] = None) -> Dict:
    """市場ストレス・急落警戒スコアを計算する。"""
    target_date = as_of or timezone.localdate()
    lookup = value_lookup or (lambda series_id: _latest_observation_meta(series_id, as_of=as_of))
    series_ids = [spec['series_id'] for spec in COMPONENT_SPECS]
    indicators = {
        i.fred_series_id: i
        for i in Indicator.objects.filter(fred_series_id__in=series_ids)
    }

    components = [
        _component_result(
            spec,
            lookup(spec['series_id']),
            indicators.get(spec['series_id']),
            target_date,
        )
        for spec in COMPONENT_SPECS
    ]

    if not any(c['score'] is not None for c in components):
        return {
            'total_score': None,
            'market_stress_score': None,
            'forward_risk_score': None,
            'level': 'unknown',
            'level_label': '判定不能',
            'components': components,
            'category_summary': [],
            'data_quality_pct': 0,
            'rule_agreement_pct': 0,
            'validation_confidence_pct': None,
            'validation_status': '検証未実施',
            'is_provisional': True,
            'quality_warnings': ['判定に使えるデータがありません。'],
        }

    cat_scores = _category_scores(components)
    total = sum(
        score * CATEGORY_WEIGHTS[category]
        for category, score in cat_scores.items()
        if score is not None
    )
    total_int = round(total)
    category_summary = _category_summary(components, cat_scores)

    expected_weight = sum(c['weight'] for c in components)
    fresh_weight = sum(c['weight'] for c in components if c['is_fresh'])
    data_quality_pct = (
        round(fresh_weight / expected_weight * 100)
        if expected_weight > 0 else 0
    )
    low_coverage_categories = [
        c['category'] for c in category_summary
        if c['coverage_pct'] < 50
    ]
    supporting_stress = any(
        (cat_scores.get(cat) or 0) >= 70
        for cat in ('volatility_sentiment', 'credit_liquidity')
    )
    level, level_label = _classify(
        total_int,
        data_quality_pct=data_quality_pct,
        low_coverage_categories=low_coverage_categories,
        supporting_stress=supporting_stress,
    )

    warnings = _quality_warnings(components, category_summary, data_quality_pct)

    return {
        'total_score': total_int,
        'market_stress_score': total_int,
        'forward_risk_score': _forward_risk_score(cat_scores),
        'level': level,
        'level_label': level_label,
        'components': components,
        'category_summary': category_summary,
        'data_quality_pct': data_quality_pct,
        'rule_agreement_pct': _rule_agreement_pct(components, total_int),
        'validation_confidence_pct': None,
        'validation_status': '検証未実施',
        'is_provisional': level == 'provisional',
        'quality_warnings': warnings,
    }
