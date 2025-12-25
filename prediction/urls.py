from django.urls import path

from . import views

app_name = 'prediction'

urlpatterns = [
    path('', views.index, name='index'),
    path('<int:row_id>/', views.detail, name='detail'),
]
