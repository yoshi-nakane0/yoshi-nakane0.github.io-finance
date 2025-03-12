# earning/urls.py
from django.urls import path
from . import views

app_name = 'earning'

urlpatterns = [
    path('', views.index, name='index'),
    # 将来的に必要なページ(例: detail, editなど)を追加
]