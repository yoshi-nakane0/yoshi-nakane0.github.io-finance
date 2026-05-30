"""macro トップ画面のコンテキスト構築。

views.py を薄く保つために集約。
重い計算（類似度・連動分析）はキャッシュして同一日内の再計算を避ける。
"""

import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.db.models import OuterRef, Subquery
from django.utils import timezone

from ..models import (
    ForecastSnapshot,
    Indicator,
    ModelValidationReport,
    Observation,
    PriceObservation,
    RegimeSnapshot,
    VintageObservation,
    WorldStateSnapshot,
)
from .crash_alert import FRESHNESS_LIMIT_DAYS, compute_crash_alert
from .crash_probability import wilson_interval
from .historical_crash import find_similar_crash_months
from .judgment import evaluate as evaluate_judgment
from .linkage import compute_pair_relationships
from .raw_archive import latest_archive_status
from .similarity import find_similar_months
from .sparkline import generate_sparkline_svg

logger = logging.getLogger(__name__)

# 計算結果は DashboardCache（DB）に precompute_dashboard コマンドで焼き付ける方式に統一。
# 以前ここにあった locmem キャッシュは Vercel サーバーレス（プロセスごとに揮発）では
# 効かなかったため撤去。

SPARKLINE_MONTHS = 24

LIGHTGBM_PREDICTION_PATH = Path('static') / 'macro' / 'lightgbm_prediction.json'
CRASH_ALERT_BACKTEST_PATH = Path('static') / 'macro' / 'crash_alert_backtest.json'
CRASH_PROBABILITY_MODEL_PATH = Path('static') / 'macro' / 'crash_probability_model.json'
REGIME_PROBABILITY_MODEL_PATH = Path('static') / 'macro' / 'regime_probability_model.json'
MIN_CRASH_PROBABILITY_VALIDATION_EVENTS = 10
CRASH_PROBABILITY_STALE_DAYS = 90
RAW_CALIBRATION_GAP_WARNING = 0.20


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


def _format_date_for_display(value) -> str:
    if value in (None, ''):
        return '—'
    if isinstance(value, datetime):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.strftime('%Y-%m-%d %H:%M')
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            if 'T' in value:
                parsed = datetime.fromisoformat(value)
                if timezone.is_aware(parsed):
                    parsed = timezone.localtime(parsed)
                return parsed.strftime('%Y-%m-%d %H:%M')
            return date.fromisoformat(value).isoformat()
        except ValueError:
            return value
    return str(value)


def _format_path_mtime(path: Path) -> str:
    if not path.exists():
        return '—'
    try:
        return _format_date_for_display(
            datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.get_current_timezone())
        )
    except OSError:
        return '—'


def _parse_iso_date(value) -> Optional[date]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            try:
                return date.fromisoformat(value)
            except ValueError:
                return None
    return None


def _pct_probability_display(value: Optional[float]) -> str:
    return f'{value * 100:.1f}%' if value is not None else '—'


