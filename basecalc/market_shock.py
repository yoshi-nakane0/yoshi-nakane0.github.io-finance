"""主要米株指数の急変判定。

日次更新で保存している価格アクション指標と市場ストレス背景を使い、
急騰・急落が続きやすいか、一時的に見えるかを判定する。
"""

from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

from django.utils import timezone

from macro.models import Observation
from macro.services.crash_alert import compute_crash_alert


TARGETS = (
    {'symbol': 'GSPC', 'label': 'S&P500'},
    {'symbol': 'DJI', 'label': 'NYダウ'},
    {'symbol': 'IXIC', 'label': 'NASDAQ'},
)

INTERMARKET_LABELS = {
    'nasdaq100_futures': 'NASDAQ100先物',
    'sp500_futures': 'S&P500先物',
    'dow_futures': 'NYダウ先物',
    'usdjpy': 'USDJPY',
    'vix': 'VIX',
    'us10y': '米10年金利',
    'crude_oil': '原油',
}

INDEX_FUTURES_TARGETS = {
    'sp500_futures': 'GSPC',
    'dow_futures': 'DJI',
    'nasdaq100_futures': 'IXIC',
}

MOMENTUM_TRIGGER_PCT = 3.5


def _latest(series_id: str, as_of: Optional[date] = None) -> Optional[Dict]:
    qs = Observation.objects.filter(indicator__fred_series_id=series_id)
    if as_of is not None:
        qs = qs.filter(observation_date__lte=as_of)
    obs = qs.select_related('indicator').order_by('-observation_date').first()
    if obs is None:
        return None
    return {
        'value': obs.value,
        'observation_date': obs.observation_date,
    }


def _category_score(alert: Dict, category: str) -> Optional[int]:
    for row in alert.get('category_summary', []):
        if row.get('category') == category:
            return row.get('avg_score')
    return None


def _fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return '—'
    sign = '+' if value > 0 else ''
    return f'{sign}{value:.1f}%'


def _move_direction(momentum_20d: Optional[float]) -> str:
    if momentum_20d is None:
        return 'unknown'
    if momentum_20d >= MOMENTUM_TRIGGER_PCT:
        return 'surge'
    if momentum_20d <= -MOMENTUM_TRIGGER_PCT:
        return 'drop'
    return 'calm'


def _severity(abs_move: float) -> str:
    if abs_move >= 10:
        return '大'
    if abs_move >= 6:
        return '中'
    return '小'


def _continuation_score(
    *,
    direction: str,
    momentum_20d: float,
    dd200: Optional[float],
    dd52w: Optional[float],
    stress_score: Optional[int],
    vol_score: Optional[int],
    credit_score: Optional[int],
) -> int:
    stress = stress_score or 0
    vol = vol_score or 0
    credit = credit_score or 0
    score = 35

    if direction == 'drop':
        if stress >= 50:
            score += 15
        if vol >= 50:
            score += 15
        if credit >= 50:
            score += 20
        if dd200 is not None and dd200 < 0:
            score += 10
        if dd52w is not None and dd52w <= -10:
            score += 10
        if momentum_20d <= -8:
            score += 10
        if credit < 25:
            score -= 10
        if stress < 30:
            score -= 10
    elif direction == 'surge':
        if stress < 30:
            score += 15
        if credit < 25:
            score += 15
        if vol < 35:
            score += 10
        if dd200 is not None and dd200 > 0:
            score += 10
        if dd52w is not None and dd52w > -5:
            score += 10
        if momentum_20d >= 8:
            score += 10
        if stress >= 50:
            score -= 20
        if credit >= 50:
            score -= 15

    return min(max(round(score), 0), 100)


def _continuation_label(score: int) -> str:
    if score >= 65:
        return '継続寄り'
    if score >= 45:
        return '中立'
    return '一時的寄り'


def _reason_text(
    *,
    direction: str,
    label: str,
    stress_score: Optional[int],
    credit_score: Optional[int],
    vol_score: Optional[int],
    dd200: Optional[float],
) -> str:
    if direction == 'calm':
        return '直近20営業日の変化は急変判定の範囲外です。'
    if direction == 'drop':
        if label == '継続寄り':
            return '下落にボラ・信用・トレンド悪化が重なっています。'
        if (credit_score or 0) < 25 and (stress_score or 0) < 30:
            return '信用・流動性が落ち着いており、指数主導の調整に見えます。'
        return '価格は弱い一方、背景悪化の広がりは限定的です。'
    if label == '継続寄り':
        return '上昇にトレンド改善と低ストレス環境が重なっています。'
    if (vol_score or 0) >= 50 or (stress_score or 0) >= 50:
        return '上昇していても市場ストレスが残り、自律反発の可能性があります。'
    if dd200 is not None and dd200 < 0:
        return '上昇していても中期トレンドはまだ弱い状態です。'
    return '上昇の背景確認は中立です。'


