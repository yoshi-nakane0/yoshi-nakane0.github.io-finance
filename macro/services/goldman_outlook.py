"""無料公開範囲の Goldman Sachs 見通しと House View の比較。"""

from __future__ import annotations

from datetime import date

from django.utils import timezone

from ..models import RegimeSnapshot


GOLDMAN_PUBLIC_SOURCES = [
    {
        'title': 'US GDP Growth Is Projected to Outperform Economist Forecasts in 2026',
        'published_at': '2026-01-15',
        'url': (
            'https://www.goldmansachs.com/insights/articles/'
            'us-gdp-growth-is-projected-to-outperform-economist-forecasts-in-2026'
        ),
    },
    {
        'title': "The Global Economy Is Forecast to Post 'Sturdy' Growth of 2.8% in 2026",
        'published_at': '2025-12-19',
        'url': (
            'https://www.goldmansachs.com/insights/articles/'
            'the-global-economy-forecast-to-post-sturdy-growth-in-2026'
        ),
    },
    {
        'title': 'The Outlook for Fed Rate Cuts in 2026',
        'published_at': '2025-12-03',
        'url': (
            'https://www.goldmansachs.com/insights/articles/'
            'the-outlook-for-fed-rate-cuts-in-2026'
        ),
    },
]


GOLDMAN_PUBLIC_OUTLOOK = {
    'as_of': '2026-01-11',
    'author': 'Goldman Sachs Research / Jan Hatzius team public outlook',
    'forecasts': {
        'global_gdp_growth_2026': 2.8,
        'us_gdp_growth_q4q4_2026': 2.5,
        'us_gdp_growth_full_year_2026': 2.8,
        'us_gdp_growth_range_2026': '2.0-2.5',
        'core_pce_dec_2026_yoy': 2.1,
        'recession_probability_12m': 0.20,
        'fed_cuts_2026_count': 2,
        'fed_terminal_rate_range': '3.0-3.25',
    },
    'qualitative_view': {
        'growth': 'US growth above consensus, supported by tax cuts and easier financial conditions.',
        'inflation': 'Core inflation moderates as tariff pass-through fades.',
        'labor': 'Labor market is the most uncertain part of the outlook.',
        'rates': 'Policy rates decline, but the 2026 path is less clear.',
    },
}


def _latest_public_source_date() -> str:
    return max(source['published_at'] for source in GOLDMAN_PUBLIC_SOURCES)


def _latest_house_view_proxy() -> dict:
    snapshot = RegimeSnapshot.objects.order_by('-snapshot_date').first()
    if snapshot is None:
        return {
            'as_of': None,
            'primary_regime': None,
            'confidence': None,
            'data_quality': None,
            'risk_probabilities': {},
            'regime_probabilities': {},
        }
    return {
        'as_of': snapshot.snapshot_date.isoformat(),
        'primary_regime': snapshot.regime_label,
        'confidence': snapshot.confidence,
        'data_quality': snapshot.data_quality,
        'risk_probabilities': snapshot.risk_probabilities or {},
        'regime_probabilities': snapshot.regime_probabilities or {},
    }


def _numeric_comparison(metric: str, goldman_value, local_value) -> dict:
    difference = None
    if isinstance(goldman_value, (int, float)) and isinstance(local_value, (int, float)):
        difference = round(local_value - goldman_value, 4)
    return {
        'metric': metric,
        'goldman_public_value': goldman_value,
        'house_view_value': local_value,
        'difference': difference,
    }


def _difference_reasons(comparison: dict) -> list[str]:
    reasons = []
    recession = comparison.get('recession_probability_12m') or {}
    if recession.get('difference') is not None:
        difference = recession['difference']
        direction = '高い' if difference > 0 else '低い'
        reasons.append(
            f"House Viewの景気後退確率はGoldman公開見通しより{abs(difference):.1%}pt{direction}。"
        )

    growth = comparison.get('growth_view') or {}
    if growth.get('house_view_regime'):
        reasons.append(
            f"House Viewの主レジームは{growth['house_view_regime']}で、"
            "Goldman公開見通しの成長観と同じ方向かを監査対象にする。"
        )

    inflation = comparison.get('inflation_view') or {}
    if inflation.get('house_view_inflation_metric') is None:
        reasons.append('インフレは直接比較できるHouse View数値がないため、レジーム判断で補助比較する。')
    return reasons[:4]


def build_goldman_outlook_comparison() -> dict:
    """公開されている Goldman 見通しを比較対象として返す。"""
    house_view = _latest_house_view_proxy()
    goldman_forecasts = GOLDMAN_PUBLIC_OUTLOOK['forecasts']
    local_risks = house_view.get('risk_probabilities') or {}
    local_recession = (
        local_risks.get('recession_probability')
        or local_risks.get('recession')
        or local_risks.get('hard_landing')
    )

    comparison = {
        'recession_probability_12m': _numeric_comparison(
            'recession_probability_12m',
            goldman_forecasts['recession_probability_12m'],
            local_recession,
        ),
        'growth_view': {
            'goldman_public_view': 'above_consensus_us_growth',
            'house_view_regime': house_view.get('primary_regime'),
            'house_view_expansion_probability': (
                house_view.get('regime_probabilities') or {}
            ).get('expansion'),
        },
        'inflation_view': {
            'goldman_public_core_pce_dec_2026_yoy': goldman_forecasts['core_pce_dec_2026_yoy'],
            'house_view_inflation_metric': None,
            'note': 'House View はレジーム中心のため、直接比較できる数値がない場合は空欄にする。',
        },
    }
    latest_source_date = _latest_public_source_date()
    outlook_age_days = (
        timezone.localdate() - date.fromisoformat(latest_source_date)
    ).days

    return {
        'source_scope': 'free_public_goldman_sachs_pages',
        'generated_at': timezone.now().isoformat(),
        'free_public_sources': GOLDMAN_PUBLIC_SOURCES,
        'goldman_sachs_public_outlook': GOLDMAN_PUBLIC_OUTLOOK,
        'house_view_snapshot': house_view,
        'comparison': comparison,
        'audit': {
            'comparison_mode': 'public_static_outlook_vs_live_house_view',
            'latest_public_source_date': latest_source_date,
            'public_outlook_age_days': max(outlook_age_days, 0),
            'difference_reasons': _difference_reasons(comparison),
            'next_review': 'Goldman公開ページが更新された時点で比較基準日を更新する。',
        },
        'limitations': [
            'Goldman Sachs の有料・会員限定レポートは使わない。',
            '公開ページに数値がない項目は、無理に推定せず比較不能として扱う。',
        ],
    }
