from django.contrib import admin
from django.urls import path
from dashboard import views  # dashboard アプリケーションの views をインポート

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.hello_world, name='hello_world'),  # 空のパスに hello_world ビューを関連付け
]