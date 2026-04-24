# earning/urls.py
from django.urls import path
from . import views

app_name = 'earning'

urlpatterns = [
    path('completed/', views.completed, name='completed'),
    path('', views.index, name='index'),
]
