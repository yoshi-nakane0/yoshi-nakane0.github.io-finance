"""マクロレジーム判定。

表示上の数値は統計的な予測確率ではなく、説明可能なルール一致度として扱う。
成長、雇用、インフレ、金融ストレス、データ品質を分けて計算し、根拠も保存する。
"""

import logging
import math
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from django.db import transaction
from django.utils import timezone

from ..models import Indicator, Observation, RegimeSnapshot

logger = logging.getLogger(__name__)

MODEL_VERSION = 'regime_v2_score'
PROBABILITY_MODEL_VERSION = 'regime_probability_v2_validated'

# 主要シグナル指標
GROWTH_KEY_SERIES = 'INDPRO'
EMPLOYMENT_KEY_SERIES = 'UNRATE'
GDP_KEY_SERIES = 'GDPC1'
INFLATION_KEY_SERIES = 'PCEPILFE'

QUALITY_SERIES = (
    'INDPRO',
    'GDPC1',
    'UNRATE',
    'PAYEMS',
    'PCEPILFE',
    'CPIAUCSL',
    'T10Y2Y',
    'BAMLH0A0HYM2',
    'VIXCLS',
)

FRESHNESS_LIMIT_DAYS = {
    Indicator.Frequency.DAILY: 10,
    Indicator.Frequency.WEEKLY: 21,
    Indicator.Frequency.MONTHLY: 45,
    Indicator.Frequency.QUARTERLY: 120,
}


def _latest_observation(
    series_id: str,
    as_of: Optional[date] = None,
) -> Optional[Observation]:
    indicator = Indicator.objects.filter(fred_series_id=series_id).first()
    if not indicator:
        return None
    qs = Observation.objects.filter(indicator=indicator)
    if as_of is not None:
        qs = qs.filter(observation_date__lte=as_of)
    return qs.order_by('-observation_date').first()


def _observation_at_or_before(series_id: str, target_date) -> Optional[Observation]:
    indicator = Indicator.objects.filter(fred_series_id=series_id).first()
    if not indicator:
        return None
    return (
        Observation.objects
        .filter(indicator=indicator, observation_date__lte=target_date)
        .order_by('-observation_date')
        .first()
    )


def _months_ago(today, months: int):
    year = today.year
    month = today.month - months
    while month <= 0:
        month += 12
        year -= 1
    day = min(today.day, 28)
    return today.replace(year=year, month=month, day=day)


def _metric_pct_change(latest: Optional[Observation], months: int) -> Optional[float]:
    if latest is None or latest.value in (None, 0):
        return None
    past = _observation_at_or_before(
        latest.indicator.fred_series_id,
        _months_ago(latest.observation_date, months),
    )
    if past is None or past.value in (None, 0):
        return None
    return (latest.value - past.value) / abs(past.value) * 100.0


def _metric_abs_change(latest: Optional[Observation], months: int) -> Optional[float]:
    if latest is None or latest.value is None:
        return None
    past = _observation_at_or_before(
        latest.indicator.fred_series_id,
        _months_ago(latest.observation_date, months),
    )
    if past is None or past.value is None:
        return None
    return latest.value - past.value


