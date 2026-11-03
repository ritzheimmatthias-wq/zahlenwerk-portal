"""Prüfung hochgeladener Dateien am echten Inhalt, nicht nur an der Endung.

Erlaubt: PDF, DOCX, XLSX, CSV, JPG, PNG und CAMT-XML. Abgelehnt werden u. a.
ZIP-Archive, Dateien mit Makros, ausführbare Dateien und Dateien, deren Inhalt
nicht zur Endung passt.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass

from defusedxml import ElementTree as DET
from django.conf import settings

ERLAUBT = {"pdf", "docx", "xlsx", "csv", "jpg", "jpeg", "png", "xml"}
ERLAUBT_TEXT = "PDF, DOCX, XLSX, CSV, JPG, PNG oder CAMT (XML)"


class Abgelehnt(Exception):
    """Text ist für den Kunden bestimmt."""


@dataclass(frozen=True)
class Pruefergebnis:
    dateityp: str


_AUSFUEHRBAR = (b"MZ", b"\x7fELF", b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe", b"#!")


def _endung(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


# PDF-Namen, die aktive Inhalte einleiten. Geprüft wird die normalisierte Struktur, also nach
# Auflösen von Hex-Escapes (/Java#53cript) und nach Entpacken komprimierter Objektströme.
_PDF_AKTIV = frozenset({"/JavaScript", "/JS", "/Launch", "/RichMedia", "/XFA"})
_PDF_MAX_OBJEKTE = 200_000
_PDF_MAX_SEITEN = 5_000
_PDF_MAX_TIEFE = 64
_PDF_NAME = re.compile(rb"/([^\s/<>\[\](){}%]*)")
_PDF_FEHLER = "Das PDF ist beschädigt oder kann nicht sicher geprüft werden. Bitte neu als PDF speichern."
_PDF_AKTIV_TEXT = "Das PDF enthält ausführbare Inhalte (JavaScript oder Startbefehle). Bitte als einfaches PDF speichern."


def _pdf_name(roh: bytes) -> str:
    """Wandelt Hex-Escapes in PDF-Namen um (#4A → J), wie es ein Leseprogramm tut."""
    return "/" + re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), roh).decode("latin-1")


def _pdf_roh(d: bytes) -> None:
    # Zusatzschutz für unkomprimierte Objekte außerhalb der Querverweistabelle.
    for m in _PDF_NAME.finditer(d):
        if _pdf_name(m.group(1)) in _PDF_AKTIV:
            raise Abgelehnt(_PDF_AKTIV_TEXT)


def _pdf_struktur(wert, tiefe: int = 0) -> None:
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NameObject
    if tiefe > _PDF_MAX_TIEFE:
        raise Abgelehnt(_PDF_FEHLER)
    if isinstance(wert, IndirectObject):
        return  # jedes indirekte Objekt wird selbst einzeln geprüft
    if isinstance(wert, NameObject):
        if str(wert) in _PDF_AKTIV:
            raise Abgelehnt(_PDF_AKTIV_TEXT)
    elif isinstance(wert, DictionaryObject):
        for k, v in wert.items():
            if str(k) in _PDF_AKTIV:
                raise Abgelehnt(_PDF_AKTIV_TEXT)
            _pdf_struktur(v, tiefe + 1)
    elif isinstance(wert, ArrayObject):
        for v in wert:
            _pdf_struktur(v, tiefe + 1)


def _pdf(d: bytes) -> None:
    if not d[:1024].lstrip().startswith(b"%PDF-"):
        raise Abgelehnt("Die Datei ist kein gültiges PDF.")
    _pdf_roh(d)
    try:
        from pypdf import PdfReader
        from pypdf.generic import IndirectObject
        leser = PdfReader(io.BytesIO(d), strict=False)
        if leser.is_encrypted and not leser.decrypt(""):
            raise Abgelehnt("Das PDF ist mit einem Kennwort geschützt. Bitte ohne Kennwort speichern.")
        nummern: set[tuple[int, int]] = set()
        for gen, eintraege in leser.xref.items():
            nummern.update((n, gen) for n in eintraege)
        nummern.update((n, 0) for n in leser.xref_objStm)
        if not nummern or len(nummern) > _PDF_MAX_OBJEKTE:
            raise Abgelehnt(_PDF_FEHLER)
        _pdf_struktur(leser.trailer)
        for n, gen in sorted(nummern):
            if n == 0:
                continue
            obj = leser.get_object(IndirectObject(n, gen, leser))
            _pdf_struktur(obj)
        seiten = len(leser.pages)
        if not 1 <= seiten <= _PDF_MAX_SEITEN:
            raise Abgelehnt(_PDF_FEHLER)
    except Abgelehnt:
        raise
    except Exception:  # beschädigt, unbekannte Struktur oder Grenzwert überschritten
        raise Abgelehnt(_PDF_FEHLER) from None


