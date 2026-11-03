"""HTML (Portalansicht) und PDF der beiden Jahresreports aus einem gespeicherten Snapshot.

Portal und PDF verwenden dieselben Funktionen und dieselben gespeicherten
Werte; hier wird nichts gerechnet, nur formatiert. Solange ein Report nicht
freigegeben ist, trägt er den Stempel ENTWURF.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from html import escape

from .ausgabe import CSS, als_pdf, eur, zahl
from .diagramme import tabelle, zeichne
from .kostenverlauf import KATEGORIE_TEXT
from .vorauszahlung import EMPFEHLUNG_TEXT

REPORT_CSS = CSS + """
.kacheln { display: flex; flex-wrap: wrap; gap: 6pt; margin: 6pt 0; }
.kachel { flex: 1 1 30%; border: 0.5pt solid #d9ded8; border-radius: 4pt; padding: 5pt 7pt; background: #fbfaf6; min-width: 120px; }
.kachel span { display: block; font-size: 7.5pt; color: #5f6d68; }
.kachel b { font-size: 11pt; font-variant-numeric: tabular-nums; }
.empfehlung { border-left: 3pt solid #2e6658; padding: 4pt 8pt; margin: 6pt 0; background: #f4f7f5; }
.empfehlung.keine, .empfehlung.pauschale { border-left-color: #9a5b12; background: #fbf5ec; }
.seite { break-before: page; } .seite:first-of-type { break-before: auto; }
.diagramm { break-inside: avoid; margin: 6pt 0 10pt; }
.diagramm-svg { display: block; max-width: 640px; }
.zurueck { font-size: 9pt; }
ul { margin: 2pt 0 6pt 14pt; padding: 0; }
@media screen { body { max-width: 760px; margin: 0 auto; padding: 16px; } .fuss { display: none; } }
@media screen and (max-width: 520px) { table { font-size: 8pt; } .kachel { flex-basis: 45%; } }
"""


def _e(x) -> str:
    return escape(str(x), quote=True)


def _d(x) -> Decimal | None:
    return None if x is None else Decimal(str(x))


def _eu(x) -> str:
    return eur(_d(x))


def _pz(x) -> str:
    return "–" if x is None else (("+" if Decimal(x) > 0 else "") + zahl(Decimal(x), 1) + " %")


def _datum(s: str) -> str:
    return date.fromisoformat(s).strftime("%d.%m.%Y")


def _stempel(status: str, nummer: int | None) -> str:
    if status == "freigegeben":
        return ""
    return f'<div class="stempel">ENTWURF{f" · Report {nummer}" if nummer else ""} · nicht weitergeben</div>'


def _dokument(titel: str, fuss: str, inhalt: str, css_href: str = "", zurueck: str = "") -> str:
    """Ohne css_href mit eingebettetem Stil (PDF). Mit css_href für die Portalansicht (strenge CSP ohne Inline-Stile)."""
    stil = f'<link rel="stylesheet" href="{_e(css_href)}">' if css_href else f"<style>{REPORT_CSS}</style>"
    kopf = f'<p class="zurueck"><a href="{_e(zurueck)}">‹ Zurück</a></p>' if zurueck else ""
    return (f'<!DOCTYPE html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta name="robots" content="noindex, nofollow"><title>{_e(titel)}</title>{stil}</head>'
            f'<body><div class="fuss">{_e(fuss)}</div>{kopf}{inhalt}</body></html>')


def _regeln(inhalt: dict) -> str:
    return "<h2>Regeln dieses Reports</h2><ul class='klein'>" + "".join(f"<li>{_e(t)}</li>" for t in inhalt["regeln_text"]) + "</ul>"


def _grundlage_text(g: dict) -> str:
    return (f"Abrechnungsversion {g['version']} ({g['status']}), Zeitraum {_datum(g['abrechnungszeitraum']['von'])} bis "
            f"{_datum(g['abrechnungszeitraum']['bis'])}, Rechenregeln {g['regeln_version']}, Ergebnis-Prüfsumme {g['ergebnis_hash'][:12]}")


# --- Report 1 -----------------------------------------------------------------------

def vorauszahlung_abschnitt(inhalt: dict, mv: dict) -> str:
    g = inhalt["grundlage"]
    netto = mv["darstellung"] == "gewerbe_netto"
    zz = mv["nutzungszeitraum"]
    t = [f"<section class='seite' id='mv-{_e(mv['id'])}'>",
         f"<h1>Vorauszahlungen und Abrechnungsspitze {_e(g['abrechnungszeitraum']['von'][:4])}</h1>",
         "<table class='angaben'>",
         f"<tr><td>Objekt</td><td>{_e(g['objekt'])}</td></tr>",
         f"<tr><td>Einheit</td><td>{_e(mv['einheit_bezeichnung'])}</td></tr>",
         f"<tr><td>Mieter</td><td>{_e(mv['mieter'])}</td></tr>",
         f"<tr><td>Abrechnungszeitraum</td><td>{_datum(g['abrechnungszeitraum']['von'])} bis {_datum(g['abrechnungszeitraum']['bis'])}</td></tr>",
         f"<tr><td>Nutzungszeitraum</td><td>{_datum(zz['von'])} bis {_datum(zz['bis'])} ({mv['nutzungstage']} von {mv['jahrestage']} Tagen)</td></tr>",
         f"<tr><td>Vertragliche Einordnung</td><td>{_e({'vorauszahlung': 'Betriebskostenvorauszahlung', 'pauschale': 'Betriebskostenpauschale', '': 'nicht erfasst'}[mv['umlageart']])}</td></tr>",
         f"<tr><td>Beträge</td><td>{'netto, ohne Umsatzsteuer' if netto else 'brutto'}</td></tr>",
         "</table>"]
    for a in mv["arten"]:
        t.append(f"<h2>{_e(a['bezeichnung'])}</h2><div class='kacheln'>"
                 f"<div class='kachel'><span>Geleistete Vorauszahlungen</span><b>{_eu(a['vorauszahlungen'])}</b></div>"
                 f"<div class='kachel'><span>Tatsächliche Kosten</span><b>{_eu(a['kosten'])}</b></div>"
                 f"<div class='kachel'><span>{_e(a['ergebnis'])}</span><b>{_eu(abs(_d(a['abrechnungsspitze'])))}</b></div>"
                 f"<div class='kachel'><span>Rechnerische Kosten je Monat</span><b>{_eu(a['monatskosten'])}</b></div>"
                 f"<div class='kachel'><span>Aktuelle Vorauszahlung je Monat</span><b>{_eu(a['aktuell_monatlich'])}</b></div>"
                 f"<div class='kachel'><span>Vorschlag je Monat</span><b>{_eu(a['vorschlag_monatlich'])}</b></div></div>")
        if a["annahmen"]:
            t.append("<table><tr><th>Bekannte Kostenänderung</th><th>Berechnung</th><th class='r'>je Jahr</th><th class='r'>je Monat</th></tr>")
            for x in a["annahmen"]:
                t.append(f"<tr><td>{_e(x['begruendung'] or x['id'])}{(' · Beleg ' + _e(x['beleg'])) if x['beleg'] else ''}</td>"
                         f"<td>{_e(x['berechnung'])}</td><td class='r'>{_eu(x['jahr'])}</td><td class='r'>{_eu(x['monat'])}</td></tr>")
            t.append("</table><p class='klein'>Annahme getrennt ausgewiesen; nur fachlich freigegebene Änderungen sind berücksichtigt.</p>")
        t.append(f"<div class='empfehlung {_e(a['empfehlung'])}'><b>{_e(EMPFEHLUNG_TEXT[a['empfehlung']])}</b><br>{_e(a['begruendung'])}</div>")
    s = mv["summe"]
    t.append(f"<p>Summe: Vorauszahlungen {_eu(s['vorauszahlungen'])}, Kosten {_eu(s['kosten'])}, Abrechnungsspitze {_eu(s['abrechnungsspitze'])} "
             f"(entspricht dem Saldo der Betriebskostenabrechnung, positiv = Nachzahlung).</p>")
    if mv["hinweise"]:
        t.append("<ul class='klein'>" + "".join(f"<li>{_e(h)}</li>" for h in mv["hinweise"]) + "</ul>")
    t.append(f"<p class='klein'>{_e(inhalt['hinweis'])} Grundlage: {_e(_grundlage_text(g))}.</p></section>")
    return "".join(t)


def vorauszahlung_html(snapshot: dict, status: str, mv_id: str | None = None, **portal) -> str:
    inhalt = snapshot["inhalt"]
    mvs = [m for m in inhalt["mietverhaeltnisse"] if mv_id is None or m["id"] == mv_id]
    if not mvs:
        raise ValueError(f"Mietverhältnis {mv_id} ist nicht im Report enthalten")
    teile = [_stempel(status, snapshot["nummer"])] + [vorauszahlung_abschnitt(inhalt, m) for m in mvs] + [_regeln(inhalt)]
    return _dokument("Vorauszahlungsanalyse", f"Vorauszahlungsanalyse · {inhalt['grundlage']['objekt']} · Report {snapshot['nummer']}",
                     "".join(teile), **portal)


# --- Report 2 -----------------------------------------------------------------------

def kostenverlauf_abschnitt(inhalt: dict) -> str:
    jahre, v = inhalt["jahre"], inhalt["vergleich"]
    a = jahre[-1]
    t = [f"<h1>Kostenverlauf {_e(a['jahr'])}</h1>",
         f"<p class='meta'>{_e(inhalt['objekt']['name'])}, {_e(inhalt['objekt']['anschrift'])} · "
         f"{'Vergleich mit ' + _e(v['vorjahr']) if v else 'Ausgangsbasis, kein Vorjahresvergleich'}</p>",
         "<div class='kacheln'>",
         f"<div class='kachel'><span>Gesamtkosten {_e(a['jahr'])}</span><b>{_eu(a['gesamt'])}</b></div>"]
    if v:
        t.append(f"<div class='kachel'><span>Vorjahr {_e(v['vorjahr'])}</span><b>{_eu(v['gesamt']['vorjahr'])}</b></div>"
                 f"<div class='kachel'><span>Veränderung</span><b>{_eu(v['gesamt']['delta'])} ({_pz(v['gesamt']['prozent'])})</b></div>")
    t.append(f"<div class='kachel'><span>je Einheit (Jahresmittel {zahl(_d(a['einheiten_mittel']))})</span><b>{_eu(a['je_einheit'])}</b></div>"
             f"<div class='kachel'><span>je m² ({zahl(_d(a['flaeche_mittel']))} m²)</span><b>{_eu(a['je_m2']) if a['je_m2'] else 'nicht belastbar'}</b></div>"
             f"<div class='kachel'><span>Abrechnungsspitze gesamt</span><b>{_eu(a['zahlungen']['abrechnungsspitze'])}</b></div></div>")
    t.append(f"<h2>Zusammenfassung{' (Entwurf, fachliche Prüfung ausstehend)' if inhalt['zusammenfassung_status'] != 'freigegeben' else ''}</h2><ul>"
             + "".join(f"<li>{_e(x)}</li>" for x in inhalt["zusammenfassung"]) + "</ul>")
    if v and v["einschraenkungen"]:
        t.append("<p class='gelb'><b>Eingeschränkt vergleichbar:</b> " + _e(" ".join(v["einschraenkungen"])) + "</p>")
    for d in inhalt["diagramme"]:
        t.append(f"<div class='diagramm'><h2>{_e(d['titel'])}</h2>{zeichne(d)}</div>")

    kopf = "<tr><th>Kostenart</th><th>Bereich</th>" + (f"<th class='r'>{_e(v['vorjahr'])}</th>" if v else "") + \
           f"<th class='r'>{_e(a['jahr'])}</th>" + ("<th class='r'>Veränderung</th><th class='r'>%</th>" if v else "") + "</tr>"
    zeilen = []
    if v:
        for x in v["kostenarten"]:
            ka = a["kostenarten"].get(x["id"]) or jahre[-2]["kostenarten"][x["id"]]
            zeilen.append(f"<tr><td>{_e(x['bezeichnung'])}</td><td>{_e(KATEGORIE_TEXT[ka['kategorie']])}</td><td class='r'>{_eu(x['vorjahr'])}</td>"
                          f"<td class='r'>{_eu(x['aktuell'])}</td><td class='r'>{_eu(x['delta'])}</td><td class='r'>{_pz(x['prozent'])}</td></tr>")
        zeilen.append(f"<tr class='summe'><td colspan='2'>Gesamt</td><td class='r'>{_eu(v['gesamt']['vorjahr'])}</td><td class='r'>{_eu(v['gesamt']['aktuell'])}</td>"
                      f"<td class='r'>{_eu(v['gesamt']['delta'])}</td><td class='r'>{_pz(v['gesamt']['prozent'])}</td></tr>")
    else:
        for x in sorted(a["kostenarten"].values(), key=lambda x: (-Decimal(x["betrag"]), x["id"])):
            zeilen.append(f"<tr><td>{_e(x['bezeichnung'])}</td><td>{_e(KATEGORIE_TEXT[x['kategorie']])}</td><td class='r'>{_eu(x['betrag'])}</td></tr>")
        zeilen.append(f"<tr class='summe'><td colspan='2'>Gesamt</td><td class='r'>{_eu(a['gesamt'])}</td></tr>")
    t.append(f"<h2>Kosten je Kostenart</h2><table class='eng'>{kopf}{''.join(zeilen)}</table>")

    t.append("<h2>Heizung, Warmwasser, Kaltwasser und sonstige Betriebskosten</h2><table><tr><th>Bereich</th>"
             + "".join(f"<th class='r'>{_e(j['jahr'])}</th>" for j in jahre) + "</tr>"
             + "".join(f"<tr><td>{_e(n)}</td>" + "".join(f"<td class='r'>{_eu(j['kategorien'][k])}</td>" for j in jahre) + "</tr>"
                       for k, n in KATEGORIE_TEXT.items()) + "</table>")
    lief = v["lieferanten"] if v else [{"id": k, "aktuell": b, "vorjahr": None, "delta": None, "prozent": None} for k, b in a["lieferanten"].items()]
    t.append("<h2>Kosten nach Versorgern und Dienstleistern</h2><table class='eng'><tr><th>Versorger / Dienstleister</th>"
             + (f"<th class='r'>{_e(v['vorjahr'])}</th>" if v else "") + f"<th class='r'>{_e(a['jahr'])}</th>"
             + ("<th class='r'>Veränderung</th><th class='r'>%</th>" if v else "") + "</tr>"
             + "".join(f"<tr><td>{_e(x['id'])}</td>" + (f"<td class='r'>{_eu(x['vorjahr'])}</td>" if v else "")
                       + f"<td class='r'>{_eu(x['aktuell'])}</td>"
                       + (f"<td class='r'>{_eu(x['delta'])}</td><td class='r'>{_pz(x['prozent'])}</td>" if v else "") + "</tr>"
                       for x in lief) + "</table>")
    b = a["bereiche"]
    z = a["zahlungen"]
    t.append(f"<h2>Wohn- und Gewerbebereich, Zahlungen</h2><table>"
             f"<tr><td>Anteile Wohnmietverhältnisse</td><td class='r'>{_eu(b['wohnen'])}</td></tr>"
             f"<tr><td>Anteile Gewerbemietverhältnisse</td><td class='r'>{_eu(b['gewerbe'])}</td></tr>"
             f"<tr><td>Leerstand (Eigentümer)</td><td class='r'>{_eu(b['leerstand'])}</td></tr>"
             f"<tr class='summe'><td>Gesamtkosten</td><td class='r'>{_eu(a['gesamt'])}</td></tr>"
             f"<tr><td>Vorauszahlungen der Mietverhältnisse (brutto)</td><td class='r'>{_eu(z['vorauszahlungen'])}</td></tr>"
             f"<tr><td>Tatsächliche Kosten der Mietverhältnisse (brutto)</td><td class='r'>{_eu(z['kosten'])}</td></tr>"
             f"<tr><td>Nachzahlungen</td><td class='r'>{_eu(z['nachzahlungen'])}</td></tr>"
             f"<tr><td>Guthaben</td><td class='r'>{_eu(z['guthaben'])}</td></tr>"
             f"<tr class='summe'><td>Abrechnungsspitze gesamt</td><td class='r'>{_eu(z['abrechnungsspitze'])}</td></tr></table>"
             "<p class='klein'>Gewerbebeträge mit Umsatzsteuer sind hier brutto einbezogen. Pauschalmietverhältnisse sind in den Zahlungen nicht enthalten.</p>")
    if inhalt["datenqualitaet"]:
        t.append("<h2>Datenqualität und Vergleichbarkeit</h2><ul class='klein'>" + "".join(f"<li>{_e(x)}</li>" for x in inhalt["datenqualitaet"]) + "</ul>")
    t.append("<h2>Diagrammdaten</h2>" + "".join(f"<p class='klein'>{_e(d['titel'])}</p>{tabelle(d)}" for d in inhalt["diagramme"]))
    t.append(_regeln(inhalt))
    t.append("<p class='klein'>" + _e(inhalt["hinweis"]) + " Grundlage: " + _e("; ".join(_grundlage_text(g) for g in inhalt["grundlage"])) + ".</p>")
    return "".join(t)


def kostenverlauf_html(snapshot: dict, status: str, **portal) -> str:
    inhalt = snapshot["inhalt"]
    return _dokument("Kostenverlaufsbericht", f"Kostenverlauf · {inhalt['objekt']['name']} · Report {snapshot['nummer']}",
                     _stempel(status, snapshot["nummer"]) + kostenverlauf_abschnitt(inhalt), **portal)


def report_pdf(snapshot: dict, status: str, mv_id: str | None = None) -> bytes:
    """PDF eines Snapshots; byte-identisch reproduzierbar (Erstellungszeit des Snapshots)."""
    html = (vorauszahlung_html(snapshot, status, mv_id) if snapshot["report"] == "vorauszahlung"
            else kostenverlauf_html(snapshot, status))
    return als_pdf(html, erstellt=datetime.fromisoformat(snapshot["erstellt"]))
