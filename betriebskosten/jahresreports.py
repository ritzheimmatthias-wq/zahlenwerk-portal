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
from .version import Ablage, Freigabefehler
from .vorauszahlung import Reportfehler, analysiere


def _endgueltig(ablage: Ablage, version: int | None) -> tuple[dict, str]:
    """Geprüfter, endgültig freigegebener Stand. Prüft Inhalt, Audit-Kette mit Kettenende und die
    vollständige Statusfolge; ein unzulässiger Verlauf (z. B. übersprungene Stufen) ergibt keinen Report."""
    try:
        if version is None:
            fertig = []
            for v in ablage.versionen():
                try:
                    if ablage.status(v) == "endgueltig":
                        fertig.append(v)
                except Freigabefehler:
                    continue  # unzulässiger Verlauf zählt nie als endgültig
            if not fertig:
                raise Reportfehler(f"{ablage.pfad}: keine endgültige Abrechnungsversion vorhanden.")
            version = fertig[-1]
        stand = ablage.pruefen(version)  # beschädigter Stand, Audit-Log oder Statusverlauf: kein Report
        status = ablage.status(version)
    except Freigabefehler as f:
        raise Reportfehler(str(f)) from None
    if status != "endgueltig":
        raise Reportfehler(f"Version {version} ist nicht endgültig freigegeben (Status {status or 'unbekannt'}).")
    return stand, status


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
    try:
        nummer, neu = ra.speichern(inhalt, zeit=datetime.now().astimezone(), person=a.person)
    except Freigabefehler as f:
        print(f"Report nicht erstellt: {f}")
        return 1
    try:
        snapshot, status = ra.pruefen(a.report, nummer), ra.status(a.report, nummer)
    except Freigabefehler as f:
        print(f"Report nicht ausgegeben: {f}")
        return 1
    ziel = Path(a.ausgabe)
    ziel.mkdir(parents=True, exist_ok=True)
    datei = ziel / f"{a.report}_r{nummer:03d}.pdf"
    datei.write_bytes(report_pdf(snapshot, status))
    print(f"Report {a.report} Nr. {nummer} ({'neu' if neu else 'unverändert'}, Status {status}) → {datei}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
