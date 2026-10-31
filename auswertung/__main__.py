"""Kommandozeile: python -m auswertung --stammdaten objekt.toml auszug1.xml auszug2.csv ..."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .bericht import als_json, als_markdown
from .camt import lese_camt_datei
from .csv_import import lese_csv_datei
from .einschaetzung import erstelle_einschaetzung
from .stammdaten import lade_stammdaten


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="auswertung", description="Erste Einschätzung aus Kontoauszügen")
    p.add_argument("--stammdaten", required=True, help="TOML-Datei mit Objekt, Konten, Einheiten, Regeln")
    p.add_argument("--json", action="store_true", help="JSON statt Markdown ausgeben")
    p.add_argument("--csv-konto", default="", help="eigene IBAN für CSV-Dateien ohne Kontospalte")
    p.add_argument("dateien", nargs="+", help="CAMT-XML- oder CSV-Dateien")
    a = p.parse_args(argv)

    sd = lade_stammdaten(a.stammdaten)
    auszuege = []
    for name in a.dateien:
        pfad = Path(name)
        if pfad.suffix.lower() == ".xml":
            auszuege.extend(lese_camt_datei(pfad))
        elif pfad.suffix.lower() == ".csv":
            auszuege.append(lese_csv_datei(pfad, konto_iban=a.csv_konto))
        else:
            print(f"Übersprungen (unbekanntes Format): {pfad.name}", file=sys.stderr)
    ergebnis = erstelle_einschaetzung(auszuege, sd)
    print(als_json(ergebnis) if a.json else als_markdown(ergebnis))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