def _row_for_target(
    target: Dict,
    *,
    alert: Dict,
    as_of: Optional[date],
) -> Dict:
    symbol = target['symbol']
    mom = _latest(f'PA_{symbol}_MOM20', as_of=as_of)
    dd200_meta = _latest(f'PA_{symbol}_DD200', as_of=as_of)
    dd52w_meta = _latest(f'PA_{symbol}_DD52W', as_of=as_of)

    momentum_20d = mom['value'] if mom else None
    dd200 = dd200_meta['value'] if dd200_meta else None
    dd52w = dd52w_meta['value'] if dd52w_meta else None
    direction = _move_direction(momentum_20d)
    stress_score = alert.get('market_stress_score')
    vol_score = _category_score(alert, 'volatility_sentiment')
    credit_score = _category_score(alert, 'credit_liquidity')

    if direction in ('surge', 'drop') and momentum_20d is not None:
        continuation_score = _continuation_score(
            direction=direction,
            momentum_20d=momentum_20d,
            dd200=dd200,
            dd52w=dd52w,
            stress_score=stress_score,
            vol_score=vol_score,
            credit_score=credit_score,
        )
        continuation_label = _continuation_label(continuation_score)
        move_label = '急騰' if direction == 'surge' else '急落'
        headline = f'{move_label} {_severity(abs(momentum_20d))} / {continuation_label}'
        tone = (
            'positive' if direction == 'surge' and continuation_label == '継続寄り'
            else 'negative' if direction == 'drop' and continuation_label == '継続寄り'
            else 'neutral'
        )
    elif direction == 'calm':
        continuation_score = None
        continuation_label = '通常変動'
        headline = '急変なし'
        tone = 'neutral'
    else:
        continuation_score = None
        continuation_label = '判定保留'
        headline = 'データ不足'
        tone = 'unknown'

    return {
        'symbol': symbol,
        'label': target['label'],
        'headline': headline,
        'tone': tone,
        'direction': direction,
        'continuation_score': continuation_score,
        'continuation_score_display': (
            f'{continuation_score}%' if continuation_score is not None else '—'
        ),
        'continuation_label': continuation_label,
        'momentum_20d': momentum_20d,
        'momentum_20d_display': _fmt_pct(momentum_20d),
        'dd200_display': _fmt_pct(dd200),
        'dd52w_display': _fmt_pct(dd52w),
        'observation_date': mom['observation_date'].isoformat() if mom else '—',
        'reason': _reason_text(
            direction=direction,
            label=continuation_label,
            stress_score=stress_score,
            credit_score=credit_score,
            vol_score=vol_score,
            dd200=dd200,
        ),
    }


def build_market_shock_context(
    *,
    alert: Optional[Dict] = None,
    as_of: Optional[date] = None,
    base_snapshot: Optional[Dict] = None,
    intermarket_context: Optional[Dict] = None,
) -> Dict:
    """急変判定の表示用コンテキストを返す。"""
    target_date = as_of or timezone.localdate()
    alert_context = alert or compute_crash_alert(as_of=target_date)
    index_rows = [
        _row_for_target(target, alert=alert_context, as_of=target_date)
        for target in TARGETS
    ]
    base_rows = _basecalc_asset_rows(base_snapshot, intermarket_context)
    rows = _merge_index_futures(index_rows, base_rows)
    active = [row for row in rows if row['direction'] in ('surge', 'drop')]
    if not active:
        summary = '主要3指数に急変判定は出ていません。'
        tone = 'neutral'
    else:
        strongest = max(
            active,
            key=lambda row: abs(row.get('momentum_20d') or 0),
        )
        move = '急騰' if strongest['direction'] == 'surge' else '急落'
        summary = (
            f"{strongest['label']}の{move}は"
            f"{strongest['continuation_label']}です。"
        )
        tone = strongest['tone']

    return {
        'summary': summary,
        'tone': tone,
        'as_of': target_date.isoformat(),
        'rows': rows,
        'has_data': any(row['direction'] != 'unknown' for row in rows),
    }


