# Zahlenwerk Kundenportal: gemeinsame Arbeitsregeln

Privates Repository `ritzheimmatthias-wq/zahlenwerk-portal`. Hier entsteht das
Kundenportal von Zahlenwerk Immobilien (Upload, Prüfung, Auswertung). Die
öffentliche Website liegt getrennt in `zahlenwerk-website` und bleibt dort.
Konzept: Claude-Dokument „Gesamtkonzept Kundenportal und Hosting“ und
Vault-Ordner `Projekte/Server und Kundenportal`.

## Feste Regeln

- Jede Aufgabe in einem eigenen Zweig mit Pull Request. Nie direkt auf `main` pushen.
- **Keine Kundendaten im Repository**, auch nicht in Tests, Beispielen, Logs,
  Screenshots, Datenbankexporten oder der Git-Historie. Testdaten sind
  ausschließlich erfunden und liegen unter `tests/beispiel/`.
- Echte Kontoauszüge (auch die eigenen von Matthias) bleiben auf seinem Rechner
  oder später auf dem Portal-Server. Sie werden nie committet und nie in Chats,
  Berichte oder Projektdateien kopiert. Ergebnisse daraus nur zusammengefasst.
- Nie Secrets committen. Laufzeitwerte nur als Variablennamen dokumentieren.
- **Lizenzen:** Nur Abhängigkeiten mit MIT-, BSD-, Apache-, MPL-, PSF- oder
  LGPL-Lizenz (LGPL nur unverändert als Bibliothek). Kein Code aus GPL- oder
  AGPL-Projekten übernehmen (z. B. Firefly III, Mietfuchs ab 0.12,
  Immobilienverwalter, NebenkostenFix, Berlussimo); diese nur als Ideenquelle.
  Jede neue Abhängigkeit mit Lizenz in `docs/abhaengigkeiten.md` eintragen.
- Kundendaten verlassen den Server nicht: keine externen KI- oder Cloud-Dienste
  in der Auswertung ohne gesonderte Entscheidung (EU-Standort, AV-Vertrag).
- Keine Änderungen an Preisen, Rechtstexten oder Fachaussagen ohne Auftrag.
- Nichts ohne ausdrückliche Freigabe von Matthias veröffentlichen oder deployen.
  Ziel-Server ist der KVM-2 `srv1657443`, erst nach Umzug, Parallelwoche und
  Neuinstallation.
- Geldbeträge immer als `Decimal`, nie als Fließkommazahl.
- Hochgeladene Dateien sind nicht vertrauenswürdig: XML nur mit `defusedxml`.

## Aufbau

- `auswertung/`: Auswertungskern (Stufe 1). Import von CAMT.052/053 und CSV,
  Zuordnung über Regeln, erste Einschätzung je Objekt und Jahr.
- `tests/`: Tests mit erfundenen Beispieldaten in `tests/beispiel/`.
- `portal/`, `kundenportal/`: Kundenportal (Django). Seiten unter `kundenportal/templates/`.
- `kundenportal/dokumentvorlagen_dateien/<Prüfstand>/`: Dokumentvorlagen unverändert aus dem
  Vault (PDF, Markdown, `manifest.json`). Nie bearbeiten oder neu formulieren; neue Fassungen nur
  als neuer Prüfstand mit Manifest. Ausgefüllte Vorlagen gehören nie ins Repository.
- `betrieb/`: Anleitung und Compose-Beispiel für den KVM-2, ohne Geheimnisse.
- `docs/`: Entscheidungen und Abhängigkeiten.

## Befehle

Python 3.12 oder neuer; für PDF-Auszüge `pdftotext` (Paket poppler-utils).

- Abhängigkeiten: `pip install -e ".[test]"`
- Tests: `python -m pytest`
- Portal lokal: `PORTAL_DEBUG=1 python manage.py runserver` (vorher `migrate`).
- Erste Einschätzung: `python -m auswertung --stammdaten <datei.toml> <Auszüge ...>`
  (Ausgabe als Markdown auf die Konsole; mit `--json` als JSON).
