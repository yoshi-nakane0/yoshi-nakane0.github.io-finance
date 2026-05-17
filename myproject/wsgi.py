import logging
import os
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')

logger = logging.getLogger(__name__)

def _is_serverless_runtime():
    return any(
        os.getenv(name)
        for name in ('VERCEL', 'AWS_LAMBDA_FUNCTION_NAME', 'LAMBDA_TASK_ROOT')
    )


def _prepare_runtime_db_path():
    if not _is_serverless_runtime():
        return
    target = Path(os.environ.get('SQLITE_DB_PATH', '/tmp/db.sqlite3'))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.exception('failed to prepare SQLite runtime directory')


_prepare_runtime_db_path()

from django.core.wsgi import get_wsgi_application  # noqa: E402

application = get_wsgi_application()


def _ensure_runtime_migrations():
    """フェッチ失敗時の空 DB やスキーマ更新時に備えて migrate を当てる。"""
    if not _is_serverless_runtime():
        return
    try:
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        from django.core.management import call_command

        executor = MigrationExecutor(connection)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if not plan:
            return
        call_command('migrate', '--noinput', verbosity=0)
    except Exception:
        logger.exception('startup migrate failed')


_ensure_runtime_migrations()


def _ensure_runtime_superuser():
    if not _is_serverless_runtime():
        return
    try:
        from myproject.auth import ensure_env_superuser

        ensure_env_superuser()
    except Exception:
        logger.exception('startup superuser provisioning failed')


_ensure_runtime_superuser()

app = application
