# dashboard/urls.py

from django.urls import path
from . import views

app_name = 'dashboard'

urlpatterns = [
    path('', views.index, name='index'),
    path('prediction/', views.prediction_list, name='prediction_list'),
    path('prediction/<int:pk>/', views.prediction_detail, name='prediction_detail'),
    path('test1/', views.test1_index, name='test1_index'),
    path('test1/<int:pk>/', views.test1_detail, name='test1_detail'),
]