def collect_key_metrics(as_of: Optional[date] = None) -> Dict[str, Optional[float]]:
    """レジーム判定に使う主要メトリクスを収集する。"""
    metrics: Dict[str, Optional[float]] = {}

    indpro_latest = _latest_observation(GROWTH_KEY_SERIES, as_of=as_of)
    if indpro_latest:
        metrics['indpro_yoy'] = indpro_latest.yoy_change
        metrics['indpro_value'] = indpro_latest.value
        metrics['indpro_3m_change_pct'] = _metric_pct_change(indpro_latest, 3)

    unrate_latest = _latest_observation(EMPLOYMENT_KEY_SERIES, as_of=as_of)
    if unrate_latest:
        metrics['unrate_value'] = unrate_latest.value
        metrics['unrate_6m_change'] = _metric_abs_change(unrate_latest, 6)

    gdp_latest = _latest_observation(GDP_KEY_SERIES, as_of=as_of)
    if gdp_latest:
        metrics['gdp_yoy'] = gdp_latest.yoy_change

    core_pce_latest = _latest_observation(INFLATION_KEY_SERIES, as_of=as_of)
    if core_pce_latest:
        metrics['core_pce_yoy'] = core_pce_latest.yoy_change
        core_pce_3m_ago = _observation_at_or_before(
            INFLATION_KEY_SERIES,
            _months_ago(core_pce_latest.observation_date, 3),
        )
        if core_pce_3m_ago:
            metrics['core_pce_yoy_3m_ago'] = core_pce_3m_ago.yoy_change

    payems_latest = _latest_observation('PAYEMS', as_of=as_of)
    if payems_latest and payems_latest.prev_value is not None:
        metrics['payems_mom'] = payems_latest.value - payems_latest.prev_value

    rsa_latest = _latest_observation('RSAFS', as_of=as_of)
    if rsa_latest:
        metrics['rsa_sales_yoy'] = rsa_latest.yoy_change

    tcu_latest = _latest_observation('TCU', as_of=as_of)
    if tcu_latest:
        metrics['tcu_3m_change'] = _metric_abs_change(tcu_latest, 3)

    sent_latest = _latest_observation('UMCSENT', as_of=as_of)
    if sent_latest:
        metrics['umcsent_3m_change'] = _metric_abs_change(sent_latest, 3)

    jolts_latest = _latest_observation('JTSJOL', as_of=as_of)
    if jolts_latest:
        metrics['jolts_yoy'] = jolts_latest.yoy_change

    wage_latest = _latest_observation('CES0500000003', as_of=as_of)
    if wage_latest:
        metrics['wage_yoy'] = wage_latest.yoy_change

    for series_id, key in (
        ('CPIAUCSL', 'cpi_yoy'),
        ('CPILFESL', 'core_cpi_yoy'),
        ('PCEPI', 'pce_yoy'),
        ('T5YIE', 'breakeven_5y'),
        ('BAMLH0A0HYM2', 'hy_spread'),
        ('T10Y2Y', 'yield_curve_2y10y'),
        ('T10Y3M', 'yield_curve_3m10y'),
        ('VIXCLS', 'vix'),
    ):
        obs = _latest_observation(series_id, as_of=as_of)
        if obs:
            metrics[key] = obs.yoy_change if key.endswith('_yoy') else obs.value

    return metrics


def _band_score(value: Optional[float], bands) -> Optional[int]:
    if value is None:
        return None
    for upper, score in bands:
        if value <= upper:
            return score
    return bands[-1][1]


def _signal_label(score: int, positive: str = '拡大寄り') -> str:
    if score >= 35:
        return positive
    if score >= 5:
        return '中立寄り'
    if score > -35:
        return '減速寄り'
    return '警戒寄り'


def _weighted_average(items: List[Tuple[Optional[float], float]]) -> Optional[float]:
    valid = [(score, weight) for score, weight in items if score is not None]
    if not valid:
        return None
    total_weight = sum(weight for _, weight in valid)
    if total_weight == 0:
        return None
    return sum(score * weight for score, weight in valid) / total_weight


def _record(
    key: str,
    series_id: str,
    metric_label: str,
    category: str,
    value: Optional[float],
    score: Optional[int],
    weight: float,
    signal_label: Optional[str] = None,
) -> Optional[Dict]:
    if value is None or score is None:
        return None
    return {
        'metric_key': key,
        'series_id': series_id,
        'metric': metric_label,
        'category': category,
        'value': value,
        'score': score,
        'weight': weight,
        'signal': signal_label or _signal_label(score),
        'contribution': round(score * weight / 100.0, 2),
    }


