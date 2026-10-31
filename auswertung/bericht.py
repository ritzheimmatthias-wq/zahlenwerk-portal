"""Ausgabe der Einschätzung als Markdown oder JSON (für Portal-Dashboard und Tests)."""
from __future__ import annotations

import json
from decimal import Decimal

from .einschaetzung import MONATE, Ergebnis
from .modell import eur, iban_kurz

ZEICHEN = {"ok": "✓", "teil": "T", "mehr": "+", "doppelt": "2×", "fehlt": "–"}



def als_markdown(e: Ergebnis) -> str:
    z: list[str] = []
    z.append(f"# Erste Einschätzung · {e.objekt} · {e.jahr}")
    z.append("")
    z.append(f"Vorläufig, Status „zu prüfen“. Grundlage: {e.anzahl_buchungen} Buchungen.")
    z.append("")
    soll = sum((h.soll_jahr for h in e.hausgeld), Decimal("0"))
    ist = sum((h.ist_jahr for h in e.hausgeld), Decimal("0"))
    ausgaben = sum((g.summe for g in e.gruppen), Decimal("0"))
    z.append(f"- Hausgeld Soll: {eur(soll)}, eingegangen: {eur(ist)}, Abweichung: {eur(ist - soll)}")
    z.append(f"- Ausgaben gesamt: {eur(-ausgaben)}")
    z.append(f"- Hinweise: {len(e.hinweise)}, davon kritisch: {sum(1 for h in e.hinweise if h[0] == 'kritisch')}")
    z.append("")
    z.append("## Hausgeld je Einheit")
    z.append("")
    z.append("Zeichen: ✓ gezahlt, T Teilzahlung, + zu viel, 2× mehrfach, – fehlt")
    z.append("")
    z.append("| Einheit | Eigentümer | " + " | ".join(MONATE) + " | Soll | Ist | Abweichung |")
    z.append("| --- | --- | " + " | ".join(["---"] * 12) + " | ---: | ---: | ---: |")
    for h in e.hausgeld:
        z.append(
            f"| {h.einheit} | {h.eigentuemer} | " + " | ".join(ZEICHEN[s] for s in h.status)
            + f" | {eur(h.soll_jahr)} | {eur(h.ist_jahr)} | {eur(h.abweichung)} |"
        )
    z.append("")
    z.append("## Ausgaben nach Gruppen")
    z.append("")
    z.append("| Gruppe | Summe | Buchungen | Größte Empfänger |")
    z.append("| --- | ---: | ---: | --- |")
    for g in e.gruppen:
        top = ", ".join(f"{n} ({eur(-b)})" for n, b in g.groesste())
        z.append(f"| {g.name} | {eur(-g.summe)} | {g.anzahl} | {top} |")
    z.append("")
    z.append("## Konten")
    z.append("")
    z.append("| Konto | Art | Anfang | Ende | Saldenkette |")
    z.append("| --- | --- | ---: | ---: | --- |")
    for k in e.konten:
        kette = "lückenlos" if not k.fehlende_monate and not k.kettenfehler else "prüfen"
        z.append(f"| {iban_kurz(k.iban)} | {k.art} | {eur(k.anfang)} | {eur(k.ende)} | {kette} |")
    z.append("")
    z.append("## Hinweise")
    z.append("")
    for s, t in e.hinweise:
        z.append(f"- **{s}:** {t}")
    if e.offene_eingaenge or e.offene_ausgaenge:
        z.append("")
        z.append("## Nicht zugeordnete Buchungen")
        z.append("")
        for b in e.offene_eingaenge + e.offene_ausgaenge:
            z.append(f"- {b.datum:%d.%m.%Y} · {eur(b.betrag)} · {b.gegen_name or '(ohne Namen)'} · {b.zweck}")
    return "\n".join(z) + "\n"


def als_dict(e: Ergebnis) -> dict:
    def d(x: Decimal | None) -> str | None:
        return None if x is None else str(x)

    return {
        "objekt": e.objekt,
        "jahr": e.jahr,
        "status": "zu_pruefen",
        "anzahl_buchungen": e.anzahl_buchungen,
        "hausgeld": [
            {"einheit": h.einheit, "eigentuemer": h.eigentuemer, "soll_monat": d(h.soll_monat),
             "ist": [d(v) for v in h.ist], "status": h.status, "abweichung": d(h.abweichung)}
            for h in e.hausgeld
        ],
        "gruppen": [
            {"gruppe": g.name, "summe": d(g.summe), "anzahl": g.anzahl,
             "groesste": [[n, d(b)] for n, b in g.groesste()]}
            for g in e.gruppen
        ],
        "konten": [
            {"konto": iban_kurz(k.iban), "art": k.art, "anfang": d(k.anfang), "ende": d(k.ende),
             "fehlende_monate": k.fehlende_monate, "kettenfehler": k.kettenfehler}
            for k in e.konten
        ],
        "hinweise": [{"schwere": s, "text": t} for s, t in e.hinweise],
        "offen": len(e.offene_eingaenge) + len(e.offene_ausgaenge),
    }


def als_json(e: Ergebnis) -> str:
    return json.dumps(als_dict(e), ensure_ascii=False, indent=2)
