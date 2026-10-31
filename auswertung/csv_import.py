"""Import von CSV-Kontoauszügen über eine Spaltenvorlage je Bank.

Deutsche Bank-CSVs unterscheiden sich in Spaltennamen, Trennzeichen und
Zeichensatz. Eine Vorlage beschreibt das, damit der Rest der Auswertung
immer dieselben Buchungen sieht.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .modell import Auszug, Buchung, iban_normal


@dataclass(frozen=True)
class CsvVorlage:
    datum: str
    betrag: str
    gegen_name: str
    gegen_iban: str
    zweck: str
    konto_iban: str | None = None  # Spalte mit eigener IBAN, falls vorhanden
    trennzeichen: str = ";"
    zeichensatz: str = "utf-8-sig"
    datumsformat: str = "%d.%m.%Y"
    dezimalkomma: bool = True
    status: str | None = None  # Spalte, in der vorgemerkte Umsätze markiert sind


# Spaltennamen des verbreiteten Exports „CSV-CAMT“ (u. a. Sparkassen).
# Vor Nutzung mit einer echten Datei der jeweiligen Bank abgleichen.
CSV_CAMT = CsvVorlage(
    datum="Buchungstag",
    betrag="Betrag",
    gegen_name="Beguenstigter/Zahlungspflichtiger",
    gegen_iban="Kontonummer/IBAN",
    zweck="Verwendungszweck",
    konto_iban="Auftragskonto",
    zeichensatz="cp1252",
    datumsformat="%d.%m.%y",
    status="Info",
)


class CsvFehler(ValueError):
    pass


def _betrag(text: str, dezimalkomma: bool) -> Decimal:
    t = text.strip().replace(" ", "").replace("€", "")
    if dezimalkomma:
        t = t.replace(".", "").replace(",", ".")
    try:
        return Decimal(t)
    except InvalidOperation as e:
        raise CsvFehler(f"Betrag nicht lesbar: {text!r}") from e


def lese_csv(daten: bytes, vorlage: CsvVorlage, konto_iban: str = "", quelle: str = "") -> Auszug:
    text = daten.decode(vorlage.zeichensatz)
    leser = csv.DictReader(io.StringIO(text), delimiter=vorlage.trennzeichen)
    fehlend = {vorlage.datum, vorlage.betrag, vorlage.zweck} - set(leser.fieldnames or [])
    if fehlend:
        raise CsvFehler(f"Spalten fehlen: {', '.join(sorted(fehlend))}")
    buchungen = []
    for nr, zeile in enumerate(leser, start=2):
        if vorlage.status and "vorgemerkt" in (zeile.get(vorlage.status) or "").lower():
            continue
        try:
            datum = datetime.strptime(zeile[vorlage.datum].strip(), vorlage.datumsformat).date()
        except ValueError as e:
            raise CsvFehler(f"Zeile {nr}: Datum nicht lesbar") from e
        iban = konto_iban or (zeile.get(vorlage.konto_iban or "", "") or "")
        buchungen.append(
            Buchung(
                iban_normal(iban),
                datum,
                _betrag(zeile[vorlage.betrag], vorlage.dezimalkomma),
                (zeile.get(vorlage.gegen_name) or "").strip(),
                iban_normal(zeile.get(vorlage.gegen_iban) or ""),
                (zeile.get(vorlage.zweck) or "").strip(),
                quelle,
            )
        )
    if not buchungen:
        raise CsvFehler("Keine Buchungen in der Datei")
    eigene = konto_iban or buchungen[0].konto_iban
    daten_ = [b.datum for b in buchungen]
    # CSV-Exporte enthalten meist keine Salden; die Saldenkette prüft dann nur CAMT.
    return Auszug(iban_normal(eigene), min(daten_), max(daten_), None, None, buchungen, quelle)


def lese_csv_datei(pfad: str | Path, vorlage: CsvVorlage = CSV_CAMT, konto_iban: str = "") -> Auszug:
    p = Path(pfad)
    return lese_csv(p.read_bytes(), vorlage, konto_iban, quelle=p.name)