def _office(d: bytes, art: str) -> None:
    ordner = {"docx": "word/", "xlsx": "xl/"}[art]
    try:
        z = zipfile.ZipFile(io.BytesIO(d))
    except zipfile.BadZipFile:
        raise Abgelehnt(f"Die Datei ist kein gültiges {art.upper()}-Dokument.") from None
    namen = z.namelist()
    if "[Content_Types].xml" not in namen or not any(n.startswith(ordner) for n in namen):
        raise Abgelehnt(f"Die Datei ist kein gültiges {art.upper()}-Dokument.")
    gesamt = 0
    for info in z.infolist():
        gesamt += info.file_size
        if info.compress_size and info.file_size / info.compress_size > 200:
            raise Abgelehnt("Die Datei ist ungewöhnlich stark gepackt und wird abgelehnt.")
        n = info.filename.lower()
        if n.endswith("vbaproject.bin") or "activex" in n or n.endswith((".exe", ".dll", ".scr", ".js", ".vbs", ".ps1", ".bat", ".cmd")):
            raise Abgelehnt("Die Datei enthält Makros oder eingebettete Programme. Bitte ohne Makros speichern.")
    if gesamt > 4 * settings.MAX_DATEIGROESSE:
        raise Abgelehnt("Die Datei ist entpackt zu groß.")
    typen = z.read("[Content_Types].xml").decode("utf-8", "replace").lower()
    if "macroenabled" in typen or "vbaproject" in typen:
        raise Abgelehnt("Die Datei enthält Makros. Bitte ohne Makros speichern.")


def _csv(d: bytes) -> None:
    if b"\0" in d[:65536]:
        raise Abgelehnt("Die CSV-Datei enthält Binärdaten.")
    for kodierung in ("utf-8-sig", "cp1252"):
        try:
            d.decode(kodierung)
            return
        except UnicodeDecodeError:
            continue
    raise Abgelehnt("Die CSV-Datei hat eine unbekannte Zeichenkodierung.")


def _xml(d: bytes) -> None:
    try:
        wurzel = DET.fromstring(d)
    except Exception:
        raise Abgelehnt("Die XML-Datei ist ungültig oder enthält unzulässige Bestandteile.") from None
    if "camt.05" not in (wurzel.tag or ""):
        raise Abgelehnt("Es werden nur XML-Kontoauszüge im Format CAMT angenommen.")


def pruefe_datei(name: str, daten: bytes) -> Pruefergebnis:
    if not daten:
        raise Abgelehnt("Die Datei ist leer.")
    if len(daten) > settings.MAX_DATEIGROESSE:
        raise Abgelehnt("Die Datei ist größer als 50 MB. Bitte teilen Sie sie auf.")
    art = _endung(name)
    if art not in ERLAUBT:
        raise Abgelehnt(f"Dieser Dateityp ist nicht erlaubt. Bitte als {ERLAUBT_TEXT} hochladen.")
    if daten.startswith(_AUSFUEHRBAR):
        raise Abgelehnt("Ausführbare Dateien werden abgelehnt.")
    if art == "pdf":
        _pdf(daten)
    elif art in ("docx", "xlsx"):
        _office(daten, art)
    elif art == "csv":
        if daten.startswith(b"PK\x03\x04"):
            raise Abgelehnt("Die Datei ist ein Archiv, keine CSV-Datei.")
        _csv(daten)
    elif art in ("jpg", "jpeg"):
        if not daten.startswith(b"\xff\xd8\xff"):
            raise Abgelehnt("Die Datei ist kein gültiges JPG-Bild.")
        art = "jpg"
    elif art == "png":
        if not daten.startswith(b"\x89PNG\r\n\x1a\n"):
            raise Abgelehnt("Die Datei ist kein gültiges PNG-Bild.")
    elif art == "xml":
        _xml(daten)
    return Pruefergebnis(art)


def sicherer_name(name: str) -> str:
    """Nur der Dateiname, ohne Pfad und Steuerzeichen, höchstens 200 Zeichen."""
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[\x00-\x1f\x7f\"<>|:*?]", "_", name).strip(" .")
    return (name or "datei")[-200:]
