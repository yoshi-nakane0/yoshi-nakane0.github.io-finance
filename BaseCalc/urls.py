from django.urls import path
from . import views

app_name = 'BaseCalc'

urlpatterns = [
    path('', views.index, name='index'),
]
