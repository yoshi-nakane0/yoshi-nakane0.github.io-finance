# myproject/urls.py
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    # トップページ → dashboard.urls
    path('', include('dashboard.urls')),
    path('calendar/', include('events.urls')),
    path('prompt/', include('prompt.urls')),
    path('earning/', include('earning.urls')),
    path('sector/', include('sector.urls')),
    path('control/', include('control.urls')),
    path('trending/', include('trending.urls')),
]
