"""マクロレジーム判定。

ルールベースで「拡大／減速／縮小／回復」と「インフレ高止まり／鈍化／正常」を判定する。
しきい値は実装初期値。後で過去データを見ながら微調整する想定。
"""

import logging
from datetime import timedelta
from typing import Dict, Optional

from django.db import transaction
from django.utils import timezone

from ..models import Indicator, Observation, RegimeSnapshot

logger = logging.getLogger(__name__)

# 主要シグナル指標
GROWTH_KEY_SERIES = 'INDPRO'  # 鉱工業生産（ISM代替）
EMPLOYMENT_KEY_SERIES = 'UNRATE'  # 失業率
GDP_KEY_SERIES = 'GDPC1'  # 実質GDP
INFLATION_KEY_SERIES = 'PCEPILFE'  # Core PCE


def _latest_observation(series_id: str) -> Optional[Observation]:
    indicator = Indicator.objects.filter(fred_series_id=series_id).first()
    if not indicator:
        return None
    return (
        Observation.objects
        .filter(indicator=indicator)
        .order_by('-observation_date')
        .first()
    )


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


def collect_key_metrics() -> Dict[str, Optional[float]]:
    """レジーム判定に使う主要メトリクスを収集する。"""
    metrics: Dict[str, Optional[float]] = {}

    # 鉱工業生産 (INDPRO)
    indpro_latest = _latest_observation(GROWTH_KEY_SERIES)
    if indpro_latest:
        metrics['indpro_yoy'] = indpro_latest.yoy_change
        metrics['indpro_value'] = indpro_latest.value
        # 3ヶ月前との比較（モメンタム）
        target_3m = _months_ago(indpro_latest.observation_date, 3)
        indpro_3m_ago = _observation_at_or_before(GROWTH_KEY_SERIES, target_3m)
        if indpro_3m_ago and indpro_3m_ago.value:
            metrics['indpro_3m_change_pct'] = (
                (indpro_latest.value - indpro_3m_ago.value) / abs(indpro_3m_ago.value) * 100.0
            )

    # 失業率 (UNRATE)
    unrate_latest = _latest_observation(EMPLOYMENT_KEY_SERIES)
    if unrate_latest:
        metrics['unrate_value'] = unrate_latest.value
        target_6m = _months_ago(unrate_latest.observation_date, 6)
        unrate_6m_ago = _observation_at_or_before(EMPLOYMENT_KEY_SERIES, target_6m)
        if unrate_6m_ago:
            metrics['unrate_6m_change'] = unrate_latest.value - unrate_6m_ago.value

    # 実質GDP (GDPC1) — 四半期データなので yoy_change が頼り
    gdp_latest = _latest_observation(GDP_KEY_SERIES)
    if gdp_latest:
        metrics['gdp_yoy'] = gdp_latest.yoy_change

    # Core PCE (PCEPILFE)
    core_pce_latest = _latest_observation(INFLATION_KEY_SERIES)
    if core_pce_latest:
        metrics['core_pce_yoy'] = core_pce_latest.yoy_change
        target_3m = _months_ago(core_pce_latest.observation_date, 3)
        core_pce_3m_ago = _observation_at_or_before(INFLATION_KEY_SERIES, target_3m)
        if core_pce_3m_ago:
            metrics['core_pce_yoy_3m_ago'] = core_pce_3m_ago.yoy_change

    return metrics


def classify_regime(metrics: Dict[str, Optional[float]]):
    """成長メトリクスから拡大／減速／縮小／回復を判定する。"""
    indpro_yoy = metrics.get('indpro_yoy')
    indpro_3m = metrics.get('indpro_3m_change_pct')
    unrate_6m = metrics.get('unrate_6m_change')
    gdp_yoy = metrics.get('gdp_yoy')

    # 縮小: 強い悪化シグナル
    if (indpro_yoy is not None and indpro_yoy < -1.0) or (
        gdp_yoy is not None and gdp_yoy < 0.0
    ):
        return RegimeSnapshot.Label.CONTRACTION, 75

    # 回復: 直近の改善モメンタム + 失業率ピーク後
    if (
        indpro_yoy is not None and -1.0 <= indpro_yoy < 1.5
        and indpro_3m is not None and indpro_3m > 0.5
        and unrate_6m is not None and unrate_6m < 0
    ):
        return RegimeSnapshot.Label.RECOVERY, 70

    # 拡大: 成長強く雇用安定
    if (
        indpro_yoy is not None and indpro_yoy > 2.0
        and (unrate_6m is None or unrate_6m <= 0.1)
    ):
        return RegimeSnapshot.Label.EXPANSION, 80

    # 減速: 中間ゾーン
    if indpro_yoy is not None and -1.0 <= indpro_yoy <= 2.0:
        if unrate_6m is not None and unrate_6m > 0.2:
            return RegimeSnapshot.Label.SLOWDOWN, 70
        return RegimeSnapshot.Label.SLOWDOWN, 55

    # 成長率が取れて正なら拡大寄り
    if indpro_yoy is not None and indpro_yoy > 0:
        return RegimeSnapshot.Label.EXPANSION, 50

    return RegimeSnapshot.Label.UNKNOWN, 0


def classify_inflation(metrics: Dict[str, Optional[float]]):
    """インフレ状態を判定する。"""
    core_pce_yoy = metrics.get('core_pce_yoy')
    core_pce_3m_ago = metrics.get('core_pce_yoy_3m_ago')

    if core_pce_yoy is None:
        return RegimeSnapshot.InflationFlag.UNKNOWN, 0

    # 高止まり
    if core_pce_yoy > 3.0:
        return RegimeSnapshot.InflationFlag.HIGH, 85

    # 鈍化: 2-3% 帯で前期比減
    if core_pce_yoy > 2.2:
        if (
            core_pce_3m_ago is not None
            and core_pce_yoy < core_pce_3m_ago - 0.15
        ):
            return RegimeSnapshot.InflationFlag.EASING, 75
        return RegimeSnapshot.InflationFlag.HIGH, 60

    # 正常: 2% 近辺
    return RegimeSnapshot.InflationFlag.NORMAL, 75


def build_current_indicator_vector() -> Dict[str, float]:
    """重要度A指標の現状値ベクトル（標準化済み）を作る。"""
    indicators = Indicator.objects.filter(is_active=True, importance='A').order_by('display_order')
    vector: Dict[str, float] = {}
    for ind in indicators:
        latest = (
            Observation.objects
            .filter(indicator=ind)
            .order_by('-observation_date')
            .first()
        )
        if latest and latest.deviation_from_long_term is not None:
            vector[ind.fred_series_id] = latest.deviation_from_long_term
    return vector


def compute_current_regime() -> Optional[RegimeSnapshot]:
    """全体オーケストレーター。現状のレジームスナップショットを保存する。"""
    metrics = collect_key_metrics()
    label, growth_conf = classify_regime(metrics)
    inflation, inf_conf = classify_inflation(metrics)

    # 確度: 成長判定とインフレ判定の単純平均
    confidence = 0
    parts = [v for v in (growth_conf, inf_conf) if v]
    if parts:
        confidence = round(sum(parts) / len(parts))

    vector = build_current_indicator_vector()

    today = timezone.localdate()
    with transaction.atomic():
        snapshot, _ = RegimeSnapshot.objects.update_or_create(
            snapshot_date=today,
            defaults={
                'regime_label': label,
                'inflation_flag': inflation,
                'confidence': confidence,
                'indicator_vector': vector,
            },
        )
    return snapshot
