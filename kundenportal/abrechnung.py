"""Betriebskostenabrechnung und Jahresreports im Portal.

Die Fachlogik liegt vollständig in `betriebskosten/`. Hier werden nur
Berechnungsstände und Report-Snapshots unveränderlich in der Datenbank
abgelegt, Freigabeschritte geprüft und Rechte durchgesetzt. Es wird nichts
versendet und an keinem Vertrag etwas geändert.
"""
from __future__ import annotations

import json
import tomllib

from django.db import transaction
from django.utils import timezone

from betriebskosten.eingabe import Eingabefehler, aus_dict
from betriebskosten.kostenverlauf import bericht
from betriebskosten.rechnen import berechne
from betriebskosten.reportablage import inhalt_hash
from betriebskosten.reportregeln import Reportkonfiguration, konfiguration_aus_dict
from betriebskosten.version import STATUS, als_dict, eingabe_hash, ergebnis_hash, kanonisch
from betriebskosten.vorauszahlung import Reportfehler, analysiere

from .models import Abrechnungslauf, Auftrag, Objekt, Report, Statuswechsel, protokoll

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


@transaction.atomic
def lauf_anlegen(auftrag: Auftrag, daten: bytes, benutzer) -> tuple[Abrechnungslauf, bool]:
    """Berechnet die Abrechnung und legt einen neuen Stand an; gleiche Eingabe ergibt keinen neuen Stand."""
    if auftrag.leistung != "betriebskosten":
        raise Fachfehler("Abrechnungsläufe gibt es nur für Betriebskostenaufträge.")
    try:
        erg = berechne(aus_dict(_toml(daten, "Abrechnungsdaten")))
    except (Eingabefehler, KeyError, ValueError, TypeError) as e:
        raise Fachfehler(f"Abrechnungsdaten: {e}") from None
    h = eingabe_hash(erg.daten)
    letzter = auftrag.abrechnungslaeufe.select_for_update().order_by("-version").first()
    if letzter and letzter.eingabe_hash == h and letzter.regeln_version == erg.regeln_version:
        return letzter, False
    ergebnis = als_dict(erg)
    jetzt = timezone.now()
    version = (letzter.version + 1) if letzter else 1
    stand = {"version": version, "erstellt": jetzt.isoformat(), "erstellt_von": benutzer.email, "eingabe_hash": h,
             "eingabe": json.loads(kanonisch(erg.daten)), "ergebnis": ergebnis, "ergebnis_hash": ergebnis_hash(ergebnis),
             "rote_warnungen": len(erg.rot), "gelbe_warnungen": len(erg.gelb)}
    lauf = Abrechnungslauf.objects.create(
        auftrag=auftrag, version=version, eingabe_hash=h, ergebnis_hash=stand["ergebnis_hash"],
        regeln_version=erg.regeln_version, rote_warnungen=len(erg.rot), gelbe_warnungen=len(erg.gelb),
        stand=stand, erstellt=jetzt, erstellt_von=benutzer)
    Statuswechsel.objects.create(lauf=lauf, status="entwurf", benutzer=benutzer)
    protokoll("abrechnung_berechnet", benutzer, f"lauf:{lauf.pk}", f"auftrag:{auftrag.pk} v{version} rot:{len(erg.rot)}")
    return lauf, True


def stand_unverfaelscht(lauf: Abrechnungslauf) -> bool:
    return ergebnis_hash(lauf.stand["ergebnis"]) == lauf.ergebnis_hash == lauf.stand["ergebnis_hash"]


@transaction.atomic
def lauf_status(lauf: Abrechnungslauf, neu: str, benutzer, notiz: str = "") -> None:
    lauf = Abrechnungslauf.objects.select_for_update().get(pk=lauf.pk)
    if neu not in STATUS or STATUS.index(neu) != STATUS.index(lauf.status) + 1:
        raise Fachfehler(f"Von „{lauf.get_status_display()}“ ist dieser Schritt nicht möglich.")
    if lauf.auftrag.abrechnungslaeufe.order_by("-version").first().pk != lauf.pk:
        raise Fachfehler("Nur der neueste Berechnungsstand kann freigegeben werden.")
    if lauf.rote_warnungen:
        raise Fachfehler("Rote Warnungen bestehen. Freigabe gesperrt.")
    if not stand_unverfaelscht(lauf):
        raise Fachfehler("Der gespeicherte Stand passt nicht zu seiner Prüfsumme.")
    lauf.status = neu
    lauf.save(update_fields=["status"])
    Statuswechsel.objects.create(lauf=lauf, status=neu, benutzer=benutzer, notiz=notiz[:500])
    protokoll("abrechnung_status", benutzer, f"lauf:{lauf.pk}", neu)


def _speichern(objekt: Objekt, inhalt: dict, laeufe: list, benutzer) -> tuple[Report, bool]:
    daten = json.loads(kanonisch(inhalt))
    h = inhalt_hash(daten)
    art = daten["report"]
    letzter = Report.objects.select_for_update().filter(objekt=objekt, art=art).order_by("-nummer").first()
    if letzter and letzter.inhalt_hash == h:
        return letzter, False
    r = Report.objects.create(objekt=objekt, art=art, nummer=(letzter.nummer + 1) if letzter else 1, inhalt=daten,
                              inhalt_hash=h, erstellt=timezone.now(), erstellt_von=benutzer)
    r.laeufe.set(laeufe)
    Statuswechsel.objects.create(report=r, status="entwurf", benutzer=benutzer)
    protokoll("report_erstellt", benutzer, f"report:{r.pk}", f"{art} nr:{r.nummer} objekt:{objekt.pk}")
    return r, True


@transaction.atomic
def vorauszahlungsreport(lauf: Abrechnungslauf, konfig: Reportkonfiguration, benutzer) -> tuple[Report, bool]:
    if not stand_unverfaelscht(lauf):
        raise Fachfehler("Der gespeicherte Stand passt nicht zu seiner Prüfsumme.")
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
    if not all(stand_unverfaelscht(x) for x in laeufe):
        raise Fachfehler("Ein gespeicherter Stand passt nicht zu seiner Prüfsumme.")
    try:
        inhalt = bericht([(x.stand, x.status) for x in laeufe], konfig)
    except Reportfehler as e:
        raise Fachfehler(str(e)) from None
    return _speichern(objekt, inhalt, laeufe, benutzer)


@transaction.atomic
def report_status(report: Report, neu: str, benutzer, notiz: str = "") -> None:
    report = Report.objects.select_for_update().get(pk=report.pk)
    if neu not in REPORT_STATUS or REPORT_STATUS.index(neu) != REPORT_STATUS.index(report.status) + 1:
        raise Fachfehler(f"Von „{report.get_status_display()}“ ist dieser Schritt nicht möglich.")
    if Report.objects.filter(objekt=report.objekt, art=report.art).order_by("-nummer").first().pk != report.pk:
        raise Fachfehler("Nur der neueste Report kann freigegeben werden.")
    if inhalt_hash(report.inhalt) != report.inhalt_hash:
        raise Fachfehler("Der Report-Inhalt passt nicht zu seiner Prüfsumme.")
    report.status = neu
    report.save(update_fields=["status"])
    Statuswechsel.objects.create(report=report, status=neu, benutzer=benutzer, notiz=notiz[:500])
    protokoll("report_status", benutzer, f"report:{report.pk}", neu)


def fuer_kunde_sichtbar(report: Report) -> bool:
    return report.status == "freigegeben"
