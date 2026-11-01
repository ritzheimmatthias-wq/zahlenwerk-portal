"""Einstellungen des Kundenportals. Alle Geheimnisse kommen aus Umgebungsvariablen.

Laufzeitvariablen (nur Namen, nie Werte dokumentieren):
PORTAL_SECRET_KEY, PORTAL_MASTER_KEY, PORTAL_HOSTS, PORTAL_BASIS_URL, PORTAL_DEBUG,
PORTAL_DB_NAME, PORTAL_DB_USER, PORTAL_DB_PASSWORD, PORTAL_DB_HOST, PORTAL_DB_PORT,
PORTAL_DATEN, PORTAL_VIRENSCAN, PORTAL_CLAMD_SOCKET, PORTAL_CLAMD_HOST, PORTAL_CLAMD_PORT,
PORTAL_MAIL_HOST, PORTAL_MAIL_PORT, PORTAL_MAIL_USER, PORTAL_MAIL_PASSWORD, PORTAL_MAIL_ABSENDER,
PORTAL_MAIL_INTERN, PORTAL_TEST
"""
from pathlib import Path
import os

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name: str, standard: str | None = None) -> str | None:
    return os.environ.get(name, standard)


TEST = env("PORTAL_TEST") == "1"
DEBUG = env("PORTAL_DEBUG") == "1"

SECRET_KEY = env("PORTAL_SECRET_KEY")
MASTER_KEY = env("PORTAL_MASTER_KEY")
if TEST or DEBUG:
    SECRET_KEY = SECRET_KEY or "nur-fuer-tests-und-entwicklung-" + "x" * 30
    # Fester Schlüssel nur für Tests/Entwicklung, 32 Byte base64
    MASTER_KEY = MASTER_KEY or "dGVzdHNjaGx1ZXNzZWwtbnVyLWZ1ZXItdGVzdHMtMzI="
if not SECRET_KEY or not MASTER_KEY:
    raise ImproperlyConfigured("PORTAL_SECRET_KEY und PORTAL_MASTER_KEY müssen gesetzt sein.")

ALLOWED_HOSTS = [h.strip() for h in (env("PORTAL_HOSTS", "") or "").split(",") if h.strip()]
if TEST or DEBUG:
    ALLOWED_HOSTS += ["localhost", "127.0.0.1", "testserver"]
BASIS_URL = (env("PORTAL_BASIS_URL", "http://localhost:8000") or "").rstrip("/")
CSRF_TRUSTED_ORIGINS = [BASIS_URL] if BASIS_URL.startswith("https://") else []

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "kundenportal",
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
    "kundenportal.middleware.Sicherheitskopf",
    "kundenportal.middleware.Zugang",
]

ROOT_URLCONF = "portal.urls"
WSGI_APPLICATION = "portal.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

if env("PORTAL_DB_NAME"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("PORTAL_DB_NAME"),
            "USER": env("PORTAL_DB_USER"),
            "PASSWORD": env("PORTAL_DB_PASSWORD"),
            "HOST": env("PORTAL_DB_HOST", "localhost"),
            "PORT": env("PORTAL_DB_PORT", "5432"),
            "CONN_MAX_AGE": 60,
        }
    }
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "entwicklung.sqlite3"}}

AUTH_USER_MODEL = "kundenportal.Benutzer"
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
if TEST:
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # nur Tests, schneller
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "anmelden"

# Sitzung: 30 Minuten ohne Aktivität, endet mit dem Browser
SESSION_COOKIE_AGE = 30 * 60
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = True
SICHER = not (TEST or DEBUG)
SESSION_COOKIE_SECURE = SICHER
CSRF_COOKIE_SECURE = SICHER
SECURE_SSL_REDIRECT = SICHER
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 31536000 if SICHER else 0
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

LANGUAGE_CODE = "de-de"
TIME_ZONE = "Europe/Berlin"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Verschlüsselte Dateien liegen außerhalb der Datenbank
DATEN_VERZEICHNIS = Path(env("PORTAL_DATEN", str(BASE_DIR / "daten")) or "")
MAX_DATEIGROESSE = 50 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024

# Virenscan: "clamd" (Pflicht im Betrieb) oder "aus" (nur Tests/Entwicklung)
VIRENSCAN = env("PORTAL_VIRENSCAN", "aus" if (TEST or DEBUG) else "clamd")
if VIRENSCAN == "aus" and not (TEST or DEBUG):
    raise ImproperlyConfigured("Ohne Virenscan darf das Portal nicht betrieben werden.")
CLAMD_SOCKET = env("PORTAL_CLAMD_SOCKET")
CLAMD_HOST = env("PORTAL_CLAMD_HOST", "clamav")
CLAMD_PORT = int(env("PORTAL_CLAMD_PORT", "3310") or 3310)

# E-Mail (Versand über das bestehende Postfach bei Hostinger)
if TEST:
    EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
elif DEBUG:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
else:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = env("PORTAL_MAIL_HOST", "")
EMAIL_PORT = int(env("PORTAL_MAIL_PORT", "465") or 465)
EMAIL_HOST_USER = env("PORTAL_MAIL_USER", "")
EMAIL_HOST_PASSWORD = env("PORTAL_MAIL_PASSWORD", "")
EMAIL_USE_SSL = EMAIL_PORT == 465
EMAIL_USE_TLS = EMAIL_PORT == 587
EMAIL_TIMEOUT = 20
DEFAULT_FROM_EMAIL = env("PORTAL_MAIL_ABSENDER", "portal@zahlenwerk-immobilien.de")
MAIL_INTERN = env("PORTAL_MAIL_INTERN", "")

# Fristen
CODE_GUELTIG_MINUTEN = 10
CODE_MAX_VERSUCHE = 5
EINLADUNG_GUELTIG_TAGE = 7
DOWNLOAD_LINK_SEKUNDEN = 300
LOESCHFRIST_TAGE = 90
ANMELDUNG_MAX_FEHLVERSUCHE = 5
ANMELDUNG_SPERRE_MINUTEN = 15

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"konsole": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["konsole"], "level": "WARNING"},
}

# Statische Dateien: im Betrieb per collectstatic, in Tests/Entwicklung direkt aus der App
WHITENOISE_USE_FINDERS = TEST or DEBUG
WHITENOISE_AUTOREFRESH = TEST or DEBUG
if TEST or DEBUG:
    STATIC_ROOT = None
