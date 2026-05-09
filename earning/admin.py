from django.contrib import admin

from earning.models import EarningsEvent, EarningsPrediction, Stock


@admin.register(Stock)
class StockAdmin(admin.ModelAdmin):
    list_display = ('symbol', 'market', 'company', 'industry', 'theme', 'watch_tier')
    search_fields = ('symbol', 'company')
    list_filter = ('market', 'industry')


@admin.register(EarningsEvent)
class EarningsEventAdmin(admin.ModelAdmin):
    list_display = ('stock', 'fiscal_period', 'event_date', 'fundamental', 'risk_value', 'updated_at')
    list_filter = ('fundamental', 'guidance_revision', 'market_interpretation')
    search_fields = ('stock__symbol', 'stock__company', 'fiscal_period')
    date_hierarchy = 'event_date'


@admin.register(EarningsPrediction)
class EarningsPredictionAdmin(admin.ModelAdmin):
    list_display = ('event', 'model_version', 'predicted_reaction', 'confidence', 'created_at')
    list_filter = ('model_version',)
