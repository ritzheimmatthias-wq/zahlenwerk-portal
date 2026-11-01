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
