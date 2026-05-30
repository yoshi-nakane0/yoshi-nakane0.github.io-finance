"""マクロページ上部の結論カードを保存・表示する。"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from django.db import transaction

from ..models import MacroConclusionSnapshot, RegimeSnapshot, VintageObservation


CATEGORY_LABELS = {
    'growth': '成長',
    'labor': '雇用',
    'financial': '金融環境',
    'inflation': '物価',
}


def _pct(value: Optional[float]) -> str:
    if value is None:
        return '--'
    return f'{value * 100:.0f}%'


def _risk_value(snapshot: Optional[RegimeSnapshot], key: str) -> Optional[float]:
    if snapshot is None:
        return None
    return (snapshot.risk_probabilities or {}).get(key)


def _category_contributions(snapshot: Optional[RegimeSnapshot]) -> dict[str, float]:
    totals = defaultdict(float)
    if snapshot is None:
        return {}
    for item in snapshot.evidence or []:
        category = item.get('category') or 'other'
        totals[category] += float(item.get('contribution') or 0.0)
    return {key: round(value, 2) for key, value in totals.items()}


def _driver_changes(
    current: RegimeSnapshot,
    previous: Optional[RegimeSnapshot],
) -> list[dict]:
    current_contrib = _category_contributions(current)
    previous_contrib = _category_contributions(previous)
    keys = sorted(set(current_contrib) | set(previous_contrib))
    rows = []
    for key in keys:
        delta = current_contrib.get(key, 0.0) - previous_contrib.get(key, 0.0)
        if abs(delta) < 0.05 and previous is not None:
            continue
        rows.append({
            'key': key,
            'label': CATEGORY_LABELS.get(key, key),
            'current': current_contrib.get(key, 0.0),
            'previous': previous_contrib.get(key, 0.0) if previous else None,
            'delta': round(delta, 2),
            'direction': '改善' if delta > 0 else '悪化' if delta < 0 else '横ばい',
        })
    rows.sort(key=lambda item: abs(item['delta']), reverse=True)
    return rows[:6]


def _latest_signal(snapshot: RegimeSnapshot, series_ids: set[str]) -> Optional[dict]:
    for item in snapshot.evidence or []:
        if item.get('series_id') in series_ids:
            return item
    return None


def _topic_mapping(snapshot: RegimeSnapshot) -> list[dict]:
    """外部レポートを引用せず、論点だけを自サイト指標に対応させる。"""
    topics = [
        ('employment', '雇用', {'UNRATE', 'PAYEMS', 'JTSJOL'}),
        ('financial_conditions', '金融環境', {'T10Y2Y', 'T10Y3M', 'BAMLH0A0HYM2', 'VIXCLS'}),
        ('inflation', '物価', {'PCEPILFE', 'CPIAUCSL', 'CPILFESL', 'PCEPI'}),
        ('consumption', '消費', {'RSAFS', 'UMCSENT'}),
        ('production', '生産・投資', {'INDPRO', 'TCU', 'GDPC1'}),
        ('ai_investment', 'AI投資', set()),
        ('oil', '原油', set()),
        ('fiscal', '財政', set()),
    ]
    rows = []
    for key, label, series_ids in topics:
        signal = _latest_signal(snapshot, series_ids) if series_ids else None
        if signal is None:
            rows.append({
                'key': key,
                'label': label,
                'site_signal': '対応する無料データが未設定',
                'alignment': '未判定',
                'source_indicator': '—',
            })
            continue
        contribution = float(signal.get('contribution') or 0.0)
        rows.append({
            'key': key,
            'label': label,
            'site_signal': signal.get('signal') or '判定あり',
            'alignment': '支援' if contribution >= 0 else '逆風',
            'source_indicator': signal.get('name') or signal.get('series_id') or '—',
        })
    return rows


def _watch_events(snapshot: RegimeSnapshot) -> list[str]:
    labels = ['雇用統計', 'CPI/PCE物価', 'FOMC', 'GDP改定値']
    if (snapshot.risk_probabilities or {}).get('financial_stress', 0) >= 0.45:
        labels.append('信用スプレッド')
    if snapshot.inflation_flag == RegimeSnapshot.InflationFlag.HIGH:
        labels.append('原油・期待インフレ')
    return labels[:6]


def _base_scenario(snapshot: RegimeSnapshot) -> str:
    recession = _risk_value(snapshot, 'recession')
    inflation = _risk_value(snapshot, 'inflation_reacceleration')
    if snapshot.regime_label == RegimeSnapshot.Label.EXPANSION:
        base = '今後3カ月は拡大基調を維持する見方です。'
    elif snapshot.regime_label == RegimeSnapshot.Label.RECOVERY:
        base = '今後3カ月は持ち直し継続を確認する局面です。'
    elif snapshot.regime_label == RegimeSnapshot.Label.CONTRACTION:
        base = '今後3カ月は景気悪化の継続に注意する局面です。'
    else:
        base = '今後3カ月は減速が一時的かを確認する局面です。'
    return f'{base} 景気後退のルール一致度は{_pct(recession)}、物価再加速は{_pct(inflation)}です。'


def _current_view(snapshot: RegimeSnapshot) -> str:
    label = snapshot.get_regime_label_display()
    inflation = snapshot.get_inflation_flag_display()
    return f'現状は「{label} × {inflation}」。ルール強度 {snapshot.rule_strength:.0f}%、データ品質 {snapshot.data_quality:.0f}% です。'


def _previous_change(
    current: RegimeSnapshot,
    previous: Optional[RegimeSnapshot],
    driver_changes: list[dict],
) -> str:
    if previous is None:
        return '前回判定がないため、今回を初回の基準として保存しています。'
    previous_risk = _risk_value(previous, 'recession')
    current_risk = _risk_value(current, 'recession')
    if previous_risk is None or current_risk is None:
        risk_change = '景気後退リスクの差分は未計算です。'
    else:
        delta = (current_risk - previous_risk) * 100
        sign = '+' if delta > 0 else ''
        risk_change = f'景気後退のルール一致度は前回 {_pct(previous_risk)} から今回 {_pct(current_risk)}（{sign}{delta:.1f}pt）です。'
    if not driver_changes:
        return f'{risk_change} 主な寄与項目は大きく変わっていません。'
    top = '、'.join(
        f"{row['label']} {row['direction']}({row['delta']:+.2f})"
        for row in driver_changes[:3]
    )
    return f'{risk_change} 主因は {top} です。'


def _model_reliability(snapshot: RegimeSnapshot) -> tuple[str, float]:
    vintage_count = VintageObservation.objects.count()
    score = min(max((snapshot.data_quality or 0) * 0.55 + (snapshot.rule_strength or 0) * 0.35, 0), 90)
    if vintage_count:
        score = min(score + 10, 100)
        vintage_note = f'ビンテージ保存 {vintage_count}件あり。'
    else:
        vintage_note = 'ビンテージ保存はこれから蓄積が必要です。'
    if score >= 75:
        label = '中'
    elif score >= 50:
        label = '低〜中'
    else:
        label = '低'
    return (
        f'信頼度は{label}。ルール一致度であり、検証済み確率ではありません。{vintage_note}',
        round(score, 1),
    )


def save_macro_conclusion(snapshot: RegimeSnapshot) -> MacroConclusionSnapshot:
    previous = (
        RegimeSnapshot.objects
        .filter(snapshot_date__lt=snapshot.snapshot_date)
        .order_by('-snapshot_date')
        .first()
    )
    driver_changes = _driver_changes(snapshot, previous)
    reliability_text, reliability_score = _model_reliability(snapshot)
    metadata = {
        'rule_probability_note': 'レジーム分布は統計的な確率ではなくルール一致度。',
        'previous_model_version': previous.model_version if previous else None,
        'current_model_version': snapshot.model_version,
    }
    with transaction.atomic():
        conclusion, _ = MacroConclusionSnapshot.objects.update_or_create(
            as_of_date=snapshot.snapshot_date,
            defaults={
                'regime_snapshot': snapshot,
                'previous_snapshot_date': previous.snapshot_date if previous else None,
                'current_view': _current_view(snapshot),
                'previous_change': _previous_change(snapshot, previous, driver_changes),
                'base_scenario_3m': _base_scenario(snapshot),
                'upside_risk': '上振れリスクは、雇用の粘り、金融環境の改善、消費の再加速です。',
                'downside_risk': '下振れリスクは、雇用悪化、信用スプレッド拡大、物価再加速による金利高止まりです。',
                'watch_events': _watch_events(snapshot),
                'model_reliability': reliability_text,
                'driver_changes': driver_changes,
                'topic_mapping': _topic_mapping(snapshot),
                'reliability_score': reliability_score,
                'metadata': metadata,
            },
        )
    return conclusion


def latest_or_create_macro_conclusion(
    snapshot: Optional[RegimeSnapshot],
) -> Optional[MacroConclusionSnapshot]:
    if snapshot is None:
        return None
    conclusion = MacroConclusionSnapshot.objects.filter(
        as_of_date=snapshot.snapshot_date,
    ).first()
    if conclusion is not None:
        return conclusion
    return save_macro_conclusion(snapshot)
