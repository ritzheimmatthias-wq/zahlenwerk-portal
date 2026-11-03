# Belegeingang, Jahresreports und Berechtigungen im Portal

Stand 02.11.2026 (mit Korrekturen aus der Prüfung vom 02.11.2026). Nichts davon ist veröffentlicht oder deployt.

## Belegeingang

Es gibt drei Wege, die alle dieselbe Prüfung durchlaufen: Inhalt statt Endung, Virenscan, Verschlüsselung je Datei und Dublettenprüfung über SHA-256.

1. **Upload im Portal**: wie bisher.
2. **Smartphone-Erfassung** unter `/auftrag/<id>/beleg/`:
   - Browserbasiert, ohne App.
   - Kamera oder vorhandenes Foto, mehrere Seiten je Beleg, Vorschau.
   - Bearbeiten vor dem Senden: drehen, Ränder zuschneiden, neu aufnehmen, entfernen.
   - Verkleinerung auf höchstens 2.400 px und etwa 2,5 MB je Seite.
   - Übertragung Seite für Seite mit Statusanzeige.
   - Nach einem Abbruch bleiben die Seiten im Browser (IndexedDB) und werden beim nächsten Öffnen fortgesetzt. Eine wiederholte Seite mit gleicher Prüfsumme wird nicht doppelt gespeichert, auch nicht nach dem Abschluss.
   - Der gespeicherte Vorgang wird erst gelöscht, wenn der Server den Abschluss bestätigt hat. Sind alle Seiten übertragen, fehlt aber die Bestätigung (Netzabbruch, verlorene Antwort, Browser geschlossen), bietet die Seite beim nächsten Öffnen den Abschluss erneut an.
   - Der Abschluss ist serverseitig idempotent (Zeilensperre): Eine Wiederholung liefert `bereits_abgeschlossen: true`, erzeugt keinen zweiten Beleg und keine zweite interne Benachrichtigung. Die Benachrichtigung geht erst nach dem Commit hinaus.
   - Geprüft im Browser (Chromium über Playwright, `tests/test_browser_belegerfassung.py`): Abbruch vor dem Abschluss mit Neustart des Browsers, Abschluss ohne Antwort, Seitenwiederholung, Drehen, Zuschneiden, Tastaturbedienung, Layout bei 320, 375 und 390 px.
   - Ohne JavaScript dient ein normales Formular für mehrere Dateien.
   - Zahlenwerk kann die Funktion immer testen. Kunden sehen sie erst mit `PORTAL_MOBILE_ERFASSUNG_FUER_KUNDEN=1`, also nach technischer Abnahme und Freigabe durch Matthias. Bis dahin gilt sie gegenüber Kunden nur als geplante Möglichkeit.
3. **Postfach belege@zahlenwerk-immobilien.de**: ein eigenständiges Hostinger-Postfach, kein Alias.

### Schnittstelle für n8n

Hostinger meldet `message.received` per Webhook an n8n. n8n prüft den Webhook, holt Nachricht und Anhänge ab (Hostinger-Mail-Schnittstelle oder IMAP) und ruft das Portal so auf:

```
POST /eingang/belege/            multipart/form-data
  meta    JSON: message_id, absender, betreff, text, empfangen (ISO 8601),
          anhaenge: [{name, sha256}]
  anhang  je Anhang eine Datei
Header
  X-Zahlenwerk-Zeit       Unix-Zeit in Sekunden (höchstens 5 Minuten alt)
  X-Zahlenwerk-Signatur   HMAC-SHA256(Geheimnis, "<Zeit>.<meta>") als Hex
```

- **Geheimnis**: nur als Server-Secret `PORTAL_BELEGE_WEBHOOK_GEHEIMNIS` und in der n8n-Credential-Verwaltung, nie im Repository oder Vault. Ohne Geheimnis antwortet der Eingang mit 404.
- **Anhänge**: Sie müssen exakt zur signierten Liste passen (Name und SHA-256, als Mehrfachmenge; gleiche Dateien zählen einzeln).
- **Zweistufig**: Zuerst sichert das Portal die Nachricht und alle Anhänge verschlüsselt (Status der Dateien „empfangen“, `roh_gespeichert`). Erst danach werden sie geprüft und zugeordnet. Ein Fehler in der zweiten Stufe verliert keinen Anhang.
- **Antwort**: `{"eingang", "status", "neu", "gesichert", "dateien"}`, `dateien` zählt übernommen, Dublette, Quarantäne und Zuordnung offen.
  - 200: fertig verarbeitet. Eine bereits verarbeitete Message-ID wird nicht noch einmal verarbeitet (`neu: false`).
  - 202: sicher gespeichert, ein anderer Lauf bearbeitet den Eingang gerade (befristeter Anspruch, 10 Minuten).
  - 500 mit `gesichert: true`: gespeichert, Verarbeitung fehlgeschlagen. Wiederholung oder Wiederaufnahme setzt fort.
  - 500 mit `gesichert: false`: nichts gespeichert. n8n soll erneut senden.
  - 400: Angaben oder Anhangsliste ungültig.
