import os
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import django

BASE_DIR = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).lower() in {"1", "true", "yes"}


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-before-deploy")
SECRET_KEY_FALLBACKS = [key.strip() for key in os.getenv("DJANGO_SECRET_KEY_FALLBACKS", "").split(",") if key.strip()]
DEBUG = env_bool("DJANGO_DEBUG", True)
if not DEBUG and (
    SECRET_KEY == "dev-only-insecure-key-change-before-deploy" or len(SECRET_KEY) < 40
):
    raise RuntimeError("Set a strong DJANGO_SECRET_KEY before running with DJANGO_DEBUG=false.")
ALLOWED_HOSTS = [
    host.strip() for host in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]
CSRF_TRUSTED_ORIGINS = [
    origin.strip() for origin in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core.apps.CoreConfig",
]
try:
    import channels  # noqa: F401

    INSTALLED_APPS.append("channels")
except ImportError:
    pass

MIDDLEWARE = [
    "core.observability.RequestContextMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "core.middleware.SecurityHeadersMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
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
                "core.context_processors.identity_options",
            ]
        },
    }
]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"safe_json": {"()": "core.observability.SafeJSONFormatter"}},
    "handlers": {"safe_console": {
        "class": "logging.StreamHandler", "formatter": "safe_json",
    }},
    "root": {"handlers": ["safe_console"], "level": "INFO"},
    "loggers": {
        "django": {"handlers": ["safe_console"], "level": "WARNING", "propagate": False},
    },
}

if env_bool("DJANGO_SQLITE_TEST", False):
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
else:
    db_url = os.getenv("DATABASE_URL", "postgresql://veyra:veyra@localhost:5432/veyra")
    parsed = urlparse(db_url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise RuntimeError(
            "DATABASE_URL must use PostgreSQL. Set DJANGO_SQLITE_TEST=1 only for tests."
        )
    db_options = {"connect_timeout": 5}
    for option, values in parse_qs(parsed.query).items():
        if option in {"sslmode", "sslrootcert", "connect_timeout"}:
            db_options[option] = values[-1]
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(parsed.path.lstrip("/")),
            "USER": unquote(parsed.username or ""),
            "PASSWORD": unquote(parsed.password or ""),
            "HOST": parsed.hostname or "localhost",
            "PORT": parsed.port or 5432,
            "CONN_MAX_AGE": 0,
            "OPTIONS": db_options,
        }
    }

AUTH_USER_MODEL = "core.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "")
ACCOUNT_SECURITY_ENCRYPTION_KEY = os.getenv("ACCOUNT_SECURITY_ENCRYPTION_KEY", "")
ACCOUNT_SECURITY_ENCRYPTION_FALLBACK_KEYS = [key.strip() for key in os.getenv("ACCOUNT_SECURITY_ENCRYPTION_FALLBACK_KEYS", "").split(",") if key.strip()]
MFA_REQUIRE_PRIVILEGED = env_bool("MFA_REQUIRE_PRIVILEGED", not DEBUG)
MIDDLEWARE.append("core.identity.MFAEnforcementMiddleware")
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "workspace_home"
LOGOUT_REDIRECT_URL = "home"
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Jakarta"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
PAYMENT_CREDENTIALS_ENCRYPTION_KEY = os.getenv("PAYMENT_CREDENTIALS_ENCRYPTION_KEY", "")
PAYMENT_CREDENTIALS_ENCRYPTION_FALLBACK_KEYS = [key.strip() for key in os.getenv("PAYMENT_CREDENTIALS_ENCRYPTION_FALLBACK_KEYS", "").split(",") if key.strip()]
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "Veyra <accounts@veyra.app>")
MAIL_HOST = os.getenv("EMAIL_HOST", "localhost")
MAIL_PORT = int(os.getenv("EMAIL_PORT", "1025"))
MAIL_USERNAME = os.getenv("EMAIL_HOST_USER", "")
MAIL_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
MAIL_USE_TLS = env_bool("EMAIL_USE_TLS")
MAIL_USE_SSL = env_bool("EMAIL_USE_SSL")
if MAIL_USE_TLS and MAIL_USE_SSL:
    raise RuntimeError("Use either EMAIL_USE_TLS or EMAIL_USE_SSL, not both.")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
