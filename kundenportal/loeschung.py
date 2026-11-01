"""Löschung von Unterlagen: Datei und Schlüssel werden entfernt, protokolliert wird ohne Inhalt."""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from . import tresor
from .models import Auftrag, Dokument, protokoll


def loesche_dokumente(qs, benutzer=None, grund: str = "") -> int:
    n = 0
    for d in qs:
        with transaction.atomic():
            tresor.entfernen(d.speicher_id)
            d.schluessel = b""
            d.geloescht = timezone.now()
            d.save(update_fields=["schluessel", "geloescht"])
            protokoll("dokument_geloescht", benutzer, f"dokument:{d.pk}", f"auftrag:{d.auftrag_id} grund:{grund}")
        n += 1
    return n


def faellige_auftraege(heute=None) -> list[Auftrag]:
    heute = heute or timezone.now().date()
    kandidaten = Auftrag.objects.filter(dokumente__geloescht__isnull=True).distinct()
    return [a for a in kandidaten if a.loeschdatum and a.loeschdatum <= heute]


def loeschlauf(heute=None) -> int:
    n = 0
    for a in faellige_auftraege(heute):
        n += loesche_dokumente(a.dokumente.filter(geloescht__isnull=True), None, "frist")
    return n
