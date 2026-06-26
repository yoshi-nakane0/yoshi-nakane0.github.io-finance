"""macro トップ画面のコンテキスト構築。

views.py を薄く保つために集約。
重い計算（類似度・連動分析）はキャッシュして同一日内の再計算を避ける。
"""

import json
import logging
import re
from calendar import monthrange
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
    MacroForecastOutcome,
    MacroForecastRun,
    ModelValidationReport,
    Observation,
    PriceObservation,
    RegimeSnapshot,
    VintageObservation,
    WorldStateSnapshot,
)
from .crash_alert import FRESHNESS_LIMIT_DAYS, compute_crash_alert
from .data_quality import build_data_quality_report
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
TOP_MACRO_SERIES = {
    'PCEPILFE',
    'UNRATE',
    'PAYEMS',
    'INDPRO',
    'T10Y2Y',
    'T10Y3M',
    'DGS10',
    'DGS2',
    'DEXJPUS',
    'VIXCLS',
    'BAMLH0A0HYM2',
}


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
                'frequency': ind.frequency,
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
            'frequency': ind.frequency,
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


def build_top_indicator_cards() -> List[Dict]:
    """トップ画面では判断に使う代表指標だけを返す。"""
    return [
        card for card in build_indicator_cards()
        if card.get('series_id') in TOP_MACRO_SERIES
    ]


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
        age_days = _freshness_age_days(latest_date, indicator.frequency, today)
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


