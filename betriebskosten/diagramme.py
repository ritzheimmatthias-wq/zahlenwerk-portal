"""Inline-SVG-Diagramme aus den gespeicherten Diagrammdaten eines Report-Snapshots.

Keine externe Bibliothek, keine Skripte: dasselbe SVG steht in Portal und PDF.
Werte werden nicht neu berechnet, nur aus dem Snapshot gezeichnet. Jede Marke
trägt einen <title> mit dem exakten Wert (Tooltip im Browser), Balken sind
direkt beschriftet, ab zwei Reihen gibt es immer eine Legende.
Farben: geprüfte kategoriale Reihenfolge (farbfehlsichtigkeitsgeprüft).
"""
from __future__ import annotations

from decimal import Decimal
from html import escape

REIHE = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4")
EINFARBIG = "#2e6658"
ANSTIEG, SENKUNG = "#eb6834", "#2a78d6"
TEXT, TEXT2, LINIE = "#17312c", "#5f6d68", "#d9ded8"
SCHRIFT = 'font-family="DejaVu Sans, Arial, sans-serif"'


def _e(x) -> str:
    return escape(str(x), quote=True)


def _z(b: Decimal, stellen: int = 0) -> str:
    s = f"{b:,.{stellen}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return s


def _eur(b: Decimal, einheit: str = "€") -> str:
    return f"{_z(b, 2)} {einheit}".strip()


def _fmt(d: dict):
    einheit = d.get("einheit", "€")
    return lambda b: _eur(b, einheit)


def _kurz(t: str, n: int = 28) -> str:
    return t if len(t) <= n else t[: n - 1] + "…"


def _skala(maximum: Decimal) -> list[Decimal]:
    if maximum <= 0:
        return [Decimal(0)]
    roh = maximum / 4
    stufe = Decimal(10) ** (len(str(int(roh))) - 1) if roh >= 1 else Decimal("0.1")
    for f in (1, 2, 2.5, 5, 10):
        s = stufe * Decimal(str(f))
        if s >= roh:
            break
    return [s * i for i in range(0, int(maximum / s) + 2)]


def _legende(reihen: list, y: int, farben) -> str:
    teile, x = [], 0
    for i, r in enumerate(reihen):
        teile.append(f'<rect x="{x}" y="{y - 9}" width="10" height="10" rx="2" fill="{farben[i]}"/>'
                     f'<text x="{x + 14}" y="{y}" font-size="10" fill="{TEXT}">{_e(r["name"])}</text>')
        x += 24 + 7 * len(r["name"])
    return "".join(teile)


def _rahmen(inhalt: str, b: int, h: int, titel: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {b} {h}" width="100%" role="img" '
            f'aria-label="{_e(titel)}" {SCHRIFT} style="max-width:{b}px;display:block">{inhalt}</svg>')


