"""Import von PDF-Kontoauszügen mit Textebene über eine Layout-Vorlage je Bank.

Das PDF wird mit `pdftotext -layout` (Poppler, als eigenes Programm aufgerufen)
in Text umgewandelt. Danach erkennt eine Vorlage Buchungszeilen, Salden und
Blattüberträge. Gescannte PDFs ohne Textebene brauchen später Texterkennung.

Vorlage „Volksbank“: Layout der Kontoauszüge einer Volksbank
(Stand Oktober 2026), wahrscheinlich ähnlich bei anderen Volks- und Raiffeisenbanken.
"""
from __future__ import annotations

import re
import subprocess
from datetime import date
from decimal import Decimal
from pathlib import Path

from .modell import Auszug, Buchung, iban_normal


class PdfFehler(ValueError):
    pass


_BETRAG = r"([\d.]+,\d{2}) ([SH])"
_BUCHUNG = re.compile(r"^\s+(\d{2})\.(\d{2})\. (\d{2})\.(\d{2})\. (.+?)\s{3,}" + _BETRAG + r"\s*$")
_SALDO = re.compile(r"(alter|neuer) Kontostand vom (\d{2})\.(\d{2})\.(\d{4})\s+" + _BETRAG)
_UEBERTRAG = re.compile(r"Übertrag (auf|von) Blatt \d+\s+" + _BETRAG)
_IBAN = re.compile(r"IBAN:\s*([A-Z]{2}\d{2}(?: ?[\dA-Z]{4}){3,7}(?: ?[\dA-Z]{1,3})?)")
# Zeilen aus Kopf und Fuß jeder Seite, die nicht zu einer Buchung gehören
_KOPF_FUSS = re.compile(
    r"Übertrag (auf|von) Blatt|Kontostand vom|Bitte beachten|Kontokorrent|Kontoauszug|"
    r"erstellt am|IBAN:|Bu-Tag|Kreditrahmen|Berater|E-Mail:|BLZ"
)


def _betrag(text: str, soll_haben: str) -> Decimal:
    wert = Decimal(text.replace(".", "").replace(",", "."))
    return -wert if soll_haben == "S" else wert


def lese_volksbank_text(text: str, quelle: str = "") -> Auszug:
    zeilen = text.splitlines()
    iban_treffer = _IBAN.search(text)
    iban = iban_normal(iban_treffer.group(1)) if iban_treffer else ""

    alt = neu = None
    alt_datum = neu_datum = None
    roh: list[dict] = []
    aktuell: dict | None = None
    einrueckung = None
    for z in zeilen:
        m = _SALDO.search(z)
        if m:
            wert = _betrag(m.group(5), m.group(6))
            tag = date(int(m.group(4)), int(m.group(3)), int(m.group(2)))
            if m.group(1) == "alter":
                alt, alt_datum = wert, tag
            else:
                neu, neu_datum = wert, tag
            aktuell = None
            continue
        if _UEBERTRAG.search(z):
            aktuell = None
            continue
        m = _BUCHUNG.match(z)
        if m:
            aktuell = {"tag": int(m.group(1)), "monat": int(m.group(2)), "art": m.group(5).strip(),
                       "betrag": _betrag(m.group(6), m.group(7)), "text": []}
            einrueckung = len(z) - len(z.lstrip()) + len(m.group(1)) + 1
            roh.append(aktuell)
            continue
        if aktuell is not None and z.strip() and not _KOPF_FUSS.search(z):
            # Folgezeilen sind weiter eingerückt als das Buchungsdatum
            if einrueckung is not None and len(z) - len(z.lstrip()) > einrueckung:
                aktuell["text"].append(z.strip())
            else:
                aktuell = None

    if alt is None or neu is None:
        raise PdfFehler("Alter oder neuer Kontostand nicht gefunden, Layout passt nicht zur Vorlage")

    buchungen = []
    for r in roh:
        # Auszug über den Jahreswechsel: Dezember-Buchungen im Januar-Auszug gehören ins Vorjahr
        j = neu_datum.year - 1 if r["monat"] > neu_datum.month else neu_datum.year
        text = r["text"]
        name = text[0] if text else ""
        zweck = " ".join(text[1:]) if len(text) > 1 else r["art"]
        buchungen.append(Buchung(iban, date(j, r["monat"], r["tag"]), r["betrag"], name, "", zweck, quelle))

    summe = sum((b.betrag for b in buchungen), Decimal("0"))
    if alt + summe != neu:
        raise PdfFehler(
            f"Saldenprüfung fehlgeschlagen: alter Kontostand + Buchungen = {alt + summe}, neuer Kontostand = {neu}. "
            "Wahrscheinlich wurde eine Buchungszeile nicht erkannt."
        )
    return Auszug(iban, alt_datum, neu_datum, alt, neu, buchungen, quelle)


def pdf_zu_text(pfad: str | Path) -> str:
    try:
        erg = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", str(pfad), "-"],
                             capture_output=True, check=True, timeout=60)
    except FileNotFoundError as e:
        raise PdfFehler("pdftotext fehlt (Paket poppler-utils installieren)") from e
    except subprocess.CalledProcessError as e:
        raise PdfFehler("PDF konnte nicht gelesen werden") from e
    text = erg.stdout.decode("utf-8", errors="replace")
    if not text.strip():
        raise PdfFehler("PDF ohne Textebene (gescannt). Bitte CSV oder CAMT verwenden.")
    return text


def lese_pdf_datei(pfad: str | Path) -> Auszug:
    p = Path(pfad)
    return lese_volksbank_text(pdf_zu_text(p), quelle=p.name)
