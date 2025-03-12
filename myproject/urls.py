from django.contrib import admin
from django.urls import path
from dashboard import views  # dashboard アプリケーションの views をインポート

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('dashboard.urls')),
]