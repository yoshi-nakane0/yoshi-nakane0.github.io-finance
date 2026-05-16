# events/urls.py
from django.urls import path
from . import views

app_name = 'events'

urlpatterns = [
    path('', views.index, name='index'),
    path('more/', views.future_more_events, name='future_more'),
    path('past/', views.past_events, name='past_events'),
]
