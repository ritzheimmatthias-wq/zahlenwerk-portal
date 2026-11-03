"""PDF-Entwürfe je Mietverhältnis und interner Kontrollbericht.

Zwei getrennte Ausgabepfade:
- Wohnraum: verständliche Bruttodarstellung, ohne Netto- oder Steuerangaben.
- Gewerbe: brutto (steuerfrei) oder netto + Umsatzsteuer + brutto, je nach
  bestätigter Konfiguration. Gesperrte Mietverhältnisse erhalten kein PDF.

Alle Werte werden maskiert, externe Ressourcen werden nicht geladen.
"""
from __future__ import annotations

import json
from decimal import Decimal
from html import escape
from pathlib import Path

from .modell import NULL
from .rechnen import Ergebnis, MvErgebnis

SCHLUESSEL_TEXT = {
    "flaeche": ("Fläche", "m²"),
    "personen": ("Personen", "Pers."),
    "einheiten": ("Einheiten", "Einh."),
    "verbrauch": ("Verbrauch", ""),
    "direkt": ("direkt zugeordnet", "Einh."),
    "messdienst": ("laut Messdienst", ""),
}
DARSTELLUNG = {
    "wohnen": "Wohnraum",
    "gewerbe_brutto": "Gewerbe ohne Umsatzsteuerausweis",
    "gewerbe_netto": "Gewerbe mit Umsatzsteuer",
    "gesperrt": "gesperrt",
}

CSS = """
@page { size: A4; margin: 18mm 16mm 20mm 18mm;
  @bottom-left { content: string(fuss); font-size: 7pt; color: #5f6d68; }
  @bottom-right { content: "Seite " counter(page) " von " counter(pages); font-size: 7pt; color: #5f6d68; } }
* { box-sizing: border-box; }
body { font-family: "Geist", "Inter", "DejaVu Sans", Arial, sans-serif; font-size: 9pt; color: #17312c; line-height: 1.4; margin: 0; }
.fuss { string-set: fuss content(); display: none; }
h1, h2 { font-family: "Lora", "DejaVu Serif", Georgia, serif; color: #173b34; font-weight: 600; margin: 0; }
h1 { font-size: 15pt; margin: 10pt 0 2pt; } h2 { font-size: 10.5pt; margin: 12pt 0 4pt; }
.absender { font-size: 7.5pt; color: #5f6d68; border-bottom: 0.5pt solid #d9ded8; padding-bottom: 2pt; width: 85mm; }
.anschrift { margin: 4pt 0 10pt; min-height: 22mm; white-space: pre-line; }
.stempel { float: right; border: 1pt solid #a23a3a; color: #a23a3a; background: #fbebeb; border-radius: 3pt; padding: 2pt 6pt; font-size: 8pt; font-weight: 700; }
.meta { color: #5f6d68; font-size: 8pt; }
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
th, td { padding: 3pt 4pt; border-bottom: 0.5pt solid #d9ded8; text-align: left; vertical-align: top; }
th { background: #f5f0e5; color: #5f6d68; font-size: 7.5pt; font-weight: 600; }
td.r, th.r { text-align: right; white-space: nowrap; }
tr.summe td { font-weight: 700; border-top: 1pt solid #2e6658; }
tr { break-inside: avoid; }
.angaben td:first-child { color: #5f6d68; width: 38%; }
.ergebnis { break-inside: avoid; margin-top: 10pt; border: 1pt solid #2e6658; border-radius: 4pt; padding: 8pt; background: #fbfaf6; }
.ergebnis b { font-family: "Lora", "DejaVu Serif", Georgia, serif; font-size: 13pt; }
.rot { color: #a23a3a; } .gelb { color: #9a5b12; }
.klein { font-size: 7.5pt; color: #5f6d68; }
table.eng { font-size: 7.5pt; } table.eng th, table.eng td { padding: 2.5pt 3pt; }
"""


def _e(x) -> str:
    return escape(str(x), quote=True)


def zahl(b: Decimal | None, stellen: int = 2) -> str:
    if b is None:
        return "–"
    if b.quantize(Decimal(1).scaleb(-stellen)) == 0:
        b = abs(b)
    s = f"{b:,.{stellen}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return s


