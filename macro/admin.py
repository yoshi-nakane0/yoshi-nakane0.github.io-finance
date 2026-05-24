from django.contrib import admin

from .models import (
    ForecastSnapshot,
    Indicator,
    Observation,
    PriceObservation,
    RawArchiveManifest,
    RegimeSnapshot,
    WorldModelRun,
)


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
        'expanding_z_score',
        'rolling_10y_z_score',
        'rolling_5y_z_score',
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


@admin.register(ForecastSnapshot)
class ForecastSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        'as_of_date',
        'model_version',
        'target',
        'horizon',
        'prediction_value',
        'realized_value',
        'error',
        'created_at',
    )
    list_filter = ('model_version', 'target', 'horizon')
    date_hierarchy = 'as_of_date'
    search_fields = ('model_version', 'target', 'horizon', 'features_hash')
    ordering = ('-as_of_date', '-created_at')


@admin.register(RawArchiveManifest)
class RawArchiveManifestAdmin(admin.ModelAdmin):
    list_display = (
        'created_at',
        'reason',
        'storage_backend',
        'row_count',
        'size_bytes',
        'checksum',
    )
    list_filter = ('reason', 'storage_backend')
    date_hierarchy = 'created_at'
    search_fields = ('path', 'checksum')
    ordering = ('-created_at',)


@admin.register(WorldModelRun)
class WorldModelRunAdmin(admin.ModelAdmin):
    list_display = (
        'started_at',
        'cadence',
        'name',
        'status',
        'finished_at',
    )
    list_filter = ('cadence', 'status')
    date_hierarchy = 'started_at'
    search_fields = ('name', 'error')
    ordering = ('-started_at',)
