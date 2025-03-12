from django.contrib import admin
from django.urls import path, include
from dashboard import views  


urlpatterns = [
    path('', include('dashboard.urls')),
    path('schedule/', include('schedule.urls')),
    path('prompt/', include('prompt.urls')),
    path('earning/', include('earning.urls')),
    path('target/', include('target.urls')),
    path('control/', include('control.urls')),
    path('trending/', include('trending.urls')),
]