def _freshness_reference_date(observation_date: date, frequency: Optional[str]) -> date:
    if frequency == Indicator.Frequency.MONTHLY:
        return observation_date.replace(
            day=monthrange(observation_date.year, observation_date.month)[1],
        )
    if frequency == Indicator.Frequency.QUARTERLY:
        quarter_end_month = ((observation_date.month - 1) // 3 + 1) * 3
        return date(
            observation_date.year,
            quarter_end_month,
            monthrange(observation_date.year, quarter_end_month)[1],
        )
    return observation_date


def _freshness_age_days(
    observation_date: date,
    frequency: Optional[str],
    today: date,
) -> int:
    reference_date = _freshness_reference_date(observation_date, frequency)
    return max((today - reference_date).days, 0)


def _coerce_date(value) -> Optional[date]:
    if value in (None, ''):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _static_indicator_freshness(cards: List[Dict]) -> Optional[Dict]:
    if not cards:
        return None

    today = timezone.localdate()
    missing = []
    stale = []
    for card in cards:
        series_id = card.get('series_id') or ''
        name = card.get('name_ja') or card.get('name') or series_id or '指標'
        latest_date = _coerce_date(card.get('latest_date'))
        if not card.get('has_data') or latest_date is None:
            missing.append({
                'series_id': series_id,
                'name': name,
                'label': f'{name}（{series_id}）' if series_id else name,
            })
            continue

        frequency = card.get('frequency')
        limit_days = FRESHNESS_LIMIT_DAYS.get(frequency)
        age_days = _freshness_age_days(latest_date, frequency, today)
        if limit_days is not None and age_days > limit_days:
            stale.append({
                'series_id': series_id,
                'name': name,
                'label': (
                    f'{name}（{latest_date.isoformat()} / '
                    f'{age_days}日経過）'
                ),
                'age_days': age_days,
            })

    total = len(cards)
    fresh_count = max(total - len(missing) - len(stale), 0)
    freshness_pct = round(fresh_count / total * 100) if total else 0
    return {
        'total_count': total,
        'fresh_count': fresh_count,
        'missing': missing,
        'stale': stale,
        'freshness_pct': freshness_pct,
    }


def _reliability_context_from_freshness(
    *,
    freshness: Dict,
    last_updated=None,
    dashboard_cache_meta: Optional[Dict] = None,
    update_status: Optional[Dict] = None,
    regime_model_version: Optional[str] = None,
) -> Dict:
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


def build_reliability_context(
    *,
    last_updated=None,
    dashboard_cache_meta: Optional[Dict] = None,
    update_status: Optional[Dict] = None,
    regime_model_version: Optional[str] = None,
) -> Dict:
    freshness = _active_indicator_freshness()
    return _reliability_context_from_freshness(
        freshness=freshness,
        last_updated=last_updated,
        dashboard_cache_meta=dashboard_cache_meta,
        update_status=update_status,
        regime_model_version=regime_model_version,
    )


def build_static_reliability_context(
    payload: Dict,
    *,
    operations_status: Optional[Dict] = None,
    regime_model_version: Optional[str] = None,
) -> Optional[Dict]:
    cards = payload.get('audit_indicator_cards') or payload.get('indicator_cards') or []
    freshness = _static_indicator_freshness(cards)
    if freshness is None:
        return None
    return _reliability_context_from_freshness(
        freshness=freshness,
        last_updated=payload.get('last_updated'),
        dashboard_cache_meta={'computed_at': payload.get('generated_at')},
        update_status=(operations_status or {}).get('latest_update_status'),
        regime_model_version=regime_model_version or payload.get('regime_model_version'),
    )


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
    is_large = total >= 300_000
    archive_recommended = total >= 500_000
    is_stale = latest is None or latest < timezone.now() - timedelta(days=7)
    tone = 'warning' if is_large or is_stale else 'good'
    if total == 0:
        tone = 'warning'
    return {
        'tone': tone,
        'total_count': total,
        'series_count': series_count,
        'latest_collected_at': _format_date_for_display(latest),
        'status_label': '蓄積済み' if total else '未蓄積',
        'is_large': is_large,
        'is_stale': is_stale,
        'archive_recommended': archive_recommended,
        'note': (
            'FREDの改定前データを蓄積しており、point-in-time検証に使えます。'
            if total else
            '次回のFRED更新から、取得時点ごとの値を保存します。'
        ),
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
        regime_rows = []
        risk_rows = []
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
            'regime_condition_bar_left_label': '悪',
            'regime_condition_bar_right_label': '良',
            'regime_update_guidance': _regime_update_guidance(),
            'rule_strength_pct': 0,
            'rule_strength_score': 0,
            'rule_strength_fraction_display': '—/5',
            'rule_strength_bar_left_label': '弱',
            'rule_strength_bar_right_label': '強',
            'data_quality_pct': 0,
            'data_quality_score': 0,
            'data_quality_fraction_display': '—/5',
            'data_quality_bar_left_label': '古',
            'data_quality_bar_right_label': '新',
            'regime_evidence': [],
            'regime_warnings': ['判定に必要なデータがまだ揃っていません。'],
            'regime_model_version': '—',
            'snapshot_date': None,
            'regime_probability_rows': regime_rows,
            'risk_probability_rows': risk_rows,
            'regime_state_sections': _regime_state_sections(
                regime_rows,
                risk_rows,
                [],
            ),
        }
    rule_strength = getattr(snapshot, 'rule_strength', None)
    if rule_strength is None:
        rule_strength = getattr(snapshot, 'confidence', 0)
    formatted_evidence = _format_regime_evidence(
        getattr(snapshot, 'evidence', []) or []
    )
    summary = _regime_summary(
        snapshot.regime_label,
        snapshot.inflation_flag,
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
    regime_rows = _regime_probability_rows(
        getattr(snapshot, 'regime_probabilities', {}) or {}
    )
    risk_rows = _risk_probability_rows(
        getattr(snapshot, 'risk_probabilities', {}) or {}
    )
    material_rows = _regime_evidence_groups(formatted_evidence)
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
        'rule_strength_bar_left_label': '弱',
        'rule_strength_bar_right_label': '強',
        'data_quality_pct': data_quality,
        'data_quality_score': _five_point_from_pct(data_quality),
        'data_quality_fraction_display': f'{_five_point_from_pct(data_quality)}/5',
        'data_quality_bar_left_label': '古',
        'data_quality_bar_right_label': '新',
        'regime_evidence': formatted_evidence,
        'regime_warnings': getattr(snapshot, 'warnings', []) or [],
        'regime_model_version': getattr(snapshot, 'model_version', 'regime_v1'),
        'snapshot_date': snapshot.snapshot_date,
        'regime_probability_rows': regime_rows,
        'risk_probability_rows': risk_rows,
        'regime_state_sections': _regime_state_sections(
            regime_rows,
            risk_rows,
            material_rows,
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
            'kind': 'meter',
            'key': key,
            'label': RegimeSnapshot.Label(key).label,
            'pct': pct,
            'display': f'{pct}%',
            'tone': 'positive' if key in (
                RegimeSnapshot.Label.EXPANSION,
                RegimeSnapshot.Label.RECOVERY,
            ) else 'negative',
            'badge_label': '良い' if key in (
                RegimeSnapshot.Label.EXPANSION,
                RegimeSnapshot.Label.RECOVERY,
            ) else '悪い',
            'badge_tone': 'positive' if key in (
                RegimeSnapshot.Label.EXPANSION,
                RegimeSnapshot.Label.RECOVERY,
            ) else 'negative',
        })
    return rows


def _risk_badge_from_pct(pct: int) -> Dict[str, str]:
    if pct >= 35:
        return {'badge_label': '悪い', 'badge_tone': 'negative'}
    return {'badge_label': '良い', 'badge_tone': 'positive'}


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
        badge = _risk_badge_from_pct(pct)
        rows.append({
            'kind': 'meter',
            'key': key,
            'label': label,
            'pct': pct,
            'display': f'{pct}%',
            'tone': badge['badge_tone'],
            **badge,
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
            'category': item.get('category') or '',
            'metric': item.get('metric', ''),
            'value_display': format_value(value, unit),
            'unit': unit,
            'observation_date': item.get('observation_date') or '—',
            'signal': item.get('signal', '—'),
            'contribution': contribution,
            'contribution_display': format_signed(contribution, 2),
            'is_negative': (contribution or 0) < 0,
        })
    return rows


def _regime_state_sections(
    regime_rows: List[Dict],
    risk_rows: List[Dict],
    material_rows: List[Dict],
) -> List[Dict]:
    return [
        {
            'key': 'direction',
            'label': '景気の向き',
            'note': '現在の指標が各局面にどれだけ近いか',
            'rows': regime_rows,
        },
        {
            'key': 'risk',
            'label': '注意リスク',
            'note': '悪化・過熱・市場ストレスの強さ',
            'rows': risk_rows,
        },
        {
            'key': 'materials',
            'label': '判断材料',
            'note': '判定に使った指標を同じ分類で整理',
            'rows': material_rows,
        },
    ]


_MATERIAL_GROUPS = (
    ('growth', '成長', {'GDPC1'}),
    ('labor', '雇用', {'UNRATE', 'PAYEMS', 'JTSJOL'}),
    ('production', '生産', {'INDPRO', 'TCU'}),
    ('inflation', '物価', {'PCEPILFE', 'PCEPI', 'CPIAUCSL', 'CPILFESL', 'T5YIE'}),
    ('consumption', '消費', {'RSAFS', 'UMCSENT'}),
)


def _material_group_key(row: Dict) -> Optional[str]:
    series_id = row.get('series_id')
    for key, _label, series_ids in _MATERIAL_GROUPS:
        if series_id in series_ids:
            return key
    category = row.get('category')
    if category == 'labor':
        return 'labor'
    if category == 'inflation':
        return 'inflation'
    if category == 'growth':
        return 'growth'
    return None


def _regime_evidence_groups(evidence: List[Dict]) -> List[Dict]:
    by_group: Dict[str, Dict[str, Dict]] = defaultdict(dict)
    for row in evidence:
        group_key = _material_group_key(row)
        if not group_key:
            continue
        series_id = row.get('series_id') or row.get('name')
        current = by_group[group_key].get(series_id)
        current_abs = abs(float(current.get('contribution') or 0.0)) if current else -1
        row_abs = abs(float(row.get('contribution') or 0.0))
        if current is None or row_abs > current_abs:
            by_group[group_key][series_id] = row

    rows = []
    label_by_key = {key: label for key, label, _series_ids in _MATERIAL_GROUPS}
    for key, label, _series_ids in _MATERIAL_GROUPS:
        group_rows = list(by_group.get(key, {}).values())
        if not group_rows:
            continue
        group_rows.sort(
            key=lambda item: abs(float(item.get('contribution') or 0.0)),
            reverse=True,
        )
        primary = group_rows[0]
        is_negative = bool(primary.get('is_negative'))
        unit = f" {primary['unit']}" if primary.get('unit') else ''
        rows.append({
            'kind': 'material',
            'key': key,
            'label': label_by_key[key],
            'tone': 'negative' if is_negative else 'positive',
            'badge_label': '悪い' if is_negative else '良い',
            'badge_tone': 'negative' if is_negative else 'positive',
            'primary_name': primary.get('name') or '—',
            'signal': primary.get('signal') or '—',
            'metric_label': primary.get('metric') or '最新値',
            'value_display': f"{primary.get('value_display') or '—'}{unit}",
            'indicator_count': len(group_rows),
            'summary': _regime_evidence_consequence(primary),
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
            'regime_condition_bar_left_label': '悪',
            'regime_condition_bar_right_label': '良',
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
        'regime_condition_bar_left_label': '悪',
        'regime_condition_bar_right_label': '良',
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
        consequence = _regime_evidence_consequence(row)
        points.append(
            f"{row['name']}が{row['signal']}（{row['metric']} {row['value_display']}{unit}）。{consequence}"
        )
    if points:
        return points
    return ['目立った支援材料はまだ少ないです。'] if positive else ['大きな悪材料は限定的です。']


def _regime_evidence_consequence(row: Dict) -> str:
    series_id = row.get('series_id')
    name = row.get('name') or ''
    is_negative = row.get('is_negative')
    if series_id == 'GDPC1' or 'GDP' in name:
        return (
            '需要が強く、企業売上や雇用を支えやすいです。'
            if not is_negative else
            '需要が弱く、企業売上や雇用の重しになりやすいです。'
        )
    if (
        series_id in ('CPIAUCSL', 'CPILFESL', 'PCEPI', 'PCEPILFE')
        or 'CPI' in name
        or '物価' in name
    ):
        return (
            '物価が落ち着けば、金利や消費への圧力が弱まりやすいです。'
            if not is_negative else
            '金利が下がりにくく、消費や株価の重しになりやすいです。'
        )
    if series_id == 'INDPRO' or '生産' in name:
        return (
            '企業活動が強く、売上や雇用を支えやすいです。'
            if not is_negative else
            '在庫や企業利益の重しになり、景気の勢いが落ちやすいです。'
        )
    if (
        series_id in ('UNRATE', 'PAYEMS', 'JTSJOL')
        or '雇用' in name
        or '失業' in name
    ):
        return (
            '家計収入が支えられ、消費が崩れにくくなります。'
            if not is_negative else
            '家計収入が弱まり、消費が鈍りやすくなります。'
        )
    if (
        series_id in ('T10Y2Y', 'T10Y3M', 'BAMLH0A0HYM2', 'VIXCLS')
        or '金利' in name
        or 'VIX' in name
    ):
        return (
            '資金調達や市場心理が落ち着き、投資家心理を支えやすいです。'
            if not is_negative else
            '資金調達や市場心理が悪化し、株価の重しになりやすいです。'
        )
    return (
        '景気判断を支える材料です。'
        if not is_negative else
        '景気判断の注意材料です。'
    )


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


def _regime_summary(
    regime_label: str,
    inflation_flag: str,
) -> Dict[str, str]:
    labels = RegimeSnapshot.Label
    flags = RegimeSnapshot.InflationFlag
    summary_map = {
        (labels.EXPANSION, flags.HIGH): ('拡大寄り・物価高止まり', '過熱気味'),
        (labels.EXPANSION, flags.EASING): ('拡大寄り・物価鈍化', '良好'),
        (labels.EXPANSION, flags.NORMAL): ('拡大寄り・物価安定', '良好'),
        (labels.SLOWDOWN, flags.HIGH): ('減速寄り・物価高止まり', '警戒'),
        (labels.SLOWDOWN, flags.EASING): ('減速寄り・物価鈍化', '様子見'),
        (labels.SLOWDOWN, flags.NORMAL): ('減速寄り・物価安定', '様子見'),
        (labels.CONTRACTION, flags.HIGH): ('縮小寄り・物価高止まり', '要警戒'),
        (labels.CONTRACTION, flags.EASING): ('縮小寄り・物価鈍化', '底探り'),
        (labels.CONTRACTION, flags.NORMAL): ('縮小寄り・物価安定', '底探り'),
        (labels.RECOVERY, flags.HIGH): ('回復寄り・物価高止まり', '回復途上'),
        (labels.RECOVERY, flags.EASING): ('回復寄り・物価鈍化', '改善'),
        (labels.RECOVERY, flags.NORMAL): ('回復寄り・物価安定', '改善'),
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


def _extract_policy_pressure(world: Optional[Dict], policy: Optional[Dict] = None) -> Dict:
    policy = policy or {}
    world = world or {}
    score_row = next(
        (
            row for row in world.get('score_rows', [])
            if row.get('field') == 'policy_pressure_score'
        ),
        {},
    )
    rows = policy.get('rows') or []
    return {
        'label': policy.get('bias_label') or '政策圧力',
        'summary': (
            policy.get('summary')
            or '政策金利見通し・米金利・期待インフレをまとめて確認します。'
        ),
        'score_display': score_row.get('display') or '—',
        'data_quality_display': policy.get('data_quality_display') or world.get('data_quality_display') or '—',
        'tone': policy.get('tone') or 'warning',
        'rows': rows[:5],
        'alerts': (policy.get('alerts') or [])[:2],
    }


def _summarize_market_stress(crash: Optional[Dict]) -> Dict:
    if not crash:
        return {
            'score': None,
            'score_display': '—',
            'level_label': '未計算',
            'data_quality_display': '—',
            'abnormal_items': [],
            'summary': '市場ストレスはまだ計算されていません。',
        }

    abnormal_items = []
    for component in crash.get('components', []):
        score = component.get('score')
        if (
            component.get('is_missing')
            or component.get('is_stale')
            or (isinstance(score, (int, float)) and score >= 70)
        ):
            abnormal_items.append(component.get('label') or component.get('series_id'))

    score = crash.get('total_score')
    return {
        'score': score,
        'score_display': f'{score}/100' if score is not None else '—',
        'level': crash.get('level'),
        'level_label': crash.get('level_label') or '—',
        'data_quality_display': f"{crash.get('data_quality_pct', 0)}%",
        'abnormal_items': [item for item in abnormal_items if item][:5],
        'summary': (
            '急落確率ではなく、現在の市場の緊張度です。'
        ),
    }


def _build_decision_confidence(
    regime: Dict,
    reliability: Dict,
    crash: Optional[Dict],
    quality_report: Optional[Dict] = None,
) -> Dict:
    data_quality = regime.get('data_quality_pct') or 0
    rule_strength = regime.get('rule_strength_pct') or 0
    crash_quality = (crash or {}).get('data_quality_pct') or 0
    failed_count = reliability.get('failed_count') or 0
    missing_count = reliability.get('missing_count') or 0
    base_score = round((data_quality + rule_strength + crash_quality) / 3)
    if failed_count:
        base_score -= 10
    if missing_count:
        base_score -= min(20, missing_count * 2)
    base_score = max(min(base_score, 100), 0)

    if base_score >= 85:
        grade = 'A'
        label = '高い'
    elif base_score >= 70:
        grade = 'B'
        label = '通常'
    elif base_score >= 50:
        grade = 'C'
        label = '注意'
    else:
        grade = 'D'
        label = '低い'

    notes = []
    if failed_count:
        notes.append(f'前回更新で{failed_count}件の失敗があります。')
    if missing_count:
        notes.append(f'未取得の指標が{missing_count}件あります。')
    if not notes:
        notes.append('主要データの取得状況と判定材料は確認済みです。')

    quality_report = quality_report or {}
    cap = quality_report.get('confidence_cap')
    grade_order = {'A': 4, 'B': 3, 'C': 2, 'D': 1}
    if cap and grade_order.get(grade, 1) > grade_order.get(cap, 1):
        grade = cap
        label = {
            'A': '高い',
            'B': '通常',
            'C': '注意',
            'D': '低い',
        }.get(grade, label)
        if grade == 'C':
            base_score = min(base_score, 69)
        elif grade == 'D':
            base_score = min(base_score, 49)
    for issue in quality_report.get('blocking_issues') or []:
        if issue not in notes:
            notes.append(issue)
    for warning in quality_report.get('warnings') or []:
        if warning not in notes:
            notes.append(warning)

    return {
        'grade': grade,
        'label': label,
        'score': base_score,
        'score_display': f'{base_score}%',
        'notes': notes,
        'data_freshness_pct': reliability.get('data_freshness_pct', 0),
        'sample_note': 'モデル予測は検証条件を満たすものだけ参考表示します。',
    }


def build_macro_decision_context(snapshot: Optional[RegimeSnapshot]) -> Dict:
    """トップ画面用に、判断に必要な要素だけを集約する。"""
    regime = build_regime_context(snapshot)
    reliability = build_reliability_context(
        last_updated=regime.get('snapshot_date'),
        regime_model_version=regime.get('regime_model_version'),
    )
    crash = build_crash_alert_context()
    world = build_world_state_context()
    quality_report = build_data_quality_report()
    try:
        from .policy_expectation import build_policy_expectation_context
        policy = build_policy_expectation_context()
    except Exception:
        logger.exception("Policy expectation context failed")
        policy = {}

    return {
        'headline': regime['regime_plain_judgment'],
        'detail': regime['regime_plain_detail'],
        'good_points': regime['regime_good_points'][:3],
        'bad_points': regime['regime_bad_points'][:3],
        'policy_pressure': _extract_policy_pressure(world, policy),
        'market_stress': _summarize_market_stress(crash),
        'confidence': _build_decision_confidence(
            regime,
            reliability,
            crash,
            quality_report,
        ),
    }


AXIS_SUMMARY_LABELS = {
    'growth_momentum': '景気',
    'inflation_pressure': '物価',
    'financial_conditions': '政策・金利',
    'nikkei_macro_bias': '日経影響',
}
HOUSE_VIEW_DIRECTION_LABELS = {
    'expansion_with_inflation_risk': '改善（物価警戒）',
    'inflation_risk': '中立（物価警戒）',
    'expansion': '改善',
    'slowdown': '弱含み',
    'contraction': '悪化',
    'recovery': '回復',
    'unknown': 'データ確認中',
}
SCENARIO_ORDER = {
    'baseline': 0,
    'upside': 1,
    'downside': 2,
}


def _extract_nikkei_impact(text: str, scenarios: List[Dict]) -> str:
    match = re.search(r'macroバイアス[は:：]\s*([^。]+)', text or '')
    if match:
        return match.group(1).strip()
    baseline = next(
        (item for item in scenarios if item.get('name_key') == 'baseline'),
        {},
    )
    return baseline.get('nikkei_bias') or '中立'


def _compact_direction(house_view: Dict, forecast: Dict, decision: Dict) -> str:
    regime_label = house_view.get('regime_label')
    if regime_label in HOUSE_VIEW_DIRECTION_LABELS:
        return HOUSE_VIEW_DIRECTION_LABELS[regime_label]

    texts = ' '.join(
        str(value or '')
        for value in (
            forecast.get('headline'),
            house_view.get('house_view'),
            decision.get('headline'),
        )
    )
    if '改善' in texts and ('中立' in texts or '弱' not in texts):
        return '中立〜改善'
    if '弱' in texts or '悪化' in texts:
        return '弱含み'
    if '改善' in texts or '拡大' in texts:
        return '改善'
    if '中立' in texts:
        return '中立'
    return decision.get('headline') or forecast.get('headline') or 'データ確認中'


def _format_data_quality(decision: Dict, house_view: Dict) -> str:
    confidence = decision.get('confidence') or {}
    grade = house_view.get('confidence_grade') or confidence.get('grade') or '—'
    score = None
    if house_view.get('confidence_score') is not None:
        score = f"{house_view.get('confidence_score')}%"
    if not score:
        score = confidence.get('score_display')
    return f'{grade} / {score or "—"}'


def _macro_current_score_display(context: Dict) -> str:
    world_scores = _world_score_map(context.get('world_state') or {})
    positive_fields = (
        'growth_score',
        'labor_score',
        'credit_score',
        'liquidity_score',
        'risk_appetite_score',
        'market_trend_score',
    )
    drag_fields = (
        'inflation_score',
        'policy_pressure_score',
        'market_stress_score',
    )
    score_inputs = [
        world_scores[field]
        for field in positive_fields
        if world_scores.get(field) is not None
    ]
    score_inputs.extend(
        100 - world_scores[field]
        for field in drag_fields
        if world_scores.get(field) is not None
    )
    if not score_inputs:
        return 'Macro現状スコア —'
    score = round(sum(score_inputs) / len(score_inputs))
    score = max(0, min(100, score))
    return f'Macro現状スコア {score}%'


def _top_data_freshness_display(context: Dict, confidence: Dict) -> str:
    quality = context.get('data_quality_report') or {}
    freshness_score = quality.get('freshness_score')
    if freshness_score is not None:
        return f'{freshness_score:.0f}%'
    if confidence.get('data_freshness_pct') is not None:
        return f"{confidence.get('data_freshness_pct')}%"
    return '—'


def _top_validation_reliability(context: Dict, house_view: Dict, decision: Dict) -> Dict:
    validation = context.get('house_view_validation') or {}
    base = {
        'data_quality': _format_data_quality(decision, house_view),
    }
    sections = validation.get('accuracy_sections') or {}
    pseudo_live = sections.get('pseudo_live') or {}
    short_term_live = sections.get('short_term_live') or {}
    operation_health = validation.get('operation_health') or {}
    supplemental = {}
    if operation_health:
        supplemental['operation_check'] = (
            f"短期確認 {operation_health.get('status_label') or '—'} / "
            f"保存 {operation_health.get('saved_forecast_count') or 0}件"
        )
    if pseudo_live:
        pseudo_sample_count = pseudo_live.get('sample_count') or 0
        pseudo_hit_rate = pseudo_live.get('hit_rate')
        if pseudo_sample_count and pseudo_hit_rate is not None:
            supplemental['pseudo_live'] = f'疑似Live {pseudo_sample_count}件 / 的中 {pseudo_hit_rate:.0%}'
        else:
            supplemental['pseudo_live'] = '疑似Live 未生成'
    if short_term_live:
        short_sample_count = short_term_live.get('sample_count') or 0
        short_hit_rate = short_term_live.get('hit_rate')
        short_pending_count = short_term_live.get('pending_count') or 0
        if short_sample_count and short_hit_rate is not None:
            supplemental['short_term_live'] = (
                f'短期Live {short_sample_count}件 / 的中 {short_hit_rate:.0%} / '
                f'待ち {short_pending_count}件'
            )
        else:
            supplemental['short_term_live'] = f'短期Live 未評価 / 待ち {short_pending_count}件'

    provided = validation.get('reliability') or {}
    if provided:
        return {
            **base,
            **provided,
            **supplemental,
            'display_status': _top_reliability_display_status(
                house_view,
                provided.get('display_status') or '表示可',
                sample_count=None,
            ),
        }

    live = sections.get('live') or {}
    sample_count = live.get('sample_count') or validation.get('sample_count') or 0
    hit_count = live.get('hit_count') or validation.get('hit_count') or 0
    hit_rate = live.get('hit_rate') if live.get('hit_rate') is not None else validation.get('hit_rate')

    if sample_count <= 0:
        model_validation = 'C / 検証不足'
        live_record = 'Live実績 未評価'
    elif sample_count < 10:
        model_validation = 'C / 暫定'
        live_record = f'Live実績 {sample_count}件 / 的中 {hit_count}件'
    else:
        grade = 'A' if (hit_rate or 0) >= 0.65 else 'B' if (hit_rate or 0) >= 0.55 else 'C'
        model_validation = f'{grade} / {hit_rate:.0%}' if hit_rate is not None else f'{grade} / —'
        live_record = f'Live実績 {sample_count}件 / 的中 {hit_count}件'

    display_status = _top_reliability_display_status(
        house_view,
        '表示可',
        sample_count=sample_count,
    )

    return {
        'data_quality': base['data_quality'],
        'model_validation': model_validation,
        'live_record': live_record,
        'display_status': display_status,
        'confidence_limit_reasons': house_view.get('confidence_limit_reasons') or [],
        **supplemental,
    }


def _top_reliability_display_status(
    house_view: Dict,
    current_status: str,
    *,
    sample_count: Optional[int],
) -> str:
    house_display_status = house_view.get('display_status') or house_view.get('publish_status')
    if house_display_status in {'reference', 'hidden', 'blocked'}:
        return {
            'reference': '参考',
            'hidden': '非表示',
            'blocked': '使用不可',
        }[house_display_status]
    if not house_view.get('display_allowed', True):
        return '参考'
    if sample_count is not None and sample_count < 10:
        return '参考'
    return current_status or '表示可'


def _top_invalidation_triggers(house_view: Dict) -> List[Dict]:
    rows = []
    for trigger in house_view.get('invalidation_triggers') or []:
        parts = re.split(r'[:：]', str(trigger), maxsplit=1)
        if len(parts) == 2:
            rows.append({'label': parts[0].strip(), 'detail': parts[1].strip()})
        else:
            rows.append({'label': '条件', 'detail': str(trigger)})
    return rows[:4]


def _scenario_reason(item: Dict) -> str:
    drivers = item.get('key_drivers') or []
    if drivers:
        return drivers[0]
    for key in ('growth_view', 'inflation_view', 'policy_view', 'market_view'):
        if item.get(key):
            return item[key]
    return '詳細は監査ページで確認してください。'


def _top_scenarios(forecast: Dict) -> List[Dict]:
    scenarios = []
    for item in forecast.get('scenarios') or []:
        scenarios.append({
            'name': item.get('name') or 'シナリオ',
            'name_key': item.get('name_key') or '',
            'probability_display': item.get('probability_display') or '—',
            'nikkei_bias': item.get('nikkei_bias') or '中立',
            'reason': _scenario_reason(item),
        })
    scenarios.sort(key=lambda item: SCENARIO_ORDER.get(item['name_key'], 99))
    return scenarios[:3]


def _compact_material_points(points: List[str], *, negative: bool) -> List[str]:
    compacted = []
    text = ' '.join(points)
    if negative and re.search(r'PCE|CPI|インフレ|物価', text, re.IGNORECASE):
        compacted.append('インフレ再加速リスクが高い')
    if negative and re.search(r'金利|利回り|10年|2年', text):
        compacted.append('米金利上昇が株価バリュエーションを圧迫')
    if negative and re.search(r'日経|日本|外部|逆風|景気サイクル', text):
        compacted.append('日本側の景気サイクルが弱い、または日経への追い風が限定的')

    for point in points:
        if point and point not in compacted:
            compacted.append(point)
        if len(compacted) >= 3:
            break
    return compacted[:3]


def _top_axis_summary(forecast: Dict) -> List[Dict]:
    rows = []
    seen = set()
    for axis in forecast.get('axes') or []:
        key = axis.get('key')
        if key not in AXIS_SUMMARY_LABELS or key in seen:
            continue
        seen.add(key)
        rows.append({
            'label': AXIS_SUMMARY_LABELS[key],
            'value': axis.get('label') or axis.get('score_display') or '—',
            'score_display': axis.get('score_display') or '—',
        })
    return rows


def _split_top_material_points(points: List[str], *, negative: bool) -> Dict[str, List[str]]:
    visible = _compact_material_points(points, negative=negative)
    detail = []
    for point in points or []:
        if point and point not in visible and point not in detail:
            detail.append(point)
    return {
        'visible': visible[:3],
        'detail': detail,
    }


def build_top_decision_context(context: Dict) -> Dict:
    """macroトップで使う、1つに統合した最終判断コンテキストを作る。"""
    house_view = context.get('house_view') or {}
    forecast = context.get('macro_forecast_report') or {}
    decision = context.get('macro_decision') or {}
    confidence = decision.get('confidence') or {}
    scenarios = _top_scenarios(forecast)
    nikkei_impact = _extract_nikkei_impact(
        forecast.get('nikkei_implication') or '',
        scenarios,
    )
    good_materials = _split_top_material_points(
        decision.get('good_points') or house_view.get('key_drivers') or [],
        negative=False,
    )
    bad_materials = _split_top_material_points(
        decision.get('bad_points') or house_view.get('main_risks') or [],
        negative=True,
    )
    risk_candidates = bad_materials['visible']
    reliability = _top_validation_reliability(context, house_view, decision)
    economic_view = _top_economic_view(
        context=context,
        direction=_compact_direction(house_view, forecast, decision),
        nikkei_impact=nikkei_impact,
        support_materials=good_materials['visible'],
        drag_materials=risk_candidates,
    )
    macro_current_score = _macro_current_score_display(context)

    return {
        'final_judgment': {
            'direction': _compact_direction(house_view, forecast, decision),
            'nikkei_impact': nikkei_impact,
            'max_risk': '・'.join(risk_candidates[:2]) if risk_candidates else '主要リスクを確認中',
            'summary': (
                house_view.get('house_view')
                or decision.get('detail')
                or forecast.get('judgment')
                or '主要データの更新後に最終判断を表示します。'
            ),
            'confidence': macro_current_score,
        },
        'nikkei': {
            'bias': nikkei_impact,
        },
        'economic_view': economic_view,
        'invalidation_triggers': _top_invalidation_triggers(house_view),
        'scenarios': scenarios,
        'axis_summary': _top_axis_summary(forecast),
        'good_points': good_materials['visible'],
        'good_points_detail': good_materials['detail'],
        'bad_points': risk_candidates,
        'bad_points_detail': bad_materials['detail'],
        'policy_pressure': decision.get('policy_pressure') or {},
        'market_stress': decision.get('market_stress') or {},
        'freshness': {
            'confidence': macro_current_score,
            'data_freshness': _top_data_freshness_display(context, confidence),
            'updated_at': context.get('last_updated') or context.get('generated_at') or '—',
        },
        'reliability': reliability,
        'driver_cards': (context.get('top_driver_cards') or context.get('indicator_cards') or [])[:5],
    }


def _top_economic_view(
    *,
    context: Dict,
    direction: str,
    nikkei_impact: str,
    support_materials: List[str],
    drag_materials: List[str],
) -> Dict:
    world_scores = _world_score_map(context.get('world_state') or {})
    strength = _economic_strength_label(world_scores, direction)
    has_inflation_or_rate_risk = _has_inflation_or_rate_risk(context, drag_materials)
    headline = (
        f'景気は{strength}。ただし物価・金利に警戒。'
        if has_inflation_or_rate_risk
        else f'景気は{strength}。信用・流動性を確認。'
    )
    stock_view = _stock_implication_label(nikkei_impact, has_inflation_or_rate_risk)
    return {
        'title': 'Macro経済判定',
        'headline': headline,
        'cards': [
            {
                'label': '経済の強弱',
                'value': strength,
                'detail': _economic_strength_detail(strength, world_scores),
            },
            {
                'label': '支えている材料',
                'value': _support_materials_display(world_scores, support_materials),
                'detail': '成長・雇用・信用・流動性など、経済を支える材料です。',
            },
            {
                'label': '重しになっている材料',
                'value': _drag_materials_display(context, drag_materials),
                'detail': '物価・金利・イベントなど、株価や消費の重しになりやすい材料です。',
            },
            {
                'label': '株価への見方',
                'value': stock_view,
                'detail': _stock_implication_detail(stock_view),
            },
        ],
    }


def _world_score_map(world_state: Dict) -> Dict[str, float]:
    result = {}
    for row in world_state.get('score_rows') or []:
        field = row.get('field')
        if not field:
            continue
        value = _safe_float(row.get('value'))
        if value is None:
            value = _safe_float(row.get('display'))
        if value is not None:
            result[field] = value
    return result


def _economic_strength_label(world_scores: Dict[str, float], direction: str) -> str:
    core_values = [
        world_scores.get('growth_score'),
        world_scores.get('labor_score'),
        world_scores.get('credit_score'),
        world_scores.get('liquidity_score'),
    ]
    core_values = [value for value in core_values if value is not None]
    if core_values:
        average = sum(core_values) / len(core_values)
        if average >= 65:
            return '強め'
        if average >= 45:
            return '中立'
        return '弱め'
    if '強' in direction or '改善' in direction:
        return '中立'
    if '弱' in direction or '悪化' in direction:
        return '弱め'
    return '中立'


def _economic_strength_detail(strength: str, world_scores: Dict[str, float]) -> str:
    if strength == '強め':
        return '成長・雇用・信用環境は底堅い。'
    if strength == '弱め':
        return '成長や雇用の弱さを確認する局面です。'
    if world_scores:
        return '強弱が混在しており、次の指標更新を確認します。'
    return '保存済みの経済判断を参考にしています。'


def _support_materials_display(world_scores: Dict[str, float], support_materials: List[str]) -> str:
    fields = [
        ('growth_score', '成長'),
        ('labor_score', '雇用'),
        ('credit_score', '信用'),
        ('liquidity_score', '流動性'),
    ]
    parts = [
        f'{label} {world_scores[field]:.0f}%'
        for field, label in fields
        if world_scores.get(field) is not None
    ]
    market_stress = world_scores.get('market_stress_score')
    if market_stress is not None and market_stress <= 30:
        parts.append(f'低ストレス {market_stress:.0f}%')
    if parts:
        return ' / '.join(parts)
    if support_materials:
        return ' / '.join(support_materials[:4])
    return '支援材料を確認中'


def _drag_materials_display(context: Dict, drag_materials: List[str]) -> str:
    parts = []
    inflation = _inflation_reacceleration_pct(context)
    if inflation is not None:
        parts.append(f'インフレ再加速 {inflation:.0f}%')
    for item in drag_materials:
        if item and item not in parts:
            parts.append(item)
        if len(parts) >= 3:
            break
    return ' / '.join(parts) if parts else '警戒材料を確認中'


def _inflation_reacceleration_pct(context: Dict) -> Optional[float]:
    house_view = context.get('house_view') or {}
    probabilities = house_view.get('probabilities') or {}
    raw = (
        probabilities.get('inflation_reacceleration')
        or (context.get('macro_forecast_report') or {}).get('inflation_reacceleration')
    )
    value = _safe_float(raw)
    if value is None:
        return None
    return value * 100 if value <= 1 else value


def _has_inflation_or_rate_risk(context: Dict, drag_materials: List[str]) -> bool:
    if _inflation_reacceleration_pct(context) is not None and _inflation_reacceleration_pct(context) >= 60:
        return True
    text = ' '.join(str(item) for item in drag_materials)
    text += ' ' + str((context.get('macro_decision') or {}).get('policy_pressure') or {})
    return any(keyword in text for keyword in ('物価', 'インフレ', '金利', 'PCE', 'CPI'))


def _stock_implication_label(nikkei_impact: str, has_risk: bool) -> str:
    if nikkei_impact == '上昇支援':
        return '条件付き追い風' if has_risk else '追い風'
    if nikkei_impact == '下落圧力':
        return '逆風'
    return '中立'


def _stock_implication_detail(stock_view: str) -> str:
    if stock_view == '条件付き追い風':
        return '経済環境は支援的だが、金利上昇時は上値が重くなりやすい。'
    if stock_view == '追い風':
        return '経済と信用環境は株価を支えやすい。'
    if stock_view == '逆風':
        return '経済環境または金融環境が株価の重しになりやすい。'
    return '方向感は限定的で、株価判断は他ページの短期材料も確認します。'


def _safe_float(value):
    try:
        return float(str(value).replace('%', '').strip())
    except (TypeError, ValueError):
        return None


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
    from .model_validation import latest_validation_reports

    return {
        (report.model_version, report.target, report.horizon): report
        for report in latest_validation_reports()
    }


def build_forecast_model_context() -> Dict:
    from . import forecast_models
    from .model_validation import model_display_grade

    reports = _latest_validation_reports()
    rows = []
    hidden_rows = []
    seen = set()
    snapshots = (
        ForecastSnapshot.objects
        .filter(
            model_version__in=[
                'return_lightgbm_v2',
                'short_horizon_return_v1',
                'macro_forecast_lightgbm_v1',
                'lightgbm_return_v1',
            ],
        )
        .order_by('-as_of_date', '-created_at')
    )
    for snapshot in snapshots:
        key = (snapshot.model_version, snapshot.target, snapshot.horizon)
        if forecast_models.is_deprecated_monthly_short_return_model(*key):
            continue
        if key in seen:
            continue
        seen.add(key)
        metadata = snapshot.metadata or {}
        report = reports.get(key)
        metrics = report.metrics if report else {}
        display_grade, display_reason = (
            model_display_grade(report)
            if report else ('hidden', '検証結果なし')
        )
        mae = (
            metadata.get('validation_mae_pct')
            or metadata.get('validation_mae')
            or metrics.get('mae')
        )
        row = {
            'target': snapshot.target,
            'horizon': snapshot.horizon,
            'prediction_display': _forecast_prediction_display(snapshot),
            'unit': metadata.get('unit') or ('%' if metadata.get('prediction_kind') == 'return_pct' else '—'),
            'mae_display': _number_display(mae),
            'baseline_mae_display': _number_display(metrics.get('baseline_mae')),
            'skill_score_display': _ratio_pct_display(metrics.get('skill_score')),
            'direction_accuracy_display': _ratio_pct_display(
                metrics.get('direction_accuracy')
            ),
            'model_version': snapshot.model_version,
            'trained_at': snapshot.as_of_date.isoformat(),
            'data_quality_display': f"{metadata.get('data_quality', 0):.0f}%",
            'kind_label': display_reason,
            'kind_tone': 'validated' if display_grade == 'show' else 'provisional',
            'display_grade': display_grade,
            'display_reason': display_reason,
        }
        if display_grade == 'show':
            rows.append(row)
        else:
            hidden_rows.append(row)
    return {
        'rows': rows,
        'hidden_rows': hidden_rows,
        'has_rows': bool(rows),
    }


def build_model_validation_context() -> Dict:
    from .model_validation import model_display_grade

    rows = []
    for report in ModelValidationReport.objects.order_by('-evaluated_at')[:12]:
        metrics = report.metrics or {}
        display_grade, display_reason = model_display_grade(report)
        rows.append({
            'model_version': report.model_version,
            'target': report.target,
            'horizon': report.horizon,
            'sample_count': report.sample_count,
            'event_count': report.event_count,
            'mae_display': _number_display(metrics.get('mae')),
            'baseline_mae_display': _number_display(metrics.get('baseline_mae')),
            'skill_score_display': _ratio_pct_display(metrics.get('skill_score')),
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
            'display_grade': display_grade,
            'display_reason': display_reason,
        })
    return {
        'rows': rows,
        'has_rows': bool(rows),
    }


def build_macro_forecast_report_context() -> Dict:
    run = (
        MacroForecastRun.objects
        .prefetch_related('scenarios')
        .order_by('-as_of')
        .first()
    )
    if run is None:
        return {}
    report = run.report or {}
    scenarios = []
    for scenario in run.scenarios.all():
        scenarios.append({
            'name': scenario.get_name_display(),
            'name_key': scenario.name,
            'probability_display': f'{scenario.probability * 100:.0f}%',
            'growth_view': scenario.growth_view,
            'inflation_view': scenario.inflation_view,
            'policy_view': scenario.policy_view,
            'market_view': scenario.market_view,
            'nikkei_bias': scenario.get_nikkei_bias_display(),
            'key_drivers': scenario.key_drivers,
            'invalidation_triggers': scenario.invalidation_triggers,
        })
    axes = []
    for key, axis in (run.state_vector.get('axes') or {}).items():
        axes.append({
            'key': key,
            'label': axis.get('label'),
            'score_display': f"{axis.get('score', 0):.0f}%",
        })
    return {
        'as_of': run.as_of.isoformat(),
        'primary_regime': run.primary_regime,
        'previous_regime': run.previous_regime,
        'confidence_display': f'{run.confidence:.0f}%',
        'data_quality_display': f'{run.data_quality_score:.0f}%',
        'headline': report.get('headline') or '',
        'judgment': report.get('judgment') or '',
        'nikkei_implication': report.get('nikkei_implication') or '',
        'change_summary': report.get('change_summary') or '',
        'what_changed': report.get('what_changed') or [],
        'market_mispricing_watch': report.get('market_mispricing_watch') or [],
        'executive_summary': report.get('executive_summary') or {},
        'what_changed_detail': report.get('what_changed_detail') or {},
        'growth_view': report.get('growth_view') or {},
        'inflation_view': report.get('inflation_view') or {},
        'labor_view': report.get('labor_view') or {},
        'policy_view': report.get('policy_view') or {},
        'market_implication': report.get('market_implication') or {},
        'scenario_table': report.get('scenario_table') or [],
        'invalidation_triggers': report.get('invalidation_triggers') or [],
        'model_reliability': report.get('model_reliability') or {},
        'axes': axes,
        'scenarios': scenarios,
        'warnings': run.warnings or [],
        'model_version': run.model_version,
    }


def build_macro_outcome_validation_context() -> Dict:
    cutoff = timezone.localdate() - timedelta(days=90)
    outcomes = list(
        MacroForecastOutcome.objects
        .filter(target_date__gte=cutoff)
        .select_related('forecast')
        .order_by('-target_date', '-evaluated_at')[:50]
    )
    if not outcomes:
        return {}

    hit_values = [
        outcome.direction_hit for outcome in outcomes
        if outcome.direction_hit is not None
    ]
    brier_values = [
        outcome.brier_score for outcome in outcomes
        if outcome.brier_score is not None
    ]
    direction_accuracy = (
        sum(1 for value in hit_values if value) / len(hit_values)
        if hit_values else None
    )
    avg_brier = (
        sum(float(value) for value in brier_values) / len(brier_values)
        if brier_values else None
    )
    rows = []
    for outcome in outcomes[:8]:
        rows.append({
            'target_date': outcome.target_date.isoformat(),
            'target_name': outcome.target_name,
            'predicted_prob_display': (
                f'{outcome.predicted_prob * 100:.0f}%'
                if outcome.predicted_prob is not None else '—'
            ),
            'actual_value_display': (
                f'{outcome.actual_value:.0f}'
                if outcome.actual_value is not None else '—'
            ),
            'brier_score_display': (
                f'{outcome.brier_score:.3f}'
                if outcome.brier_score is not None else '—'
            ),
            'direction_hit_display': (
                '的中' if outcome.direction_hit is True
                else '外れ' if outcome.direction_hit is False
                else '—'
            ),
            'model_version': outcome.forecast.model_version,
        })
    return {
        'period_label': '過去90日',
        'total_count': len(outcomes),
        'direction_accuracy_display': (
            f'{direction_accuracy * 100:.0f}%' if direction_accuracy is not None else '—'
        ),
        'avg_brier_score_display': (
            f'{avg_brier:.3f}' if avg_brier is not None else '—'
        ),
        'rows': rows,
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
