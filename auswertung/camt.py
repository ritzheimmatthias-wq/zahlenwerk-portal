"""Import von CAMT.052 und CAMT.053 (ISO 20022) ohne Abhängigkeit vom Namensraum.

Hochgeladene Dateien sind nicht vertrauenswürdig, deshalb defusedxml.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from xml.etree.ElementTree import Element

from defusedxml import ElementTree as SafeET

from .modell import Auszug, Buchung, iban_normal


class CamtFehler(ValueError):
    pass


def _lokal(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _kinder(el: Element | None, name: str) -> list[Element]:
    if el is None:
        return []
    return [k for k in el if _lokal(k.tag) == name]


def _kind(el: Element | None, *pfad: str) -> Element | None:
    for name in pfad:
        if el is None:
            return None
        treffer = _kinder(el, name)
        el = treffer[0] if treffer else None
    return el


def _text(el: Element | None, *pfad: str) -> str:
    z = _kind(el, *pfad)
    return (z.text or "").strip() if z is not None else ""


def _suche(el: Element | None, name: str) -> Element | None:
    if el is None:
        return None
    for k in el.iter():
        if _lokal(k.tag) == name:
            return k
    return None


def _datum(el: Element | None) -> date | None:
    if el is None:
        return None
    t = _text(el, "Dt") or _text(el, "DtTm")
    if not t:
        return None
    return datetime.fromisoformat(t[:10]).date()


def _betrag_mit_vorzeichen(el: Element) -> Decimal:
    amt = _kind(el, "Amt")
    if amt is None or not (amt.text or "").strip():
        raise CamtFehler("Betrag fehlt")
    wert = Decimal(amt.text.strip())
    ind = _text(el, "CdtDbtInd")
    if ind == "DBIT":
        return -wert
    if ind == "CRDT":
        return wert
    raise CamtFehler(f"Unbekanntes Soll/Haben-Kennzeichen: {ind!r}")


def _gegenpartei(ntry: Element, eingang: bool) -> tuple[str, str]:
    """Bei Eingängen der Zahler (Dbtr), bei Ausgängen der Empfänger (Cdtr)."""
    parties = _suche(ntry, "RltdPties")
    rolle, konto = ("Dbtr", "DbtrAcct") if eingang else ("Cdtr", "CdtrAcct")
    partei = _kind(parties, rolle)
    # camt.053.001.08 und neuer: <Dbtr><Pty><Nm>
    name = _text(partei, "Nm") or _text(partei, "Pty", "Nm")
    iban = _text(_kind(parties, konto), "Id", "IBAN")
    return name, iban_normal(iban)


def _zweck(ntry: Element) -> str:
    teile = [(e.text or "").strip() for e in ntry.iter() if _lokal(e.tag) == "Ustrd"]
    if not teile:
        teile = [_text(ntry, "AddtlNtryInf")]
    return " ".join(t for t in teile if t)


def lese_camt(daten: bytes, quelle: str = "") -> list[Auszug]:
    try:
        wurzel = SafeET.fromstring(daten)
    except SafeET.ParseError as e:  # type: ignore[attr-defined]
        raise CamtFehler(f"Keine gültige XML-Datei: {e}") from e
    berichte = [e for e in wurzel.iter() if _lokal(e.tag) in ("Stmt", "Rpt")]
    if not berichte:
        raise CamtFehler("Kein CAMT.052/053-Auszug gefunden (Stmt/Rpt fehlt)")

    auszuege: list[Auszug] = []
    for stmt in berichte:
        iban = iban_normal(_text(stmt, "Acct", "Id", "IBAN"))
        anfang = ende = None
        anfang_dt = ende_dt = None
        for bal in _kinder(stmt, "Bal"):
            code = _text(bal, "Tp", "CdOrPrtry", "Cd")
            if code in ("OPBD", "PRCD"):
                anfang, anfang_dt = _betrag_mit_vorzeichen(bal), _datum(_kind(bal, "Dt"))
            elif code == "CLBD":
                ende, ende_dt = _betrag_mit_vorzeichen(bal), _datum(_kind(bal, "Dt"))
        zeitraum = _kind(stmt, "FrToDt")
        von = _datum_text(_text(zeitraum, "FrDtTm")) or anfang_dt
        bis = _datum_text(_text(zeitraum, "ToDtTm")) or ende_dt

        buchungen = []
        for ntry in _kinder(stmt, "Ntry"):
            if _text(ntry, "Sts") in ("PDNG", "INFO") or _text(ntry, "Sts", "Cd") in ("PDNG", "INFO"):
                continue  # vorgemerkte Umsätze gehören nicht in die Auswertung
            betrag = _betrag_mit_vorzeichen(ntry)
            datum = _datum(_kind(ntry, "BookgDt")) or _datum(_kind(ntry, "ValDt"))
            if datum is None:
                raise CamtFehler("Buchungsdatum fehlt")
            name, gegen_iban = _gegenpartei(ntry, betrag > 0)
            buchungen.append(
                Buchung(iban, datum, betrag, name, gegen_iban, _zweck(ntry), quelle)
            )
        kennung = _text(stmt, "Id")
        auszuege.append(Auszug(iban, von, bis, anfang, ende, buchungen, f"{quelle} {kennung}".strip()))
    return auszuege


def _datum_text(t: str) -> date | None:
    return datetime.fromisoformat(t[:10]).date() if t else None


def lese_camt_datei(pfad: str | Path) -> list[Auszug]:
    p = Path(pfad)
    return lese_camt(p.read_bytes(), quelle=p.name)
