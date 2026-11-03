"""Kommandozeile: python -m betriebskosten daten.toml --ausgabe ordner [--ablage ordner --person name]

Berechnet die Abrechnung, legt optional einen neuen Berechnungsstand in der
Ablage an und schreibt Ergebnisdaten, Kontrollbericht und PDF-Entwürfe.
Versendet nichts.
"""
from __future__ import annotations

import argparse
from datetime import datetime

from .ausgabe import DARSTELLUNG, eur, schreibe_lauf
from .eingabe import lade
from .rechnen import berechne
from .version import Ablage, eingabe_hash


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="betriebskosten", description="Betriebskostenabrechnung berechnen (Entwürfe)")
    p.add_argument("daten", help="TOML-Datei mit Objekt, Einheiten, Mietverhältnissen, Kosten und Messdienstwerten")
    p.add_argument("--ausgabe", required=True, help="Ordner für JSON, Kontrollbericht und PDF-Entwürfe")
    p.add_argument("--ablage", help="Ordner für versionierte Berechnungsstände und Audit-Log")
    p.add_argument("--person", default="", help="verantwortliche Person für das Audit-Log")
    a = p.parse_args(argv)

    daten = lade(a.daten)
    erg = berechne(daten)
    version = erstellt = None
    status = "entwurf"
    if a.ablage:
        ablage = Ablage(a.ablage)
        version, neu = ablage.speichern(erg, zeit=datetime.now().astimezone(), person=a.person or "unbekannt")
        erstellt = datetime.fromisoformat(ablage.lesen(version)["erstellt"])
        status = ablage.status(version)
        print(f"Berechnungsstand Version {version} ({'neu' if neu else 'unverändert'}, Status {status})")
    dateien = schreibe_lauf(erg, a.ausgabe, version=version, status=status, eingabe_hash=eingabe_hash(daten), erstellt=erstellt)
    for m in erg.mietverhaeltnisse:
        print(f"{m.id:>6}  {DARSTELLUNG[m.darstellung]:<36} Saldo {eur(m.saldo_brutto):>14}")
    print(f"{len(erg.rot)} rote, {len(erg.gelb)} gelbe Warnungen. {len(dateien)} Dateien in {a.ausgabe}")
    for w in erg.warnungen:
        print(f"  [{w.stufe}] {w.text}")
    return 1 if erg.rot else 0


if __name__ == "__main__":
    raise SystemExit(main())
