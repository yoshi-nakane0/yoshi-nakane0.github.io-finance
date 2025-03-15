import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv()

SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-default-key-for-development')

DEBUG = os.environ.get('DEBUG', 'False') == 'false'

# Clean up the ALLOWED_HOSTS section
ALLOWED_HOSTS = [
    'yoshi-nakane0-github-io-finance.vercel.app',
    'yoshi-nakane0-github-io-finance-4rm6k1mok-yns-projects-de0414f8.vercel.app',
    '.vercel.app',  # Wildcards all Vercel domains
    'localhost',    # For local development
    '127.0.0.1'     # For local development
]
#ALLOWED_HOSTS = [
#    'yoshi-nakane0-github-io-finance.vercel.app',  # 以前から設定されていたホスト
#    'yoshi-nakane0-github-io-finance-bhczxq6om-yns-projects-de0414f8.vercel.app',  # 新しく追加するホスト
#]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'dashboard',
    'schedule',
    'prompt',
    'technical',
    'earning',
    'control',
    'trending',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
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

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


LANGUAGE_CODE = 'ja'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True

STATIC_URL = '/static/'  # 正しい形式
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, 'static'),
]
# Whitenoise の設定 (静的ファイルを圧縮)
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# Vercel 用の設定 (最後に追加)
import dj_database_url

# Vercel で PostgreSQL を使う場合 (必要に応じて設定)
if 'DATABASE_URL' in os.environ:
    DATABASES['default'] = dj_database_url.config(conn_max_age=600)

# Vercel での静的ファイル処理 (重要)
STATIC_ROOT = os.path.join(BASE_DIR, 'public')  # または '.vercel/output/static'