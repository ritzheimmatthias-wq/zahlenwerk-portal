# zahlenwerk-portal

Kundenportal von Zahlenwerk Immobilien. Privat.

Aktueller Stand: Stufe 1 und 2, Auswertungskern und PDF-Bericht. Liest Kontoauszüge (CAMT.052/053,
CSV oder PDF mit Textebene) und eine Stammdaten-Datei und erstellt eine erste Einschätzung:

- Hausgeld je Einheit und Monat gegen das Soll (gezahlt, Teilzahlung, zu viel, fehlt)
- Ausgaben nach Gruppen mit größten Empfängern
- Saldenkette je Konto und fehlende Monate
- nicht zugeordnete Buchungen

Auf Wunsch als PDF im Zahlenwerk-Design mit Steckbrief des Objekts (WeasyPrint,
braucht die Systembibliothek Pango). Freie Steckbrief-Angaben stehen im Abschnitt
`[steckbrief]` der Stammdaten-Datei.

```
pip install -e ".[test]"
python -m pytest
python -m auswertung --stammdaten tests/beispiel/stammdaten.toml tests/beispiel/*.xml
python -m auswertung --stammdaten tests/beispiel/stammdaten.toml --pdf bericht.pdf tests/beispiel/*.xml
```

Alle Beispieldaten sind erfunden. Arbeitsregeln: [AGENTS.md](AGENTS.md).

## Betriebskostenabrechnung (Generator, Entwurf)

`betriebskosten/` berechnet aus geprüften Stammdaten, Belegen, Verteilerschlüsseln,
Vorauszahlungen und Messdienstwerten je Mietverhältnis eine Abrechnung:
Wohnraum mit Bruttodarstellung, Gewerbe je nach bestätigter Konfiguration brutto
oder netto + Umsatzsteuer + brutto. Ungeklärte Steuerfälle sperren die Abrechnung.
Jeder Lauf liefert Ergebnisdaten (JSON), einen internen Kontrollbericht, PDF-Entwürfe
und optional einen unveränderlichen, versionierten Berechnungsstand mit Audit-Log.
Nichts wird versendet.

```
python -m betriebskosten tests/beispiel/betriebskosten_pilot.toml --ausgabe ausgabe/ --ablage ablage/ --person "Name"
```

Das Pilotobjekt in `tests/beispiel/betriebskosten_pilot.toml` ist vollständig erfunden.

### Deterministisch und reproduzierbar

Alle Beträge, Verteilungen, Zeitanteile, Umsatzsteuer, Rundungen und Salden werden im
Anwendungscode berechnet. Kein Sprachmodell rechnet oder verändert Zahlen. Jeder
Berechnungsstand speichert Eingabedaten, Regelversion, Rundungsregeln, Verteilerschlüssel,
Bezugsgrößen, die einzelnen Rechenschritte (Gewichte, exakte und gerundete Anteile,
Restcent), Warnungen und Freigaben, dazu Prüfsummen von Eingabe und Ergebnis.
Gleiche Eingabe und gleiche Regelversion ergeben dasselbe Ergebnis und byte-gleiche PDFs
(`Ablage.nachrechnen` prüft das, einschließlich des tatsächlich gespeicherten Ergebnisinhalts).

Vor jeder Freigabe, Wiederverwendung und Reporterstellung prüft `Ablage.pruefen` den Stand:
Prüfsummen werden aus dem gespeicherten Inhalt neu gebildet, Warnungszähler nachgezählt und
mit dem verketteten Audit-Log abgeglichen. Ein beschädigter Stand oder ein beschädigtes
Audit-Log sperrt alle weiteren Schritte. Grenze: Wer Ablage und Audit-Log gemeinsam und
konsistent neu schreiben darf, wird so allein nicht erkannt; dafür braucht es eingeschränkte
Schreibrechte auf dem Server.

## Jahresreports (Entwurf)

Aus einer **endgültig freigegebenen** Abrechnungsversion entstehen zwei Reports:

1. **Vorauszahlungsanalyse je Mietverhältnis** (`betriebskosten/vorauszahlung.py`):
   geleistete Vorauszahlungen, tatsächliche Kosten, Abrechnungsspitze, Kosten je Monat,
   aktuelle Vorauszahlung und Empfehlung (unverändert, erhöhen, absenken, keine belastbare
   Empfehlung). Pauschalen erhalten keine Empfehlung. Rundung, Bagatellgrenze und
   Mindestnutzungstage sind konfigurierbar (`tests/beispiel/jahresreports_regeln.toml`);
   freigegebene Kostenänderungen erscheinen als getrennte Annahme. Bei einem verkürzten
   Abrechnungsjahr (weniger als zwölf Monate) gibt es keine Empfehlung, weil eine
   Hochrechnung auf zwölf Monate ohne fachliche Regel nicht belastbar ist.
2. **Kostenverlaufsbericht für den Eigentümer** (`betriebskosten/kostenverlauf.py`):
   Gesamtkosten, Veränderungen je Kostenart, Versorger und Bereich, Kennzahlen je Einheit
   und m², Wohn-/Gewerbeanteile, Vorauszahlungen gegen Kosten, Kostentreiber nach festen
   Schwellen, Diagramme als Inline-SVG. Ein einzelnes Jahr ist Ausgangsbasis ohne Trend.

Reports werden als versionierte Snapshots in der Ablage gespeichert (`reports/`), haben
eigene Freigabestufen (entwurf → fachlich_geprueft → freigegeben) und stehen erst nach
Freigabe zum Download bereit. Vor jeder Freigabe und Ausgabe wird der Snapshot erneut gegen
Prüfsumme und Audit-Log geprüft (`ReportAblage.pruefen`, `ReportAblage.freigegeben`). Portalansicht und PDF nutzen dieselben gespeicherten Werte.

```
python -m betriebskosten.jahresreports vorauszahlung --ablage ablage/ --konfig tests/beispiel/jahresreports_regeln.toml --ausgabe reports/ --person "Name"
python -m betriebskosten.jahresreports kostenverlauf --ablage ablage2025/ --ablage ablage2026/ --konfig tests/beispiel/jahresreports_regeln.toml --ausgabe reports/ --person "Name"
```
