"""Betriebskostenabrechnung und Jahresreports im Portal.

Die Fachlogik liegt vollständig in `betriebskosten/`. Hier werden nur
Berechnungsstände und Report-Snapshots unveränderlich in der Datenbank
abgelegt, Freigabeschritte geprüft und Rechte durchgesetzt. Es wird nichts
versendet und an keinem Vertrag etwas geändert.
"""
from __future__ import annotations

import hashlib
import json
import logging
import tomllib
from datetime import date

from django.db import transaction
from django.utils import timezone

from betriebskosten.eingabe import Eingabefehler, aus_dict
from betriebskosten.kostenverlauf import bericht
from betriebskosten.rechnen import berechne
from betriebskosten.reportablage import inhalt_hash
from betriebskosten.reportregeln import Reportkonfiguration, konfiguration_aus_dict
from betriebskosten.version import STATUS, als_dict, eingabe_hash, ergebnis_hash, kanonisch, stand_fehler
from betriebskosten.vorauszahlung import Reportfehler, analysiere

from .models import Abrechnungslauf, Auftrag, Objekt, Report, Statuswechsel, protokoll

log = logging.getLogger(__name__)

MAX_TOML = 2 * 1024 * 1024
REPORT_STATUS = [s for s, _ in Report.STATUS]


class Fachfehler(Exception):
    """Text ist für Zahlenwerk bestimmt."""


