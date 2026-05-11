from django.urls import include, path

from . import views

app_name = 'prediction'

urlpatterns = [
    path('', views.index, name='index'),
    path('authenticate/', views.authenticate, name='authenticate'),
    path('logout/', views.logout, name='logout'),
    path('refresh/', views.refresh, name='refresh'),
    path('api/', include('prediction.api.urls')),
]