def _score_growth(metrics: Dict[str, Optional[float]]) -> Tuple[Optional[float], List[Dict]]:
    specs = [
        ('indpro_yoy', 'INDPRO', '前年比', 1.35, [
            (-1.0, -85), (0.5, -25), (2.0, 35), (float('inf'), 75),
        ]),
        ('indpro_3m_change_pct', 'INDPRO', '3カ月変化', 0.85, [
            (-0.6, -65), (0.0, -30), (0.6, 15), (float('inf'), 45),
        ]),
        ('gdp_yoy', 'GDPC1', '前年比', 1.2, [
            (0.0, -90), (0.5, -20), (2.0, 25), (float('inf'), 65),
        ]),
        ('rsa_sales_yoy', 'RSAFS', '前年比', 0.75, [
            (-1.0, -65), (0.5, -30), (3.0, 15), (float('inf'), 45),
        ]),
        ('tcu_3m_change', 'TCU', '3カ月変化', 0.55, [
            (-0.8, -55), (-0.2, -25), (0.4, 5), (float('inf'), 35),
        ]),
        ('umcsent_3m_change', 'UMCSENT', '3カ月変化', 0.45, [
            (-5.0, -45), (0.0, -20), (5.0, 10), (float('inf'), 30),
        ]),
    ]
    records = []
    scores = []
    for key, series_id, metric_label, weight, bands in specs:
        value = metrics.get(key)
        score = _band_score(value, bands)
        rec = _record(key, series_id, metric_label, 'growth', value, score, weight)
        if rec:
            records.append(rec)
            scores.append((score, weight))
    return _weighted_average(scores), records


def _score_labor(metrics: Dict[str, Optional[float]]) -> Tuple[Optional[float], List[Dict]]:
    specs = [
        ('unrate_6m_change', 'UNRATE', '6カ月変化', 1.1, [
            (-0.2, 50), (0.1, 25), (0.4, -35), (float('inf'), -80),
        ]),
        ('unrate_value', 'UNRATE', '水準', 0.7, [
            (4.0, 30), (4.8, 10), (5.8, -30), (float('inf'), -65),
        ]),
        ('payems_mom', 'PAYEMS', '前月差', 0.85, [
            (0.0, -55), (75.0, -15), (200.0, 20), (float('inf'), 45),
        ]),
        ('jolts_yoy', 'JTSJOL', '前年比', 0.45, [
            (-15.0, -55), (-5.0, -25), (3.0, 5), (float('inf'), 30),
        ]),
    ]
    records = []
    scores = []
    for key, series_id, metric_label, weight, bands in specs:
        value = metrics.get(key)
        score = _band_score(value, bands)
        rec = _record(key, series_id, metric_label, 'labor', value, score, weight)
        if rec:
            records.append(rec)
            scores.append((score, weight))
    return _weighted_average(scores), records


def _score_financial(metrics: Dict[str, Optional[float]]) -> Tuple[Optional[float], List[Dict]]:
    specs = [
        ('hy_spread', 'BAMLH0A0HYM2', '水準', 0.9, [
            (3.5, 30), (5.0, 0), (7.0, -45), (float('inf'), -80),
        ]),
        ('yield_curve_2y10y', 'T10Y2Y', '水準', 0.75, [
            (-0.75, -65), (0.0, -35), (0.5, 0), (float('inf'), 25),
        ]),
        ('yield_curve_3m10y', 'T10Y3M', '水準', 0.65, [
            (-1.0, -70), (0.0, -40), (0.75, 0), (float('inf'), 25),
        ]),
        ('vix', 'VIXCLS', '水準', 0.55, [
            (16.0, 20), (24.0, 0), (32.0, -35), (float('inf'), -70),
        ]),
    ]
    records = []
    scores = []
    for key, series_id, metric_label, weight, bands in specs:
        value = metrics.get(key)
        score = _band_score(value, bands)
        rec = _record(
            key,
            series_id,
            metric_label,
            'financial',
            value,
            score,
            weight,
            signal_label=(
                '金融安定寄り' if score is not None and score >= 5 else None
            ),
        )
        if rec:
            records.append(rec)
            scores.append((score, weight))
    return _weighted_average(scores), records


