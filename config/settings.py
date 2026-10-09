from pathlib import Path
import os

import dj_database_url
from decouple import config

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config('SECRET_KEY', default='django-insecure-dev-key-change-in-production')
DEBUG = config('DEBUG', default=not bool(os.environ.get('RENDER')), cast=bool)
ALLOWED_HOSTS = [
    host.strip()
    for host in config('ALLOWED_HOSTS', default='localhost,127.0.0.1').split(',')
    if host.strip()
]

RENDER_EXTERNAL_HOSTNAME = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)
    CSRF_TRUSTED_ORIGINS = [f"https://{RENDER_EXTERNAL_HOSTNAME}"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_htmx",
    "accounts",
    "dashboard",
    "patients",
    "bite_cases",
    "vaccination",
    "inventory",
    "reports",
    "settings_app",
    "audit",
    "pre_registrations",
    "doctor",
    "nurse",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounts.middleware.PatientPortalAccessMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "config.context_processors.settings_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

def _mysql_database_from_env():
    """Local MySQL config (defaults match XAMPP's MariaDB: root, no password, 127.0.0.1:3306)."""
    return {
        "ENGINE": "django.db.backends.mysql",
        "NAME": config('DB_NAME', default='animalbite'),
        "USER": config('DB_USER', default='root'),
        "PASSWORD": config('DB_PASSWORD', default=''),
        "HOST": config('DB_HOST', default='127.0.0.1'),
        "PORT": config('DB_PORT', default='3306'),
        "CONN_MAX_AGE": 600,
        "OPTIONS": {
            "charset": "utf8mb4",
            "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
        },
    }


def _ensure_mysql_database_exists(database):
    """Create the target MySQL database if it does not already exist (local/XAMPP dev)."""
    import MySQLdb

    conn = None
    try:
        conn = MySQLdb.connect(
            host=database["HOST"],
            port=int(database["PORT"]),
            user=database["USER"],
            password=database["PASSWORD"],
            charset="utf8mb4",
        )
        with conn.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{database['NAME']}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci"
            )
        conn.commit()
    except MySQLdb.Error:
        # Server unreachable or credentials pending: let Django raise on first real connect.
        pass
    finally:
        if conn is not None:
            conn.close()


DATABASE_URL = config('DATABASE_URL', default='')
if DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=600,
            conn_health_checks=True,
        )
    }
else:
    DATABASES = {"default": _mysql_database_from_env()}
    # Local development against XAMPP's MySQL/MariaDB: make sure the schema exists.
    if not os.environ.get('RENDER'):
        _ensure_mysql_database_exists(DATABASES["default"])

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Manila"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        ),
    },
}

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "dashboard:index"
LOGOUT_REDIRECT_URL = "accounts:login"

AUTH_USER_MODEL = "accounts.User"

X_FRAME_OPTIONS = "SAMEORIGIN"
# Same-origin framing is required by the in-app previews. Render's shared hostname
# also should not opt all sibling subdomains into HSTS or browser preload.
SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W019", "security.W021"]

if os.environ.get('RENDER'):
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000

# --- Semaphore SMS (RHUDumingag) ---
SEMAPHORE_API_KEY = config('SEMAPHORE_API_KEY', default='')
SEMAPHORE_SENDER_NAME = config('SEMAPHORE_SENDER_NAME', default='RHUDumingag')
SEMAPHORE_ENABLED = config('SEMAPHORE_ENABLED', default=False, cast=bool)
# Optional: base URL override (useful for mocking)
SEMAPHORE_API_URL = config('SEMAPHORE_API_URL', default='https://api.semaphore.co/api/v4/messages')
