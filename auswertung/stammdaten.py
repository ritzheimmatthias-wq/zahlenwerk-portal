"""Stammdaten eines Objekts aus einer TOML-Datei (Konten, Einheiten, Eigentümer, Regeln)."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from .modell import iban_normal


@dataclass(frozen=True)
class Eigentuemer:
    name: str
    ibans: tuple[str, ...] = ()
    von: date | None = None
    bis: date | None = None

    def gilt_am(self, tag: date) -> bool:
        return (self.von is None or tag >= self.von) and (self.bis is None or tag <= self.bis)


@dataclass(frozen=True)
class Einheit:
    nummer: str
    hausgeld_soll: Decimal  # monatlich
    eigentuemer: tuple[Eigentuemer, ...] = ()

    def eigentuemer_am(self, tag: date) -> Eigentuemer | None:
        for e in self.eigentuemer:
            if e.gilt_am(tag):
                return e
        return None


@dataclass(frozen=True)
class Kostengruppe:
    gruppe: str
    muster: tuple[str, ...]


@dataclass
class Stammdaten:
    objekt: str
    jahr: int
    konten: dict[str, str]  # IBAN -> Art (z. B. Giro, Rücklage)
    einheiten: list[Einheit]
    kostengruppen: list[Kostengruppe] = field(default_factory=list)


def lade_stammdaten(pfad: str | Path) -> Stammdaten:
    with open(pfad, "rb") as f:
        d = tomllib.load(f)
    einheiten = []
    for e in d.get("einheiten", []):
        eig = tuple(
            Eigentuemer(
                name=x["name"],
                ibans=tuple(iban_normal(i) for i in x.get("ibans", [])),
                von=x.get("von"),
                bis=x.get("bis"),
            )
            for x in e.get("eigentuemer", [])
        )
        einheiten.append(Einheit(e["nummer"], Decimal(str(e["hausgeld_soll"])), eig))
    return Stammdaten(
        objekt=d["objekt"]["name"],
        jahr=int(d["objekt"]["jahr"]),
        konten={iban_normal(k["iban"]): k.get("art", "Konto") for k in d.get("konten", [])},
        einheiten=einheiten,
        kostengruppen=[
            Kostengruppe(k["gruppe"], tuple(m.lower() for m in k["muster"]))
            for k in d.get("kostengruppen", [])
        ],
    )
