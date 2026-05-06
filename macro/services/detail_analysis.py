"""指標詳細ページ向け分析。

ML依存なしで、現在状態の解釈・歴史的クラッシュ時の値・SP500との相関を計算する。
"""

from datetime import date
from typing import Dict, List, Optional

from dateutil.relativedelta import relativedelta

from ..models import Indicator, Observation, PriceObservation
from .historical_crash import HISTORICAL_CRASH_MONTHS
from .judgment import evaluate as evaluate_judgment
from .linkage import _pearson


# ステージ→経済視点の文言
ECONOMIC_PHRASE = {
    1: '非常に良好',
    2: '良好',
    3: '中立',
    4: '警戒',
    5: '深刻',
}

# ステージ→市場視点の文言
MARKET_PHRASE = {
    1: '株式市場に強い追い風',
    2: '追い風',
    3: '中立',
    4: '逆風',
    5: '強い逆風',
}


def interpret_state(
    indicator: Indicator,
    observation: Optional[Observation],
) -> Dict:
    """現在状態の解釈テキストを生成する。"""
    if observation is None or not indicator.judgment_rule:
        return {
            'has_interpretation': False,
            'sentences': [],
        }

    economic_stage, market_stage = evaluate_judgment(observation, indicator.judgment_rule)
    sentences: List[str] = []

    if economic_stage:
        sentences.append(
            f'経済全体への影響は「{ECONOMIC_PHRASE[economic_stage]}」。'
        )
    if market_stage:
        sentences.append(
            f'株式市場への影響は「{MARKET_PHRASE[market_stage]}」。'
        )

    if observation.prev_value is not None:
        if observation.value > observation.prev_value:
            trend = '前回値より上昇'
        elif observation.value < observation.prev_value:
            trend = '前回値より下落'
        else:
            trend = '前回値と同水準'
        sentences.append(f'{trend}。')

    if not sentences:
        return {'has_interpretation': False, 'sentences': []}

    return {
        'has_interpretation': True,
        'economic_stage': economic_stage,
        'market_stage': market_stage,
        'sentences': sentences,
    }


def get_values_at_crash_months(indicator: Indicator) -> List[Dict]:
    """歴史的クラッシュ月時点での観測値を返す。"""
    rows: List[Dict] = []
    obs_qs = (
        Observation.objects
        .filter(indicator=indicator)
        .order_by('observation_date')
    )
    obs_list = list(obs_qs)
    if not obs_list:
        return []

    for month_start, label, _period in HISTORICAL_CRASH_MONTHS:
        month_end = (
            month_start.replace(day=1) + relativedelta(months=1)
            - relativedelta(days=1)
        )
        # その月以下で最新の観測を取る
        match = None
        for o in obs_list:
            if o.observation_date <= month_end:
                match = o
            else:
                break
        if match is None:
            continue
        rows.append({
            'month_label': month_start.strftime('%Y年%m月'),
            'crash_label': label,
            'value': match.value,
            'yoy_change': match.yoy_change,
            'observation_date': match.observation_date,
        })
    return rows


def correlation_with_sp500(
    indicator: Indicator,
    months: int = 24,
) -> Optional[float]:
    """指標値とSP500月次終値の過去 months ヶ月のピアソン相関を返す。

    データ不足の場合 None を返す。
    """
    indicator_obs = list(
        Observation.objects
        .filter(indicator=indicator)
        .order_by('-observation_date')
        .values_list('observation_date', 'value')[:months * 3]
    )
    if not indicator_obs:
        return None

    # 月初日キーで月次集約（同月複数あれば最後の値を採用）
    monthly_indicator: Dict[date, float] = {}
    for d, v in indicator_obs:
        monthly_indicator[d.replace(day=1)] = v

    sp500_obs = list(
        PriceObservation.objects
        .filter(ticker=PriceObservation.Ticker.SP500)
        .order_by('-observation_month')
        .values_list('observation_month', 'close_price')[:months * 2]
    )
    if not sp500_obs:
        return None
    monthly_sp500: Dict[date, float] = {
        d.replace(day=1): v for d, v in sp500_obs
    }

    # 共通月を昇順に
    common_months = sorted(set(monthly_indicator.keys()) & set(monthly_sp500.keys()))
    if len(common_months) < 12:
        return None
    if len(common_months) > months:
        common_months = common_months[-months:]

    xs = [monthly_indicator[m] for m in common_months]
    ys = [monthly_sp500[m] for m in common_months]
    return _pearson(xs, ys)


def correlation_label(corr: Optional[float]) -> str:
    """相関係数を日本語ラベルに変換。"""
    if corr is None:
        return 'データ不足'
    if corr >= 0.7:
        return '強い正の連動'
    if corr >= 0.3:
        return '中程度の正の連動'
    if corr > -0.3:
        return '弱い / 無相関'
    if corr > -0.7:
        return '中程度の逆連動'
    return '強い逆連動'
