# Abhängigkeiten und Lizenzen

| Paket | Zweck | Lizenz | Seit |
| --- | --- | --- | --- |
| defusedxml | sicheres Einlesen von CAMT-XML aus Uploads | PSF-2.0 | 31.10.2026 |
| pytest (nur Tests) | Testlauf | MIT | 31.10.2026 |
| WeasyPrint (optional `[pdf]`) | PDF-Bericht aus HTML/CSS | BSD-3-Clause | 01.11.2026 |
| ↳ pydyf, tinycss2, cssselect2, webencodings | Abhängigkeiten von WeasyPrint | BSD | 01.11.2026 |
| ↳ tinyhtml5, fonttools, cffi, Pillow | Abhängigkeiten von WeasyPrint | MIT, MIT-0, MIT-CMU | 01.11.2026 |
| ↳ Pyphen | Silbentrennung (von WeasyPrint geladen) | dreifach lizenziert GPL/LGPL/MPL 1.1, genutzt unter MPL 1.1 bzw. LGPL, unverändert | 01.11.2026 |
| Pango (Systembibliothek) | Textsatz für WeasyPrint | LGPL-2.1, unverändert als Bibliothek | 01.11.2026 |
| Django (optional `[portal]`) | Kundenportal: Anmeldung, Formulare, Datenbank | BSD-3-Clause | 01.11.2026 |
| ↳ asgiref, sqlparse | Abhängigkeiten von Django | BSD | 01.11.2026 |
| argon2-cffi | Passwort-Hashes (Argon2) | MIT | 01.11.2026 |
| cryptography | AES-256-GCM-Verschlüsselung der Dateien | Apache-2.0 oder BSD-3-Clause | 01.11.2026 |
| whitenoise | Auslieferung der eigenen CSS-Datei | MIT | 01.11.2026 |
| gunicorn (nur Betrieb) | Anwendungsserver | MIT | 01.11.2026 |
| psycopg (nur Betrieb) | PostgreSQL-Anbindung | LGPL-3.0, unverändert als Bibliothek | 01.11.2026 |
| pytest-django (nur Tests) | Tests des Portals | BSD-3-Clause | 01.11.2026 |
| ClamAV (eigener Container) | Virenscan, nur über das Netzprotokoll angesprochen | GPL-2.0, eigenes Programm, kein Code übernommen | 01.11.2026 |
| PostgreSQL (eigener Container) | Datenbank | PostgreSQL-Lizenz (BSD-ähnlich) | 01.11.2026 |
| pdftotext (poppler-utils, Systemprogramm) | Text aus PDF-Kontoauszügen | GPL-2.0/3.0, nur als eigenes Programm aufgerufen, kein Code übernommen | 01.11.2026 |

## Entscheidung CAMT-Parser (31.10.2026)

Geprüft wurde `pycamt` 1.1.1 (MIT). Es liefert allgemeine Wörterbücher und
benötigt `lxml`. Für das Portal brauchen wir dagegen gezielt Gegenpartei aus
Sicht des Kontoinhabers (bei Eingängen der Zahler, bei Ausgängen der Empfänger),
Salden mit Vorzeichen und einen gegen schädliche XML-Dateien abgesicherten
Import. Das ist mit rund 150 Zeilen auf Basis von `defusedxml` übersichtlicher
und ohne weitere Abhängigkeit möglich. `pycamt` bleibt Vergleichsmaßstab, falls
eine Bank ein Format liefert, das unser Parser nicht versteht.

Weitere Kandidaten für spätere Stufen (noch nicht eingebunden): DuckDB (MIT,
Analysen), OCRmyPDF (MPL-2.0), Docling (MIT). WeasyPrint ist seit Stufe 2 eingebunden.

## PDF-Kontoauszüge (01.11.2026)

Ein echter Volksbank-Auszug (7 Blätter, 62 Buchungen) wurde lokal getestet und
danach gelöscht: alle Buchungen erkannt, Saldenkette auf den Cent geschlossen.
Die Vorlage `lese_volksbank_text` bildet dieses Layout nach; die Testdatei
`tests/beispiel/volksbank_auszug.txt` ist erfunden. Jeder PDF-Import prüft
alter Kontostand + Buchungen = neuer Kontostand und bricht sonst ab, damit
keine Zeile unbemerkt verloren geht. Gescannte PDFs brauchen Texterkennung (Stufe 4).

## PDF-Bericht (01.11.2026)

`auswertung/pdf_bericht.py` setzt Steckbrief und erste Einschätzung mit
WeasyPrint als A4-PDF: Seite 1 hochkant (Kennzahlen, Steckbrief, Hinweise),
danach quer (Hausgeld je Monat, Konten, Ausgaben, offene Buchungen). Alle Werte
werden maskiert, externe Ressourcen werden nicht nachgeladen. Schriften: Geist
und Lora, falls auf dem Server installiert (beide OFL), sonst DejaVu. Der
Bericht trägt immer den Vermerk „Entwurf · vorläufig, zu prüfen“ und keine
Telefonnummer.

## Kundenportal Stufe 1 (01.11.2026)

Django statt eines Mikro-Frameworks, weil Anmeldung, Sitzungen, CSRF-Schutz,
Passwort-Hashing und Datenbank-Migrationen geprüft mitgeliefert werden. Weniger
eigener Sicherheitscode heißt weniger Fehlerquellen für ein Ein-Personen-Projekt.
Die Seiten werden auf dem Server erzeugt, ohne JavaScript und ohne externe
Schriften oder Dienste (strenge Content-Security-Policy).
