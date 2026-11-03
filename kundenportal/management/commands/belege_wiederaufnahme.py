"""Setzt gesicherte, aber nicht abgeschlossene Maileingänge fort (z. B. nach einem Abbruch).

Für einen regelmäßigen systemd-Timer gedacht. Verarbeitet nur bereits verschlüsselt gesicherte
Eingänge; holt nichts aus dem Postfach und versendet nichts an Kunden.
"""
from django.core.management.base import BaseCommand

from kundenportal.belege import wiederaufnehmen


class Command(BaseCommand):
    help = "Setzt gesicherte, nicht abgeschlossene Maileingänge fort."

    def handle(self, *args, **opts):
        ok, fehler = wiederaufnehmen()
        self.stdout.write(f"{ok} fortgesetzt, {fehler} weiterhin mit Fehler")