def _classify_regime_detail(metrics: Dict[str, Optional[float]]) -> Dict:
    growth_score, growth_records = _score_growth(metrics)
    labor_score, labor_records = _score_labor(metrics)
    financial_score, financial_records = _score_financial(metrics)
    records = growth_records + labor_records + financial_records

    if not records:
        return {
            'label': RegimeSnapshot.Label.UNKNOWN,
            'rule_strength': 0,
            'activity_score': None,
            'growth_score': None,
            'labor_score': None,
            'financial_score': None,
            'records': [],
        }

    activity_score = _weighted_average([
        (growth_score, 0.45),
        (labor_score, 0.35),
        (financial_score, 0.20),
    ])
    if activity_score is None:
        activity_score = 0

    indpro_yoy = metrics.get('indpro_yoy')
    indpro_3m = metrics.get('indpro_3m_change_pct')
    unrate_6m = metrics.get('unrate_6m_change')
    vix = metrics.get('vix')
    fast_shock = (
        indpro_yoy is not None and -10.0 < indpro_yoy <= -4.0
        and indpro_3m is not None and indpro_3m <= -3.0
        and vix is not None and vix >= 35.0
    )

    if (
        indpro_yoy is not None and -1.0 <= indpro_yoy < 1.5
        and indpro_3m is not None and indpro_3m > 0.5
        and unrate_6m is not None and unrate_6m < 0
    ):
        label = RegimeSnapshot.Label.RECOVERY
    elif fast_shock or activity_score <= -55 or (
        growth_score is not None and growth_score <= -70
    ):
        label = RegimeSnapshot.Label.CONTRACTION
    elif (
        activity_score >= 35
        and (growth_score is None or growth_score >= 25)
        and (labor_score is None or labor_score >= 0)
    ):
        label = RegimeSnapshot.Label.EXPANSION
    else:
        label = RegimeSnapshot.Label.SLOWDOWN

    strength = _rule_strength(label, activity_score, records)
    return {
        'label': label,
        'rule_strength': strength,
        'activity_score': round(activity_score, 1),
        'growth_score': round(growth_score, 1) if growth_score is not None else None,
        'labor_score': round(labor_score, 1) if labor_score is not None else None,
        'financial_score': (
            round(financial_score, 1) if financial_score is not None else None
        ),
        'records': records,
    }


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def _softmax(logits: Dict[str, float]) -> Dict[str, float]:
    if not logits:
        return {}
    max_logit = max(logits.values())
    exps = {
        key: math.exp(max(min(value - max_logit, 30), -30))
        for key, value in logits.items()
    }
    total = sum(exps.values())
    if total <= 0:
        return {key: 0 for key in logits}
    return {key: value / total for key, value in exps.items()}


def _score_or_zero(value: Optional[float]) -> float:
    return float(value) if value is not None else 0.0


def regime_probability_distribution(
    metrics: Dict[str, Optional[float]],
    regime_detail: Optional[Dict] = None,
) -> Dict[str, float]:
    """景気4分類を割合で返す。

    v1 は既存スコアを確率分布へ変換する説明可能モデル。
    """
    detail = regime_detail or _classify_regime_detail(metrics)
    if not detail.get('records'):
        return {}

    activity = _score_or_zero(detail.get('activity_score'))
    growth = _score_or_zero(detail.get('growth_score'))
    labor = _score_or_zero(detail.get('labor_score'))
    financial = _score_or_zero(detail.get('financial_score'))
    indpro_3m = _score_or_zero(metrics.get('indpro_3m_change_pct'))
    unrate_6m = _score_or_zero(metrics.get('unrate_6m_change'))

    recovery_signal = (
        max(indpro_3m, 0.0) * 0.55
        + max(-unrate_6m, 0.0) * 1.20
        + max(growth, 0.0) * 0.012
    )
    logits = {
        RegimeSnapshot.Label.EXPANSION: (
            activity * 0.040 + growth * 0.018 + labor * 0.014 + financial * 0.006
        ),
        RegimeSnapshot.Label.SLOWDOWN: (
            0.55 + max(-growth, 0.0) * 0.018 + max(-financial, 0.0) * 0.010
            - abs(activity) * 0.006
        ),
        RegimeSnapshot.Label.CONTRACTION: (
            -activity * 0.045 + max(-growth, 0.0) * 0.020
            + max(-labor, 0.0) * 0.018 + max(-financial, 0.0) * 0.014
        ),
        RegimeSnapshot.Label.RECOVERY: (
            0.20 + recovery_signal - max(activity, 0.0) * 0.012
            - max(-financial, 0.0) * 0.004
        ),
    }
    label = detail.get('label')
    if label in logits:
        logits[label] += 0.35
    return _softmax(logits)