def eur(b: Decimal | None) -> str:
    return "–" if b is None else f"{zahl(b)} €"


def _datum(d) -> str:
    return d.strftime("%d.%m.%Y")


def _kopf(erg: Ergebnis, m: MvErgebnis, titel: str, version: int | None, status: str) -> str:
    o = erg.daten.objekt
    stempel = "" if status == "endgueltig" else f'<div class="stempel">ENTWURF{f" · Version {version}" if version else ""} · nicht versenden</div>'
    return f"""{stempel}
<div class="absender">{_e(o.vermieter)}{(" · " + _e(o.vermieter_anschrift)) if o.vermieter_anschrift else ""}</div>
<div class="anschrift">{_e(m.mieter)}{chr(10) + _e(m.anschrift) if m.anschrift else ""}</div>
<h1>{_e(titel)} {erg.daten.zeitraum.von.year if erg.daten.zeitraum.von.year == erg.daten.zeitraum.bis.year else ""}</h1>
<table class="angaben">
<tr><td>Objekt</td><td>{_e(o.name)}, {_e(o.anschrift)}</td></tr>
<tr><td>Mieteinheit</td><td>{_e(m.einheit_bezeichnung)}</td></tr>
<tr><td>Abrechnungszeitraum</td><td>{_datum(erg.daten.zeitraum.von)} bis {_datum(erg.daten.zeitraum.bis)} ({erg.daten.zeitraum.tage} Tage)</td></tr>
<tr><td>Ihr Nutzungszeitraum</td><td>{_datum(m.nutzungszeitraum.von)} bis {_datum(m.nutzungszeitraum.bis)} ({m.nutzungszeitraum.tage} Tage)</td></tr>
</table>"""


def _bezug(z) -> tuple[str, str]:
    name, einheit = SCHLUESSEL_TEXT[z.schluessel]
    if z.schluessel == "messdienst":
        return "", ""
    zeit = " zeitanteilig" if z.schluessel in ("flaeche", "personen", "einheiten", "direkt") else ""
    return f"{zahl(z.bezug_gesamt)} {einheit}".strip(), f"{zahl(z.bezug_anteil)} {einheit}{zeit}".strip()


def _belegliste(m: MvErgebnis) -> str:
    zeilen = []
    for z in m.zeilen:
        quellen = []
        for h in z.herkunft:
            if "beleg" in h:
                quellen.append(" ".join(x for x in (h["beleg"], h.get("lieferant", ""), h.get("rechnungsnummer", "")) if x))
            else:
                quellen.append(f"Messdienst {h['messdienst']} {h.get('anbieter', '')}, Einheit {h['einheit']}".strip())
        zeilen.append(f"<tr><td>{_e(z.bezeichnung)}</td><td>{_e('; '.join(dict.fromkeys(quellen)))}</td></tr>")
    return "<h2>Belegverzeichnis</h2><table><tr><th>Kostenart</th><th>Belege und Messdienstwerte</th></tr>" + "".join(zeilen) + "</table>"


