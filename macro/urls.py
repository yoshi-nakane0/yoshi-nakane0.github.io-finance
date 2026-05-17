# macro/urls.py
from django.urls import path
from . import views

app_name = 'macro'

urlpatterns = [
    path('', views.index, name='index'),
    path('refresh/', views.refresh, name='refresh'),
    path(
        'recompute-crash-backtest/',
        views.recompute_crash_backtest,
        name='recompute_crash_backtest',
    ),
    path(
        'indicator/<str:series_id>/',
        views.indicator_detail,
        name='indicator_detail',
    ),
    path(
        'similar/<str:month>/',
        views.similar_period_detail,
        name='similar_detail',
    ),
]
