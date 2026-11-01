# Abhängigkeiten und Lizenzen

| Paket | Zweck | Lizenz | Seit |
| --- | --- | --- | --- |
| defusedxml | sicheres Einlesen von CAMT-XML aus Uploads | PSF-2.0 | 31.10.2026 |
| pytest (nur Tests) | Testlauf | MIT | 31.10.2026 |
| pdftotext (poppler-utils, Systemprogramm) | Text aus PDF-Kontoauszügen | GPL-2.0/3.0, nur als eigenes Programm aufgerufen, kein Code übernommen | 01.11.2026 |

## Entscheidung CAMT-Parser (31.10.2026)

Geprüft wurde `pycamt` 1.1.1 (MIT). Es liefert allgemeine Wörterbücher und
benötigt `lxml`. Für das Portal brauchen wir dagegen gezielt Gegenpartei aus
Sicht des Kontoinhabers (bei Eingängen der Zahler, bei Ausgängen der Empfänger),
Salden mit Vorzeichen und einen gegen schädliche XML-Dateien abgesicherten
Import. Das ist mit rund 150 Zeilen auf Basis von `defusedxml` übersichtlicher
und ohne weitere Abhängigkeit möglich. `pycamt` bleibt Vergleichsmaßstab, falls
eine Bank ein Format liefert, das unser Parser nicht versteht.

Weitere Kandidaten für spätere Stufen (noch nicht eingebunden): WeasyPrint (BSD,
PDF-Berichte), DuckDB (MIT, Analysen), OCRmyPDF (MPL-2.0), Docling (MIT).

## PDF-Kontoauszüge (01.11.2026)

Ein echter Volksbank-Auszug (7 Blätter, 62 Buchungen) wurde lokal getestet und
danach gelöscht: alle Buchungen erkannt, Saldenkette auf den Cent geschlossen.
Die Vorlage `lese_volksbank_text` bildet dieses Layout nach; die Testdatei
`tests/beispiel/volksbank_auszug.txt` ist erfunden. Jeder PDF-Import prüft
alter Kontostand + Buchungen = neuer Kontostand und bricht sonst ab, damit
keine Zeile unbemerkt verloren geht. Gescannte PDFs brauchen Texterkennung (Stufe 4).
