# myproject/urls.py
from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView
from django.conf import settings

urlpatterns = [
    path('admin/', admin.site.urls),
    path('favicon.ico', RedirectView.as_view(url=settings.STATIC_URL + 'images/ico/favicon.ico')),
    # トップページ → dashboard.urls
    path('', include('dashboard.urls')),
    path('calendar/', include('events.urls')),
    path('prompt/', include('prompt.urls')),
    path('earning/', include('earning.urls')),
    path('sector/', include('sector.urls')),
    path('explanation/', include('explanation.urls')),
    path('person/', include('person.urls')),
    path('prediction/', include('prediction.urls')),
]