def abrechnung_html(erg: Ergebnis, m: MvErgebnis, *, version: int | None = None, status: str = "entwurf") -> str:
    if m.gesperrt:
        raise ValueError(f"{m.id}: Abrechnung ist gesperrt und erhält keinen PDF-Entwurf")
    netto = m.darstellung == "gewerbe_netto"
    titel = "Betriebskostenabrechnung" if m.nutzung == "wohnen" else "Nebenkostenabrechnung Gewerbe"
    teile = [f'<!DOCTYPE html><html lang="de"><head><meta charset="utf-8"><title>{_e(titel)}</title><style>{CSS}</style></head><body>',
             f'<div class="fuss">{_e(erg.daten.objekt.vermieter)} · {_e(m.einheit_bezeichnung)} · Abrechnung {_e(m.id)}</div>',
             _kopf(erg, m, titel, version, status)]

    def tabelle(zeilen, ueberschrift):
        if not zeilen:
            return ""
        kopf = "<tr><th>Kostenart</th><th class='r'>Gesamtkosten</th><th>Schlüssel und Gesamtgröße</th><th class='r'>Ihr Anteil</th>"
        kopf += "<th class='r'>Netto</th><th class='r'>USt</th><th class='r'>Brutto</th></tr>" if netto else "<th class='r'>Ihr Betrag</th></tr>"
        rows = []
        for z in zeilen:
            g, a = _bezug(z)
            gesamt = z.gesamt_netto if (netto and z.gesamt_netto is not None) else z.gesamt_brutto
            if z.schluessel == "messdienst":
                k = kontrolle[z.kostenart]
                gesamt = k.gesamt_netto if (netto and k.gesamt_netto is not None) else k.gesamt_brutto
            schl = SCHLUESSEL_TEXT[z.schluessel][0] + (f" · {g}" if g else "")
            r = f"<tr><td>{_e(z.bezeichnung)}</td><td class='r'>{eur(gesamt)}</td><td>{_e(schl)}</td><td class='r'>{_e(a)}</td>"
            r += f"<td class='r'>{eur(z.betrag)}</td><td class='r'>{eur(z.ust)}</td><td class='r'>{eur(z.betrag + z.ust)}</td></tr>" if netto else f"<td class='r'>{eur(z.betrag)}</td></tr>"
            rows.append(r)
        s_b = sum((z.betrag for z in zeilen), NULL)
        s_u = sum((z.ust for z in zeilen), NULL)
        summe = f"<tr class='summe'><td colspan='4'>Summe {_e(ueberschrift)}</td>"
        summe += f"<td class='r'>{eur(s_b)}</td><td class='r'>{eur(s_u)}</td><td class='r'>{eur(s_b + s_u)}</td></tr>" if netto else f"<td class='r'>{eur(s_b)}</td></tr>"
        return f"<h2>{_e(ueberschrift)}</h2><table class='{'eng' if netto else ''}'>{kopf}{''.join(rows)}{summe}</table>"

    kontrolle = {k.kostenart: k for k in erg.kontrolle}
    betrieb = [z for z in m.zeilen if not z.heizkosten]
    heiz = [z for z in m.zeilen if z.heizkosten]
    teile.append(tabelle(betrieb, "Betriebskosten"))
    if heiz:
        anbieter = sorted({h.get("anbieter", "") for z in heiz for h in z.herkunft if h.get("anbieter")})
        teile.append(tabelle(heiz, "Heiz- und Warmwasserkosten"))
        teile.append(f"<p class='klein'>Übernommen aus der Abrechnung des Messdienstes{(' ' + _e(', '.join(anbieter))) if anbieter else ''}.</p>")
    if netto:
        satz = zahl(m.ust_satz * 100, 0) if m.ust_satz is not None else "–"
        teile.append(f"<p class='klein'>Umsatzsteuer {satz} % gemäß der für dieses Mietverhältnis hinterlegten und bestätigten Konfiguration.</p>")

    vz = "<h2>Ihre Vorauszahlungen</h2><table><tr><th>Art</th>"
    vz += "<th class='r'>Netto</th><th class='r'>USt</th><th class='r'>Brutto</th></tr>" if netto else "<th class='r'>Betrag</th></tr>"
    for v in m.vorauszahlungen:
        art = "Betriebskosten" if v.art == "betriebskosten" else "Heizkosten"
        vz += f"<tr><td>{art}</td><td class='r'>{eur(v.betrag)}</td><td class='r'>{eur(v.ust)}</td><td class='r'>{eur(v.brutto)}</td></tr>" if netto else f"<tr><td>{art}</td><td class='r'>{eur(v.betrag)}</td></tr>"
    vz += (f"<tr class='summe'><td>Summe</td><td class='r'>{eur(m.vz)}</td><td class='r'>{eur(m.vz_ust)}</td><td class='r'>{eur(m.vz_brutto)}</td></tr>"
           if netto else f"<tr class='summe'><td>Summe</td><td class='r'>{eur(m.vz)}</td></tr>") + "</table>"
    teile.append(vz)

    saldo = m.saldo_brutto
    art = "Nachzahlung" if saldo > 0 else ("Guthaben" if saldo < 0 else "Ausgeglichen")
    if netto:
        teile.append(f"""<div class="ergebnis"><table>
<tr><td>Kosten</td><td class='r'>{eur(m.kosten)}</td><td class='r'>{eur(m.kosten_ust)}</td><td class='r'>{eur(m.kosten_brutto)}</td></tr>
<tr><td>abzüglich Vorauszahlungen</td><td class='r'>{eur(m.vz)}</td><td class='r'>{eur(m.vz_ust)}</td><td class='r'>{eur(m.vz_brutto)}</td></tr>
<tr class='summe'><td>{art}</td><td class='r'>{eur(abs(m.saldo))}</td><td class='r'>{eur(abs(m.saldo_ust))}</td><td class='r'><b>{eur(abs(saldo))}</b></td></tr>
</table><p class='klein'>Spalten: netto, Umsatzsteuer, brutto. Vorzeichen in der Ergebniszeile entfallen; maßgeblich ist die Bezeichnung.</p></div>""")
    else:
        teile.append(f"""<div class="ergebnis"><table>
<tr><td>Ihr Kostenanteil</td><td class='r'>{eur(m.kosten)}</td></tr>
<tr><td>abzüglich Ihrer Vorauszahlungen</td><td class='r'>{eur(m.vz)}</td></tr>
<tr class='summe'><td>{art}</td><td class='r'><b>{eur(abs(saldo))}</b></td></tr></table></div>""")
    teile.append(f"<p class='klein'>Die Anteile je Kostenart sind auf Cent gerundet. Rundungsdifferenzen werden so ausgeglichen, dass die Summe aller Anteile genau den Gesamtkosten entspricht. Abweichung Ihrer Summe von der ungerundeten Rechnung: {zahl(m.rundung)} €. Bei Ein- oder Auszug im Abrechnungszeitraum wird nach Tagen anteilig berechnet.</p>")
    teile.append(_belegliste(m))
    teile.append(f"<p class='klein'>Abrechnung {_e(m.id)} · Rechenregeln {_e(erg.regeln_version)}{f' · Version {version}' if version else ''}. Die zugrunde liegenden Belege können nach Absprache eingesehen werden.</p>")
    teile.append("</body></html>")
    return "".join(teile)


