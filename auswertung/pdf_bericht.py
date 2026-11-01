"""PDF-Bericht im Zahlenwerk-Design: Steckbrief und erste Einschätzung.

`als_html` erzeugt ein vollständiges HTML-Dokument (testbar ohne PDF-Werkzeug),
`als_pdf` setzt es mit WeasyPrint (BSD) in ein PDF. Alle Werte aus Uploads und
Stammdaten werden maskiert; WeasyPrint lädt keine externen Ressourcen nach.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from html import escape
from pathlib import Path

from .einschaetzung import MONATE, Ergebnis
from .modell import eur, iban_kurz
from .stammdaten import Stammdaten

NULL = Decimal("0.00")

SCHWERE = {"kritisch": ("kritisch", "bad"), "pruefen": ("prüfen", "warn"), "info": ("Info", "info")}
STATUS = {
    "ok": ("✓", "ok", "gezahlt"),
    "teil": ("T", "part", "Teilzahlung"),
    "mehr": ("+", "warn", "zu viel"),
    "doppelt": ("2×", "warn", "mehrfach"),
    "fehlt": ("–", "bad", "fehlt"),
}

CSS = """
@page { size: A4; margin: 18mm 16mm 20mm 16mm;
  @bottom-left { content: "Zahlenwerk Immobilien · zahlenwerk-immobilien.de"; font-size: 7.5pt; color: #5f6d68; }
  @bottom-right { content: "Seite " counter(page) " von " counter(pages); font-size: 7.5pt; color: #5f6d68; } }
@page breit { size: A4 landscape; }
* { box-sizing: border-box; }
body { font-family: "Geist", "Inter", "DejaVu Sans", Arial, sans-serif; font-size: 9pt; color: #17312c; line-height: 1.4; margin: 0; }
h1, h2, h3 { font-family: "Lora", "DejaVu Serif", Georgia, serif; color: #173b34; font-weight: 600; margin: 0; }
h1 { font-size: 18pt; } h2 { font-size: 12pt; margin: 14pt 0 6pt; } h3 { font-size: 10pt; margin: 8pt 0 4pt; }
.kopf { border-bottom: 2pt solid #2e6658; padding-bottom: 8pt; margin-bottom: 10pt; }
.marke { font-family: "Lora", "DejaVu Serif", Georgia, serif; color: #2e6658; font-size: 10pt; font-weight: 600; }
.meta { color: #5f6d68; font-size: 8pt; }
.stempel { display: inline-block; border: 1pt solid #9a5b12; color: #9a5b12; background: #f9eeda; border-radius: 3pt; padding: 1pt 5pt; font-size: 8pt; font-weight: 600; }
.kpis { display: flex; gap: 6pt; margin: 8pt 0; }
.kpi { flex: 1; border: 0.75pt solid #d9ded8; border-radius: 4pt; padding: 6pt; background: #fbfaf6; }
.kpi b { display: block; font-family: "Lora", "DejaVu Serif", Georgia, serif; font-size: 12pt; color: #173b34; }
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
th, td { padding: 3pt 4pt; border-bottom: 0.5pt solid #d9ded8; text-align: left; vertical-align: top; }
th { background: #f5f0e5; color: #5f6d68; font-size: 7.5pt; font-weight: 600; }
td.r, th.r { text-align: right; white-space: nowrap; }
td.c, th.c { text-align: center; }
tr { break-inside: avoid; }
.steckbrief td:first-child { color: #5f6d68; width: 34%; }
.q { color: #5f6d68; font-size: 7pt; border: 0.5pt solid #d9ded8; border-radius: 2pt; padding: 0 2pt; margin-left: 4pt; }
.pill { display: inline-block; font-size: 7pt; font-weight: 600; padding: 0.5pt 4pt; border-radius: 6pt; white-space: nowrap; }
.ok { background: #e5f4e4; color: #2f7a4f; } .warn { background: #f9eeda; color: #9a5b12; }
.bad { background: #fbebeb; color: #a23a3a; } .info, .part { background: #e7f1fb; color: #2a5b86; }
.hg td.m { text-align: center; padding: 2pt 1pt; font-size: 7.5pt; }
.hg td.m span { display: block; border-radius: 2pt; }
.hg th.m { text-align: center; padding: 3pt 1pt; }
.balken { height: 6pt; background: #f5f0e5; border-radius: 3pt; }
.balken i { display: block; height: 6pt; background: #2e6658; border-radius: 3pt; }
ul.hinweise { list-style: none; padding: 0; margin: 0; }
ul.hinweise li { padding: 3pt 0; border-bottom: 0.5pt solid #d9ded8; break-inside: avoid; }
.breit { page: breit; break-before: page; }
.legende { margin: 4pt 0; font-size: 7.5pt; color: #5f6d68; }
.legende span { margin-right: 8pt; }
.hinweis { margin-top: 12pt; padding: 6pt 8pt; background: #f5f0e5; border-left: 2pt solid #2e6658; font-size: 8pt; }
"""


def _e(x: object) -> str:
    return escape(str(x), quote=True)


def _datum(d: date) -> str:
    return f"{d:%d.%m.%Y}"


def _steckbrief(sd: Stammdaten, e: Ergebnis) -> list[tuple[str, str, str]]:
    """(Angabe, Wert, Quelle). Freie Angaben aus den Stammdaten, Rest aus der Auswertung abgeleitet."""
    zeilen: list[tuple[str, str, str]] = [(k, v, "Stammdaten") for k, v in sd.steckbrief.items()]
    zeilen.append(("Einheiten", str(len(sd.einheiten)), "Stammdaten"))
    wechsel = sum(1 for x in sd.einheiten if len(x.eigentuemer) > 1)
    if wechsel:
        zeilen.append(("Eigentümerwechsel im Jahr", str(wechsel), "Stammdaten"))
    soll = sum((x.hausgeld_soll for x in sd.einheiten), NULL)
    zeilen.append((f"Hausgeld Soll {e.jahr}", f"{eur(soll)} je Monat, {eur(soll * 12)} im Jahr", "Stammdaten"))
    for k in e.konten:
        stand = f"Ende {e.jahr}: {eur(k.ende)}" if k.ende is not None else "kein Endsaldo"
        zeilen.append((f"{k.art} {iban_kurz(k.iban)}", stand + " (vorläufig)", "Kontoauszüge"))
    return zeilen


def als_html(e: Ergebnis, sd: Stammdaten, stand: date | None = None) -> str:
    stand = stand or date.today()
    soll = sum((h.soll_jahr for h in e.hausgeld), NULL)
    ist = sum((h.ist_jahr for h in e.hausgeld), NULL)
    ausgaben = -sum((g.summe for g in e.gruppen), NULL)
    kritisch = sum(1 for h in e.hinweise if h[0] == "kritisch")
    t: list[str] = []
    a = t.append
    a('<!DOCTYPE html><html lang="de"><head><meta charset="utf-8">')
    a(f"<title>Erste Einschätzung {_e(e.objekt)} {e.jahr}</title><style>{CSS}</style></head><body>")
    a('<div class="kopf"><div class="marke">Zahlenwerk Immobilien</div>')
    a(f"<h1>Erste Einschätzung · {_e(e.objekt)} · {e.jahr}</h1>")
    a(f'<div class="meta">Stand {_datum(stand)} · Grundlage: {e.anzahl_buchungen} Buchungen aus Kontoauszügen und Stammdaten · '
      '<span class="stempel">Entwurf · vorläufig, zu prüfen</span></div></div>')

    a('<div class="kpis">')
    for titel, wert, unter in [
        (f"Hausgeld Soll {e.jahr}", eur(soll), ""),
        ("Hausgeld eingegangen", eur(ist), f"Abweichung {eur(ist - soll)}"),
        ("Ausgaben gesamt", eur(ausgaben), f"{sum(g.anzahl for g in e.gruppen)} Buchungen"),
        ("Hinweise", str(len(e.hinweise)), f"davon kritisch {kritisch}"),
    ]:
        a(f'<div class="kpi"><span class="meta">{_e(titel)}</span><b>{_e(wert)}</b><span class="meta">{_e(unter)}</span></div>')
    a("</div>")

    a("<h2>Steckbrief</h2><table class=\"steckbrief\"><tbody>")
    for k, v, q in _steckbrief(sd, e):
        a(f'<tr><td>{_e(k)}</td><td>{_e(v)}<span class="q">{_e(q)}</span></td></tr>')
    a("</tbody></table>")

    a("<h2>Hinweise</h2>")
    if e.hinweise:
        a('<ul class="hinweise">')
        for s, text in e.hinweise:
            label, cls = SCHWERE.get(s, (s, "info"))
            a(f'<li><span class="pill {cls}">{_e(label)}</span> {_e(text)}</li>')
        a("</ul>")
    else:
        a('<p class="meta">Keine Auffälligkeiten.</p>')

    # Breite Seite für die Monatsübersicht
    a('<section class="breit"><h2>Hausgeld je Einheit</h2><div class="legende">')
    for z, cls, txt in STATUS.values():
        a(f'<span><span class="pill {cls}">{_e(z)}</span> {_e(txt)}</span>')
    a('</div><table class="hg"><thead><tr><th>Einheit</th><th>Eigentümer</th>')
    a("".join(f'<th class="m">{m}</th>' for m in MONATE))
    a('<th class="r">Soll</th><th class="r">Ist</th><th class="r">Abweichung</th></tr></thead><tbody>')
    for h in e.hausgeld:
        zellen = []
        for i, s in enumerate(h.status):
            z, cls, txt = STATUS[s]
            zellen.append(f'<td class="m"><span class="{cls}" title="{_e(MONATE[i] + ": " + txt)}">{_e(z)}</span></td>')
        abw = eur(h.abweichung)
        if h.abweichung != 0:
            abw = f'<span class="pill {"bad" if h.abweichung < 0 else "warn"}">{_e(abw)}</span>'
        a(f"<tr><td>{_e(h.einheit)}</td><td>{_e(h.eigentuemer)}</td>{''.join(zellen)}"
          f'<td class="r">{_e(eur(h.soll_jahr))}</td><td class="r">{_e(eur(h.ist_jahr))}</td><td class="r">{abw}</td></tr>')
    a(f'<tr><td colspan="14"><b>Summe</b></td><td class="r"><b>{_e(eur(soll))}</b></td>'
      f'<td class="r"><b>{_e(eur(ist))}</b></td><td class="r"><b>{_e(eur(ist - soll))}</b></td></tr>')
    a("</tbody></table>")

    a("<h2>Konten</h2><table><thead><tr><th>Konto</th><th>Art</th>"
      f'<th class="r">Anfang {e.jahr}</th><th class="r">Ende {e.jahr}</th><th>Saldenkette</th></tr></thead><tbody>')
    for k in e.konten:
        if not k.fehlende_monate and not k.kettenfehler:
            kette = '<span class="pill ok">lückenlos</span>'
        else:
            teile = []
            if k.fehlende_monate:
                teile.append("fehlt: " + ", ".join(MONATE[m - 1] for m in k.fehlende_monate))
            if k.kettenfehler:
                teile.append(f"{len(k.kettenfehler)} Bruch")
            kette = f'<span class="pill bad">{_e("; ".join(teile))}</span>'
        a(f"<tr><td>{_e(iban_kurz(k.iban))}</td><td>{_e(k.art)}</td>"
          f'<td class="r">{_e(eur(k.anfang))}</td><td class="r">{_e(eur(k.ende))}</td><td>{kette}</td></tr>')
    a("</tbody></table>")

    a('<h2>Ausgaben nach Gruppen</h2><table><thead><tr><th>Gruppe</th><th style="width:30%"></th>'
      '<th class="r">Summe</th><th class="r">Buchungen</th><th>Größte Empfänger</th></tr></thead><tbody>')
    groesste = max((-g.summe for g in e.gruppen), default=NULL)
    for g in e.gruppen:
        anteil = int((-g.summe / groesste * 100).quantize(Decimal("1"))) if groesste else 0
        top = ", ".join(f"{n} ({eur(-b)})" for n, b in g.groesste())
        a(f'<tr><td>{_e(g.name)}</td><td><div class="balken"><i style="width:{anteil}%"></i></div></td>'
          f'<td class="r">{_e(eur(-g.summe))}</td><td class="r">{g.anzahl}</td><td>{_e(top)}</td></tr>')
    a("</tbody></table>")

    offen = e.offene_eingaenge + e.offene_ausgaenge
    if offen:
        a(f"<h2>Nicht zugeordnete Buchungen ({len(offen)})</h2><table><thead><tr><th>Datum</th>"
          '<th class="r">Betrag</th><th>Gegenseite</th><th>Verwendungszweck</th></tr></thead><tbody>')
        for b in offen:
            a(f'<tr><td>{_datum(b.datum)}</td><td class="r">{_e(eur(b.betrag))}</td>'
              f"<td>{_e(b.gegen_name or '(ohne Namen)')}</td><td>{_e(b.zweck)}</td></tr>")
        a("</tbody></table>")
    a('<div class="hinweis">Automatisch erstellte Ersteinschätzung aus den eingereichten Unterlagen. '
      "Alle Werte sind vorläufig und werden von Zahlenwerk geprüft, bevor sie in eine Abrechnung oder einen "
      "Prüfbericht übernommen werden.</div></section>")
    a("</body></html>")
    return "".join(t)


def _kein_nachladen(url: str) -> None:
    raise ValueError(f"Externe Ressourcen sind im Bericht nicht erlaubt: {url[:80]}")


def als_pdf(e: Ergebnis, sd: Stammdaten, stand: date | None = None) -> bytes:
    # erst hier importieren: Markdown/JSON brauchen kein PDF-Werkzeug
    from weasyprint import HTML
    from weasyprint.urls import URLFetcher

    class KeinNachladen(URLFetcher):
        def fetch(self, url, headers=None):
            _kein_nachladen(url)

    return HTML(string=als_html(e, sd, stand), url_fetcher=KeinNachladen()).write_pdf()


def schreibe_pdf(pfad: str | Path, e: Ergebnis, sd: Stammdaten, stand: date | None = None) -> Path:
    pfad = Path(pfad)
    pfad.write_bytes(als_pdf(e, sd, stand))
    return pfad
