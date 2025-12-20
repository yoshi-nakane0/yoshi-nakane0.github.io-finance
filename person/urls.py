from django.urls import path
from . import views

app_name = 'person'

urlpatterns = [
    path('', views.index, name='index'),
]