def risk_probability_distribution(
    metrics: Dict[str, Optional[float]],
    regime_probabilities: Dict[str, float],
) -> Dict[str, float]:
    """主要リスクを0〜1の割合で返す。"""
    core_pce = metrics.get('core_pce_yoy')
    core_pce_3m_ago = metrics.get('core_pce_yoy_3m_ago')
    breakeven = metrics.get('breakeven_5y')
    hy_spread = metrics.get('hy_spread')
    vix = metrics.get('vix')
    spread_2y10y = metrics.get('yield_curve_2y10y')

    pce_momentum = (
        core_pce - core_pce_3m_ago
        if core_pce is not None and core_pce_3m_ago is not None else 0.0
    )
    inflation_logit = (
        ((core_pce or 2.2) - 2.6) * 1.35
        + pce_momentum * 3.0
        + max((breakeven or 2.2) - 2.4, 0.0) * 1.15
    )
    stress_logit = (
        ((hy_spread or 4.0) - 4.5) * 0.55
        + ((vix or 18.0) - 22.0) * 0.08
        + max(-(spread_2y10y or 0.0), 0.0) * 0.45
    )
    recession = (
        regime_probabilities.get(RegimeSnapshot.Label.CONTRACTION, 0.0)
        + regime_probabilities.get(RegimeSnapshot.Label.SLOWDOWN, 0.0) * 0.35
    )
    acceleration = (
        regime_probabilities.get(RegimeSnapshot.Label.EXPANSION, 0.0)
        + regime_probabilities.get(RegimeSnapshot.Label.RECOVERY, 0.0) * 0.35
    )
    return {
        'recession': min(max(recession, 0.0), 1.0),
        'acceleration': min(max(acceleration, 0.0), 1.0),
        'inflation_reacceleration': _sigmoid(inflation_logit),
        'financial_stress': _sigmoid(stress_logit),
    }


def _rule_strength(label: str, activity_score: float, records: List[Dict]) -> int:
    if label == RegimeSnapshot.Label.UNKNOWN or not records:
        return 0
    expected_positive = label in (
        RegimeSnapshot.Label.EXPANSION,
        RegimeSnapshot.Label.RECOVERY,
    )
    aligned = 0
    for rec in records:
        score = rec['score']
        if expected_positive and score >= 5:
            aligned += 1
        elif not expected_positive and score <= 5:
            aligned += 1
    consensus = aligned / len(records)
    coverage = min(len(records) / 8.0, 1.0)
    strength = 35 + min(abs(activity_score), 85) * 0.45
    strength += consensus * 18 + coverage * 14
    return int(round(max(0, min(strength, 95))))


def classify_regime(metrics: Dict[str, Optional[float]]):
    """成長・雇用・金融ストレスから拡大／減速／縮小／回復を判定する。"""
    detail = _classify_regime_detail(metrics)
    return detail['label'], detail['rule_strength']


def classify_inflation(metrics: Dict[str, Optional[float]]):
    """インフレ状態を判定する。"""
    core_pce_yoy = metrics.get('core_pce_yoy')
    core_pce_3m_ago = metrics.get('core_pce_yoy_3m_ago')

    if core_pce_yoy is None:
        return RegimeSnapshot.InflationFlag.UNKNOWN, 0

    if core_pce_yoy > 3.0:
        return RegimeSnapshot.InflationFlag.HIGH, 85

    if core_pce_yoy > 2.2:
        if (
            core_pce_3m_ago is not None
            and core_pce_yoy < core_pce_3m_ago - 0.15
        ):
            return RegimeSnapshot.InflationFlag.EASING, 75
        return RegimeSnapshot.InflationFlag.HIGH, 60

    return RegimeSnapshot.InflationFlag.NORMAL, 75


