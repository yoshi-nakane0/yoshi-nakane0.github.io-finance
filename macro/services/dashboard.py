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
        nydow_val = item.get('nydow_next_return')
        nasdaq_val = item.get('nasdaq_next_return')
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
            'nydow_return_display': format_pct(nydow_val),
            'nydow_return_pos': (nydow_val or 0) >= 0,
            'nydow_return_value': nydow_val,
            'nasdaq_return_display': format_pct(nasdaq_val),
            'nasdaq_return_pos': (nasdaq_val or 0) >= 0,
            'nasdaq_return_value': nasdaq_val,
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
        summary_label = '判定データ不足'
        return {
            'regime_label': '—',
            'inflation_flag': '—',
            'regime_summary_label': summary_label,
            'regime_summary_lines': [summary_label],
            'regime_tone_label': 'データ待ち',
            'regime_plain_judgment': 'データ不足',
            'regime_plain_detail': '景気の良し悪しを判断するには、主要指標の取得が必要です。',
            'regime_good_points': [],
            'regime_bad_points': ['主要指標がまだ揃っていません。'],
            'regime_outlook': 'まずデータ更新後に再判定してください。',
            'regime_condition_score': 0,
            'regime_condition_score_display': '—',
            'regime_condition_fraction_display': '—/5',
            'regime_condition_bar_pct': 0,
            'regime_condition_pct_display': '—%',
            'regime_condition_label': '判定保留',
            'regime_condition_note': '主要指標の取得後に、1〜5で景気の良し悪しを表示します。',
            'regime_condition_tone': 'unknown',
            'regime_update_guidance': _regime_update_guidance(),
            'rule_strength_pct': 0,
            'rule_strength_score': 0,
            'rule_strength_fraction_display': '—/5',
            'data_quality_pct': 0,
            'data_quality_score': 0,
            'data_quality_fraction_display': '—/5',
            'regime_evidence': [],
            'regime_warnings': ['判定に必要なデータがまだ揃っていません。'],
            'regime_model_version': '—',
            'snapshot_date': None,
        }
    summary = _regime_summary(
        snapshot.regime_label,
        snapshot.inflation_flag,
    )
    rule_strength = getattr(snapshot, 'rule_strength', None)
    if rule_strength is None:
        rule_strength = getattr(snapshot, 'confidence', 0)
    formatted_evidence = _format_regime_evidence(
        getattr(snapshot, 'evidence', []) or []
    )
    data_quality = int(round(getattr(snapshot, 'data_quality', 0) or 0))
    rule_strength_pct = int(round(rule_strength or 0))
    condition = _regime_condition_summary(
        snapshot.regime_label,
        snapshot.inflation_flag,
        formatted_evidence,
        rule_strength or 0,
        data_quality,
    )
    return {
        'regime_label': snapshot.get_regime_label_display(),
        'inflation_flag': snapshot.get_inflation_flag_display(),
        'regime_summary_label': summary['label'],
        'regime_summary_lines': _split_regime_summary(summary['label']),
        'regime_tone_label': summary['tone'],
        'regime_plain_judgment': _regime_plain_judgment(
            snapshot.regime_label,
            snapshot.inflation_flag,
        ),
        'regime_plain_detail': _regime_plain_detail(
            snapshot.regime_label,
            snapshot.inflation_flag,
        ),
        'regime_good_points': _regime_material_points(
            formatted_evidence,
            positive=True,
        ),
        'regime_bad_points': _regime_material_points(
            formatted_evidence,
            positive=False,
        ),
        'regime_outlook': _regime_outlook(
            snapshot.regime_label,
            snapshot.inflation_flag,
        ),
        **condition,
        'regime_update_guidance': _regime_update_guidance(),
        'rule_strength_pct': rule_strength_pct,
        'rule_strength_score': _five_point_from_pct(rule_strength_pct),
        'rule_strength_fraction_display': (
            f'{_five_point_from_pct(rule_strength_pct)}/5'
        ),
        'data_quality_pct': data_quality,
        'data_quality_score': _five_point_from_pct(data_quality),
        'data_quality_fraction_display': f'{_five_point_from_pct(data_quality)}/5',
        'regime_evidence': formatted_evidence,
        'regime_warnings': getattr(snapshot, 'warnings', []) or [],
        'regime_model_version': getattr(snapshot, 'model_version', 'regime_v1'),
        'snapshot_date': snapshot.snapshot_date,
    }


def _format_regime_evidence(evidence: List[Dict]) -> List[Dict]:
    rows = []
    for item in evidence:
        value = item.get('value')
        unit = item.get('unit') or ''
        contribution = item.get('contribution')
        rows.append({
            'series_id': item.get('series_id', ''),
            'name': item.get('name') or item.get('series_id', ''),
            'metric': item.get('metric', ''),
            'value_display': format_value(value, unit),
            'unit': unit,
            'observation_date': item.get('observation_date') or '—',
            'signal': item.get('signal', '—'),
            'contribution_display': format_signed(contribution, 2),
            'is_negative': (contribution or 0) < 0,
        })
    return rows


