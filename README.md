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
