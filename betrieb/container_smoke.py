"""Smoke-Test für das Portal-Image, nur in der CI und lokal (docker run --network none).

Prüft mit ausschließlich temporären Testwerten: alle Pakete aus pyproject.toml importierbar,
Django-Systemprüfung, Migrationen auf einer leeren Wegwerf-Datenbank, statische Dateien
gesammelt und ausgeliefert, Anmeldeseite erreichbar, PDF-Erzeugung und PDF-Prüfung verfügbar.
Startet keinen Server nach außen und berührt keine echten Daten.
"""
import importlib
import os
import sys
import tempfile
import tomllib
from pathlib import Path

APP = Path("/app")
# Wie gunicorn und manage.py: das Arbeitsverzeichnis /app zuerst, nicht die installierte Kopie.
sys.path.insert(0, str(APP))
pakete = tomllib.loads((APP / "pyproject.toml").read_text())["tool"]["setuptools"]["packages"]
for p in pakete:
    importlib.import_module(p)
print("Pakete importierbar:", ", ".join(pakete))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "portal.settings")
import portal.settings as einstellungen  # noqa: E402

wegwerf = tempfile.mkdtemp(prefix="smoke-")
einstellungen.DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": f"{wegwerf}/smoke.sqlite3"}}
einstellungen.DATEN_VERZEICHNIS = Path(wegwerf) / "daten"

import django  # noqa: E402

django.setup()
from django.conf import settings  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.db.migrations.executor import MigrationExecutor  # noqa: E402
from django.db import connection  # noqa: E402
from django.test import Client  # noqa: E402

call_command("check", fail_level="ERROR")
call_command("migrate", verbosity=0)
offen = MigrationExecutor(connection).migration_plan(MigrationExecutor(connection).loader.graph.leaf_nodes())
assert not offen, f"Nicht angewendete Migrationen: {offen}"
print("Systemprüfung und Migrationen erfolgreich")

static = Path(settings.STATIC_ROOT)
quellen = sorted(p.relative_to(APP / "kundenportal" / "static") for p in (APP / "kundenportal" / "static").rglob("*") if p.is_file())
fehlend = [str(q) for q in quellen if not (static / q).is_file()]
assert quellen and not fehlend, f"Statische Dateien fehlen im Image: {fehlend}"

c = Client()
for pfad in ["/anmelden/", *(f"/{settings.STATIC_URL.strip('/')}/{q.as_posix()}" for q in quellen)]:
    antwort = c.get(pfad, secure=True)
    assert antwort.status_code == 200, f"{pfad}: HTTP {antwort.status_code}"
print("Anmeldeseite und", len(quellen), "statische Dateien ausgeliefert")

import weasyprint  # noqa: E402
from kundenportal.dateipruefung import pruefe_datei  # noqa: E402

pdf = weasyprint.HTML(string="<h1>Smoke-Test</h1>").write_pdf()
assert pruefe_datei("smoke.pdf", pdf).dateityp == "pdf"
importlib.import_module("gunicorn")
print("PDF-Erzeugung und -Prüfung verfügbar. Smoke-Test bestanden.")
sys.exit(0)
