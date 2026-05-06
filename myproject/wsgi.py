import logging
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')

application = get_wsgi_application()


def _ensure_runtime_migrations():
    """Vercel等のサーバーレス環境では build フックが走らないため、起動時に migrate を当てる。"""
    try:
        from django.core.management import call_command
        call_command('migrate', '--noinput', verbosity=0)
    except Exception:
        logging.getLogger(__name__).exception('startup migrate failed')


_ensure_runtime_migrations()

app = application
