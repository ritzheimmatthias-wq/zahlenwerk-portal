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

Das Audit-Log ist verkettet (jeder Eintrag verweist auf seinen Vorgänger); das Kettenende
`audit.kopf.json` hält Anzahl und Prüfsumme des letzten Eintrags. Freigaben prüfen zusätzlich die
vollständige Statusfolge (entwurf → fachlich_geprueft → eigentuemer_freigegeben → endgueltig, ohne
Sprünge, Doppelungen oder Rückschritte, jeweils mit Person). Grenze: Wer Log und Kettenende gemeinsam
konsistent neu schreibt, wird allein dadurch nicht erkannt; dagegen helfen eingeschränkte Schreibrechte.

```
python -m betriebskosten tests/beispiel/betriebskosten_pilot.toml --ausgabe ausgabe/ --ablage ablage/ --person "Name"
```

Das Pilotobjekt in `tests/beispiel/betriebskosten_pilot.toml` ist vollständig erfunden.