def balken(d: dict, abweichung: bool = False) -> str:
    fmt = _fmt(d)
    kat = d["kategorien"]
    werte = [Decimal(w) for w in d["reihen"][0]["werte"]]
    b, links, rechts, zeile = 640, 190, 90, 22
    h = 10 + zeile * len(kat)
    breite = b - links - rechts
    m = max([abs(w) for w in werte] + [Decimal(1)])
    teile = []
    null = links + (breite // 2 if abweichung else 0)
    skala = Decimal(breite // 2 if abweichung else breite) / m
    teile.append(f'<line x1="{null}" y1="0" x2="{null}" y2="{h}" stroke="{LINIE}"/>')
    for i, (k, w) in enumerate(zip(kat, werte)):
        y = 6 + i * zeile
        lang = float(abs(w) * skala)
        x = null - lang if (abweichung and w < 0) else null
        farbe = (ANSTIEG if w > 0 else SENKUNG) if abweichung else EINFARBIG
        wert = (("+" if w > 0 else "") + fmt(w)) if abweichung else fmt(w)
        teile.append(f'<text x="{links - 8}" y="{y + 11}" font-size="10" fill="{TEXT}" text-anchor="end">{_e(_kurz(k))}</text>')
        teile.append(f'<rect x="{x:.1f}" y="{y}" width="{max(lang, 1):.1f}" height="14" rx="3" fill="{farbe}">'
                     f'<title>{_e(k)}: {_e(wert)}</title></rect>')
        tx = (x - 4) if (abweichung and w < 0) else (null + lang + 4)
        teile.append(f'<text x="{tx:.1f}" y="{y + 11}" font-size="9.5" fill="{TEXT2}" '
                     f'text-anchor="{"end" if (abweichung and w < 0) else "start"}">{_e(wert)}</text>')
    return _rahmen("".join(teile), b, h, d["titel"])


def gruppiert(d: dict) -> str:
    fmt = _fmt(d)
    kat, reihen = d["kategorien"], d["reihen"]
    b, links, unten, oben = 640, 64, 46, 22
    h = 230
    werte = [[Decimal(w) for w in r["werte"]] for r in reihen]
    stufen = _skala(max([max(w) for w in werte] + [Decimal(1)]))
    m = stufen[-1] or Decimal(1)
    breite, hoehe = b - links - 10, h - unten - oben
    gruppe = breite / max(len(kat), 1)
    balken_b = min(28.0, (gruppe - 8) / len(reihen) - 2)
    teile = [_legende(reihen, 12, REIHE)]
    for s in stufen:
        y = oben + hoehe - float(s / m) * hoehe
        teile.append(f'<line x1="{links}" y1="{y:.1f}" x2="{b - 10}" y2="{y:.1f}" stroke="{LINIE}" stroke-width="0.6"/>'
                     f'<text x="{links - 6}" y="{y + 3:.1f}" font-size="9" fill="{TEXT2}" text-anchor="end">{_z(s)}</text>')
    for i, k in enumerate(kat):
        gx = links + i * gruppe + (gruppe - len(reihen) * (balken_b + 2)) / 2
        for j, r in enumerate(reihen):
            w = werte[j][i]
            hh = float(w / m) * hoehe if w > 0 else 0
            x, y = gx + j * (balken_b + 2), oben + hoehe - hh
            teile.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{balken_b:.1f}" height="{max(hh, 0.5):.1f}" rx="3" fill="{REIHE[j]}">'
                         f'<title>{_e(k)}, {_e(r["name"])}: {_e(fmt(w))}</title></rect>')
        teile.append(f'<text x="{links + i * gruppe + gruppe / 2:.1f}" y="{oben + hoehe + 13}" font-size="8.5" fill="{TEXT}" '
                     f'text-anchor="middle">{_e(_kurz(k, int(gruppe / 5.2) or 4))}</text>')
    return _rahmen("".join(teile), b, h, d["titel"])


def linie(d: dict) -> str:
    fmt = _fmt(d)
    kat, reihen = d["kategorien"], d["reihen"]
    b, links, rechts, oben, unten = 640, 64, 130, 24, 28
    h = 230
    werte = [[Decimal(w) for w in r["werte"]] for r in reihen]
    stufen = _skala(max([max(w) for w in werte] + [Decimal(1)]))
    m = stufen[-1] or Decimal(1)
    breite, hoehe = b - links - rechts, h - oben - unten
    schritt = breite / max(len(kat) - 1, 1)
    teile = [_legende(reihen, 12, REIHE)] if len(reihen) > 1 else []
    for s in stufen:
        y = oben + hoehe - float(s / m) * hoehe
        teile.append(f'<line x1="{links}" y1="{y:.1f}" x2="{links + breite}" y2="{y:.1f}" stroke="{LINIE}" stroke-width="0.6"/>'
                     f'<text x="{links - 6}" y="{y + 3:.1f}" font-size="9" fill="{TEXT2}" text-anchor="end">{_z(s)}</text>')
    for i, k in enumerate(kat):
        teile.append(f'<text x="{links + i * schritt:.1f}" y="{h - 8}" font-size="9" fill="{TEXT}" text-anchor="middle">{_e(k)}</text>')
    for j, r in enumerate(reihen):
        punkte = [(links + i * schritt, oben + hoehe - float(w / m) * hoehe) for i, w in enumerate(werte[j])]
        teile.append(f'<polyline fill="none" stroke="{REIHE[j]}" stroke-width="2" points="'
                     + " ".join(f"{x:.1f},{y:.1f}" for x, y in punkte) + '"/>')
        for (x, y), k, w in zip(punkte, kat, werte[j]):
            teile.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{REIHE[j]}" stroke="#ffffff" stroke-width="2">'
                         f'<title>{_e(r["name"])} {_e(k)}: {_e(fmt(w))}</title></circle>')
        x, y = punkte[-1]
        teile.append(f'<text x="{x + 8:.1f}" y="{y + 3:.1f}" font-size="9" fill="{TEXT}">{_e(_kurz(r["name"], 20))}</text>')
    return _rahmen("".join(teile), b, h, d["titel"])


def zeichne(d: dict) -> str:
    if d["typ"] == "balken":
        return balken(d)
    if d["typ"] == "abweichung":
        return balken(d, abweichung=True)
    if d["typ"] == "gruppiert":
        return gruppiert(d)
    if d["typ"] == "linie":
        return linie(d)
    raise ValueError(f"Unbekannter Diagrammtyp {d['typ']}")


def tabelle(d: dict) -> str:
    """Tabellenansicht derselben Diagrammdaten (Barrierefreiheit, Abgleich)."""
    fmt = _fmt(d)
    kopf = "".join(f"<th class='r'>{_e(r['name'])}</th>" for r in d["reihen"])
    zeilen = "".join(
        f"<tr><td>{_e(k)}</td>" + "".join(f"<td class='r'>{_e(fmt(Decimal(r['werte'][i])))}</td>" for r in d["reihen"]) + "</tr>"
        for i, k in enumerate(d["kategorien"]))
    return f"<table class='diagrammtabelle'><tr><th></th>{kopf}</tr>{zeilen}</table>"
