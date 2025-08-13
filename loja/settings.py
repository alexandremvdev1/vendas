# loja/settings.py
from pathlib import Path
import os
import dj_database_url  # pip install dj-database-url psycopg2-binary
import cloudinary       # pip install cloudinary django-cloudinary-storage

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'django-insecure-f#bkh*7ca_)d@f^ic58ya@1ksk_-q+t0xcl&-@te9y^7^5vy%1')
DEBUG = os.getenv('DJANGO_DEBUG', 'true').lower() == 'true'

ALLOWED_HOSTS = [
    *[h for h in os.getenv('ALLOWED_HOSTS', '').split(',') if h],
    '127.0.0.1', 'localhost',
    'vendas-ozvo.onrender.com',
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

# ========= Cloudinary (ativação condicional) =========
# Só ativa se tiver CLOUDINARY_URL OU (cloud_name + api_key + api_secret)
USE_CLOUDINARY = bool(
    os.getenv('CLOUDINARY_URL') or
    (
        os.getenv('CLOUDINARY_CLOUD_NAME') and
        os.getenv('CLOUDINARY_API_KEY') and
        os.getenv('CLOUDINARY_API_SECRET')
    )
)

if USE_CLOUDINARY:
    INSTALLED_APPS += ['cloudinary', 'cloudinary_storage']

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

ROOT_URLCONF = 'loja.urls'

TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [],
    'APP_DIRS': True,
    'OPTIONS': {'context_processors': [
        'django.template.context_processors.request',
        'django.contrib.auth.context_processors.auth',
        'django.contrib.messages.context_processors.messages',
    ]},
}]

WSGI_APPLICATION = 'loja.wsgi.application'

# ---------------- Banco de dados ----------------
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
        conn_max_age=600,
        ssl_require=DATABASE_URL.startswith("postgres"),
    )
}

# ---------------- i18n ----------------
LANGUAGE_CODE = 'pt-br'
TIME_ZONE = 'America/Araguaina'
USE_I18N = True
USE_TZ = True

# ---------------- Static & Media ----------------
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# >>> Cloudinary: apenas quando USE_CLOUDINARY = True
if USE_CLOUDINARY:
    # 1) Configurar o SDK do Cloudinary (necessário para CloudinaryField gerar URLs)
    # Opção A: usar CLOUDINARY_URL (cloudinary://API_KEY:API_SECRET@CLOUD_NAME?secure=true)
    if os.getenv('CLOUDINARY_URL'):
        # O SDK lê CLOUDINARY_URL automaticamente; reforçamos secure=True
        cloudinary.config(secure=True)
    else:
        # Opção B: variáveis separadas
        cloudinary.config(
            cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME'),
            api_key=os.getenv('CLOUDINARY_API_KEY'),
            api_secret=os.getenv('CLOUDINARY_API_SECRET'),
            secure=True,
        )

    # 2) Config do pacote django-cloudinary-storage (usado se você tiver FileField/ImageField com storage)
    CLOUDINARY_STORAGE = {
        'CLOUD_NAME': os.getenv('CLOUDINARY_CLOUD_NAME'),
        'API_KEY': os.getenv('CLOUDINARY_API_KEY'),
        'API_SECRET': os.getenv('CLOUDINARY_API_SECRET'),
        'SECURE': True,
    }
    # Se desejar que uploads "genéricos" (não CloudinaryField) usem Cloudinary como storage:
    DEFAULT_FILE_STORAGE = 'cloudinary_storage.storage.MediaCloudinaryStorage'

# ---------------- Auth ----------------
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'home'
LOGOUT_REDIRECT_URL = 'login'

# ---------------- HTTPS/Proxy/CSRF ----------------
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = os.getenv('SECURE_SSL_REDIRECT', 'false').lower() == 'true'
SESSION_COOKIE_SECURE = os.getenv('SESSION_COOKIE_SECURE', 'false').lower() == 'true'
CSRF_COOKIE_SECURE = os.getenv('CSRF_COOKIE_SECURE', 'false').lower() == 'true'

CSRF_TRUSTED_ORIGINS = [
    *[o for o in os.getenv('CSRF_TRUSTED_ORIGINS', '').split(',') if o],
    'http://127.0.0.1:8000',
    'http://localhost:8000',
    'https://vendas-ozvo.onrender.com',
]

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# --------- Mercado Pago ---------
MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN", "")
MP_PUBLIC_KEY = os.getenv("MP_PUBLIC_KEY", "")
MP_WEBHOOK_URL = os.getenv("MP_WEBHOOK_URL", "")

# --------- Logging ---------
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

# --------- E-mail ---------
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = "smtp.gmail.com"
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_TIMEOUT = 15
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "yarinemanuelle7@gmail.com")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "gxir mcgn edke zgvm")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "YARIN IMPRESSÕES <yarinemanuelle7@gmail.com>")
SERVER_EMAIL = os.getenv("SERVER_EMAIL", "YARIN IMPRESSÕES <yarinemanuelle7@gmail.com>")

SITE_BASE_URL = os.getenv("SITE_BASE_URL", "http://localhost:8000")
