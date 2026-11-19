"""Kunden-Checklisten als PDF je Leistung, Download beim Upload-Schritt eines Auftrags.

Die Dateien sind Blankovorlagen ohne Kundendaten. Ausgeliefert wird nur der geprüfte
Dateistand (SHA-256); eine geänderte Datei wird nicht ausgeliefert, bis die Prüfsumme hier
bewusst angepasst ist.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

ORDNER = Path(__file__).resolve().parent / "checklisten"


@dataclass(frozen=True)
class Checkliste:
    titel: str
    datei: str
    sha256: str


CHECKLISTEN: dict[str, Checkliste] = {
    "betriebskosten": Checkliste(
        titel="Checkliste Betriebskostenabrechnung",
        datei="Checkliste Betriebskostenabrechnung.pdf",
        sha256="08959a17f6029294b1ea7d211686642d1de391ae5265fb4badf72256cf84d07f",
    ),
}


class Pruefsummenfehler(Exception):
    pass


def fuer(leistung: str) -> Checkliste | None:
    return CHECKLISTEN.get(leistung)


def lesen(c: Checkliste) -> bytes:
    inhalt = (ORDNER / c.datei).read_bytes()
    if hashlib.sha256(inhalt).hexdigest() != c.sha256:
        raise Pruefsummenfehler(c.datei)
    return inhalt