def _toml(daten: bytes, was: str) -> dict:
    if len(daten) > MAX_TOML:
        raise Fachfehler(f"{was}: Datei ist größer als 2 MB.")
    try:
        return tomllib.loads(daten.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise Fachfehler(f"{was}: keine gültige TOML-Datei ({str(e)[:120]}).") from None


def konfiguration(daten: bytes | None) -> Reportkonfiguration:
    if not daten:
        return Reportkonfiguration()
    try:
        return konfiguration_aus_dict(_toml(daten, "Report-Regeln"))
    except (Eingabefehler, KeyError, ValueError) as e:
        raise Fachfehler(f"Report-Regeln: {e}") from None


# --- Zuordnung zu Objekt, Auftrag und Zeitraum (Befund B1) ----------------------------

def _zuordnung(auftrag: Auftrag, roh: dict, erg) -> dict:
    """Prüft vor jeder Berechnung, dass die Daten zu genau diesem Objekt, Auftrag und Jahr gehören.

    Verbindlich ist die Objektkennung aus dem Portal (`objekt.portal_id`), nicht Name oder Anschrift.
    Das Auftragsjahr ist das Jahr, in dem der Abrechnungszeitraum endet; bei Objekten mit Kalenderjahr muss
    der Zeitraum auch in diesem Jahr beginnen. Ein Zeitraum über mehr als zwölf Monate wird abgelehnt.
    """
    o = roh.get("objekt") if isinstance(roh.get("objekt"), dict) else {}
    kennung = str(o.get("portal_id", "")).strip().lower()
    if not kennung:
        raise Fachfehler("Abrechnungsdaten: objekt.portal_id fehlt. Bitte die Objektkennung aus dem Portal eintragen "
                         f"(für dieses Objekt: {auftrag.objekt.kennung}).")
    if kennung != str(auftrag.objekt.kennung):
        raise Fachfehler("Abrechnungsdaten gehören zu einem anderen Objekt: objekt.portal_id passt nicht zu "
                         f"„{auftrag.objekt.bezeichnung}“. Nichts wurde gespeichert.")
    von, bis = erg.daten.zeitraum.von, erg.daten.zeitraum.bis
    if bis.year != auftrag.jahr:
        raise Fachfehler(f"Abrechnungszeitraum endet am {bis:%d.%m.%Y}; der Auftrag gilt für {auftrag.jahr}. Nichts wurde gespeichert.")
    if auftrag.objekt.wirtschaftsjahr.strip().lower() == "kalenderjahr" and von.year != auftrag.jahr:
        raise Fachfehler(f"Abrechnungszeitraum beginnt am {von:%d.%m.%Y}; das Objekt rechnet nach Kalenderjahr {auftrag.jahr} ab.")
    try:
        grenze = von.replace(year=von.year + 1)
    except ValueError:
        grenze = date(von.year + 1, 3, 1)
    if bis >= grenze:
        raise Fachfehler("Abrechnungszeitraum ist länger als zwölf Monate.")
    return {"objekt_kennung": kennung, "objekt": auftrag.objekt_id, "auftrag": auftrag.pk, "auftrag_jahr": auftrag.jahr}


def _zuordnung_fehler(lauf: Abrechnungslauf) -> list[str]:
    z = lauf.stand.get("zuordnung") if isinstance(lauf.stand, dict) else None
    if not isinstance(z, dict):
        return ["Stand ohne Objektzuordnung (Altstand, bitte neu importieren)"]
    a = lauf.auftrag
    fehler = []
    if z.get("objekt_kennung") != str(a.objekt.kennung) or z.get("objekt") != a.objekt_id:
        fehler.append("Stand gehört zu einem anderen Objekt")
    if z.get("auftrag") != a.pk or z.get("auftrag_jahr") != a.jahr:
        fehler.append("Stand gehört zu einem anderen Auftrag")
    try:
        if date.fromisoformat(lauf.stand["ergebnis"]["abrechnungszeitraum"]["bis"]).year != a.jahr:
            fehler.append("Abrechnungszeitraum passt nicht zum Auftragsjahr")
    except (KeyError, TypeError, ValueError):
        fehler.append("Abrechnungszeitraum fehlt")
    return fehler


# --- Unveränderlicher Statusverlauf (Befund W6) -------------------------------------------

def _anker_lauf(lauf: Abrechnungslauf) -> str:
    return hashlib.sha256(f"lauf:{lauf.auftrag_id}:{lauf.version}:{lauf.eingabe_hash}:{lauf.ergebnis_hash}".encode()).hexdigest()


def _anker_report(r: Report) -> str:
    return hashlib.sha256(f"report:{r.objekt_id}:{r.art}:{r.nummer}:{r.inhalt_hash}".encode()).hexdigest()


def _ereignis(*, lauf=None, report=None, status: str, benutzer, notiz: str = "") -> Statuswechsel:
    qs = Statuswechsel.objects.filter(lauf=lauf) if lauf else Statuswechsel.objects.filter(report=report)
    letzter = qs.order_by("-id").first()
    w = Statuswechsel(lauf=lauf, report=report, status=status, benutzer=benutzer, notiz=notiz[:500],
                      zeit=timezone.now(), bezug_hash=_anker_lauf(lauf) if lauf else _anker_report(report),
                      vorher_hash=letzter.eintrag_hash if letzter else "")
    w.eintrag_hash = w.berechne_hash()
    w.save()
    return w


def verlauf_fehler(ziel, folge: list[str], anker: str, aktuell: str) -> list[str]:
    """Prüft Kette, Reihenfolge, Inhaltsbezug und Übereinstimmung mit dem aktuellen Status."""
    eintraege = list(ziel.statuswechsel.order_by("id"))
    if not eintraege:
        return ["Statusverlauf fehlt"]
    fehler = []
    vorher = ""
    for i, w in enumerate(eintraege):
        if w.vorher_hash != vorher or w.eintrag_hash != w.berechne_hash():
            fehler.append("Statusverlauf ist verändert")
            break
        if w.bezug_hash != anker:
            fehler.append("Statusverlauf gehört zu einem anderen Inhalt")
            break
        if i >= len(folge) or w.status != folge[i]:
            fehler.append("Statusverlauf ist lückenhaft oder in falscher Reihenfolge")
            break
        vorher = w.eintrag_hash
    if not fehler and eintraege[-1].status != aktuell:
        fehler.append("Aktueller Status passt nicht zum Statusverlauf")
    return fehler


# --- Vollständige Prüfung eines Abrechnungslaufs (Befund W1) ------------------------------

def lauf_fehler(lauf: Abrechnungslauf) -> list[str]:
    stand = lauf.stand if isinstance(lauf.stand, dict) else {}
    fehler = stand_fehler(stand)  # Prüfsummen neu aus Eingabe und Ergebnis, Warnungszähler nachgezählt
    try:
        if (stand.get("version") != lauf.version or stand.get("eingabe_hash") != lauf.eingabe_hash
                or stand.get("ergebnis_hash") != lauf.ergebnis_hash
                or stand["ergebnis"]["regeln_version"] != lauf.regeln_version
                or stand.get("rote_warnungen") != lauf.rote_warnungen or stand.get("gelbe_warnungen") != lauf.gelbe_warnungen):
            fehler.append("Datenbankfelder passen nicht zum gespeicherten Stand")
    except (KeyError, TypeError):
        fehler.append("Stand ist unvollständig")
    fehler += _zuordnung_fehler(lauf)
    fehler += verlauf_fehler(lauf, list(STATUS), _anker_lauf(lauf), lauf.status)
    return fehler


def lauf_pruefen(lauf: Abrechnungslauf) -> None:
    fehler = lauf_fehler(lauf)
    if fehler:
        protokoll("abrechnung_integritaet", None, f"lauf:{lauf.pk}", "; ".join(fehler))
        log.error("Abrechnungslauf %s nicht unverfälscht: %s", lauf.pk, "; ".join(fehler))
        raise Fachfehler(f"Berechnungsstand Version {lauf.version} ist nicht unverfälscht ({'; '.join(fehler)}). "
                         "Freigabe und Reports sind gesperrt.")


def stand_unverfaelscht(lauf: Abrechnungslauf) -> bool:
    return not lauf_fehler(lauf)


@transaction.atomic
def lauf_anlegen(auftrag: Auftrag, daten: bytes, benutzer) -> tuple[Abrechnungslauf, bool]:
    """Berechnet die Abrechnung und legt einen neuen Stand an; gleiche Eingabe ergibt keinen neuen Stand."""
    if auftrag.leistung != "betriebskosten":
        raise Fachfehler("Abrechnungsläufe gibt es nur für Betriebskostenaufträge.")
    roh = _toml(daten, "Abrechnungsdaten")
    try:
        erg = berechne(aus_dict(roh))
    except (Eingabefehler, KeyError, ValueError, TypeError) as e:
        raise Fachfehler(f"Abrechnungsdaten: {e}") from None
    zuordnung = _zuordnung(auftrag, roh, erg)
    h = eingabe_hash(erg.daten)
    letzter = auftrag.abrechnungslaeufe.select_for_update().order_by("-version").first()
    if letzter and letzter.eingabe_hash == h and letzter.regeln_version == erg.regeln_version:
        lauf_pruefen(letzter)  # einen beschädigten Altstand nie als „unverändert“ zurückgeben
        return letzter, False
    ergebnis = als_dict(erg)
    jetzt = timezone.now()
    version = (letzter.version + 1) if letzter else 1
    stand = {"version": version, "erstellt": jetzt.isoformat(), "erstellt_von": benutzer.email, "eingabe_hash": h,
             "eingabe": json.loads(kanonisch(erg.daten)), "ergebnis": ergebnis, "ergebnis_hash": ergebnis_hash(ergebnis),
             "rote_warnungen": len(erg.rot), "gelbe_warnungen": len(erg.gelb), "zuordnung": zuordnung}
    lauf = Abrechnungslauf.objects.create(
        auftrag=auftrag, version=version, eingabe_hash=h, ergebnis_hash=stand["ergebnis_hash"],
        regeln_version=erg.regeln_version, rote_warnungen=len(erg.rot), gelbe_warnungen=len(erg.gelb),
        stand=stand, erstellt=jetzt, erstellt_von=benutzer)
    _ereignis(lauf=lauf, status="entwurf", benutzer=benutzer)
    protokoll("abrechnung_berechnet", benutzer, f"lauf:{lauf.pk}", f"auftrag:{auftrag.pk} v{version} rot:{len(erg.rot)}")
    return lauf, True


@transaction.atomic
def lauf_status(lauf: Abrechnungslauf, neu: str, benutzer, notiz: str = "") -> None:
    lauf = Abrechnungslauf.objects.select_for_update().select_related("auftrag__objekt").get(pk=lauf.pk)
    lauf_pruefen(lauf)
    if neu not in STATUS or STATUS.index(neu) != STATUS.index(lauf.status) + 1:
        raise Fachfehler(f"Von „{lauf.get_status_display()}“ ist dieser Schritt nicht möglich.")
    if lauf.auftrag.abrechnungslaeufe.order_by("-version").first().pk != lauf.pk:
        raise Fachfehler("Nur der neueste Berechnungsstand kann freigegeben werden.")
    if lauf.rote_warnungen:
        raise Fachfehler("Rote Warnungen bestehen. Freigabe gesperrt.")
    lauf.status = neu
    lauf.save(update_fields=["status"])
    _ereignis(lauf=lauf, status=neu, benutzer=benutzer, notiz=notiz)
    protokoll("abrechnung_status", benutzer, f"lauf:{lauf.pk}", neu)


# --- Reports (Befund W2) ---------------------------------------------------------------------

def report_fehler(r: Report) -> list[str]:
    fehler = []
    inhalt = r.inhalt if isinstance(r.inhalt, dict) else {}
    if inhalt_hash(inhalt) != r.inhalt_hash:
        fehler.append("Inhalt passt nicht zur Prüfsumme")
    if inhalt.get("report") != r.art:
        fehler.append("Reportart passt nicht zum Inhalt")
    fehler += verlauf_fehler(r, REPORT_STATUS, _anker_report(r), r.status)
    laeufe = list(r.laeufe.select_related("auftrag__objekt"))
    if not laeufe:
        fehler.append("Report ohne Berechnungsgrundlage")
    for lauf in laeufe:
        if lauf.auftrag.objekt_id != r.objekt_id:
            fehler.append("Grundlage gehört zu einem anderen Objekt")
        if lauf_fehler(lauf):
            fehler.append(f"Grundlage Version {lauf.version} ist nicht unverfälscht")
    grundlage = inhalt.get("grundlage")
    grundlage = grundlage if isinstance(grundlage, list) else [grundlage]
    if sorted((g or {}).get("ergebnis_hash", "") for g in grundlage) != sorted(x.ergebnis_hash for x in laeufe):
        fehler.append("Grundlage im Inhalt passt nicht zu den gespeicherten Berechnungsständen")
    return fehler


def report_pruefen(r: Report) -> None:
    fehler = report_fehler(r)
    if fehler:
        protokoll("report_integritaet", None, f"report:{r.pk}", "; ".join(fehler))
        log.error("Report %s nicht unverfälscht: %s", r.pk, "; ".join(fehler))
        raise Fachfehler(f"{r.get_art_display()} Nr. {r.nummer} ist nicht unverfälscht ({'; '.join(fehler)}). Gesperrt.")


def gepruefter_snapshot(r: Report) -> dict | None:
    """Snapshot für Anzeige und PDF, nur nach erneuter vollständiger Prüfung; sonst None."""
    try:
        report_pruefen(r)
    except Fachfehler:
        return None
    return r.snapshot()


def _speichern(objekt: Objekt, inhalt: dict, laeufe: list, benutzer) -> tuple[Report, bool]:
    daten = json.loads(kanonisch(inhalt))
    h = inhalt_hash(daten)
    art = daten["report"]
    letzter = Report.objects.select_for_update().filter(objekt=objekt, art=art).order_by("-nummer").first()
    if letzter and letzter.inhalt_hash == h:
        report_pruefen(letzter)
        return letzter, False
    r = Report.objects.create(objekt=objekt, art=art, nummer=(letzter.nummer + 1) if letzter else 1, inhalt=daten,
                              inhalt_hash=h, erstellt=timezone.now(), erstellt_von=benutzer)
    r.laeufe.set(laeufe)
    _ereignis(report=r, status="entwurf", benutzer=benutzer)
    protokoll("report_erstellt", benutzer, f"report:{r.pk}", f"{art} nr:{r.nummer} objekt:{objekt.pk}")
    return r, True


@transaction.atomic
def vorauszahlungsreport(lauf: Abrechnungslauf, konfig: Reportkonfiguration, benutzer) -> tuple[Report, bool]:
    lauf_pruefen(lauf)
    try:
        inhalt = analysiere(lauf.stand, lauf.status, konfig)
    except Reportfehler as e:
        raise Fachfehler(str(e)) from None
    return _speichern(lauf.auftrag.objekt, inhalt, [lauf], benutzer)


def endgueltige_laeufe(objekt: Objekt) -> list[Abrechnungslauf]:
    """Je Betriebskostenauftrag des Objekts der neueste endgültige Stand."""
    erg = []
    for a in objekt.auftraege.filter(leistung="betriebskosten"):
        lauf = a.abrechnungslaeufe.filter(status="endgueltig").order_by("-version").first()
        if lauf:
            erg.append(lauf)
    return erg


@transaction.atomic
def kostenverlaufsbericht(objekt: Objekt, konfig: Reportkonfiguration, benutzer) -> tuple[Report, bool]:
    laeufe = endgueltige_laeufe(objekt)
    if not laeufe:
        raise Fachfehler("Für dieses Objekt gibt es noch keine endgültige Betriebskostenabrechnung.")
    for x in laeufe:
        if x.auftrag.objekt_id != objekt.pk:
            raise Fachfehler("Ein Berechnungsstand gehört zu einem anderen Objekt.")
        lauf_pruefen(x)
    try:
        inhalt = bericht([(x.stand, x.status) for x in laeufe], konfig)
    except Reportfehler as e:
        raise Fachfehler(str(e)) from None
    return _speichern(objekt, inhalt, laeufe, benutzer)


@transaction.atomic
def report_status(report: Report, neu: str, benutzer, notiz: str = "") -> None:
    report = Report.objects.select_for_update().get(pk=report.pk)
    report_pruefen(report)
    if neu not in REPORT_STATUS or REPORT_STATUS.index(neu) != REPORT_STATUS.index(report.status) + 1:
        raise Fachfehler(f"Von „{report.get_status_display()}“ ist dieser Schritt nicht möglich.")
    if Report.objects.filter(objekt=report.objekt, art=report.art).order_by("-nummer").first().pk != report.pk:
        raise Fachfehler("Nur der neueste Report kann freigegeben werden.")
    report.status = neu
    report.save(update_fields=["status"])
    _ereignis(report=report, status=neu, benutzer=benutzer, notiz=notiz)
    protokoll("report_status", benutzer, f"report:{report.pk}", neu)


def fuer_kunde_sichtbar(report: Report) -> bool:
    return report.status == "freigegeben"
