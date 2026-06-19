from django.urls import path

from . import views

app_name = 'explanation'

urlpatterns = [
    path('', views.index, name='index'),
    path('audit/', views.audit, name='audit'),
    path('api/latest/', views.latest_api, name='latest_api'),
    path('precompute/', views.precompute, name='precompute'),
]
