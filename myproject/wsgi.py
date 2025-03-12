import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')  # 'myproject' をあなたのプロジェクト名に

application = get_wsgi_application()