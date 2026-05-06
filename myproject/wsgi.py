import logging
import os
import urllib.request
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')

logger = logging.getLogger(__name__)

MACRO_DATA_DB_URL = (
    'https://raw.githubusercontent.com/'
    'yoshi-nakane0/yoshi-nakane0.github.io-finance/macro-data/db.sqlite3'
)


def _is_serverless_runtime():
    return any(
        os.getenv(name)
        for name in ('VERCEL', 'AWS_LAMBDA_FUNCTION_NAME', 'LAMBDA_TASK_ROOT')
    )


def _fetch_macro_data_db():
    """サーバーレスのコールドスタート時に macro-data ブランチから観測値入り DB を取得。"""
    import time
    if not _is_serverless_runtime():
        return
    target = Path(os.environ.get('SQLITE_DB_PATH', '/tmp/db.sqlite3'))
    if target.exists() and target.stat().st_size > 1_000_000:
        print(f"[wsgi] reuse runtime db at {target} ({target.stat().st_size} bytes)")
        return
    t0 = time.perf_counter()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(MACRO_DATA_DB_URL, timeout=5) as response:
            payload = response.read()
        target.write_bytes(payload)
        print(f"[wsgi] fetched {len(payload)} bytes from macro-data in {time.perf_counter()-t0:.2f}s")
    except Exception as exc:
        print(f"[wsgi] fetch failed after {time.perf_counter()-t0:.2f}s: {exc}")


_fetch_macro_data_db()

from django.core.wsgi import get_wsgi_application  # noqa: E402

application = get_wsgi_application()


def _ensure_runtime_migrations():
    """フェッチ失敗時の空 DB やスキーマ更新時に備えて migrate を当てる。"""
    import time
    t0 = time.perf_counter()
    try:
        from django.core.management import call_command
        call_command('migrate', '--noinput', verbosity=0)
        print(f"[wsgi] migrate finished in {time.perf_counter()-t0:.2f}s")
    except Exception as exc:
        print(f"[wsgi] migrate failed after {time.perf_counter()-t0:.2f}s: {exc}")


_ensure_runtime_migrations()

app = application
