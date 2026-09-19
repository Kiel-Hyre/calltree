"""
Django settings for the Web-based Automated Call Tree System.

Every deployment-specific value is read from the environment (see .env.example)
so the same image can run locally, in Docker, and on Google Cloud Run.
"""

import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def env_int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or "").strip() or default)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float((os.getenv(name) or "").strip() or default)
    except ValueError:
        return default


# --------------------------------------------------------------------------
# Core
# --------------------------------------------------------------------------
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "insecure-development-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

# Cloud Run terminates TLS at the load balancer and forwards the scheme.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

if not DEBUG:
    SESSION_COOKIE_SECURE = env_bool("DJANGO_SECURE_COOKIES", True)
    CSRF_COOKIE_SECURE = env_bool("DJANGO_SECURE_COOKIES", True)
    SECURE_HSTS_SECONDS = env_int("DJANGO_HSTS_SECONDS", 0)
    # Safe behind Cloud Run because SECURE_PROXY_SSL_HEADER is set above.
    # Turn it off only when something in front already forces HTTPS.
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SSL_REDIRECT", True)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "core",
    "ingestion",
    "engine",
    "dissemination",
    "accountability",
    "api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "calltree.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "calltree.wsgi.application"
ASGI_APPLICATION = "calltree.asgi.application"

# --------------------------------------------------------------------------
# Database
#
# Relational storage backs Django auth, the employee directory, geofences and
# the immutable drill audit trail. Live safety status is additionally mirrored
# into Cloud Firestore (see FIRESTORE_ENABLED) for real-time dashboard sync.
# --------------------------------------------------------------------------
_DATABASE_URL = os.getenv("DATABASE_URL") or ""
if not _DATABASE_URL:
    # Development fallback. data/ is gitignored, so create it here rather
    # than making a fresh clone fail on `migrate` with "unable to open
    # database file".
    _sqlite_dir = BASE_DIR / "data"
    _sqlite_dir.mkdir(parents=True, exist_ok=True)
    _DATABASE_URL = "sqlite:///" + str(_sqlite_dir / "calltree.sqlite3")

DATABASES = {
    "default": dj_database_url.parse(
        _DATABASE_URL,
        conn_max_age=env_int("DB_CONN_MAX_AGE", 600),
        conn_health_checks=True,
    )
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --------------------------------------------------------------------------
# Django REST Framework
#
# The Vue 3 SPA is served from the same origin as the API, so session
# authentication plus CSRF is both the simplest and the safest option: no
# access token ever has to be parked in browser storage.
# --------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": env_int("API_PAGE_SIZE", 50),
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

if DEBUG:
    REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"].append(
        "rest_framework.renderers.BrowsableAPIRenderer"
    )

# --------------------------------------------------------------------------
# Internationalisation
# --------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = os.getenv("DJANGO_TIME_ZONE", "Asia/Manila")
USE_I18N = True
USE_TZ = True

# --------------------------------------------------------------------------
# Static files + the compiled Vue 3 single-page application
#
# `npm run build` in frontend/ emits a hashed bundle into frontend/dist.
# Django serves dist/index.html as the SPA shell and WhiteNoise serves the
# asset files, so one container ships both tiers.
# --------------------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

FRONTEND_DIST = Path(os.getenv("FRONTEND_DIST") or (BASE_DIR / "frontend" / "dist"))
SPA_INDEX_FILE = FRONTEND_DIST / "index.html"

STATICFILES_DIRS = [d for d in (BASE_DIR / "static", FRONTEND_DIST) if d.is_dir()]

# The Vite bundle is already content-hashed; the manifest storage would choke
# on the source maps and assets it does not know about.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
WHITENOISE_INDEX_FILE = False

# Vite dev server origin, used only when running the SPA in hot-reload mode.
VITE_DEV_SERVER_URL = os.getenv("VITE_DEV_SERVER_URL", "")

