# zahlenwerk-portal

Kundenportal von Zahlenwerk Immobilien. Privat.

Aktueller Stand: Auswertungskern, PDF-Bericht und Kundenportal Stufe 1 (noch nicht in Betrieb). Liest Kontoauszüge (CAMT.052/053,
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

## Kundenportal

Django-Anwendung in `portal/` (Einstellungen) und `kundenportal/` (Seiten, Datenmodell).
Einladung per Link, Anmeldung mit Passwort und Code per E-Mail, Upload mit Inhaltsprüfung,
Virenscan und Verschlüsselung je Datei, Prüfung durch Zahlenwerk, Löschjob nach 90 Tagen.

```
pip install -e ".[test]"
PORTAL_DEBUG=1 python manage.py migrate
PORTAL_DEBUG=1 python manage.py createsuperuser
PORTAL_DEBUG=1 python manage.py runserver
```

Im Entwicklungsmodus erscheinen E-Mails (z. B. der Anmeldecode) in der Konsole.
Betrieb auf dem KVM-2: [betrieb/README.md](betrieb/README.md).

### Dokumentvorlagen

Unter `/vorlagen/` liegt nach der Anmeldung eine Bibliothek mit neun eigenen,
ausfüllbaren Blankomustern zur Wohnraumvermietung (Prüfstand 01.11.2026). Die
Dateien stammen unverändert aus dem Vault und liegen mit `manifest.json` unter
`kundenportal/dokumentvorlagen_dateien/2026-11-01/`. Jede Auslieferung prüft die
SHA-256-Prüfsumme. Je Vorlage gibt es die vollständige Fassung, eine Fassung ohne
die Anwendungshilfe (letzte Seite) und die Markdown-Textfassung; die
Mieterselbstauskunft zusätzlich getrennt nach Phase A, B und C. Das Portal füllt,
speichert und versendet nichts.

Zahlenwerk-Konten sehen die Bibliothek immer. Kunden erst, wenn
`PORTAL_VORLAGEN_FUER_KUNDEN=1` gesetzt ist, und das nur nach ausdrücklicher
Freigabe durch Matthias.

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
(`Ablage.nachrechnen` prüft das).

## Jahresreports (Entwurf)

Aus einer **endgültig freigegebenen** Abrechnungsversion entstehen zwei Reports:

1. **Vorauszahlungsanalyse je Mietverhältnis** (`betriebskosten/vorauszahlung.py`):
   geleistete Vorauszahlungen, tatsächliche Kosten, Abrechnungsspitze, Kosten je Monat,
   aktuelle Vorauszahlung und Empfehlung (unverändert, erhöhen, absenken, keine belastbare
   Empfehlung). Pauschalen erhalten keine Empfehlung. Rundung, Bagatellgrenze und
   Mindestnutzungstage sind konfigurierbar (`tests/beispiel/jahresreports_regeln.toml`);
   freigegebene Kostenänderungen erscheinen als getrennte Annahme.
2. **Kostenverlaufsbericht für den Eigentümer** (`betriebskosten/kostenverlauf.py`):
   Gesamtkosten, Veränderungen je Kostenart, Versorger und Bereich, Kennzahlen je Einheit
   und m², Wohn-/Gewerbeanteile, Vorauszahlungen gegen Kosten, Kostentreiber nach festen
   Schwellen, Diagramme als Inline-SVG. Ein einzelnes Jahr ist Ausgangsbasis ohne Trend.

Reports werden als versionierte Snapshots in der Ablage gespeichert (`reports/`), haben
eigene Freigabestufen (entwurf → fachlich_geprueft → freigegeben) und stehen erst nach
Freigabe zum Download bereit. Portalansicht und PDF nutzen dieselben gespeicherten Werte.

```
python -m betriebskosten.jahresreports vorauszahlung --ablage ablage/ --konfig tests/beispiel/jahresreports_regeln.toml --ausgabe reports/ --person "Name"
python -m betriebskosten.jahresreports kostenverlauf --ablage ablage2025/ --ablage ablage2026/ --konfig tests/beispiel/jahresreports_regeln.toml --ausgabe reports/ --person "Name"
```
