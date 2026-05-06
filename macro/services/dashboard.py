"""macro トップ画面のコンテキスト構築。

views.py を薄く保つために集約。
重い計算（類似度・連動分析）はキャッシュして同一日内の再計算を避ける。
"""

import json
import logging
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.db.models import OuterRef, Subquery
from django.utils import timezone

from ..models import Indicator, Observation, PriceObservation, RegimeSnapshot
from .crash_alert import compute_crash_alert
from .historical_crash import find_similar_crash_months
from .judgment import evaluate as evaluate_judgment
from .linkage import compute_pair_relationships
from .similarity import find_similar_months
from .sparkline import generate_sparkline_svg
from .upcoming_events import load_upcoming_high_impact_events

logger = logging.getLogger(__name__)

# 計算結果は DashboardCache（DB）に precompute_dashboard コマンドで焼き付ける方式に統一。
# 以前ここにあった locmem キャッシュは Vercel サーバーレス（プロセスごとに揮発）では
# 効かなかったため撤去。

SPARKLINE_MONTHS = 24

LIGHTGBM_PREDICTION_PATH = Path('static') / 'macro' / 'lightgbm_prediction.json'


def format_value(value: Optional[float], unit: str) -> str:
    if value is None:
        return '—'
    abs_val = abs(value)
    if unit in ('千人', '千件', '百万$', '十億$') or abs_val >= 1000:
        return f'{value:,.0f}'
    if abs_val >= 100:
        return f'{value:,.1f}'
    return f'{value:.2f}'


def format_pct(value: Optional[float]) -> str:
    if value is None:
        return '—'
    sign = '+' if value > 0 else ''
    return f'{sign}{value:.1f}%'


