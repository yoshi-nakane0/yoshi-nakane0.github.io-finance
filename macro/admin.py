from django.contrib import admin

from .models import Indicator, Observation, PriceObservation, RegimeSnapshot


@admin.register(Indicator)
class IndicatorAdmin(admin.ModelAdmin):
    list_display = (
        'fred_series_id',
        'name_ja',
        'category',
        'importance',
        'frequency',
        'is_active',
        'display_order',
    )
    list_filter = ('category', 'importance', 'frequency', 'is_active')
    search_fields = ('fred_series_id', 'name_ja', 'name_en')
    ordering = ('display_order', 'fred_series_id')


@admin.register(Observation)
class ObservationAdmin(admin.ModelAdmin):
    list_display = (
        'indicator',
        'observation_date',
        'value',
        'prev_value',
        'yoy_change',
        'deviation_from_long_term',
    )
    list_filter = ('indicator',)
    date_hierarchy = 'observation_date'
    ordering = ('indicator', '-observation_date')


@admin.register(RegimeSnapshot)
class RegimeSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        'snapshot_date',
        'regime_label',
        'inflation_flag',
        'confidence',
    )
    list_filter = ('regime_label', 'inflation_flag')
    date_hierarchy = 'snapshot_date'
    ordering = ('-snapshot_date',)


@admin.register(PriceObservation)
class PriceObservationAdmin(admin.ModelAdmin):
    list_display = ('ticker', 'observation_month', 'close_price')
    list_filter = ('ticker',)
    date_hierarchy = 'observation_month'
    ordering = ('ticker', '-observation_month')
