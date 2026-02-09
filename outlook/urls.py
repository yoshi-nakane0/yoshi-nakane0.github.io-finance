from django.urls import path

from . import views

app_name = "outlook"

urlpatterns = [
    path("", views.index, name="index"),
]

