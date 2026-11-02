"""Einstellungen für die Tests: keine Geheimnisse, keine echte Datenbank, kein Mailversand.

Standard ist SQLite im Speicher. Mit PORTAL_TEST_POSTGRES=1 laufen die Tests gegen eine
temporäre, leere PostgreSQL-Instanz (nur CI-Job „postgres“ oder lokaler Wegwerf-Container).
"""
import os

os.environ["PORTAL_TEST"] = "1"
os.environ.pop("PORTAL_DB_NAME", None)

from .settings import *  # noqa: E402,F401,F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
if os.environ.get("PORTAL_TEST_POSTGRES") == "1":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("PORTAL_TEST_PG_NAME", "pruefung"),
            "USER": os.environ.get("PORTAL_TEST_PG_USER", "pruefung"),
            "PASSWORD": os.environ.get("PORTAL_TEST_PG_PASSWORD", ""),
            "HOST": os.environ.get("PORTAL_TEST_PG_HOST", "127.0.0.1"),
            "PORT": os.environ.get("PORTAL_TEST_PG_PORT", "5432"),
        }
    }