def _merge_index_futures(index_rows: List[Dict], base_rows: List[Dict]) -> List[Dict]:
    by_symbol = {row.get('symbol'): dict(row) for row in index_rows}
    merged = []
    for row in base_rows:
        target_symbol = INDEX_FUTURES_TARGETS.get(row.get('symbol'))
        if target_symbol and target_symbol in by_symbol:
            by_symbol[target_symbol]['futures'] = _futures_summary(row)
            if by_symbol[target_symbol].get('direction') == 'unknown':
                by_symbol[target_symbol].update(
                    {
                        'headline': row.get('headline'),
                        'tone': row.get('tone'),
                        'direction': row.get('direction'),
                        'momentum_20d': row.get('momentum_20d'),
                        'momentum_20d_display': row.get('momentum_20d_display'),
                        'continuation_score_display': row.get('continuation_score_display'),
                        'reason': row.get('reason'),
                    }
                )
        else:
            merged.append(row)
    merged.extend(by_symbol.get(target['symbol']) for target in TARGETS)
    return [row for row in merged if row]


def _futures_summary(row: Dict) -> Dict:
    return {
        'label': row.get('label'),
        'headline': row.get('headline'),
        'direction': row.get('direction'),
        'momentum_20d_display': row.get('momentum_20d_display'),
        'continuation_score_display': row.get('continuation_score_display'),
        'reason': row.get('reason'),
    }


def _basecalc_asset_rows(
    base_snapshot: Optional[Dict],
    intermarket_context: Optional[Dict],
) -> List[Dict]:
    rows = []
    if isinstance(base_snapshot, dict):
        change_pct = _to_float(base_snapshot.get('change_pct'))
        rows.append(
            _simple_asset_row(
                symbol=base_snapshot.get('symbol') or 'NIY=F',
                label='日経先物',
                change_pct=change_pct,
                threshold=3.0,
                reason='日経先物本体の直近変化率を確認しています。',
            )
        )
    components = (
        intermarket_context.get('components')
        if isinstance(intermarket_context, dict)
        else {}
    ) or {}
    for key, component in components.items():
        if key not in INTERMARKET_LABELS or not isinstance(component, dict):
            continue
        change_pct = _to_float(component.get('change_pct'))
        if change_pct is None:
            score = _to_float(component.get('score'))
            change_pct = score / 20 if score is not None else None
        rows.append(
            _simple_asset_row(
                symbol=key,
                label=INTERMARKET_LABELS[key],
                change_pct=change_pct,
                threshold=2.0 if key != 'vix' else 8.0,
                reason='basecalc内の補助市場データから急変を確認しています。',
            )
        )
    return rows


def _simple_asset_row(symbol, label, change_pct, threshold, reason):
    direction = _move_direction_from_change(change_pct, threshold)
    if direction == 'surge':
        headline = f"急騰 {_severity(abs(change_pct or 0))}"
        tone = 'positive'
        continuation_label = '要確認'
        score = min(100, round(abs(change_pct or 0) / max(threshold, 0.1) * 35))
    elif direction == 'drop':
        headline = f"急落 {_severity(abs(change_pct or 0))}"
        tone = 'negative'
        continuation_label = '要確認'
        score = min(100, round(abs(change_pct or 0) / max(threshold, 0.1) * 35))
    elif direction == 'calm':
        headline = '急変なし'
        tone = 'neutral'
        continuation_label = '通常変動'
        score = None
    else:
        headline = 'データ不足'
        tone = 'unknown'
        continuation_label = '判定保留'
        score = None
    return {
        'symbol': symbol,
        'label': label,
        'headline': headline,
        'tone': tone,
        'direction': direction,
        'continuation_score': score,
        'continuation_score_display': f'{score}%' if score is not None else '—',
        'continuation_label': continuation_label,
        'momentum_20d': change_pct,
        'momentum_20d_display': _fmt_pct(change_pct),
        'dd200_display': '—',
        'dd52w_display': '—',
        'observation_date': '—',
        'reason': reason,
    }


def _move_direction_from_change(change_pct, threshold):
    if change_pct is None:
        return 'unknown'
    if change_pct >= threshold:
        return 'surge'
    if change_pct <= -threshold:
        return 'drop'
    return 'calm'


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
