from django.urls import path

from . import views

app_name = 'prediction_api'

urlpatterns = [
    path('sentiment/summary/', views.summary, name='sentiment_summary'),
    path('sentiment/articles/', views.articles, name='sentiment_articles'),
]