- **Parallelität**: Pro Message-ID gibt es genau einen Eingang (eindeutiger Schlüssel, Zeilensperre). Verarbeiten darf nur, wer den befristeten Anspruch per bedingtem Update erhält; jeder Anspruch trägt ein eindeutiges Token. Unter der Zeilensperre prüft der Lauf vor jeder Änderung erneut Status, Token und Ablaufzeit. Ein verspäteter Lauf, dessen Anspruch abgelaufen und übernommen ist, oder der einen bereits abgeschlossenen Eingang vorfindet, schreibt nichts (auch keinen Fehlerstatus) und wird protokolliert (`maileingang_veralteter_lauf`). Ein abgebrochener Lauf verliert den Anspruch nach Ablauf.
- **Wiederaufnahme**: `python manage.py belege_wiederaufnahme` setzt gesicherte, aber nicht abgeschlossene Eingänge fort (für einen systemd-Timer, z. B. alle 15 Minuten). Holt nichts aus dem Postfach und versendet nichts an Kunden.
- **Dubletten**: je Auftrag. Dieselbe Datei für einen anderen Auftrag wird dort als eigener Beleg verknüpft, nicht verworfen.
- **Zuordnung**: Absender → Kundenzugang → freigegebene Objekte → offener Auftrag. Eine Auftragsnummer im Betreff („A-123“ oder „Auftrag 123“) gilt nur, wenn der Auftrag zum Absender gehört. Ohne eindeutige Zuordnung bleibt der Anhang verschlüsselt mit dem Status „Zuordnung offen“, bis Zahlenwerk ihn unter `/intern/belege/` zuordnet.
- **Quarantäne**:
  - Unzulässige oder verdächtige Anhänge (zum Beispiel Makros, falscher Inhalt) werden verschlüsselt in Quarantäne gelegt.
  - Bei einem Virenfund wird nur der Befund gespeichert, nicht die Datei.
- **Nachrichtentext**: wird nur als Hinweis gespeichert und angezeigt (escaped), nie ausgeführt.
- **Eingangsbestätigung an den Absender**: standardmäßig aus (`PORTAL_BELEGE_EINGANGSBESTAETIGUNG=1` schaltet sie ein). Sie bestätigt nur den Eingang und keine fachliche Freigabe.
- **Kopie an info@**: wird in Hostinger oder n8n eingerichtet. belege@ bleibt die führende Quelle.
- **IMAP-Rückfallweg**: `python manage.py belege_imap_abruf` liest per SSL/TLS auf Port 993. Zugangsdaten kommen aus `PORTAL_BELEGE_IMAP_BENUTZER` und `PORTAL_BELEGE_IMAP_PASSWORT`. Er setzt zuerst offene Eingänge fort. Eine Nachricht wird erst als gelesen markiert, wenn sie mit allen Anhängen verschlüsselt gesichert ist.
- **OCR**: Es gibt noch keine OCR-Auswertung. Eingegangene Belege prüft Zahlenwerk.

## Abrechnung und Jahresreports