def _regime_condition_summary(
    regime_label: str,
    inflation_flag: str,
    evidence: List[Dict],
    rule_strength: float,
    data_quality: float,
) -> Dict:
    labels = RegimeSnapshot.Label
    flags = RegimeSnapshot.InflationFlag
    base_scores = {
        labels.EXPANSION: 5,
        labels.RECOVERY: 4,
        labels.SLOWDOWN: 2,
        labels.CONTRACTION: 1,
    }
    score = base_scores.get(regime_label)
    if score is None or data_quality < 40:
        return {
            'regime_condition_score': 0,
            'regime_condition_score_display': '—',
            'regime_condition_fraction_display': '—/5',
            'regime_condition_bar_pct': 0,
            'regime_condition_pct_display': '—%',
            'regime_condition_label': '判定保留',
            'regime_condition_note': '主要指標の不足が大きく、良い/悪いをまだ点数化しません。',
            'regime_condition_tone': 'unknown',
        }

    if inflation_flag == flags.HIGH and regime_label in (
        labels.EXPANSION,
        labels.RECOVERY,
    ):
        score -= 1

    positive_count = len([row for row in evidence if not row.get('is_negative')])
    negative_count = len([row for row in evidence if row.get('is_negative')])
    if score >= 4 and negative_count >= positive_count + 2:
        score -= 1
    elif score <= 2 and positive_count >= negative_count + 2:
        score += 1

    if rule_strength < 45 or data_quality < 55:
        score = min(max(score, 2), 4)

    score = min(max(score, 1), 5)
    label_map = {
        5: '良い',
        4: 'やや良い',
        3: 'ふつう',
        2: 'やや悪い',
        1: '悪い',
    }
    note_map = {
        5: '成長が強く、物価も大きな重しではありません。',
        4: '景気は良い方向ですが、物価や一部指標に確認点があります。',
        3: '良い材料と悪い材料が混在しています。',
        2: '景気は弱めで、悪化サインを優先して見ます。',
        1: '悪化サインが強く、防御寄りに見る局面です。',
    }
    note = note_map[score]
    if rule_strength < 45 or data_quality < 55:
        note = f'{note} ただし根拠の揃い方やデータ鮮度が弱いため暫定です。'

    if score >= 4:
        tone = 'positive'
    elif score == 3:
        tone = 'neutral'
    else:
        tone = 'negative'

    return {
        'regime_condition_score': score,
        'regime_condition_score_display': str(score),
        'regime_condition_fraction_display': f'{score}/5',
        'regime_condition_bar_pct': score * 20,
        'regime_condition_pct_display': f'{score * 20}%',
        'regime_condition_label': label_map[score],
        'regime_condition_note': note,
        'regime_condition_tone': tone,
    }


