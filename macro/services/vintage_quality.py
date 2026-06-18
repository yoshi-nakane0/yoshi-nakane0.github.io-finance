"""改定前データを使える状態かを確認するレポート。"""

from __future__ import annotations

from django.db.models import Count
from django.utils import timezone

from ..models import Indicator, VintageObservation


def build_vintage_quality_report() -> dict:
    fred_indicators = list(
        Indicator.objects
        .filter(is_active=True, source=Indicator.Source.FRED)
        .order_by('fred_series_id')
    )
    vintage_counts = {
        row['indicator_id']: row['count']
        for row in (
            VintageObservation.objects
            .filter(indicator__in=fred_indicators)
            .values('indicator_id')
            .annotate(count=Count('id'))
        )
    }
    covered = [
        indicator for indicator in fred_indicators
        if vintage_counts.get(indicator.id, 0) > 0
    ]
    missing = [
        indicator.fred_series_id for indicator in fred_indicators
        if vintage_counts.get(indicator.id, 0) == 0
    ]
    total = len(fred_indicators)
    covered_count = len(covered)
    coverage = round(covered_count / total * 100, 2) if total else 100.0
    strict_ready = coverage >= 95.0
    warnings = []
    if not strict_ready:
        warnings.append(
            '改定前データのカバー率が不足しているため、一部は後から修正された統計で判断される可能性があります。'
        )

    return {
        'generated_at': timezone.now().isoformat(),
        'fred_active_series_count': total,
        'vintage_covered_series_count': covered_count,
        'vintage_coverage_pct': coverage,
        'strict_point_in_time_ready': strict_ready,
        'missing_vintage_series': missing,
        'covered_vintage_series': [indicator.fred_series_id for indicator in covered],
        'warnings': warnings,
    }
