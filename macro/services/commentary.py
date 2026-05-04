"""指標サマリ・類似局面・連動マップの自動解説テキスト生成。

ルールベースで、現在のレジーム・キー指標・類似局面集計から
平易な日本語コメントを組み立てる。
"""

import logging
from statistics import mean
from typing import Dict, List, Optional

from ..models import RegimeSnapshot
from .regime import _latest_observation

logger = logging.getLogger(__name__)


REGIME_SUMMARY = {
    RegimeSnapshot.Label.EXPANSION: '景気は拡大局面（生産・雇用が伸びている）',
    RegimeSnapshot.Label.SLOWDOWN: '景気は減速局面（拡大ペースが落ちてきている）',
    RegimeSnapshot.Label.CONTRACTION: '景気は縮小局面（リセッションの可能性）',
    RegimeSnapshot.Label.RECOVERY: '景気は回復局面（底を打って持ち直している）',
    RegimeSnapshot.Label.UNKNOWN: '景気局面は判定不能',
}

INFLATION_SUMMARY = {
    RegimeSnapshot.InflationFlag.HIGH: 'インフレは高止まり（FRB目標2%超）',
    RegimeSnapshot.InflationFlag.EASING: 'インフレは鈍化中（落ち着きつつある）',
    RegimeSnapshot.InflationFlag.NORMAL: 'インフレは正常（目標近辺）',
    RegimeSnapshot.InflationFlag.UNKNOWN: 'インフレ状態は判定不能',
}


def _format_pct(value: Optional[float], digits: int = 1) -> str:
    if value is None:
        return '—'
    sign = '+' if value > 0 else ''
    return f'{sign}{value:.{digits}f}%'


def _build_risk_flags() -> List[str]:
    """主要指標の現状値からリスク状態を文章で返す。"""
    flags: List[str] = []

    spread = _latest_observation('T10Y2Y')
    if spread and spread.value is not None:
        if spread.value < 0:
            flags.append(
                f'米2-10年金利差が{spread.value:+.2f}%と逆イールド。'
                'リセッション接近のシグナルとして警戒される局面です。'
            )
        elif spread.value < 0.3:
            flags.append(
                f'米2-10年金利差が{spread.value:+.2f}%と縮小気味。'
                '逆イールド一歩手前で景気減速懸念が残ります。'
            )

    vix = _latest_observation('VIXCLS')
    if vix and vix.value is not None:
        if vix.value >= 30:
            flags.append(
                f'VIX が{vix.value:.1f}と高水準。市場の警戒感が強いリスクオフ局面です。'
            )
        elif vix.value <= 14:
            flags.append(
                f'VIX が{vix.value:.1f}と低水準。市場は楽観的でリスクオン傾向です。'
            )

    hy = _latest_observation('BAMLH0A0HYM2')
    if hy and hy.value is not None and hy.value >= 5.0:
        flags.append(
            f'ハイイールド社債スプレッドが{hy.value:.2f}%と拡大。'
            '信用不安が意識されています。'
        )

    return flags


def _aggregate_similar_returns(similar_periods: List[Dict]) -> Dict[str, Optional[float]]:
    """類似局面リストから翌月リターン平均を集計する。"""
    nikkei_vals = []
    spx_vals = []
    for p in similar_periods or []:
        n = p.get('nikkei_return_value')
        s = p.get('spx_return_value')
        if isinstance(n, (int, float)):
            nikkei_vals.append(n)
        if isinstance(s, (int, float)):
            spx_vals.append(s)
    return {
        'nikkei_avg': mean(nikkei_vals) if nikkei_vals else None,
        'spx_avg': mean(spx_vals) if spx_vals else None,
        'count': max(len(nikkei_vals), len(spx_vals)),
    }