def _inflation_records(metrics: Dict[str, Optional[float]]) -> List[Dict]:
    specs = [
        ('core_pce_yoy', 'PCEPILFE', '前年比', 1.0),
        ('pce_yoy', 'PCEPI', '前年比', 0.65),
        ('cpi_yoy', 'CPIAUCSL', '前年比', 0.55),
        ('core_cpi_yoy', 'CPILFESL', '前年比', 0.55),
        ('breakeven_5y', 'T5YIE', '水準', 0.45),
    ]
    records: List[Dict] = []
    for key, series_id, metric_label, weight in specs:
        value = metrics.get(key)
        if value is None:
            continue
        if key == 'breakeven_5y':
            score = _band_score(value, [(2.2, 25), (2.7, 0), (3.2, -35), (float('inf'), -65)])
        else:
            score = _band_score(value, [(2.2, 30), (3.0, -15), (4.0, -45), (float('inf'), -75)])
        rec = _record(
            key,
            series_id,
            metric_label,
            'inflation',
            value,
            score,
            weight,
            signal_label='物価安定寄り' if score >= 5 else '物価高止まり',
        )
        if rec:
            records.append(rec)
    return records


def _data_quality(as_of: Optional[date] = None) -> Tuple[int, List[str]]:
    today = as_of or timezone.localdate()
    indicators = {
        i.fred_series_id: i
        for i in Indicator.objects.filter(fred_series_id__in=QUALITY_SERIES)
    }
    missing = []
    stale = []
    freshness_scores = []

    for series_id in QUALITY_SERIES:
        indicator = indicators.get(series_id)
        if indicator is None:
            missing.append(series_id)
            freshness_scores.append(0)
            continue
        obs = _latest_observation(series_id, as_of=as_of)
        if obs is None:
            missing.append(indicator.name_ja)
            freshness_scores.append(0)
            continue
        limit = FRESHNESS_LIMIT_DAYS.get(indicator.frequency, 60)
        age_days = max((today - obs.observation_date).days, 0)
        if age_days > limit:
            stale.append(f'{indicator.name_ja}（{obs.observation_date.isoformat()}）')
        over = max(age_days - limit, 0)
        score = max(0, 100 - (over / max(limit, 1)) * 100)
        freshness_scores.append(score)

    coverage_pct = (
        (len(QUALITY_SERIES) - len(missing)) / len(QUALITY_SERIES) * 100
        if QUALITY_SERIES else 0
    )
    freshness_pct = (
        sum(freshness_scores) / len(freshness_scores)
        if freshness_scores else 0
    )
    quality = int(round(coverage_pct * 0.65 + freshness_pct * 0.35))

    warnings: List[str] = []
    if len(missing) >= 2:
        warnings.append(f'主要指標の欠損が{len(missing)}件あります。')
    elif len(missing) == 1:
        warnings.append(f'主要指標が1件欠損しています: {missing[0]}')
    if stale:
        warnings.append(
            '観測日が古い主要指標があります: ' + '、'.join(stale[:3])
        )
    if 'GDPC1' in indicators:
        warnings.append('GDPは四半期データのため、直近変化の反映が遅れます。')
    if quality < 60:
        warnings.append('データ鮮度またはカバレッジが低く、判定は暫定扱いです。')

    return quality, warnings


def _build_evidence(records: List[Dict], as_of: Optional[date] = None) -> List[Dict]:
    if not records:
        return []
    indicators = {
        i.fred_series_id: i
        for i in Indicator.objects.filter(
            fred_series_id__in={r['series_id'] for r in records}
        )
    }
    evidence = []
    for rec in sorted(records, key=lambda r: abs(r['contribution']), reverse=True):
        indicator = indicators.get(rec['series_id'])
        obs = _latest_observation(rec['series_id'], as_of=as_of)
        evidence.append({
            'series_id': rec['series_id'],
            'name': indicator.name_ja if indicator else rec['series_id'],
            'category': rec['category'],
            'metric': rec['metric'],
            'value': rec['value'],
            'unit': indicator.unit if indicator else '',
            'observation_date': (
                obs.observation_date.isoformat() if obs else None
            ),
            'signal': rec['signal'],
            'contribution': rec['contribution'],
        })
    return evidence[:10]


