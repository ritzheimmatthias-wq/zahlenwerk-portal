"""Datenmodell des Betriebskostenabrechnungsgenerators (ohne Django, gut testbar).

Alle Geldbeträge sind `Decimal` mit zwei Nachkommastellen. Zeiträume sind
einschließlich Anfangs- und Endtag. Das Modell trifft keine steuerlichen oder
rechtlichen Entscheidungen: Steuerfälle und Umlagefähigkeit kommen als
bestätigte Konfiguration herein, fehlende Bestätigungen werden zu Warnungen.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
NULL = Decimal("0.00")

NUTZUNGEN = ("wohnen", "gewerbe")
POOLS = ("alle", "wohnen", "gewerbe")
SCHLUESSEL = ("flaeche", "personen", "einheiten", "verbrauch", "direkt", "messdienst")
STEUERBEHANDLUNG = ("steuerfrei", "steuerpflichtig", "ungeklaert")
VZ_ARTEN = ("betriebskosten", "heizkosten")


def cent(b: Decimal) -> Decimal:
    return b.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Zeitraum:
    von: date
    bis: date

    def __post_init__(self):
        if self.bis < self.von:
            raise ValueError(f"Zeitraum endet vor dem Beginn: {self.von} bis {self.bis}")

    @property
    def tage(self) -> int:
        return (self.bis - self.von).days + 1

    def schnitt(self, other: "Zeitraum") -> "Zeitraum | None":
        von, bis = max(self.von, other.von), min(self.bis, other.bis)
        return Zeitraum(von, bis) if von <= bis else None

    def enthaelt(self, other: "Zeitraum") -> bool:
        return self.von <= other.von and other.bis <= self.bis


@dataclass(frozen=True)
class Abschnitt:
    """Zeitlicher Zustand einer Einheit. Eine Flächenteilung ist ein neuer Abschnitt, kein Überschreiben."""

    zeitraum: Zeitraum
    nutzung: str
    flaeche: Decimal


@dataclass(frozen=True)
class Einheit:
    id: str
    bezeichnung: str
    abschnitte: tuple[Abschnitt, ...]
    leerstand_verbrauch: dict = field(default_factory=dict)  # Kostenart -> Verbrauch im Leerstand


@dataclass(frozen=True)
class Steuerfall:
    """Vom Eigentümer bzw. der Steuerberatung bestätigte Behandlung eines Mietverhältnisses."""

    behandlung: str = "ungeklaert"
    satz: Decimal | None = None  # z. B. Decimal("0.19")
    kostenbasis: str = "netto"  # Bemessung des Kostenanteils bei Steuerpflicht: netto oder brutto
    ohne_ust: tuple[str, ...] = ()  # Kostenarten, auf die laut Konfiguration keine USt aufgeschlagen wird
    bestaetigt_von: str = ""
    bestaetigt_am: date | None = None

    @property
    def bestaetigt(self) -> bool:
        return bool(self.bestaetigt_von and self.bestaetigt_am)


@dataclass(frozen=True)
class Vorauszahlung:
    art: str  # betriebskosten oder heizkosten
    brutto: Decimal
    netto: Decimal | None = None
    ust: Decimal | None = None


@dataclass(frozen=True)
class Personen:
    ab: date
    anzahl: int


@dataclass(frozen=True)
class Mietverhaeltnis:
    id: str
    einheit: str
    mieter: str
    zeitraum: Zeitraum  # Vertrags- bzw. Nutzungszeitraum (Ende offen = Ende des Abrechnungsjahres)
    vorauszahlungen: tuple[Vorauszahlung, ...] = ()
    personen: tuple[Personen, ...] = ()
    verbrauch: dict = field(default_factory=dict)  # Kostenart -> Verbrauch im Nutzungszeitraum
    steuer: Steuerfall | None = None
    anschrift: str = ""


@dataclass(frozen=True)
class Kostenart:
    id: str
    bezeichnung: str
    schluessel: str
    pool: str = "alle"
    umlagefaehig_bestaetigt: bool = False
    heizkosten: bool = False  # Vorauszahlungsart heizkosten statt betriebskosten


@dataclass(frozen=True)
class Position:
    """Eine Steuerzeile eines Belegs. Ein Beleg mit zwei Steuersätzen hat zwei Positionen."""

    beleg_id: str
    kostenart: str
    brutto: Decimal
    netto: Decimal | None = None
    ust: Decimal | None = None
    ust_satz: Decimal | None = None
    leistungszeitraum: Zeitraum | None = None
    abgrenzen: bool = False  # zeitanteilig auf das Abrechnungsjahr kürzen
    einheit: str = ""  # nur bei Schlüssel direkt
    lieferant: str = ""
    rechnungsnummer: str = ""
    beleg_sha256: str = ""


@dataclass(frozen=True)
class Messwert:
    einheit: str
    kostenart: str
    brutto: Decimal
    netto: Decimal | None = None
    mietverhaeltnis: str = ""  # leer = ganze Einheit im Jahr (ohne Zwischenablesung); "leerstand" = Eigentümer


@dataclass(frozen=True)
class Messdienstabrechnung:
    id: str
    anbieter: str
    gesamt_brutto: Decimal
    werte: tuple[Messwert, ...]
    dokument_sha256: str = ""


@dataclass(frozen=True)
class Objekt:
    name: str
    anschrift: str
    vermieter: str
    vermieter_anschrift: str = ""


@dataclass(frozen=True)
class Abrechnungsdaten:
    objekt: Objekt
    zeitraum: Zeitraum
    einheiten: tuple[Einheit, ...]
    mietverhaeltnisse: tuple[Mietverhaeltnis, ...]
    kostenarten: tuple[Kostenart, ...]
    positionen: tuple[Position, ...]
    messdienst: tuple[Messdienstabrechnung, ...] = ()
    vorjahr: dict = field(default_factory=dict)  # Kostenart -> Vorjahresbetrag brutto
    vorjahr_schwelle: Decimal = Decimal("0.25")

    def einheit(self, eid: str) -> Einheit | None:
        return next((e for e in self.einheiten if e.id == eid), None)

    def kostenart(self, kid: str) -> Kostenart | None:
        return next((k for k in self.kostenarten if k.id == kid), None)
