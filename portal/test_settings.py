"""Einstellungen für die Tests: keine Geheimnisse, keine echte Datenbank, kein Mailversand."""
import os

os.environ["PORTAL_TEST"] = "1"
os.environ.pop("PORTAL_DB_NAME", None)

from .settings import *  # noqa: E402,F401,F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