def kontrollbericht_html(erg: Ergebnis, *, version: int | None = None, eingabe_hash: str = "") -> str:
    d = erg.daten
    w = "".join(f"<tr><td class='{x.stufe}'>{_e(x.stufe)}</td><td>{_e(x.code)}</td><td>{_e(x.text)}</td></tr>" for x in erg.warnungen) \
        or "<tr><td colspan='3'>Keine Warnungen.</td></tr>"
    k = "".join(
        f"<tr><td>{_e(x.bezeichnung)}</td><td>{_e(x.pool)}</td><td>{_e(x.schluessel)}</td><td class='r'>{eur(x.gesamt_brutto)}</td>"
        f"<td class='r'>{eur(x.verteilt_brutto)}</td><td class='r'>{eur(x.verteilt_brutto - x.gesamt_brutto)}</td>"
        f"<td class='r'>{eur(x.gesamt_netto)}</td><td class='r'>{eur(x.leerstand_brutto)}</td><td class='r'>{eur(x.vorjahr)}</td>"
        f"<td>{_e(', '.join(x.belege))}</td></tr>" for x in erg.kontrolle)
    mv = "".join(
        f"<tr><td>{_e(m.id)}</td><td>{_e(m.einheit)}</td><td>{_e(DARSTELLUNG[m.darstellung])}</td><td>{m.nutzungszeitraum.tage}</td>"
        f"<td class='r'>{eur(m.kosten)}</td><td class='r'>{eur(m.kosten_ust)}</td><td class='r'>{eur(m.vz_brutto)}</td>"
        f"<td class='r'>{eur(m.saldo_brutto)}</td><td class='r'>{zahl(m.rundung)}</td><td class='r'>{eur(m.vorsteueranteil_info) if m.darstellung == 'gewerbe_netto' else ''}</td></tr>"
        for m in erg.mietverhaeltnisse)
    leer = "".join(f"<tr><td>{_e(e)}</td><td class='r'>{eur(b)}</td></tr>" for e, b in erg.leerstand.items()) \
        or "<tr><td colspan='2'>Kein Leerstand.</td></tr>"
    gesamt = sum((x.gesamt_brutto for x in erg.kontrolle), NULL)
    anteile = sum((z.anteil_brutto for m in erg.mietverhaeltnisse for z in m.zeilen), NULL) + sum(erg.leerstand.values(), NULL)
    return f"""<!DOCTYPE html><html lang="de"><head><meta charset="utf-8"><title>Kontrollbericht</title><style>{CSS}</style></head><body>
<div class="fuss">Interner Kontrollbericht · nicht an Mieter</div>
<div class="stempel">INTERN</div>
<h1>Kontrollbericht Betriebskosten {_datum(d.zeitraum.von)} bis {_datum(d.zeitraum.bis)}</h1>
<p class="meta">{_e(d.objekt.name)} · Rechenregeln {_e(erg.regeln_version)}{f' · Version {version}' if version else ''}{f' · Eingabe {eingabe_hash[:16]}…' if eingabe_hash else ''}</p>
<p><b>{len(erg.rot)} rote</b> und <b>{len(erg.gelb)} gelbe</b> Warnungen. {'Freigabe gesperrt.' if erg.rot else 'Keine Sperre durch rote Warnungen.'}</p>
<h2>Abstimmung</h2><table><tr><td>Gesamtkosten aller Kostenarten</td><td class='r'>{eur(gesamt)}</td></tr>
<tr><td>Summe aller Anteile einschließlich Leerstand</td><td class='r'>{eur(anteile)}</td></tr>
<tr><td>Differenz</td><td class='r'>{eur(anteile - gesamt)}</td></tr></table>
<h2>Warnungen</h2><table><tr><th>Stufe</th><th>Code</th><th>Text</th></tr>{w}</table>
<h2>Kostenarten</h2><table><tr><th>Kostenart</th><th>Pool</th><th>Schlüssel</th><th class='r'>Gesamt brutto</th><th class='r'>Verteilt</th><th class='r'>Differenz</th><th class='r'>Gesamt netto</th><th class='r'>Leerstand</th><th class='r'>Vorjahr</th><th>Belege</th></tr>{k}</table>
<h2>Mietverhältnisse</h2><table><tr><th>MV</th><th>Einheit</th><th>Darstellung</th><th>Tage</th><th class='r'>Kosten</th><th class='r'>USt</th><th class='r'>VZ brutto</th><th class='r'>Saldo brutto</th><th class='r'>Rundung</th><th class='r'>Vorsteueranteil (Info)</th></tr>{mv}</table>
<p class="klein">Positiver Saldo = Nachzahlung, negativer Saldo = Guthaben. Der Vorsteueranteil ist eine rechnerische Information für den Eigentümer und keine steuerliche Feststellung.</p>
<h2>Leerstand (trägt der Eigentümer)</h2><table>{leer}</table>
</body></html>"""