def _trend_word(value: Optional[float]) -> str:
    if value is None:
        return ''
    if value >= 1.5:
        return 'やや強い上昇傾向'
    if value >= 0.3:
        return 'やや上昇傾向'
    if value > -0.3:
        return 'ほぼ横ばい'
    if value > -1.5:
        return 'やや下落傾向'
    return 'やや強い下落傾向'


def build_overview_commentary(
    snapshot: Optional[RegimeSnapshot],
    similar_periods: List[Dict],
) -> Dict:
    """ページ最上部に出す「現状サマリ」を組み立てる。"""
    sentences: List[str] = []

    if snapshot is not None:
        regime_text = REGIME_SUMMARY.get(
            snapshot.regime_label, REGIME_SUMMARY[RegimeSnapshot.Label.UNKNOWN]
        )
        inflation_text = INFLATION_SUMMARY.get(
            snapshot.inflation_flag,
            INFLATION_SUMMARY[RegimeSnapshot.InflationFlag.UNKNOWN],
        )
        sentences.append(f'{regime_text}。{inflation_text}。')

    sentences.extend(_build_risk_flags())

    agg = _aggregate_similar_returns(similar_periods)
    if agg['count'] > 0:
        nikkei_word = _trend_word(agg['nikkei_avg'])
        spx_word = _trend_word(agg['spx_avg'])
        if nikkei_word and spx_word:
            sentences.append(
                f'過去の類似{agg["count"]}局面では、翌月平均は'
                f'日経 {_format_pct(agg["nikkei_avg"])}、'
                f'S&P {_format_pct(agg["spx_avg"])}。'
                f'日米とも{nikkei_word}でした。'
            )
        else:
            sentences.append(
                f'過去の類似{agg["count"]}局面の翌月平均: '
                f'日経 {_format_pct(agg["nikkei_avg"])} / '
                f'S&P {_format_pct(agg["spx_avg"])}。'
            )

    if not sentences:
        sentences.append(
            'データが揃っていません。「更新」ボタンを押して指標を取得してください。'
        )

    return {
        'sentences': sentences,
    }


def build_similar_explanation(similar_periods: List[Dict]) -> Dict:
    """過去類似局面セクションの解説。"""
    help_text = (
        '現在の指標構成と最も似ていた過去5ヶ月を距離が近い順に並べています。'
        '各月の「翌月リターン」は当時の日経・S&Pが実際にどう動いたかの過去事実で、'
        '将来の上昇・下落を保証するものではありません。'
    )
    if not similar_periods:
        return {'help': help_text, 'summary': ''}

    agg = _aggregate_similar_returns(similar_periods)
    if agg['count'] == 0:
        return {'help': help_text, 'summary': ''}

    nikkei_word = _trend_word(agg['nikkei_avg'])
    spx_word = _trend_word(agg['spx_avg'])
    summary = (
        f'上位{agg["count"]}件の翌月平均: '
        f'日経 {_format_pct(agg["nikkei_avg"])} / '
        f'S&P {_format_pct(agg["spx_avg"])}。'
    )
    if nikkei_word == spx_word and nikkei_word:
        summary += f'日米とも{nikkei_word}の地合いでした。'
    elif nikkei_word and spx_word:
        summary += f'日経は{nikkei_word}、S&Pは{spx_word}でした。'
    return {'help': help_text, 'summary': summary}


def build_linkage_explanation(linkages: List[Dict]) -> Dict:
    """連動マップセクションの解説。"""
    help_text = (
        'ある指標が別の指標に何ヶ月先行して動くかを過去10年から計算。'
        '右の数字（相関係数）が +1 に近いほど同方向、-1 に近いほど逆方向に動く関係です。'
        '先行指標を見ることで遅行する指標の方向感を予想する手がかりになります。'
    )
    if not linkages:
        return {'help': help_text, 'summary': ''}

    top = linkages[0]
    summary = f'最も強い関係: {top.get("relation_text", "")}（相関 {top.get("correlation_display", "")}）。'
    return {'help': help_text, 'summary': summary}
