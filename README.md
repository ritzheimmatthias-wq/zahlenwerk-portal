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

### Betriebskosten, Jahresreports und Belegeingang

Unter `/intern/objekt/<id>/abrechnung/` legt Zahlenwerk unveränderliche
Berechnungsstände an, gibt sie stufenweise frei und erzeugt daraus die beiden
Jahresreports. Eigentümer sehen nur freigegebene Reports ihres Objekts (Ansicht und PDF).
Belege kommen per Upload, Smartphone-Erfassung (`/auftrag/<id>/beleg/`, für Kunden erst mit
`PORTAL_MOBILE_ERFASSUNG_FUER_KUNDEN=1`) oder über das Postfach belege@ (n8n ruft
`/eingang/belege/` signiert auf; Rückfallweg `python manage.py belege_imap_abruf`).
Der Eingang über belege@ ist zweistufig (erst verschlüsselt sichern, dann verarbeiten);
`python manage.py belege_wiederaufnahme` setzt abgebrochene Eingänge fort. Abrechnungsdaten müssen
die Portal-Kennung des Objekts (`[objekt] portal_id`) enthalten und zum Auftragsjahr passen.
Einzelheiten, Schnittstelle und Berechtigungscheck: [docs/belegeingang-und-reports.md](docs/belegeingang-und-reports.md).

Tests gegen PostgreSQL: `PORTAL_TEST_POSTGRES=1 PORTAL_TEST_PG_HOST=… python -m pytest` (nur Wegwerf-Datenbank).
Browsertests: `pip install -e ".[test,browser]" && python -m playwright install chromium`, dann
`python -m pytest tests/test_browser_belegerfassung.py`.

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
mit dem verketteten Audit-Log samt Kettenende abgeglichen; außerdem wird die vollständige
Statusfolge geprüft (keine übersprungenen, doppelten oder rückwärts gerichteten Stufen). Ein
beschädigter Stand, ein beschädigtes Audit-Log oder ein unzulässiger Statusverlauf sperrt alle
weiteren Schritte, auch Reporterstellung und Download. Grenze: Wer Ablage, Audit-Log und Kettenende
gemeinsam und konsistent neu schreiben darf, wird so allein nicht erkannt; dafür braucht es
eingeschränkte Schreibrechte auf dem Server.

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
