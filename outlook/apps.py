import os
import sys

from django.conf import settings
from django.apps import AppConfig


class OutlookConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "outlook"
    _startup_sync_done = False

    def ready(self):
        if self.__class__._startup_sync_done:
            return
        if not settings.DEBUG or "runserver" not in sys.argv:
            return
        if os.environ.get("RUN_MAIN") not in {"true", "1"} and "--noreload" not in sys.argv:
            return

        self.__class__._startup_sync_done = True
        from .views import sync_local_outlook_data_from_remote

        sync_local_outlook_data_from_remote(force=True)