def format_signed(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return '—'
    sign = '+' if value > 0 else ''
    return f'{sign}{value:.{digits}f}'


def _direction_from(prev_value, current_value) -> str:
    if prev_value is None or current_value is None:
        return '—'
    if current_value > prev_value:
        return '↑'
    if current_value < prev_value:
        return '↓'
    return '→'


def _bulk_load_monthly_values(
    indicator_ids: List[int], months_back: int,
) -> Dict[int, List[float]]:
    """全指標分の月次値を 1 クエリで取得して指標 ID ごとに返す。"""
    if not indicator_ids:
        return {}
    today = timezone.localdate()
    cutoff = today.replace(day=1) - relativedelta(months=months_back)
    rows = (
        Observation.objects
        .filter(indicator_id__in=indicator_ids, observation_date__gte=cutoff)
        .order_by('indicator_id', 'observation_date')
        .values_list('indicator_id', 'observation_date', 'value')
    )
    monthly_map: Dict[int, Dict[date, float]] = defaultdict(dict)
    for ind_id, obs_date, value in rows:
        key = obs_date.replace(day=1)
        monthly_map[ind_id][key] = value
    result: Dict[int, List[float]] = {}
    for ind_id, month_dict in monthly_map.items():
        sorted_months = sorted(month_dict.keys())
        if len(sorted_months) > months_back:
            sorted_months = sorted_months[-months_back:]
        result[ind_id] = [month_dict[m] for m in sorted_months]
    return result


def build_indicator_cards() -> List[Dict]:
    """全アクティブ指標のカード情報を作る。

    最新観測値はサブクエリで 1 クエリ、スパークラインの月次値も 1 クエリでまとめて取得する。
    """
    latest_obs_qs = (
        Observation.objects
        .filter(indicator=OuterRef('pk'))
        .order_by('-observation_date')
    )
    indicators = list(
        Indicator.objects
        .filter(is_active=True)
        .annotate(
            latest_obs_date=Subquery(latest_obs_qs.values('observation_date')[:1]),
            latest_value=Subquery(latest_obs_qs.values('value')[:1]),
            latest_prev_value=Subquery(latest_obs_qs.values('prev_value')[:1]),
            latest_yoy=Subquery(latest_obs_qs.values('yoy_change')[:1]),
        )
        .order_by('display_order')
    )

    monthly_by_id = _bulk_load_monthly_values(
        [i.id for i in indicators], SPARKLINE_MONTHS,
    )

    cards: List[Dict] = []
    for ind in indicators:
        if ind.latest_obs_date is None:
            cards.append({
                'indicator': ind,
                'series_id': ind.fred_series_id,
                'name_ja': ind.name_ja,
                'category': ind.get_category_display(),
                'importance': ind.importance,
                'has_data': False,
                'yoy_display': '—',
                'direction_arrow': '—',
                'economic_stage': None,
                'market_stage': None,
                'sparkline_svg': '',
                'latest_date': None,
            })
            continue

        proxy_obs = SimpleNamespace(
            value=ind.latest_value,
            prev_value=ind.latest_prev_value,
            yoy_change=ind.latest_yoy,
        )
        economic_stage, market_stage = evaluate_judgment(
            proxy_obs, ind.judgment_rule,
        )
        cards.append({
            'indicator': ind,
            'series_id': ind.fred_series_id,
            'name_ja': ind.name_ja,
            'category': ind.get_category_display(),
            'importance': ind.importance,
            'has_data': True,
            'latest_date': ind.latest_obs_date,
            'yoy_display': format_pct(ind.latest_yoy),
            'yoy_value': ind.latest_yoy,
            'economic_stage': economic_stage,
            'market_stage': market_stage,
            'direction_arrow': _direction_from(
                ind.latest_prev_value, ind.latest_value,
            ),
            'sparkline_svg': generate_sparkline_svg(
                monthly_by_id.get(ind.id, []),
            ),
        })
    return cards


def _name_lookup() -> Dict[str, str]:
    return {
        i.fred_series_id: i.name_ja
        for i in Indicator.objects.filter(is_active=True).only('fred_series_id', 'name_ja')
    }


def build_similar_periods(top_n: int = 5) -> List[Dict]:
    try:
        raw = find_similar_months(top_n=top_n)
    except Exception:
        logger.exception("Similarity computation failed")
        return []

    results = []
    for item in raw:
        main3 = item.get('main3', {})
        nikkei_val = item.get('nikkei_next_return')
        spx_val = item.get('spx_next_return')
        results.append({
            'month_label': item['month_start'].strftime('%Y年%m月'),
            'month_start': item['month_start'].isoformat(),
            'distance_display': f"{item['distance']:.2f}",
            'core_pce_display': format_value(main3.get('PCEPILFE'), 'index'),
            'indpro_display': format_value(main3.get('INDPRO'), 'index'),
            'spread_display': format_value(main3.get('T10Y2Y'), '%'),
            'nikkei_return_display': format_pct(nikkei_val),
            'nikkei_return_pos': (nikkei_val or 0) >= 0,
            'nikkei_return_value': nikkei_val,
            'spx_return_display': format_pct(spx_val),
            'spx_return_pos': (spx_val or 0) >= 0,
            'spx_return_value': spx_val,
        })
    return results


def _linkage_interpretation(
    leader_name: str,
    follower_name: str,
    lag_months: int,
    correlation: float,
) -> str:
    """各ペアの読み方を自然言語で返す。"""
    abs_corr = abs(correlation)
    if abs_corr >= 0.7:
        strength = '強く'
    elif abs_corr >= 0.4:
        strength = ''
    else:
        strength = 'やや弱く'

    if lag_months > 0:
        if correlation >= 0:
            return (
                f'{leader_name}が上がると約{lag_months}ヶ月後に'
                f'{follower_name}も{strength}上がりやすい関係。'
            )
        return (
            f'{leader_name}が上がると約{lag_months}ヶ月後に'
            f'{follower_name}は{strength}下がりやすい関係。'
        )
    if correlation >= 0:
        return (
            f'{leader_name}と{follower_name}は同時に'
            f'{strength}同じ方向へ動きやすい関係。'
        )
    return (
        f'{leader_name}と{follower_name}は同時に'
        f'{strength}逆方向へ動きやすい関係。'
    )


def build_linkages(top_n: int = 10) -> List[Dict]:
    try:
        raw = compute_pair_relationships(top_n=top_n)
    except Exception:
        logger.exception("Linkage computation failed")
        return []
    names = _name_lookup()
    results = []
    for item in raw:
        leader_id = item['leader']
        follower_id = item['follower']
        lag = item['lag_months']
        corr = item['correlation']
        leader_name = names.get(leader_id, leader_id)
        follower_name = names.get(follower_id, follower_id)
        if lag > 0:
            relation = f"{leader_name} → {follower_name}（約{lag}ヶ月先行）"
        else:
            relation = f"{leader_name} ⇔ {follower_name}（同時連動）"
        interpretation = _linkage_interpretation(
            leader_name, follower_name, lag, corr,
        )
        results.append({
            'relation_text': relation,
            'correlation': corr,
            'correlation_display': f'{corr:+.2f}',
            'lag_months': lag,
            'is_negative': corr < 0,
            'interpretation': interpretation,
        })
    return results


def build_regime_context(snapshot: Optional[RegimeSnapshot]) -> Dict:
    if snapshot is None:
        return {
            'regime_label': '—',
            'inflation_flag': '—',
            'confidence_pct': 0,
            'snapshot_date': None,
        }
    return {
        'regime_label': snapshot.get_regime_label_display(),
        'inflation_flag': snapshot.get_inflation_flag_display(),
        'confidence_pct': int(round(snapshot.confidence)),
        'snapshot_date': snapshot.snapshot_date,
    }


def build_crash_alert_context() -> Dict:
    """クラッシュ警戒度の表示用コンテキストを作る。"""
    raw = compute_crash_alert()
    components = []
    for c in raw['components']:
        components.append({
            'series_id': c['series_id'],
            'label': c['label'],
            'value_display': format_value(c['value'], ''),
            'score': c['score'],
        })
    return {
        'total_score': raw['total_score'],
        'level': raw['level'],
        'level_label': raw['level_label'],
        'components': components,
    }


def build_historical_crash_similarity(top_n: int = 3) -> List[Dict]:
    """歴史的クラッシュ月との類似度を返す。"""
    return find_similar_crash_months(top_n=top_n)


def _classify_predicted_return(pct: float) -> str:
    """予測リターン値からレベルを返す（CSSクラスに使う）。"""
    if pct >= 0:
        return 'positive'
    if pct >= -3:
        return 'neutral'
    if pct >= -7:
        return 'warn'
    return 'danger'


def load_lightgbm_prediction() -> Optional[Dict]:
    """学習済みの予測 JSON を読み込んで表示用に整形する。

    JSON が存在しない・壊れている場合は None を返す（画面ではセクション非表示）。
    """
    path = Path(settings.BASE_DIR) / LIGHTGBM_PREDICTION_PATH
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        logger.exception("LightGBM prediction JSON の読み込みに失敗")
        return None

    horizons = []
    for h in raw.get('horizons', []):
        pct = h.get('predicted_return_pct')
        mae = h.get('validation_mae_pct')
        if pct is None:
            continue
        horizons.append({
            'months': h.get('months'),
            'predicted_return_pct': pct,
            'predicted_return_display': f'{pct:+.2f}%',
            'validation_mae_pct': mae,
            'validation_mae_display': f'±{mae:.2f}%' if mae is not None else '—',
            'level': _classify_predicted_return(pct),
        })

    if not horizons:
        return None

    return {
        'predicted_at': raw.get('predicted_at'),
        'horizons': horizons,
        'training_samples': raw.get('training_samples'),
        'feature_count': raw.get('feature_count'),
        'model_version': raw.get('model_version'),
    }


def build_upcoming_events(days_ahead: int = 7) -> List[Dict]:
    items = load_upcoming_high_impact_events(days_ahead=days_ahead)
    return [{
        'date_label': item['date'].strftime('%m/%d (%a)'),
        'time': item['time'],
        'currency': item['currency'],
        'event': item['event'],
    } for item in items]
