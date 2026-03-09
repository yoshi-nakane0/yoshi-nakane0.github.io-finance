import os
import shutil
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from dotenv import load_dotenv

load_dotenv()


def env_bool(key, default=False):
    value = os.getenv(key)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv('SECRET_KEY', 'default-development-key-never-use-in-production')

DEBUG = env_bool('DEBUG', True)


def build_database_from_url(database_url):
    parsed = urlparse(database_url)
    scheme = parsed.scheme.lower()

    if scheme in {'postgres', 'postgresql', 'pgsql'}:
        query_params = parse_qs(parsed.query)
        sslmode = (query_params.get('sslmode') or ['require'])[0]
        database_config = {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': unquote(parsed.path.lstrip('/')),
            'USER': unquote(parsed.username or ''),
            'PASSWORD': unquote(parsed.password or ''),
            'HOST': parsed.hostname or '',
            'PORT': str(parsed.port or ''),
        }
        if sslmode:
            database_config['OPTIONS'] = {'sslmode': sslmode}
        return database_config

    if scheme == 'sqlite':
        sqlite_path = unquote(parsed.path or '')
        if not sqlite_path or sqlite_path == '/':
            sqlite_path = str(BASE_DIR / 'db.sqlite3')
        return {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': Path(sqlite_path),
        }

    raise ValueError(f'Unsupported DATABASE_URL scheme: {scheme}')


def default_sqlite_database_path():
    if DEBUG:
        return BASE_DIR / 'db.sqlite3'
    return Path(os.getenv('SQLITE_DB_PATH', '/tmp/db.sqlite3'))


def bootstrap_sqlite_database(sqlite_path, source_path=None):
    sqlite_path = Path(sqlite_path)
    bundled_sqlite_path = Path(source_path) if source_path else BASE_DIR / 'db.sqlite3'
    if sqlite_path == bundled_sqlite_path:
        return
    if not bundled_sqlite_path.exists():
        return
    if sqlite_path.exists():
        try:
            if sqlite_schema_signature(sqlite_path) == sqlite_schema_signature(
                bundled_sqlite_path
            ):
                return
        except sqlite3.Error:
            pass
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(bundled_sqlite_path, sqlite_path)


def sqlite_schema_signature(sqlite_path):
    sqlite_path = Path(sqlite_path)
    with sqlite3.connect(sqlite_path) as connection:
        rows = connection.execute(
            """
            SELECT type, name, IFNULL(sql, '')
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
            ORDER BY type, name
            """
        ).fetchall()
    return tuple(rows)

ALLOWED_HOSTS = ['.vercel.app', 'localhost', '127.0.0.1']

INSTALLED_APPS = [
    'django.contrib.messages',
    'django.contrib.staticfiles',   
    'dashboard',
    'events',
    'prompt',
    'earning',
    'sector',
    'outlook',
    'explanation',
    'person',
    'prediction',
    'basecalc',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.humanize',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.middleware.gzip.GZipMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
]

ROOT_URLCONF = 'myproject.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'myproject.wsgi.application'

DATABASE_URL = (os.getenv('DATABASE_URL') or '').strip()
if DATABASE_URL:
    DATABASES = {'default': build_database_from_url(DATABASE_URL)}
else:
    sqlite_database_path = default_sqlite_database_path()
    bootstrap_sqlite_database(sqlite_database_path)
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': sqlite_database_path,
        }
    }

# キャッシュ設定
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'earnings-cache',
    }
}

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = 'ja'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True

# 静的ファイル設定
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
if DEBUG:
    STATICFILES_STORAGE = 'django.contrib.staticfiles.storage.StaticFilesStorage'
    WHITENOISE_USE_FINDERS = True
    WHITENOISE_AUTOREFRESH = True
else:
    STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
    WHITENOISE_USE_FINDERS = False
    WHITENOISE_AUTOREFRESH = False

WHITENOISE_MANIFEST_STRICT = False

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