if RESEND_API_KEY:
    MAIL_BACKEND = "core.email_backend.ResendEmailBackend"
    MAIL_OPTIONS = {}
elif DEBUG:
    MAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
    MAIL_OPTIONS = {}
else:
    MAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    MAIL_OPTIONS = {
        "host": MAIL_HOST, "port": MAIL_PORT, "username": MAIL_USERNAME, "password": MAIL_PASSWORD,
        "use_tls": MAIL_USE_TLS, "use_ssl": MAIL_USE_SSL, "timeout": 10,
    }
if django.VERSION >= (6, 1):
    MAILERS = {
        "default": {
            "BACKEND": MAIL_BACKEND,
            "OPTIONS": MAIL_OPTIONS,
        }
    }
else:
    EMAIL_BACKEND = MAIL_BACKEND
    EMAIL_HOST = MAIL_HOST
    EMAIL_PORT = MAIL_PORT
    EMAIL_HOST_USER = MAIL_USERNAME
    EMAIL_HOST_PASSWORD = MAIL_PASSWORD
    EMAIL_USE_TLS = MAIL_USE_TLS
    EMAIL_USE_SSL = MAIL_USE_SSL
    EMAIL_TIMEOUT = 10
if not DEBUG:
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 12
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
CSRF_COOKIE_SAMESITE = "Lax"
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_HSTS_SECONDS", "3600"))
    if SECURE_HSTS_SECONDS <= 0:
        raise RuntimeError("Set a positive DJANGO_HSTS_SECONDS outside development.")
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
    }

if "channels" in INSTALLED_APPS:
    if env_bool("DJANGO_SQLITE_TEST", False):
        CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
    else:
        CHANNEL_LAYERS = {
            "default": {
                "BACKEND": "channels_redis.core.RedisChannelLayer",
                "CONFIG": {"hosts": [{"address": REDIS_URL, "socket_connect_timeout": 5}]},
            }
        }

if not DEBUG:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": os.getenv("REDIS_URL", "redis://localhost:6379/1"),
            "KEY_PREFIX": "veyra",
        }
    }

# Platform subscription billing uses a dedicated Midtrans merchant, separate from tenant payments.
VEYRA_BILLING_MIDTRANS_SERVER_KEY = os.environ.get("VEYRA_BILLING_MIDTRANS_SERVER_KEY", "")
VEYRA_BILLING_MIDTRANS_SIGNATURE_SECRET_KEY = os.environ.get("VEYRA_BILLING_MIDTRANS_SIGNATURE_SECRET_KEY", "")
VEYRA_BILLING_MIDTRANS_ENV = os.environ.get("VEYRA_BILLING_MIDTRANS_ENV", "sandbox")
VEYRA_BILLING_PUBLIC_ORIGIN = os.environ.get("VEYRA_BILLING_PUBLIC_ORIGIN", "").rstrip("/")

# Private durable copy of normalized uploads. Application routes enforce all media access.
SUPABASE_MEDIA_URL = os.getenv("SUPABASE_MEDIA_URL", "").rstrip("/")
SUPABASE_MEDIA_BUCKET = os.getenv("SUPABASE_MEDIA_BUCKET", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
media_settings = (SUPABASE_MEDIA_URL, SUPABASE_MEDIA_BUCKET, SUPABASE_SERVICE_ROLE_KEY)
if any(media_settings):
    if not all(media_settings) or urlparse(SUPABASE_MEDIA_URL).scheme != "https":
        raise RuntimeError("Configure an HTTPS Supabase media URL, private bucket and service-role key together.")
