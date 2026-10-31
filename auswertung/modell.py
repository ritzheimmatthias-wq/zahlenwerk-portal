"""Gemeinsame Datentypen. Beträge immer als Decimal."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class Buchung:
    """Eine Kontobewegung aus Sicht des Kontoinhabers (Eingang positiv, Ausgang negativ)."""

    konto_iban: str
    datum: date
    betrag: Decimal
    gegen_name: str = ""
    gegen_iban: str = ""
    zweck: str = ""
    quelle: str = ""

    @property
    def ist_eingang(self) -> bool:
        return self.betrag > 0


@dataclass
class Auszug:
    """Ein Kontoauszug mit Anfangs- und Endsaldo (soweit die Datei sie enthält)."""

    konto_iban: str
    von: date | None
    bis: date | None
    anfangssaldo: Decimal | None
    endsaldo: Decimal | None
    buchungen: list[Buchung] = field(default_factory=list)
    quelle: str = ""


def iban_normal(iban: str) -> str:
    return "".join(iban.split()).upper()


def iban_kurz(iban: str) -> str:
    """Nur die letzten vier Stellen, für Berichte."""
    i = iban_normal(iban)
    return "…" + i[-4:] if i else ""


def eur(b: Decimal | None) -> str:
    """Deutsches Format, z. B. 1.234,50 €."""
    if b is None:
        return "–"
    s = f"{b:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} €"
