from django.urls import path
from . import views

app_name = 'basecalc'

urlpatterns = [
    path('', views.index, name='index'),
    path('history/', views.history, name='history'),
    path('validation/', views.validation, name='validation'),
    path('api/snapshot/', views.snapshot_api, name='snapshot_api'),
    path('api/performance/', views.performance_api, name='performance_api'),
]