def als_pdf(html: str) -> bytes:
    from weasyprint import HTML
    from weasyprint.urls import URLFetcher

    class KeinNachladen(URLFetcher):
        def fetch(self, url, headers=None):
            raise ValueError(f"Externe Ressourcen sind nicht erlaubt: {url[:80]}")

    return HTML(string=html, url_fetcher=KeinNachladen()).write_pdf()


def schreibe_lauf(erg: Ergebnis, ziel: str | Path, *, version: int | None = None, status: str = "entwurf",
                  eingabe_hash: str = "") -> list[Path]:
    """Schreibt Ergebnisdaten (JSON), Kontrollbericht und je nicht gesperrtem Mietverhältnis einen PDF-Entwurf."""
    from .version import als_dict

    ziel = Path(ziel)
    ziel.mkdir(parents=True, exist_ok=True)
    dateien = [ziel / "ergebnis.json"]
    dateien[0].write_text(json.dumps(als_dict(erg), ensure_ascii=False, indent=1), encoding="utf-8")
    p = ziel / "kontrollbericht.pdf"
    p.write_bytes(als_pdf(kontrollbericht_html(erg, version=version, eingabe_hash=eingabe_hash)))
    dateien.append(p)
    for m in erg.mietverhaeltnisse:
        if m.gesperrt:
            continue
        p = ziel / f"abrechnung_{m.id}.pdf"
        p.write_bytes(als_pdf(abrechnung_html(erg, m, version=version, status=status)))
        dateien.append(p)
    return dateien