- **Abrechnungsdaten**: TOML (Format wie `tests/beispiel/betriebskosten_pilot.toml`) unter `/intern/objekt/<id>/abrechnung/` hochladen. Jeder Lauf wird im Rechenkern berechnet und als unveränderlicher Stand gespeichert (`Abrechnungslauf`, mit Prüfsummen). Gleiche Eingabe ergibt keinen neuen Stand, aber nur, wenn der vorhandene Stand unverfälscht ist.
- **Bindung an Objekt und Auftrag**: `[objekt] portal_id` muss die Portal-Kennung des Objekts enthalten (angezeigt auf der Abrechnungsseite). Der Abrechnungszeitraum muss im Auftragsjahr enden, bei Objekten mit Wirtschaftsjahr „Kalenderjahr“ auch dort beginnen, und darf höchstens zwölf Monate umfassen. Die geprüfte Zuordnung wird im Stand gespeichert.
- **Freigabestufen**: Entwurf → fachlich geprüft → vom Eigentümer freigegeben → endgültig. Rote Warnungen sperren. Nur der neueste Stand ist freigabefähig.
- **Integritätsprüfung**: Vor jeder Freigabe, Wiederverwendung, Reporterstellung, Anzeige und jedem Download werden Prüfsummen, Warnungszähler und Datenbankfelder aus dem gespeicherten Inhalt neu gebildet und mit dem Statusverlauf abgeglichen. Jeder `Statuswechsel` ist mit dem Inhalt (`bezug_hash`) und mit dem vorherigen Eintrag (`vorher_hash`) verkettet. Ein Fehler sperrt den Schritt und wird protokolliert (`abrechnung_integritaet`); Kunden sehen einen verfälschten Report nicht, Zahlenwerk erhält 403.
- **Datenbankschutz** (Migration 0003): In PostgreSQL verhindern Trigger das Ändern und Löschen von Statuswechseln und das Ändern von Lauf- und Reportinhalten außer dem Status. Löschen ist nur in einer Transaktion mit `SET LOCAL zahlenwerk.loeschung = 'erlaubt'` möglich. In SQLite (nur Entwicklung und Tests) ist nur das Ändern gesperrt.
- **Grenze**: Wer Datenbankverwalter ist, kann Trigger abschalten und Inhalt, Prüfsummen und Kette gemeinsam neu schreiben. Das erkennt die Anwendung allein nicht. Dagegen helfen ein eingeschränkter Datenbankbenutzer für die Anwendung und Sicherungen außerhalb des Servers.
- **Reports**: Sie entstehen nur aus endgültigen Ständen und werden als unveränderliche Snapshots (`Report`) mit Regeln und Prüfsumme gespeichert. Ihre Freigabestufen sind Entwurf → fachlich geprüft → freigegeben, jede Stufe mit `Statuswechsel` (wer, wann).
- **Portalansicht und PDF**: Beide werden aus demselben gespeicherten Inhalt erzeugt. Die Portalansicht nutzt ein eigenes Stylesheet `/bericht.css`, weil die Content-Security-Policy keine Inline-Stile erlaubt.

## Datenschutz- und Berechtigungscheck

| Bereich | Regel | Geprüft durch |
|---|---|---|
| Interne Seiten `/intern/…` | nur Zahlenwerk (`is_staff`), sonst 403 | `test_reports_rollen_und_mandantentrennung` |
| Reports | Eigentümer nur für freigegebene Objekte des eigenen Kunden und nur im Status „freigegeben“; sonst 404 | dito |
| Fremder Kunde | weder Objekt noch Report noch PDF, 404 | dito |
| Report aus fremdem Lauf | 404 | `test_report_nur_aus_endgueltigem_stand` |
| Smartphone-Erfassung | eigener, offener Auftrag; Vorgang nur für den Ersteller; Kunden erst nach Freigabe; Abschluss idempotent | `test_mobile_erfassung_*`, `test_mobiler_abschluss_idempotent_*`, `test_browser_belegerfassung.py` |
| Abrechnungsimport | nur mit passender Objektkennung und passendem Zeitraum | `test_import_fremdes_objekt_und_falsches_jahr_abgewiesen` |
| Verfälschte Stände und Reports | weder Freigabe noch Report noch Auslieferung | `tests/test_portal_korrekturen.py`, `tests/test_postgres.py` |
| Eingang belege@ | Signatur, Zeitfenster, Anhangsliste; keine Zuordnung ohne passenden Kundenzugang | `test_eingang_*` |
| Dateien | jede Datei mit eigenem Schlüssel verschlüsselt, auch Quarantäne und offene Zuordnung | `test_eingang_quarantaene_und_zuordnung_offen` |
| Protokoll | Berechnung, Freigaben, Report-Ansicht und -Download, Eingänge, Abweisungen, ohne Inhalte | `Protokoll` |
| Automatischer Versand | keiner an Mieter oder Eigentümer; Eingangsbestätigung standardmäßig aus | `test_eingang_zuordnung_dubletten_und_text_nur_hinweis` |
| Sprachmodell | wird nicht verwendet; keine Netzwerk- oder KI-Bibliothek im Rechenkern | `test_kein_sprachmodell_im_rechenweg` |

Offen vor einem Betrieb:

- Löschfristen für Maileingänge und Quarantäne festlegen. Bisher gilt die Löschung nur für Dokumente von Aufträgen.
- n8n-Workflow bauen und Hostinger-Webhook einrichten (Server-Secrets).
- Rolle „Mitarbeitende“ ohne Freigaberecht, falls benötigt.