def _number_display(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return '—'
    return f'{value:.{digits}f}'


def _ratio_pct_display(value: Optional[float]) -> str:
    if value is None:
        return '—'
    return f'{value * 100:.1f}%'


def _validation_event_interval_display(
    event_count: Optional[int],
    sample_count: Optional[int],
) -> str:
    if event_count is None or sample_count in (None, 0):
        return '—'
    interval = wilson_interval(int(event_count), int(sample_count))
    if interval is None:
        return '—'
    return f'{interval[0] * 100:.1f}%〜{interval[1] * 100:.1f}%'


def _failure_item_label(item: Dict) -> str:
    series_id = (
        item.get('series_id')
        or item.get('ticker')
        or item.get('target')
        or item.get('phase')
        or item.get('name')
        or '更新処理'
    )
    error = item.get('error') or item.get('message') or ''
    return f'{series_id}: {error}' if error else str(series_id)


def _normalize_update_failures(update_status: Optional[Dict]) -> List[Dict]:
    if not update_status:
        return []
    raw_items = (
        update_status.get('failed')
        or update_status.get('failed_items')
        or update_status.get('failures')
        or []
    )
    failures = []
    for item in raw_items:
        if isinstance(item, dict):
            failures.append({
                **item,
                'label': _failure_item_label(item),
            })
        else:
            failures.append({'label': str(item)})
    return failures


def _active_indicator_freshness() -> Dict:
    latest_obs_qs = (
        Observation.objects
        .filter(indicator=OuterRef('pk'))
        .order_by('-observation_date')
    )
    indicators = list(
        Indicator.objects
        .filter(is_active=True)
        .annotate(
            latest_obs_date=Subquery(
                latest_obs_qs.values('observation_date')[:1],
            ),
        )
        .order_by('display_order')
    )
    today = timezone.localdate()
    missing = []
    stale = []
    for indicator in indicators:
        latest_date = indicator.latest_obs_date
        if latest_date is None:
            missing.append({
                'series_id': indicator.fred_series_id,
                'name': indicator.name_ja,
                'label': f'{indicator.name_ja}（{indicator.fred_series_id}）',
            })
            continue
        limit_days = FRESHNESS_LIMIT_DAYS.get(indicator.frequency)
        age_days = max((today - latest_date).days, 0)
        if limit_days is not None and age_days > limit_days:
            stale.append({
                'series_id': indicator.fred_series_id,
                'name': indicator.name_ja,
                'label': (
                    f'{indicator.name_ja}（{latest_date.isoformat()} / '
                    f'{age_days}日経過）'
                ),
                'age_days': age_days,
            })
    total = len(indicators)
    fresh_count = max(total - len(missing) - len(stale), 0)
    freshness_pct = round(fresh_count / total * 100) if total else 0
    return {
        'total_count': total,
        'fresh_count': fresh_count,
        'missing': missing,
        'stale': stale,
        'freshness_pct': freshness_pct,
    }


def build_reliability_context(
    *,
    last_updated=None,
    dashboard_cache_meta: Optional[Dict] = None,
    update_status: Optional[Dict] = None,
    regime_model_version: Optional[str] = None,
) -> Dict:
    freshness = _active_indicator_freshness()
    failures = _normalize_update_failures(update_status)
    outcome = (update_status or {}).get('status') or 'unknown'
    outcome_label_map = {
        'success': '成功',
        'partial': '一部失敗',
        'failed': '失敗',
        'skipped': '未実行',
        'unknown': '記録なし',
    }
    warnings = []
    if failures:
        warnings.append(f'前回更新で {len(failures)} 件の失敗があります。')
    if outcome == 'skipped':
        warnings.append((update_status or {}).get('message') or '前回更新は実行されませんでした。')
    if freshness['missing']:
        warnings.append(f'未取得の指標が {len(freshness["missing"])} 件あります。')

    freshness_pct = freshness['freshness_pct']
    if outcome == 'failed' or freshness_pct < 60:
        tone = 'danger'
        freshness_label = '不足'
    elif failures or outcome in ('partial', 'skipped') or freshness_pct < 80:
        tone = 'warning'
        freshness_label = '注意'
    else:
        tone = 'good'
        freshness_label = '十分'

    cache_computed_at = (dashboard_cache_meta or {}).get('computed_at')
    return {
        'tone': tone,
        'freshness_label': freshness_label,
        'last_data_date': _format_date_for_display(last_updated),
        'dashboard_cache_computed_at': (
            _format_date_for_display(cache_computed_at)
            if cache_computed_at else '表示時に再計算'
        ),
        'data_freshness_pct': freshness_pct,
        'missing_count': len(freshness['missing']),
        'stale_count': len(freshness['stale']),
        'total_count': freshness['total_count'],
        'failed_count': len(failures),
        'failed_items': failures[:8],
        'has_more_failed_items': len(failures) > 8,
        'missing_items': freshness['missing'][:6],
        'stale_items': freshness['stale'][:6],
        'warnings': warnings,
        'update_status_label': outcome_label_map.get(outcome, outcome),
        'update_message': (update_status or {}).get('message', ''),
        'last_update_finished_at': _format_date_for_display(
            (update_status or {}).get('finished_at')
            or (update_status or {}).get('recorded_at')
        ),
        'regime_model_version': regime_model_version or '—',
    }


def build_raw_archive_context() -> Dict:
    return latest_archive_status()


def build_vintage_status_context() -> Dict:
    total = VintageObservation.objects.count()
    series_count = (
        VintageObservation.objects
        .values('indicator_id')
        .distinct()
        .count()
    )
    latest = (
        VintageObservation.objects
        .order_by('-collected_at')
        .values_list('collected_at', flat=True)
        .first()
    )
    tone = 'good' if total else 'warning'
    return {
        'tone': tone,
        'total_count': total,
        'series_count': series_count,
        'latest_collected_at': _format_date_for_display(latest),
        'status_label': '保存中' if total else '未蓄積',
        'note': (
            'FREDの改定前データを保存しており、point-in-time検証に使えます。'
            if total else
            '次回のFRED更新から、取得時点ごとの値を保存します。'
        ),
    }


def build_macro_conclusion_context(
    snapshot: Optional[RegimeSnapshot] = None,
) -> Optional[Dict]:
    if snapshot is None:
        snapshot = RegimeSnapshot.objects.order_by('-snapshot_date').first()
    if snapshot is None:
        return None
    from .macro_conclusion import latest_or_create_macro_conclusion

    conclusion = latest_or_create_macro_conclusion(snapshot)
    if conclusion is None:
        return None
    return {
        'as_of_date': conclusion.as_of_date.isoformat(),
        'previous_snapshot_date': (
            conclusion.previous_snapshot_date.isoformat()
            if conclusion.previous_snapshot_date else '—'
        ),
        'current_view': conclusion.current_view,
        'previous_change': conclusion.previous_change,
        'base_scenario_3m': conclusion.base_scenario_3m,
        'upside_risk': conclusion.upside_risk,
        'downside_risk': conclusion.downside_risk,
        'watch_events': conclusion.watch_events or [],
        'model_reliability': conclusion.model_reliability,
        'driver_changes': [
            {
                **row,
                'delta_display': format_signed(row.get('delta'), 2),
            }
            for row in conclusion.driver_changes or []
        ],
        'topic_mapping': conclusion.topic_mapping or [],
        'reliability_score': conclusion.reliability_score,
        'reliability_score_display': f'{conclusion.reliability_score:.0f}%',
        'rule_probability_note': (
            conclusion.metadata or {}
        ).get('rule_probability_note', ''),
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
            'regime_probability_rows': [],
            'risk_probability_rows': [],
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
        'regime_probability_rows': _regime_probability_rows(
            getattr(snapshot, 'regime_probabilities', {}) or {}
        ),
        'risk_probability_rows': _risk_probability_rows(
            getattr(snapshot, 'risk_probabilities', {}) or {}
        ),
    }


def _probability_pct(value: Optional[float]) -> int:
    return int(round((value or 0.0) * 100))


def _regime_probability_rows(probabilities: Dict[str, float]) -> List[Dict]:
    label_order = [
        RegimeSnapshot.Label.EXPANSION,
        RegimeSnapshot.Label.SLOWDOWN,
        RegimeSnapshot.Label.CONTRACTION,
        RegimeSnapshot.Label.RECOVERY,
    ]
    rows = []
    for key in label_order:
        value = probabilities.get(key)
        pct = _probability_pct(value)
        rows.append({
            'key': key,
            'label': RegimeSnapshot.Label(key).label,
            'pct': pct,
            'display': f'{pct}%',
        })
    return rows


def _risk_probability_rows(probabilities: Dict[str, float]) -> List[Dict]:
    labels = {
        'recession': '景気後退',
        'acceleration': '景気加速',
        'inflation_reacceleration': '物価再加速',
        'financial_stress': '金融ストレス',
    }
    rows = []
    for key, label in labels.items():
        value = probabilities.get(key)
        pct = _probability_pct(value)
        rows.append({
            'key': key,
            'label': label,
            'pct': pct,
            'display': f'{pct}%',
        })
    return rows


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
        '急落スコアの月次検証と確率モデルは月末データ反映後に更新',
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
    """市場ストレス・急落警戒スコアの表示用コンテキストを作る。"""
    raw = compute_crash_alert()
    backtest = load_crash_alert_backtest()
    components = []
    for c in raw['components']:
        if c.get('is_missing'):
            freshness_label = '欠損'
        elif c.get('is_stale'):
            freshness_label = '古い'
        else:
            freshness_label = '新鮮'
        components.append({
            'series_id': c['series_id'],
            'label': c['label'],
            'category': c.get('category', ''),
            'category_label': c.get('category_label', ''),
            'value_display': format_value(c['value'], ''),
            'score': c['score'] if c['score'] is not None else '—',
            'observation_date': c.get('observation_date') or '—',
            'age_days': c.get('age_days'),
            'age_days_display': (
                f"{c['age_days']}日" if c.get('age_days') is not None else '—'
            ),
            'freshness_label': freshness_label,
            'is_missing': c.get('is_missing', False),
            'is_stale': c.get('is_stale', False),
            'warning': c.get('warning'),
        })
    category_summary = []
    for cat in raw.get('category_summary', []):
        avg_score = cat.get('avg_score')
        category_summary.append({
            **cat,
            'avg_score_display': avg_score if avg_score is not None else '—',
        })
    return {
        'total_score': raw['total_score'],
        'market_stress_score': raw.get('market_stress_score'),
        'forward_risk_score': raw.get('forward_risk_score'),
        'level': raw['level'],
        'level_label': raw['level_label'],
        'components': components,
        'category_summary': category_summary,
        'data_quality_pct': raw.get('data_quality_pct', 0),
        'rule_agreement_pct': raw.get('rule_agreement_pct', 0),
        'validation_confidence_pct': raw.get('validation_confidence_pct'),
        'validation_status': (
            '月次検証あり'
            if backtest else raw.get('validation_status', '検証未実施')
        ),
        'backtest_summary': backtest,
        'is_provisional': raw.get('is_provisional', False),
        'quality_warnings': raw.get('quality_warnings', []),
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


def load_crash_alert_backtest() -> Optional[Dict]:
    """市場ストレススコアの検証 JSON を読み込んで表示用に整形する。"""
    path = Path(settings.BASE_DIR) / CRASH_ALERT_BACKTEST_PATH
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        logger.exception("Crash alert backtest JSON の読み込みに失敗")
        return None

    roc_auc = raw.get('roc_auc')
    pr_auc = raw.get('pr_auc')
    threshold_25 = next(
        (row for row in raw.get('thresholds', []) if row.get('threshold') == 25),
        {},
    )
    return {
        'target': raw.get('target'),
        'horizon_days': raw.get('horizon_days'),
        'drawdown_threshold_pct': raw.get('drawdown_threshold_pct'),
        'updated_at': _format_path_mtime(path),
        'sample_count': raw.get('sample_count'),
        'event_count': raw.get('event_count'),
        'roc_auc_display': f'{roc_auc:.2f}' if roc_auc is not None else '—',
        'pr_auc_display': f'{pr_auc:.2f}' if pr_auc is not None else '—',
        'precision_25_display': (
            f"{threshold_25.get('precision') * 100:.1f}%"
            if threshold_25.get('precision') is not None else '—'
        ),
        'recall_25_display': (
            f"{threshold_25.get('recall') * 100:.1f}%"
            if threshold_25.get('recall') is not None else '—'
        ),
        'calm_miss_count': raw.get('calm_miss_count'),
        'note': raw.get('note'),
    }


def load_crash_probability_model() -> Optional[Dict]:
    """急落確率モデル JSON を読み込んで表示用に整形する。"""
    path = Path(settings.BASE_DIR) / CRASH_PROBABILITY_MODEL_PATH
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        logger.exception("Crash probability model JSON の読み込みに失敗")
        return None

    probability = raw.get('current_probability')
    raw_probability = raw.get('current_raw_probability')
    validation = raw.get('validation') or {}
    roc_auc = validation.get('roc_auc')
    pr_auc = validation.get('pr_auc')
    brier = validation.get('brier_score')
    thresholds = validation.get('thresholds') or []
    threshold_10 = next(
        (row for row in thresholds if row.get('threshold') == 0.1),
        {},
    )
    validation_samples = raw.get('validation_samples')
    validation_event_count = raw.get('validation_event_count')
    baseline_rate = (
        validation_event_count / validation_samples
        if validation_event_count is not None and validation_samples
        else None
    )
    trained_date = _parse_iso_date(raw.get('trained_at'))
    model_age_days = (
        (timezone.localdate() - trained_date).days
        if trained_date is not None else None
    )
    raw_gap = (
        abs(raw_probability - probability)
        if raw_probability is not None and probability is not None
        else None
    )
    reliability_warnings = []
    if (
        validation_event_count is not None
        and validation_event_count < MIN_CRASH_PROBABILITY_VALIDATION_EVENTS
    ):
        reliability_warnings.append(
            f'検証イベントが{validation_event_count}件と少ないため、確率は参考値です。'
        )
    if raw_gap is not None and raw_gap >= RAW_CALIBRATION_GAP_WARNING:
        reliability_warnings.append(
            'raw推定と校正後の差が大きく、モデル校正は不安定です。'
        )
    if model_age_days is not None and model_age_days > CRASH_PROBABILITY_STALE_DAYS:
        reliability_warnings.append(
            f'学習から{model_age_days}日経過しており、モデル鮮度に注意が必要です。'
        )
    if not reliability_warnings:
        reliability_warnings.extend(raw.get('limitations', [])[:1])

    if (
        validation_event_count is not None
        and validation_event_count < MIN_CRASH_PROBABILITY_VALIDATION_EVENTS
    ):
        reliability_label = '低'
        reliability_tone = 'danger'
    elif reliability_warnings:
        reliability_label = '注意'
        reliability_tone = 'warning'
    else:
        reliability_label = '通常'
        reliability_tone = 'good'

    return {
        'model_version': raw.get('model_version'),
        'trained_at': raw.get('trained_at'),
        'model_age_days': model_age_days,
        'prediction_label': raw.get('prediction_label'),
        'current_probability_pct': round(probability * 100, 1) if probability is not None else None,
        'current_probability_display': _pct_probability_display(probability),
        'raw_probability_display': _pct_probability_display(raw_probability),
        'raw_calibration_gap_display': _pct_probability_display(raw_gap),
        'target': raw.get('target'),
        'horizon_days': raw.get('horizon_days'),
        'drawdown_threshold_pct': raw.get('drawdown_threshold_pct'),
        'target_mode': raw.get('target_mode'),
        'target_mode_label': (
            '日次最大ドローダウン'
            if raw.get('target_mode') == 'daily_max_drawdown'
            else '月次 fallback'
            if raw.get('target_mode') == 'monthly_fallback'
            else raw.get('target_mode') or '—'
        ),
        'daily_price_coverage_display': (
            f"{raw.get('daily_price_coverage_pct'):.1f}%"
            if raw.get('daily_price_coverage_pct') is not None else '—'
        ),
        'sample_count': raw.get('sample_count'),
        'event_count': raw.get('event_count'),
        'validation_samples': validation_samples,
        'validation_event_count': validation_event_count,
        'validation_event_rate_display': _pct_probability_display(baseline_rate),
        'validation_event_interval_display': _validation_event_interval_display(
            validation_event_count,
            validation_samples,
        ),
        'roc_auc_display': f'{roc_auc:.2f}' if roc_auc is not None else '—',
        'pr_auc_display': f'{pr_auc:.2f}' if pr_auc is not None else '—',
        'brier_score_display': f'{brier:.2f}' if brier is not None else '—',
        'threshold_10_precision_display': (
            f"{threshold_10.get('precision') * 100:.1f}%"
            if threshold_10.get('precision') is not None else '—'
        ),
        'threshold_10_recall_display': (
            f"{threshold_10.get('recall') * 100:.1f}%"
            if threshold_10.get('recall') is not None else '—'
        ),
        'limitations': raw.get('limitations', []),
        'reliability_label': reliability_label,
        'reliability_tone': reliability_tone,
        'reliability_warnings': reliability_warnings,
    }


WORLD_STATE_SCORE_LABELS = (
    ('growth_score', '成長'),
    ('labor_score', '雇用'),
    ('inflation_score', '物価リスク'),
    ('policy_pressure_score', '政策圧力'),
    ('credit_score', '信用'),
    ('liquidity_score', '流動性'),
    ('risk_appetite_score', 'リスク選好'),
    ('market_trend_score', '市場トレンド'),
    ('market_stress_score', '市場ストレス'),
)


def build_world_state_context() -> Dict:
    snapshot = WorldStateSnapshot.objects.order_by('-as_of_date').first()
    if snapshot is None:
        return {
            'has_snapshot': False,
            'as_of_date': '—',
            'data_quality_display': '—',
            'summary': 'World State はまだ作成されていません。',
            'positive_drivers': [],
            'negative_drivers': ['compute_world_state 実行後に表示されます。'],
            'score_rows': [],
            'warnings': [],
            'model_version': '—',
        }

    explanation = snapshot.explanation or {}
    score_rows = []
    for field, label in WORLD_STATE_SCORE_LABELS:
        value = getattr(snapshot, field, None)
        score_rows.append({
            'field': field,
            'label': label,
            'value': value,
            'display': f'{value:.0f}' if value is not None else '—',
            'bar_pct': int(round(value or 0)),
        })
    return {
        'has_snapshot': True,
        'as_of_date': snapshot.as_of_date.isoformat(),
        'data_quality_display': f'{snapshot.data_quality:.0f}%',
        'summary': explanation.get('summary') or '状態ベクトルを作成済みです。',
        'positive_drivers': explanation.get('positive_drivers') or [],
        'negative_drivers': explanation.get('negative_drivers') or [],
        'score_rows': score_rows,
        'warnings': snapshot.warnings or [],
        'model_version': snapshot.model_version,
    }


def _forecast_prediction_display(snapshot: ForecastSnapshot) -> str:
    unit = (snapshot.metadata or {}).get('unit')
    if unit == '%':
        return format_pct(snapshot.prediction_value)
    if unit:
        return f'{snapshot.prediction_value:+.2f} {unit}'
    if (snapshot.metadata or {}).get('prediction_kind') == 'return_pct':
        return format_pct(snapshot.prediction_value)
    return _number_display(snapshot.prediction_value)


def _latest_validation_reports() -> Dict[tuple, ModelValidationReport]:
    reports = {}
    for report in ModelValidationReport.objects.order_by('-evaluated_at'):
        key = (report.model_version, report.target, report.horizon)
        if key not in reports:
            reports[key] = report
    return reports


def build_forecast_model_context() -> Dict:
    reports = _latest_validation_reports()
    rows = []
    seen = set()
    snapshots = (
        ForecastSnapshot.objects
        .filter(
            model_version__in=[
                'return_lightgbm_v2',
                'macro_forecast_lightgbm_v1',
                'lightgbm_return_v1',
            ],
        )
        .order_by('-as_of_date', '-created_at')
    )
    for snapshot in snapshots:
        key = (snapshot.model_version, snapshot.target, snapshot.horizon)
        if key in seen:
            continue
        seen.add(key)
        metadata = snapshot.metadata or {}
        report = reports.get(key)
        metrics = report.metrics if report else {}
        mae = (
            metadata.get('validation_mae_pct')
            or metadata.get('validation_mae')
            or metrics.get('mae')
        )
        rows.append({
            'target': snapshot.target,
            'horizon': snapshot.horizon,
            'prediction_display': _forecast_prediction_display(snapshot),
            'unit': metadata.get('unit') or ('%' if metadata.get('prediction_kind') == 'return_pct' else '—'),
            'mae_display': _number_display(mae),
            'model_version': snapshot.model_version,
            'trained_at': snapshot.as_of_date.isoformat(),
            'data_quality_display': f"{metadata.get('data_quality', 0):.0f}%",
            'kind_label': '参考値',
            'kind_tone': 'provisional',
        })
    return {
        'rows': rows,
        'has_rows': bool(rows),
    }


def build_model_validation_context() -> Dict:
    rows = []
    for report in ModelValidationReport.objects.order_by('-evaluated_at')[:12]:
        metrics = report.metrics or {}
        rows.append({
            'model_version': report.model_version,
            'target': report.target,
            'horizon': report.horizon,
            'sample_count': report.sample_count,
            'event_count': report.event_count,
            'mae_display': _number_display(metrics.get('mae')),
            'rmse_display': _number_display(metrics.get('rmse')),
            'direction_accuracy_display': _ratio_pct_display(
                metrics.get('direction_accuracy')
            ),
            'roc_auc_display': _number_display(metrics.get('roc_auc')),
            'pr_auc_display': _number_display(metrics.get('pr_auc')),
            'brier_score_display': _number_display(metrics.get('brier_score')),
            'validation_method': (
                metrics.get('validation_method')
                or report.validation_method
            ),
            'warnings': report.warnings or [],
            'evaluated_at': _format_date_for_display(report.evaluated_at),
        })
    return {
        'rows': rows,
        'has_rows': bool(rows),
    }


def load_regime_probability_model() -> Optional[Dict]:
    """景気確率モデルの履歴検証 JSON を読み込む。"""
    path = Path(settings.BASE_DIR) / REGIME_PROBABILITY_MODEL_PATH
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        logger.exception("Regime probability model JSON の読み込みに失敗")
        return None

    metrics = raw.get('metrics') or {}
    event_interval = raw.get('event_rate_interval')
    event_interval_display = '—'
    if event_interval:
        event_interval_display = (
            f'{event_interval[0] * 100:.1f}%〜{event_interval[1] * 100:.1f}%'
        )
    sample_count = raw.get('sample_count') or 0
    event_count = raw.get('event_count') or 0
    warnings = []
    if sample_count < 60:
        warnings.append('検証サンプルが少ないため、景気確率は暫定です。')
    if event_count < 5:
        warnings.append('景気後退イベントが少ないため、確率の振れに注意が必要です。')
    return {
        'model_version': raw.get('model_version'),
        'evaluated_at': raw.get('evaluated_at'),
        'target': raw.get('target'),
        'horizon_months': raw.get('horizon_months'),
        'sample_count': sample_count,
        'event_count': event_count,
        'event_interval_display': event_interval_display,
        'roc_auc_display': (
            f"{metrics.get('roc_auc'):.2f}"
            if metrics.get('roc_auc') is not None else '—'
        ),
        'pr_auc_display': (
            f"{metrics.get('pr_auc'):.2f}"
            if metrics.get('pr_auc') is not None else '—'
        ),
        'brier_score_display': (
            f"{metrics.get('brier_score'):.2f}"
            if metrics.get('brier_score') is not None else '—'
        ),
        'warnings': warnings,
        'tone': 'warning' if warnings else 'good',
    }


def build_monthly_model_status() -> Dict:
    """月次で更新する参考モデルの状態を表示用にまとめる。"""
    backtest = load_crash_alert_backtest()
    probability = load_crash_probability_model()
    lightgbm = load_lightgbm_prediction()
    regime_probability = load_regime_probability_model()

    cards = []
    warnings = []

    if backtest:
        cards.append({
            'label': '急落警戒スコア検証',
            'updated_at': backtest.get('updated_at') or '—',
            'sample_label': (
                f"検証 {backtest.get('sample_count') or '—'}件 / "
                f"イベント {backtest.get('event_count') or '—'}件"
            ),
            'metric_label': (
                f"ROC-AUC {backtest.get('roc_auc_display')} / "
                f"PR-AUC {backtest.get('pr_auc_display')}"
            ),
            'model_label': (
                f"{backtest.get('target') or '—'} "
                f"{backtest.get('horizon_days') or '—'}日 "
                f"{backtest.get('drawdown_threshold_pct') or '—'}%"
            ),
        })
    else:
        warnings.append('急落警戒スコアの月次検証ファイルがありません。')

    if probability:
        reliability_label = probability.get('reliability_label') or '—'
        cards.append({
            'label': '急落確率モデル',
            'updated_at': probability.get('trained_at') or '—',
            'sample_label': (
                f"検証 {probability.get('validation_samples') or '—'}件 / "
                f"イベント {probability.get('validation_event_count') or '—'}件"
            ),
            'metric_label': (
                f"ROC-AUC {probability.get('roc_auc_display')} / "
                f"PR-AUC {probability.get('pr_auc_display')} / "
                f"信頼性 {reliability_label}"
            ),
            'model_label': probability.get('model_version') or '—',
        })
        if probability.get('reliability_tone') != 'good':
            warnings.extend(
                f"急落確率モデル: {warning}"
                for warning in probability.get('reliability_warnings', [])[:2]
            )
    else:
        warnings.append('急落確率モデルの学習結果ファイルがありません。')

    if lightgbm:
        mae_labels = [
            f"{h.get('months')}ヶ月 {h.get('validation_mae_display')}"
            for h in lightgbm.get('horizons', [])
            if h.get('months') and h.get('validation_mae_display')
        ]
        cards.append({
            'label': 'リターン参考予測',
            'updated_at': lightgbm.get('predicted_at') or '—',
            'sample_label': (
                f"学習 {lightgbm.get('training_samples') or '—'}件 / "
                f"特徴量 {lightgbm.get('feature_count') or '—'}"
            ),
            'metric_label': '検証誤差 ' + ' / '.join(mae_labels) if mae_labels else '検証誤差 —',
            'model_label': lightgbm.get('model_version') or '—',
        })
    else:
        warnings.append('LightGBM参考予測の学習結果ファイルがありません。')

    if regime_probability:
        cards.append({
            'label': '景気確率モデル',
            'updated_at': regime_probability.get('evaluated_at') or '—',
            'sample_label': (
                f"検証 {regime_probability.get('sample_count') or '—'}件 / "
                f"後退 {regime_probability.get('event_count') or '—'}件"
            ),
            'metric_label': (
                f"ROC-AUC {regime_probability.get('roc_auc_display')} / "
                f"PR-AUC {regime_probability.get('pr_auc_display')} / "
                f"Brier {regime_probability.get('brier_score_display')}"
            ),
            'model_label': regime_probability.get('model_version') or '—',
        })
        warnings.extend(
            f"景気確率モデル: {warning}"
            for warning in regime_probability.get('warnings', [])[:2]
        )
    else:
        warnings.append('景気確率モデルの検証ファイルがありません。')

    latest_training_candidates = [
        value for value in (
            probability.get('trained_at') if probability else None,
            lightgbm.get('predicted_at') if lightgbm else None,
            regime_probability.get('evaluated_at') if regime_probability else None,
        )
        if value
    ]
    latest_training_date = max(latest_training_candidates) if latest_training_candidates else '—'
    latest_backtest_date = backtest.get('updated_at') if backtest else '—'

    return {
        'tone': 'warning' if warnings else 'good',
        'status_label': '要確認' if warnings else '更新済み',
        'latest_training_date': latest_training_date,
        'latest_backtest_date': latest_backtest_date,
        'cards': cards,
        'warnings': warnings,
        'has_any': bool(cards),
    }


def build_forecast_monitor_context() -> Dict:
    from .forecast_tracking import build_forecast_monitor_context as _build
    return _build()


def build_world_model_operations_context() -> Dict:
    from .operations import build_operations_context
    return build_operations_context()