def _five_point_from_pct(value: int) -> int:
    value = min(max(int(value or 0), 0), 100)
    if value == 0:
        return 1
    return min(((value - 1) // 20) + 1, 5)


def _regime_plain_judgment(regime_label: str, inflation_flag: str) -> str:
    labels = RegimeSnapshot.Label
    flags = RegimeSnapshot.InflationFlag
    base_map = {
        labels.EXPANSION: '景気は良い寄り',
        labels.RECOVERY: '景気は持ち直し中',
        labels.SLOWDOWN: '景気は弱含み',
        labels.CONTRACTION: '景気は悪い寄り',
        labels.UNKNOWN: '判定保留',
    }
    base = base_map.get(regime_label, '判定保留')
    if inflation_flag == flags.HIGH and regime_label in (
        labels.EXPANSION,
        labels.RECOVERY,
    ):
        return f'{base}だが物価が重い'
    if inflation_flag == flags.HIGH and regime_label in (
        labels.SLOWDOWN,
        labels.CONTRACTION,
    ):
        return f'{base}で物価も重い'
    return base


def _regime_plain_detail(regime_label: str, inflation_flag: str) -> str:
    labels = RegimeSnapshot.Label
    flags = RegimeSnapshot.InflationFlag
    detail_map = {
        labels.EXPANSION: '生産・雇用・金融環境が全体として強めです。',
        labels.RECOVERY: '悪化局面からの改善を確認中です。',
        labels.SLOWDOWN: '成長ペースが落ち、慎重に見る局面です。',
        labels.CONTRACTION: '景気悪化のサインが強く、守りを優先する局面です。',
        labels.UNKNOWN: '判断材料が不足しています。',
    }
    detail = detail_map.get(regime_label, '判断材料が不足しています。')
    if inflation_flag == flags.HIGH:
        return f'{detail} ただし物価高が金利や消費の重しになります。'
    if inflation_flag == flags.EASING:
        return f'{detail} 物価は落ち着く方向です。'
    if inflation_flag == flags.NORMAL:
        return f'{detail} 物価は比較的安定しています。'
    return detail


def _regime_material_points(evidence: List[Dict], *, positive: bool) -> List[str]:
    rows = [
        row for row in evidence
        if bool(row.get('is_negative')) is not positive
    ]
    points = []
    for row in rows[:3]:
        unit = f" {row['unit']}" if row.get('unit') else ''
        points.append(
            f"{row['name']}が{row['signal']}（{row['metric']} {row['value_display']}{unit}）"
        )
    if points:
        return points
    return ['目立った支援材料はまだ少ないです。'] if positive else ['大きな悪材料は限定的です。']


def _regime_outlook(regime_label: str, inflation_flag: str) -> str:
    labels = RegimeSnapshot.Label
    flags = RegimeSnapshot.InflationFlag
    if regime_label == labels.EXPANSION:
        outlook = '次の焦点は、雇用と生産の強さが続くかです。'
    elif regime_label == labels.RECOVERY:
        outlook = '次の焦点は、持ち直しが雇用と消費へ広がるかです。'
    elif regime_label == labels.SLOWDOWN:
        outlook = '次の焦点は、減速が一時的か景気悪化へ進むかです。'
    elif regime_label == labels.CONTRACTION:
        outlook = '次の焦点は、悪化が止まり回復サインが出るかです。'
    else:
        outlook = '次の焦点は、主要データを揃えて判定できる状態にすることです。'
    if inflation_flag == flags.HIGH:
        return f'{outlook} 物価高が残る間は、改善しても上値は重く見ます。'
    if inflation_flag == flags.EASING:
        return f'{outlook} 物価鈍化が続けば、先行きは改善しやすくなります。'
    return outlook


def _regime_update_guidance() -> List[str]:
    return [
        '金利・VIXなど市場系は毎営業日確認',
        '景気・雇用・物価は週1回と主要発表後に更新',
        '局面判定は最低でも月1回、CPI・PCE・雇用統計後は再判定',
    ]


def _split_regime_summary(label: str) -> List[str]:
    if '。' not in label:
        return [label]
    first, rest = label.split('。', 1)
    lines = [f'{first}。'] if first else []
    if rest:
        lines.append(rest)
    return lines or [label]


def _regime_summary(regime_label: str, inflation_flag: str) -> Dict[str, str]:
    labels = RegimeSnapshot.Label
    flags = RegimeSnapshot.InflationFlag
    summary_map = {
        (labels.EXPANSION, flags.HIGH): ('景気は拡大寄り、物価は高止まり', '過熱気味'),
        (labels.EXPANSION, flags.EASING): ('景気は拡大寄り、物価は鈍化方向', '良好'),
        (labels.EXPANSION, flags.NORMAL): ('景気は拡大寄り、物価は安定圏', '良好'),
        (labels.SLOWDOWN, flags.HIGH): ('景気は減速寄り、物価は高止まり', '警戒'),
        (labels.SLOWDOWN, flags.EASING): ('景気は減速寄り、物価は鈍化方向', '様子見'),
        (labels.SLOWDOWN, flags.NORMAL): ('景気は減速寄り、物価は安定圏', '様子見'),
        (labels.CONTRACTION, flags.HIGH): ('景気は縮小寄り、物価は高止まり', '要警戒'),
        (labels.CONTRACTION, flags.EASING): ('景気は縮小寄り、物価は鈍化方向', '底探り'),
        (labels.CONTRACTION, flags.NORMAL): ('景気は縮小寄り、物価は安定圏', '底探り'),
        (labels.RECOVERY, flags.HIGH): ('景気は回復寄り、物価は高止まり', '回復途上'),
        (labels.RECOVERY, flags.EASING): ('景気は回復寄り、物価は鈍化方向', '改善'),
        (labels.RECOVERY, flags.NORMAL): ('景気は回復寄り、物価は安定圏', '改善'),
    }
    label, tone = summary_map.get(
        (regime_label, inflation_flag),
        ('判定が安定していません', '確認中'),
    )
    return {'label': label, 'tone': tone}


def build_crash_alert_context() -> Dict:
    """クラッシュ警戒度の表示用コンテキストを作る。"""
    raw = compute_crash_alert()
    components = []
    for c in raw['components']:
        components.append({
            'series_id': c['series_id'],
            'label': c['label'],
            'category': c.get('category', ''),
            'value_display': format_value(c['value'], ''),
            'score': c['score'],
        })
    return {
        'total_score': raw['total_score'],
        'level': raw['level'],
        'level_label': raw['level_label'],
        'components': components,
        'category_summary': raw.get('category_summary', []),
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
