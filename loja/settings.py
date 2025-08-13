# loja/settings.py
"""
Django settings for loja project.
Gerado por 'django-admin startproject' – ajustado para Render + Neon (Postgres).
"""

from pathlib import Path
import os
import dj_database_url  # pip install dj-database-url psycopg2-binary

BASE_DIR = Path(__file__).resolve().parent.parent

# ---------------- Segurança / Debug ----------------
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'django-insecure-f#bkh*7ca_)d@f^ic58ya@1ksk_-q+t0xcl&-@te9y^7^5vy%1')
DEBUG = os.getenv('DJANGO_DEBUG', 'true').lower() == 'true'

ALLOWED_HOSTS = [
    *[h for h in os.getenv('ALLOWED_HOSTS', '').split(',') if h],
    '127.0.0.1', 'localhost',
    'vendas-ozvo.onrender.com',  # domínio Render
]

# ---------------- Apps ----------------
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'vendas',
]

# ---------------- Middleware ----------------
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    # WhiteNoise para estáticos no Render
    'whitenoise.middleware.WhiteNoiseMiddleware',

    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'loja.urls'

# ---------------- Templates ----------------
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],  # usamos templates do app (APP_DIRS=True)
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'loja.wsgi.application'

# ---------------- Banco de dados ----------------
# Preferir DATABASE_URL do ambiente; fallback: Neon que você passou (sem channel_binding)
DEFAULT_SQLITE_URL = f"sqlite:///{BASE_DIR / 'db.sqlite3'}"
DEFAULT_NEON_URL = (
    "postgresql://neondb_owner:npg_ItqpbK5NLaD4"
    "@ep-silent-sea-adlwde7u-pooler.c-2.us-east-1.aws.neon.tech/neondb"
    "?sslmode=require"
)

DATABASE_URL = os.getenv("DATABASE_URL") or (DEFAULT_NEON_URL if os.getenv("RENDER") else DEFAULT_SQLITE_URL)

DATABASES = {
    "default": dj_database_url.parse(
        DATABASE_URL,
        conn_max_age=600,   # pool de conexões
        ssl_require=True if DATABASE_URL.startswith("postgres") else False,
    )
}

# ---------------- Validações de senha ----------------
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ---------------- Internacionalização ----------------
LANGUAGE_CODE = 'pt-br'
TIME_ZONE = 'America/Araguaina'
USE_I18N = True
USE_TZ = True

# ---------------- Arquivos estáticos e mídia ----------------
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# ---------------- Login/Logout ----------------
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'home'
LOGOUT_REDIRECT_URL = 'login'

# ---------------- HTTPS/Proxy/CSRF (produção) ----------------
# Render passa X-Forwarded-Proto
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

SECURE_SSL_REDIRECT = os.getenv('SECURE_SSL_REDIRECT', 'false').lower() == 'true'
SESSION_COOKIE_SECURE = os.getenv('SESSION_COOKIE_SECURE', 'false').lower() == 'true'
CSRF_COOKIE_SECURE = os.getenv('CSRF_COOKIE_SECURE', 'false').lower() == 'true'

CSRF_TRUSTED_ORIGINS = [
    *[o for o in os.getenv('CSRF_TRUSTED_ORIGINS', '').split(',') if o],
    'http://127.0.0.1:8000',
    'http://localhost:8000',
    'https://vendas-ozvo.onrender.com',  # domínio Render com HTTPS
]

# ---------------- Primary key default ----------------
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# =======================
# Mercado Pago (env vars)
# =======================
MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN", "")
MP_PUBLIC_KEY = os.getenv("MP_PUBLIC_KEY", "")
MP_WEBHOOK_URL = os.getenv("MP_WEBHOOK_URL", "")  # precisa ser HTTPS público em produção

# ============== Logging ==============
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {
        "vendas": {"handlers": ["console"], "level": "INFO"},
        "vendas.views": {"handlers": ["console"], "level": "INFO"},
        "mercadopago": {"handlers": ["console"], "level": "WARNING"},
        "django.request": {"handlers": ["console"], "level": "WARNING"},
    },
}

# ============================ E-mail (SMTP – Gmail) ============================
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = "smtp.gmail.com"
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_USE_SSL = False
EMAIL_TIMEOUT = 15

EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "yarinemanuelle7@gmail.com")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "gxir mcgn edke zgvm")  # use senha de app

DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "YARIN IMPRESSÕES <yarinemanuelle7@gmail.com>")
SERVER_EMAIL = os.getenv("SERVER_EMAIL", "YARIN IMPRESSÕES <yarinemanuelle7@gmail.com>")

# Base pública para montar URLs em e-mails (quando não temos request)
SITE_BASE_URL = os.getenv("SITE_BASE_URL", "http://localhost:8000")
