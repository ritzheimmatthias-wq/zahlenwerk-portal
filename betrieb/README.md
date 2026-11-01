# Betrieb des Kundenportals auf dem KVM-2

Stand: vorbereitet, **nicht in Betrieb**. Live erst nach Rückfallwoche, Upgrade auf
KVM 2, Neuinstallation und ausdrücklicher Freigabe durch Matthias. Dieser Ordner
enthält keine Geheimnisse.

## Bausteine

- `portal`: Django mit gunicorn hinter dem vorhandenen Traefik, nur über HTTPS.
- `db`: PostgreSQL, nur im internen Netz erreichbar. Hält Stammdaten, Status und Dateiverweise.
- `clamav`: Virenscanner. Ohne erreichbaren Scanner nimmt das Portal keine Uploads an.
- `loeschjob`: täglich, löscht Unterlagen 90 Tage nach Auftragsabschluss.
- Volume `portal-daten`: verschlüsselte Dateien (AES-256-GCM, je Datei eigener Schlüssel).

## Laufzeitvariablen (nur Namen)

In `betrieb/.env` auf dem Server, `chmod 600`, nie im Repository:

`PORTAL_SECRET_KEY`, `PORTAL_MASTER_KEY` (32 Byte, base64), `PORTAL_HOSTS`, `PORTAL_DOMAIN`,
`PORTAL_BASIS_URL`, `PORTAL_DB_NAME`, `PORTAL_DB_USER`, `PORTAL_DB_PASSWORD`,
`PORTAL_MAIL_HOST`, `PORTAL_MAIL_PORT`, `PORTAL_MAIL_USER`, `PORTAL_MAIL_PASSWORD`,
`PORTAL_MAIL_ABSENDER`, `PORTAL_MAIL_INTERN`.

Der Hauptschlüssel `PORTAL_MASTER_KEY` gehört zusätzlich offline in den Passwortmanager.
Ohne ihn sind die Dateien auch aus der Sicherung nicht lesbar.

## Erste Einrichtung (später, nach Freigabe)

1. Schlüssel auf dem Server erzeugen, z. B. `python3 -c "import secrets,base64;print(base64.b64encode(secrets.token_bytes(32)).decode())"`.
2. `.env` anlegen, `docker compose -f compose.beispiel.yml up -d --build`.
3. `docker compose run --rm portal python manage.py migrate`.
4. Eigenen Zugang anlegen: `docker compose run --rm portal python manage.py createsuperuser`
   (danach im Admin-Bereich des Portals arbeiten, die Django-Verwaltung ist nicht eingebunden).
5. Täglichen systemd-Timer für `docker compose run --rm loeschjob` einrichten.
6. Sicherung: Datenbank-Dump und Volume `portal-daten` per restic zur Hetzner Storage Box,
   Aufbewahrung höchstens 30 Tage (7 täglich, 4 wöchentlich). Wiederherstellung monatlich testen.
7. Prüfen: Anmeldung mit Code, Upload, Virenscan mit EICAR-Testdatei, Download, Löschjob.

## Grenzen der ersten Version

- Das Löschen entfernt Datei und Schlüssel. In den Sicherungen verschwinden die Dateien nach
  Ablauf der Aufbewahrung (rund 120 Tage nach Auftragsende).
- Nachrichten, Steckbrief-Bearbeitung durch Kunden und die Einbindung der Auswertung folgen.
