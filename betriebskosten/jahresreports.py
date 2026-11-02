"""Kommandozeile für die beiden Jahresreports (Entwürfe, kein Versand).

  python -m betriebskosten.jahresreports vorauszahlung --ablage A --version 3 --konfig regeln.toml --ausgabe X --person P
  python -m betriebskosten.jahresreports kostenverlauf --ablage A2024 --ablage A2025 --konfig regeln.toml --ausgabe X --person P

Je Ablage wird die neueste endgültige Version verwendet, sofern --version nicht
angegeben ist. Der Snapshot wird in der Ablage des aktuellsten Jahres gespeichert.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from .kostenverlauf import bericht
from .reportablage import ReportAblage
from .reportausgabe import report_pdf
from .reportregeln import Reportkonfiguration, lade_konfiguration
from .version import Ablage
from .vorauszahlung import Reportfehler, analysiere


def _endgueltig(ablage: Ablage, version: int | None) -> tuple[dict, str]:
    if version is None:
        fertig = [v for v in ablage.versionen() if ablage.status(v) == "endgueltig"]
        if not fertig:
            raise Reportfehler(f"{ablage.pfad}: keine endgültige Abrechnungsversion vorhanden.")
        version = fertig[-1]
    return ablage.lesen(version), ablage.status(version)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="betriebskosten.jahresreports")
    p.add_argument("report", choices=("vorauszahlung", "kostenverlauf"))
    p.add_argument("--ablage", action="append", required=True)
    p.add_argument("--version", type=int)
    p.add_argument("--konfig")
    p.add_argument("--ausgabe", required=True)
    p.add_argument("--person", default="unbekannt")
    a = p.parse_args(argv)
    konfig = lade_konfiguration(a.konfig) if a.konfig else Reportkonfiguration()
    ablagen = [Ablage(x) for x in a.ablage]
    try:
        if a.report == "vorauszahlung":
            stand, status = _endgueltig(ablagen[-1], a.version)
            inhalt = analysiere(stand, status, konfig)
        else:
            staende = [_endgueltig(x, None) for x in ablagen]
            inhalt = bericht(staende, konfig)
            ablagen.sort(key=lambda x: _endgueltig(x, None)[0]["ergebnis"]["abrechnungszeitraum"]["von"])
    except Reportfehler as f:
        print(f"Report nicht erstellt: {f}")
        return 1
    ra = ReportAblage(ablagen[-1])
    nummer, neu = ra.speichern(inhalt, zeit=datetime.now().astimezone(), person=a.person)
    snapshot, status = ra.lesen(a.report, nummer), ra.status(a.report, nummer)
    ziel = Path(a.ausgabe)
    ziel.mkdir(parents=True, exist_ok=True)
    datei = ziel / f"{a.report}_r{nummer:03d}.pdf"
    datei.write_bytes(report_pdf(snapshot, status))
    print(f"Report {a.report} Nr. {nummer} ({'neu' if neu else 'unverändert'}, Status {status}) → {datei}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
