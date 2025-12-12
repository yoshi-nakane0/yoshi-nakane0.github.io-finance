from django.urls import path
from . import views

app_name = 'trending'

urlpatterns = [
    path('', views.index, name='index'),
]
