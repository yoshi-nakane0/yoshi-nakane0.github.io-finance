from django.contrib import admin

from .models import (
    DailyPriceObservation,
    FeatureSnapshot,
    ForecastSnapshot,
    Indicator,
    IndicatorSeries,
    MacroForecastOutcome,
    MacroForecastRun,
    MacroScenario,
    ModelValidationReport,
    Observation,
    ObservationVintage,
    PriceObservation,
    RawArchiveManifest,
    RegimeSnapshot,
    WorldStateSnapshot,
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


@admin.register(IndicatorSeries)
class IndicatorSeriesAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'source', 'frequency', 'category', 'is_active')
    list_filter = ('source', 'frequency', 'category', 'is_active')
    search_fields = ('code', 'name')
    ordering = ('category', 'code')


@admin.register(ObservationVintage)
class ObservationVintageAdmin(admin.ModelAdmin):
    list_display = (
        'series',
        'observation_date',
        'realtime_start',
        'realtime_end',
        'value',
        'fetched_at',
    )
    list_filter = ('series',)
    date_hierarchy = 'observation_date'
    ordering = ('series', '-observation_date', '-realtime_start')


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


@admin.register(DailyPriceObservation)
class DailyPriceObservationAdmin(admin.ModelAdmin):
    list_display = ('ticker', 'observation_date', 'close_price', 'source')
    list_filter = ('ticker', 'source')
    date_hierarchy = 'observation_date'
    ordering = ('ticker', '-observation_date')


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


@admin.register(MacroForecastRun)
class MacroForecastRunAdmin(admin.ModelAdmin):
    list_display = (
        'as_of',
        'primary_regime',
        'confidence',
        'data_quality_score',
        'model_version',
    )
    list_filter = ('primary_regime', 'model_version')
    date_hierarchy = 'as_of'
    search_fields = ('primary_regime', 'model_version')
    ordering = ('-as_of',)


@admin.register(MacroScenario)
class MacroScenarioAdmin(admin.ModelAdmin):
    list_display = ('run', 'name', 'probability', 'nikkei_bias')
    list_filter = ('name', 'nikkei_bias')
    search_fields = ('growth_view', 'inflation_view', 'policy_view', 'market_view')
    ordering = ('-run__as_of', 'name')


@admin.register(MacroForecastOutcome)
class MacroForecastOutcomeAdmin(admin.ModelAdmin):
    list_display = (
        'forecast',
        'target_date',
        'target_name',
        'predicted_prob',
        'actual_value',
        'brier_score',
        'direction_hit',
    )
    list_filter = ('target_name', 'direction_hit')
    date_hierarchy = 'target_date'
    ordering = ('-target_date', '-evaluated_at')


@admin.register(WorldStateSnapshot)
class WorldStateSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        'as_of_date',
        'cadence',
        'growth_score',
        'market_stress_score',
        'data_quality',
        'model_version',
    )
    list_filter = ('cadence', 'model_version')
    date_hierarchy = 'as_of_date'
    search_fields = ('model_version',)
    ordering = ('-as_of_date',)


@admin.register(FeatureSnapshot)
class FeatureSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        'as_of_date',
        'namespace',
        'target',
        'horizon',
        'model_version',
        'data_quality',
    )
    list_filter = ('namespace', 'target', 'horizon', 'model_version')
    date_hierarchy = 'as_of_date'
    search_fields = ('namespace', 'target', 'model_version', 'feature_hash')
    ordering = ('-as_of_date', '-created_at')


@admin.register(ModelValidationReport)
class ModelValidationReportAdmin(admin.ModelAdmin):
    list_display = (
        'evaluated_at',
        'model_version',
        'target',
        'horizon',
        'validation_method',
        'sample_count',
        'event_count',
    )
    list_filter = ('model_version', 'target', 'horizon', 'validation_method')
    date_hierarchy = 'evaluated_at'
    search_fields = ('model_version', 'target', 'horizon')
    ordering = ('-evaluated_at',)


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
