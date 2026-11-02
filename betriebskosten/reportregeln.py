"""Fachlich konfigurierbare Regeln und freigegebene Annahmen für die beiden Jahresreports.

Alle Grenzen stehen hier als Konfiguration und werden in jedem Report-Snapshot
und im PDF sichtbar ausgegeben. Nichts davon ist im Rechenweg versteckt.
Beträge und Quoten werden als Text erwartet ("5.00", "0.05"), nie als Fließkommazahl.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from pathlib import Path

from .eingabe import Eingabefehler, _dez, _geld, _wahl

REPORT_REGELN_VERSION = "2026.11-1"
RUNDUNGSARTEN = ("kaufmaennisch", "aufrunden")
AENDERUNGSARTEN = ("prozent", "betrag")


@dataclass(frozen=True)
class Regeln:
    # Vorauszahlungsanalyse
    rundung_auf: Decimal = Decimal("1.00")  # Vorschlag auf volle Euro
    rundungsart: str = "kaufmaennisch"
    bagatelle_betrag: Decimal = Decimal("5.00")  # je Monat
    bagatelle_prozent: Decimal = Decimal("0.05")  # bezogen auf die aktuelle Vorauszahlung
    mindest_nutzungstage: int = 180  # darunter keine belastbare Hochrechnung
    # Kostenverlaufsbericht
    treiber_prozent: Decimal = Decimal("0.10")
    treiber_betrag: Decimal = Decimal("100.00")
    top_anzahl: int = 5

    def beschreibung(self) -> list[str]:
        return [
            f"Monatliche Ist-Kosten = Kostenanteil ÷ Nutzungstage × Tage des Abrechnungsjahres ÷ 12, kaufmännisch auf Cent.",
            f"Unverändert, wenn die Abweichung zur aktuellen Vorauszahlung höchstens {self.bagatelle_betrag} € "
            f"oder höchstens {self.bagatelle_prozent * 100:.1f} % der aktuellen Vorauszahlung beträgt (Bagatellgrenze).",
            f"Vorschlag {'kaufmännisch gerundet' if self.rundungsart == 'kaufmaennisch' else 'aufgerundet'} auf {self.rundung_auf} €.",
            f"Keine belastbare Empfehlung bei weniger als {self.mindest_nutzungstage} Nutzungstagen.",
            "Kein pauschaler Sicherheitsaufschlag. Bekannte Kostenänderungen nur, wenn fachlich freigegeben, und getrennt ausgewiesen.",
            f"Kostentreiber: Anstieg von mindestens {self.treiber_betrag} € und mindestens {self.treiber_prozent * 100:.0f} % gegenüber dem Vorjahr.",
        ]

    def runde(self, b: Decimal) -> Decimal:
        modus = ROUND_HALF_UP if self.rundungsart == "kaufmaennisch" else ROUND_CEILING
        return ((b / self.rundung_auf).quantize(Decimal(1), rounding=modus) * self.rundung_auf).quantize(Decimal("0.01"))


@dataclass(frozen=True)
class Kostenaenderung:
    """Konkret bekannte Kostenänderung für das Folgejahr (z. B. neuer Grundsteuerbescheid)."""

    id: str
    kostenart: str
    art: str  # prozent: Quote auf den Kostenanteil, betrag: objektweite Änderung pro Jahr (brutto)
    wert: Decimal
    begruendung: str
    beleg: str = ""
    gilt_ab: date | None = None
    freigegeben_von: str = ""
    freigegeben_am: date | None = None

    @property
    def freigegeben(self) -> bool:
        return bool(self.freigegeben_von and self.freigegeben_am)


@dataclass(frozen=True)
class Anmerkung:
    """Fachlich freigegebene Erläuterung zu einer Kostenart (einziger zulässiger Weg, Ursachen zu nennen)."""

    kostenart: str
    text: str
    freigegeben_von: str = ""
    freigegeben_am: date | None = None

    @property
    def freigegeben(self) -> bool:
        return bool(self.freigegeben_von and self.freigegeben_am)


@dataclass(frozen=True)
class Reportkonfiguration:
    regeln: Regeln = Regeln()
    kostenaenderungen: tuple[Kostenaenderung, ...] = ()
    anmerkungen: tuple[Anmerkung, ...] = field(default_factory=tuple)


def lade_konfiguration(pfad: str | Path) -> Reportkonfiguration:
    with open(pfad, "rb") as f:
        return konfiguration_aus_dict(tomllib.load(f))


def konfiguration_aus_dict(d: dict) -> Reportkonfiguration:
    r = d.get("regeln", {})
    standard = Regeln()
    regeln = Regeln(
        rundung_auf=_geld(r.get("rundung_auf", str(standard.rundung_auf)), "regeln.rundung_auf"),
        rundungsart=_wahl(r.get("rundungsart", standard.rundungsart), RUNDUNGSARTEN, "regeln.rundungsart"),
        bagatelle_betrag=_geld(r.get("bagatelle_betrag", str(standard.bagatelle_betrag)), "regeln.bagatelle_betrag"),
        bagatelle_prozent=_dez(r.get("bagatelle_prozent", str(standard.bagatelle_prozent)), "regeln.bagatelle_prozent"),
        mindest_nutzungstage=int(r.get("mindest_nutzungstage", standard.mindest_nutzungstage)),
        treiber_prozent=_dez(r.get("treiber_prozent", str(standard.treiber_prozent)), "regeln.treiber_prozent"),
        treiber_betrag=_geld(r.get("treiber_betrag", str(standard.treiber_betrag)), "regeln.treiber_betrag"),
        top_anzahl=int(r.get("top_anzahl", standard.top_anzahl)),
    )
    if regeln.rundung_auf <= 0:
        raise Eingabefehler("regeln.rundung_auf muss größer als 0 sein")
    aenderungen = tuple(
        Kostenaenderung(
            id=k["id"], kostenart=k["kostenart"], art=_wahl(k["art"], AENDERUNGSARTEN, f"{k['id']}.art"),
            wert=_dez(k["wert"], f"{k['id']}.wert"), begruendung=k.get("begruendung", ""), beleg=k.get("beleg", ""),
            gilt_ab=k.get("gilt_ab"), freigegeben_von=k.get("freigegeben_von", ""), freigegeben_am=k.get("freigegeben_am"),
        )
        for k in d.get("kostenaenderungen", [])
    )
    anmerkungen = tuple(
        Anmerkung(a["kostenart"], a["text"], a.get("freigegeben_von", ""), a.get("freigegeben_am"))
        for a in d.get("anmerkungen", [])
    )
    return Reportkonfiguration(regeln, aenderungen, anmerkungen)
