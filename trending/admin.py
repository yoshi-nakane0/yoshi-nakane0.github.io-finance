from django.contrib import admin
from .models import Analyst

@admin.register(Analyst)
class AnalystAdmin(admin.ModelAdmin):
    list_display = ('name', 'affiliation', 'category', 'score')
    list_filter = ('category',)
    search_fields = ('name', 'affiliation')
    list_editable = ('score',)