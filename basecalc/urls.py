from django.urls import path
from . import views

app_name = 'basecalc'

urlpatterns = [
    path('', views.index, name='index'),
]