def _extra_warnings(
    regime_detail: Dict,
    inflation_flag: str,
) -> List[str]:
    warnings = []
    financial_score = regime_detail.get('financial_score')
    label = regime_detail.get('label')
    if (
        financial_score is not None and financial_score <= -45
        and label in (RegimeSnapshot.Label.EXPANSION, RegimeSnapshot.Label.RECOVERY)
    ):
        warnings.append('景気指標は改善寄りですが、金融ストレスは弱めのサインです。')
    if (
        inflation_flag == RegimeSnapshot.InflationFlag.HIGH
        and label in (RegimeSnapshot.Label.EXPANSION, RegimeSnapshot.Label.RECOVERY)
    ):
        warnings.append('景気判断とは別に、物価高止まりが重しになる可能性があります。')
    return warnings


def build_regime_assessment_from_metrics(
    metrics: Dict[str, Optional[float]],
    *,
    as_of: Optional[date] = None,
) -> Dict:
    """与えた指標セットでレジーム判定を返す。"""
    regime_detail = _classify_regime_detail(metrics)
    inflation_flag, inflation_strength = classify_inflation(metrics)
    inflation_records = _inflation_records(metrics)

    strength_parts = [
        v for v in (regime_detail['rule_strength'], inflation_strength) if v
    ]
    rule_strength = (
        int(round(sum(strength_parts) / len(strength_parts)))
        if strength_parts else 0
    )
    data_quality, warnings = _data_quality(as_of=as_of)
    warnings.extend(_extra_warnings(regime_detail, inflation_flag))

    records = regime_detail['records'] + inflation_records
    evidence = _build_evidence(records, as_of=as_of)
    if len(evidence) < 5:
        warnings.append('判定根拠に使える主要指標が5件未満です。')

    regime_probabilities = regime_probability_distribution(metrics, regime_detail)
    risk_probabilities = risk_probability_distribution(metrics, regime_probabilities)

    return {
        'regime_label': regime_detail['label'],
        'inflation_flag': inflation_flag,
        'rule_strength': rule_strength,
        'data_quality': data_quality,
        'evidence': evidence,
        'warnings': warnings,
        'model_version': MODEL_VERSION,
        'probability_model_version': PROBABILITY_MODEL_VERSION,
        'metrics': metrics,
        'regime_probabilities': regime_probabilities,
        'risk_probabilities': risk_probabilities,
        'scores': {
            'activity': regime_detail.get('activity_score'),
            'growth': regime_detail.get('growth_score'),
            'labor': regime_detail.get('labor_score'),
            'financial': regime_detail.get('financial_score'),
        },
    }


def build_current_regime_assessment(as_of: Optional[date] = None) -> Dict:
    """現在または指定日時点の判定結果を構造化して返す。"""
    metrics = collect_key_metrics(as_of=as_of)
    return build_regime_assessment_from_metrics(metrics, as_of=as_of)


def build_current_indicator_vector() -> Dict[str, float]:
    """重要度A指標の現状値ベクトル（時点別標準化済み）を作る。"""
    indicators = Indicator.objects.filter(is_active=True, importance='A').order_by('display_order')
    vector: Dict[str, float] = {}
    for ind in indicators:
        latest = (
            Observation.objects
            .filter(indicator=ind)
            .order_by('-observation_date')
            .first()
        )
        if latest and latest.expanding_z_score is not None:
            vector[ind.fred_series_id] = latest.expanding_z_score
    return vector


def compute_current_regime() -> Optional[RegimeSnapshot]:
    """現状のレジームスナップショットを保存する。"""
    assessment = build_current_regime_assessment()
    vector = build_current_indicator_vector()

    today = timezone.localdate()
    with transaction.atomic():
        snapshot, _ = RegimeSnapshot.objects.update_or_create(
            snapshot_date=today,
            defaults={
                'regime_label': assessment['regime_label'],
                'inflation_flag': assessment['inflation_flag'],
                'confidence': assessment['rule_strength'],
                'rule_strength': assessment['rule_strength'],
                'data_quality': assessment['data_quality'],
                'evidence': assessment['evidence'],
                'warnings': assessment['warnings'],
                'model_version': assessment['model_version'],
                'indicator_vector': vector,
                'regime_probabilities': assessment['regime_probabilities'],
                'risk_probabilities': assessment['risk_probabilities'],
            },
        )
    return snapshot
