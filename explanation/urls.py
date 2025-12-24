# explanation/urls.py
from django.urls import path
from . import views

app_name = 'explanation'

urlpatterns = [
    path('', views.index, name='index'),
    path('us-macro/gdp/', views.us_macro_gdp, name='us_macro_gdp'),
    path('us-macro/ism/', views.us_macro_ism, name='us_macro_ism'),
    path('us-macro/pmi/', views.us_macro_pmi, name='us_macro_pmi'),
]
