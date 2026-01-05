# bias/urls.py
from django.urls import path
from . import views

app_name = 'bias'

urlpatterns = [
    path('', views.index, name='index'),
]
