# Abhängigkeiten und Lizenzen

| Paket | Zweck | Lizenz | Seit |
| --- | --- | --- | --- |
| defusedxml | sicheres Einlesen von CAMT-XML aus Uploads | PSF-2.0 | 31.10.2026 |
| pytest (nur Tests) | Testlauf | MIT | 31.10.2026 |

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