# --------------------------------------------------------------------------
# Email (SMTP fallback channel of the Cloud Dissemination Module)
# --------------------------------------------------------------------------
EMAIL_ENABLED = env_bool("EMAIL_ENABLED", False)
EMAIL_BACKEND = os.getenv(
    "DJANGO_EMAIL_BACKEND",
    "django.core.mail.backends.smtp.EmailBackend"
    if EMAIL_ENABLED
    else "django.core.mail.backends.console.EmailBackend",
)
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = env_int("EMAIL_PORT", 587)
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "DSO Safety Officer <noreply@example.com>")
EMAIL_TIMEOUT = env_int("EMAIL_TIMEOUT", 20)

# --------------------------------------------------------------------------
# Google Cloud Platform
# --------------------------------------------------------------------------
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID", "")

FIRESTORE_ENABLED = env_bool("FIRESTORE_ENABLED", False)
FIRESTORE_DATABASE = os.getenv("FIRESTORE_DATABASE", "(default)")
FIRESTORE_COLLECTION_PREFIX = os.getenv("FIRESTORE_COLLECTION_PREFIX", "calltree")

PUBSUB_ENABLED = env_bool("PUBSUB_ENABLED", False)
PUBSUB_TOPIC_NOTIFICATIONS = os.getenv("PUBSUB_TOPIC_NOTIFICATIONS", "calltree-notifications")
PUBSUB_PUSH_TOKEN = os.getenv("PUBSUB_PUSH_TOKEN", "")
# When Pub/Sub is disabled the dissemination module falls back to a local
# background-thread dispatcher so the system stays fully functional off-cloud.
DISPATCH_FALLBACK_THREADS = env_int("DISPATCH_FALLBACK_THREADS", 4)

# --------------------------------------------------------------------------
# M360 SMS Gateway
#
# Endpoint and payload field names are configurable: M360 has revised its
# broadcast API between versions, so a correction stays a .env edit.
# --------------------------------------------------------------------------
M360_ENABLED = env_bool("M360_ENABLED", False)
M360_BROADCAST_URL = os.getenv("M360_BROADCAST_URL", "https://api.m360.com.ph/v3/api/broadcast")
M360_BALANCE_URL = os.getenv("M360_BALANCE_URL", "https://api.m360.com.ph/v3/api/balance")
M360_APP_KEY = os.getenv("M360_APP_KEY", "")
M360_APP_SECRET = os.getenv("M360_APP_SECRET", "")
M360_SHORTCODE_MASK = os.getenv("M360_SHORTCODE_MASK", "")
M360_TIMEOUT = env_int("M360_TIMEOUT", 15)
M360_WEBHOOK_TOKEN = os.getenv("M360_WEBHOOK_TOKEN", "")
SMS_MAX_LENGTH = env_int("SMS_MAX_LENGTH", 320)

# --------------------------------------------------------------------------
# USGS Earthquake Notification Service ingestion
# --------------------------------------------------------------------------
USGS_FEED_URL = os.getenv(
    "USGS_FEED_URL",
    "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson",
)
USGS_WEBHOOK_TOKEN = os.getenv("USGS_WEBHOOK_TOKEN", "")
USGS_POLL_INTERVAL_SECONDS = env_int("USGS_POLL_INTERVAL_SECONDS", 60)

# Trigger Filter defaults (Proximity Analysis & Logic Engine). These are the
# fallback values; the Safety Officer can override them per location.
TRIGGER_MIN_MAGNITUDE = env_float("TRIGGER_MIN_MAGNITUDE", 5.0)
TRIGGER_RADIUS_KM = env_float("TRIGGER_RADIUS_KM", 300.0)
AUTO_TRIGGER_ENABLED = env_bool("AUTO_TRIGGER_ENABLED", False)

# --------------------------------------------------------------------------
# Drill / compliance policy
# --------------------------------------------------------------------------
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")
DEFAULT_RESPONSE_WINDOW_MINUTES = env_int("DEFAULT_RESPONSE_WINDOW_MINUTES", 5)
DEFAULT_REMINDER_INTERVAL_MINUTES = env_int("DEFAULT_REMINDER_INTERVAL_MINUTES", 2)
DEFAULT_MAX_REMINDERS = env_int("DEFAULT_MAX_REMINDERS", 2)

# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "{levelname} {asctime} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
    },
    "root": {"handlers": ["console"], "level": os.getenv("DJANGO_LOG_LEVEL", "INFO")},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}
