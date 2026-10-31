# zahlenwerk-portal

Kundenportal von Zahlenwerk Immobilien. Privat.

Aktueller Stand: Stufe 1, Auswertungskern. Liest Kontoauszüge (CAMT.052/053
oder CSV) und eine Stammdaten-Datei und erstellt eine erste Einschätzung:

- Hausgeld je Einheit und Monat gegen das Soll (gezahlt, Teilzahlung, zu viel, fehlt)
- Ausgaben nach Gruppen mit größten Empfängern
- Saldenkette je Konto und fehlende Monate
- nicht zugeordnete Buchungen

```
pip install -e ".[test]"
python -m pytest
python -m auswertung --stammdaten tests/beispiel/stammdaten.toml tests/beispiel/*.xml
```

Alle Beispieldaten sind erfunden. Arbeitsregeln: [AGENTS.md](AGENTS.md).
