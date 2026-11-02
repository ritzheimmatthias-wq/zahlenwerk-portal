# Belegeingang, Jahresreports und Berechtigungen im Portal

Stand 02.11.2026. Nichts davon ist veröffentlicht oder deployt.

## Belegeingang

Es gibt drei Wege, die alle dieselbe Prüfung durchlaufen: Inhalt statt Endung, Virenscan, Verschlüsselung je Datei und Dublettenprüfung über SHA-256.

1. **Upload im Portal**: wie bisher.
2. **Smartphone-Erfassung** unter `/auftrag/<id>/beleg/`:
   - Browserbasiert, ohne App.
   - Kamera oder vorhandenes Foto, mehrere Seiten je Beleg, Vorschau.
   - Bearbeiten vor dem Senden: drehen, Ränder zuschneiden, neu aufnehmen, entfernen.
   - Verkleinerung auf höchstens 2.400 px und etwa 2,5 MB je Seite.
   - Übertragung Seite für Seite mit Statusanzeige.
   - Nach einem Abbruch bleiben die Seiten im Browser (IndexedDB) und werden beim nächsten Öffnen fortgesetzt. Eine wiederholte Seite mit gleicher Prüfsumme wird nicht doppelt gespeichert.
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
- **Anhänge**: Sie müssen exakt zur signierten Liste passen (Name und SHA-256).
- **Antwort**: `{"eingang", "status", "neu"}`.
  - 500 bedeutet: Der Eingang ist gespeichert, aber die Verarbeitung ist fehlgeschlagen. n8n soll erneut senden.
  - Eine bereits verarbeitete Message-ID wird nicht noch einmal verarbeitet (`neu: false`).
- **Zuordnung**: Absender → Kundenzugang → freigegebene Objekte → offener Auftrag. Eine Auftragsnummer im Betreff („A-123“ oder „Auftrag 123“) gilt nur, wenn der Auftrag zum Absender gehört. Ohne eindeutige Zuordnung bleibt der Anhang verschlüsselt mit dem Status „Zuordnung offen“, bis Zahlenwerk ihn unter `/intern/belege/` zuordnet.
- **Quarantäne**:
  - Unzulässige oder verdächtige Anhänge (zum Beispiel Makros, falscher Inhalt) werden verschlüsselt in Quarantäne gelegt.
  - Bei einem Virenfund wird nur der Befund gespeichert, nicht die Datei.
- **Nachrichtentext**: wird nur als Hinweis gespeichert und angezeigt (escaped), nie ausgeführt.
- **Eingangsbestätigung an den Absender**: standardmäßig aus (`PORTAL_BELEGE_EINGANGSBESTAETIGUNG=1` schaltet sie ein). Sie bestätigt nur den Eingang und keine fachliche Freigabe.
- **Kopie an info@**: wird in Hostinger oder n8n eingerichtet. belege@ bleibt die führende Quelle.
- **IMAP-Rückfallweg**: `python manage.py belege_imap_abruf` liest per SSL/TLS auf Port 993. Zugangsdaten kommen aus `PORTAL_BELEGE_IMAP_BENUTZER` und `PORTAL_BELEGE_IMAP_PASSWORT`. Eine Nachricht wird erst nach erfolgreicher Übernahme als gelesen markiert.
- **OCR**: Es gibt noch keine OCR-Auswertung. Eingegangene Belege prüft Zahlenwerk.

## Abrechnung und Jahresreports

- **Abrechnungsdaten**: TOML (Format wie `tests/beispiel/betriebskosten_pilot.toml`) unter `/intern/objekt/<id>/abrechnung/` hochladen. Jeder Lauf wird im Rechenkern berechnet und als unveränderlicher Stand gespeichert (`Abrechnungslauf`, mit Prüfsummen). Gleiche Eingabe ergibt keinen neuen Stand.
- **Freigabestufen**: Entwurf → fachlich geprüft → vom Eigentümer freigegeben → endgültig. Rote Warnungen sperren. Nur der neueste Stand ist freigabefähig. Ein verfälschter Stand wird über die Prüfsumme erkannt.
- **Reports**: Sie entstehen nur aus endgültigen Ständen und werden als unveränderliche Snapshots (`Report`) mit Regeln und Prüfsumme gespeichert. Ihre Freigabestufen sind Entwurf → fachlich geprüft → freigegeben, jede Stufe mit `Statuswechsel` (wer, wann).
- **Portalansicht und PDF**: Beide werden aus demselben gespeicherten Inhalt erzeugt. Die Portalansicht nutzt ein eigenes Stylesheet `/bericht.css`, weil die Content-Security-Policy keine Inline-Stile erlaubt.

## Datenschutz- und Berechtigungscheck

| Bereich | Regel | Geprüft durch |
|---|---|---|
| Interne Seiten `/intern/…` | nur Zahlenwerk (`is_staff`), sonst 403 | `test_reports_rollen_und_mandantentrennung` |
| Reports | Eigentümer nur für freigegebene Objekte des eigenen Kunden und nur im Status „freigegeben“; sonst 404 | dito |
| Fremder Kunde | weder Objekt noch Report noch PDF, 404 | dito |
| Report aus fremdem Lauf | 404 | `test_report_nur_aus_endgueltigem_stand` |
| Smartphone-Erfassung | eigener, offener Auftrag; Vorgang nur für den Ersteller; Kunden erst nach Freigabe | `test_mobile_erfassung_*` |
| Eingang belege@ | Signatur, Zeitfenster, Anhangsliste; keine Zuordnung ohne passenden Kundenzugang | `test_eingang_*` |
| Dateien | jede Datei mit eigenem Schlüssel verschlüsselt, auch Quarantäne und offene Zuordnung | `test_eingang_quarantaene_und_zuordnung_offen` |
| Protokoll | Berechnung, Freigaben, Report-Ansicht und -Download, Eingänge, Abweisungen, ohne Inhalte | `Protokoll` |
| Automatischer Versand | keiner an Mieter oder Eigentümer; Eingangsbestätigung standardmäßig aus | `test_eingang_zuordnung_dubletten_und_text_nur_hinweis` |
| Sprachmodell | wird nicht verwendet; keine Netzwerk- oder KI-Bibliothek im Rechenkern | `test_kein_sprachmodell_im_rechenweg` |

Offen vor einem Betrieb:

- Löschfristen für Maileingänge und Quarantäne festlegen. Bisher gilt die Löschung nur für Dokumente von Aufträgen.
- n8n-Workflow bauen und Hostinger-Webhook einrichten (Server-Secrets).
- Rolle „Mitarbeitende“ ohne Freigaberecht, falls benötigt.
