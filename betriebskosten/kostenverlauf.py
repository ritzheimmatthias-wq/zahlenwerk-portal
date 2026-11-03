"""Report 2: jährlicher Kostenverlaufsbericht für den Eigentümer (ganzes Objekt).

Grundlage sind ausschließlich endgültig freigegebene Berechnungsstände je Jahr.
Alle Kennzahlen, Veränderungen, Kostentreiber und Diagrammdaten werden hier
deterministisch berechnet. Texte entstehen aus festen Vorlagen und nennen nur
Zahlen aus dem Bericht; Ursachen erscheinen ausschließlich als fachlich
freigegebene Anmerkung. Bei nur einem Jahr wird kein Trend behauptet.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from .modell import NULL, cent
from .reportregeln import REPORT_REGELN_VERSION, Reportkonfiguration
from .vorauszahlung import Reportfehler, _d, _eur, _tage, grundlage, grundlage_pruefen

KATEGORIE_TEXT = {"heizung": "Heizung", "warmwasser": "Warmwasser", "kaltwasser": "Kaltwasser", "sonstige": "Sonstige Betriebskosten"}
MAX_BALKEN = 10


def _prozent(delta: Decimal, basis: Decimal | None) -> Decimal | None:
    if not basis:
        return None
    return (delta / basis * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _pz(p: Decimal | None) -> str:
    return "–" if p is None else f"{p:+.1f} %".replace(".", ",")


def _jahr_label(zr: dict) -> str:
    von, bis = date.fromisoformat(zr["von"]), date.fromisoformat(zr["bis"])
    if von.year == bis.year and (von.month, von.day, bis.month, bis.day) == (1, 1, 12, 31):
        return str(von.year)
    return f"{von:%d.%m.%Y}–{bis:%d.%m.%Y}"


def jahreswerte(stand: dict) -> dict:
    erg, ein = stand["ergebnis"], stand["eingabe"]
    zr = erg["abrechnungszeitraum"]
    tage = _tage(zr)
    von, bis = date.fromisoformat(zr["von"]), date.fromisoformat(zr["bis"])
    kostenarten_ein = {k["id"]: k for k in ein["kostenarten"]}

    kostenarten, lieferanten, kategorien = {}, defaultdict(lambda: NULL), defaultdict(lambda: NULL)
    for k in erg["kontrolle"]:
        betrag = _d(k["gesamt_brutto"])
        kat = k.get("kategorie", "sonstige")
        kostenarten[k["kostenart"]] = {"id": k["kostenart"], "bezeichnung": k["bezeichnung"], "kategorie": kat,
                                       "pool": k["pool"], "schluessel": k["schluessel"], "betrag": betrag}
        kategorien[kat] += betrag
        for s in k.get("schritte", []):
            if "messdienst" in s:
                lieferanten[s.get("anbieter") or "ohne Angabe"] += _d(s["betrag_brutto"])
            else:
                for p in s["positionen"]:
                    lieferanten[p.get("lieferant") or "ohne Angabe"] += _d(p["brutto"])
    gesamt = sum((x["betrag"] for x in kostenarten.values()), NULL)
    if sum(lieferanten.values(), NULL) != gesamt:
        raise Reportfehler(f"{_jahr_label(zr)}: Summe nach Versorgern weicht von den Gesamtkosten ab (Stand ohne Rechenschritte?).")

    einheit_tage, flaeche_tage, flaeche_fehlt, wechsel, struktur = 0, Decimal(0), [], [], set()
    for e in ein["einheiten"]:
        teile = []
        for a in e["abschnitte"]:
            a_von, a_bis = max(von, date.fromisoformat(a["zeitraum"]["von"])), min(bis, date.fromisoformat(a["zeitraum"]["bis"]))
            if a_von > a_bis:
                continue
            t = (a_bis - a_von).days + 1
            teile.append(t)
            einheit_tage += t
            flaeche_tage += _d(a["flaeche"]) * t
            struktur.add((e["id"], a["nutzung"], a["flaeche"]))
            if _d(a["flaeche"]) <= 0:
                flaeche_fehlt.append(e["id"])
        if len(teile) > 1 or (teile and sum(teile) < tage):
            wechsel.append(e["id"])
    einheiten_mittel = (Decimal(einheit_tage) / tage).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    flaeche_mittel = (flaeche_tage / tage).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    bereiche = {"wohnen": NULL, "gewerbe": NULL, "leerstand": sum((_d(v) for v in erg["leerstand"].values()), NULL)}
    zahlungen = {"vorauszahlungen": NULL, "kosten": NULL, "abrechnungsspitze": NULL, "nachzahlungen": NULL, "guthaben": NULL}
    for m in erg["mietverhaeltnisse"]:
        bereiche[m["nutzung"]] += sum((_d(z["anteil_brutto"]) for z in m["zeilen"]), NULL)
        if m.get("umlageart") == "pauschale":
            continue
        s = _d(m["saldo_brutto"])
        zahlungen["vorauszahlungen"] += _d(m["vz_brutto"])
        zahlungen["kosten"] += _d(m["kosten_brutto"])
        zahlungen["abrechnungsspitze"] += s
        zahlungen["nachzahlungen" if s > 0 else "guthaben"] += s if s else NULL
    if sum(bereiche.values(), NULL) != gesamt:
        raise Reportfehler(f"{_jahr_label(zr)}: Wohn-, Gewerbe- und Leerstandsanteile ergeben nicht die Gesamtkosten.")

    verbrauch = {}
    mv_ein = {m["id"]: m for m in ein["mietverhaeltnisse"]}
    for kid, k in kostenarten_ein.items():
        if k["schluessel"] != "verbrauch":
            continue
        werte = [mv_ein[m["id"]]["verbrauch"].get(kid) for m in erg["mietverhaeltnisse"]]
        leer = [e.get("leerstand_verbrauch", {}).get(kid) for e in ein["einheiten"]]
        verbrauch[kid] = {"bezeichnung": k["bezeichnung"], "vollstaendig": all(w is not None for w in werte),
                          "summe": sum((_d(w) for w in werte + leer if w is not None), Decimal(0))}

    return {
        "jahr": _jahr_label(zr), "zeitraum": zr, "tage": tage, "version": stand["version"],
        "gesamt": gesamt, "kostenarten": kostenarten, "kategorien": {k: kategorien.get(k, NULL) for k in KATEGORIE_TEXT},
        "lieferanten": dict(sorted(lieferanten.items(), key=lambda x: (-x[1], x[0]))),
        "einheiten_mittel": einheiten_mittel, "flaeche_mittel": flaeche_mittel,
        "flaeche_belastbar": not flaeche_fehlt and flaeche_mittel > 0, "flaeche_fehlt": sorted(set(flaeche_fehlt)),
        "strukturwechsel": wechsel, "struktur": sorted(struktur),
        "je_einheit": cent(gesamt / einheiten_mittel) if einheiten_mittel else None,
        "je_m2": cent(gesamt / flaeche_mittel) if (not flaeche_fehlt and flaeche_mittel > 0) else None,
        "bereiche": bereiche, "zahlungen": zahlungen, "verbrauch": verbrauch,
        "heizkosten_ohne_kategorie": sorted(k["id"] for k in kostenarten_ein.values()
                                            if k.get("heizkosten") and k.get("kategorie", "sonstige") == "sonstige"
                                            and k["id"] in kostenarten),
    }


def _veraenderung(vorher: Decimal | None, nachher: Decimal | None) -> dict:
    v, n = vorher if vorher is not None else NULL, nachher if nachher is not None else NULL
    return {"vorjahr": vorher, "aktuell": nachher, "delta": n - v, "prozent": _prozent(n - v, vorher)}


def vergleiche(a: dict, b: dict, konfig: Reportkonfiguration) -> dict:
    r = konfig.regeln
    einschraenkungen = []
    if abs(a["tage"] - b["tage"]) > 1:
        einschraenkungen.append(f"Abrechnungszeitraum {a['tage']} Tage ({a['jahr']}) gegenüber {b['tage']} Tagen ({b['jahr']}).")
    if a["struktur"] != b["struktur"]:
        einschraenkungen.append(
            f"Einheitenstruktur geändert: {a['einheiten_mittel']} → {b['einheiten_mittel']} Einheiten im Jahresmittel, "
            f"{a['flaeche_mittel']} → {b['flaeche_mittel']} m².".replace(".", ","))
    if b["strukturwechsel"]:
        einschraenkungen.append(f"Strukturänderung im Jahr {b['jahr']} bei {', '.join(b['strukturwechsel'])}.")
    neu = sorted(set(b["kostenarten"]) - set(a["kostenarten"]))
    weg = sorted(set(a["kostenarten"]) - set(b["kostenarten"]))
    if neu:
        einschraenkungen.append("Neue Kostenarten: " + ", ".join(b["kostenarten"][k]["bezeichnung"] for k in neu) + ".")
    if weg:
        einschraenkungen.append("Entfallene Kostenarten: " + ", ".join(a["kostenarten"][k]["bezeichnung"] for k in weg) + ".")

    def zeilen(alt: dict, jetzt: dict, name) -> list:
        erg = []
        for k in list(jetzt) + [k for k in alt if k not in jetzt]:
            v = _veraenderung(_wert(alt.get(k)), _wert(jetzt.get(k)))
            erg.append({"id": k, "bezeichnung": name(k), **v})
        return erg

    def _wert(x):
        return x["betrag"] if isinstance(x, dict) else x

    kostenarten = zeilen(a["kostenarten"], b["kostenarten"],
                         lambda k: (b["kostenarten"].get(k) or a["kostenarten"][k])["bezeichnung"])
    lieferanten = zeilen(a["lieferanten"], b["lieferanten"], lambda k: k)
    kategorien = zeilen(a["kategorien"], b["kategorien"], lambda k: KATEGORIE_TEXT[k])

    def treiber(liste: list, steigend: bool) -> list:
        erg = []
        for x in liste:
            if not x["vorjahr"] or x["aktuell"] is None or x["prozent"] is None:
                continue
            d, p = x["delta"], x["delta"] / x["vorjahr"]
            if steigend and d >= r.treiber_betrag and p >= r.treiber_prozent:
                erg.append(x)
            if not steigend and -d >= r.treiber_betrag and -p >= r.treiber_prozent:
                erg.append(x)
        return sorted(erg, key=lambda x: (-abs(x["delta"]), x["id"]))

    gesamt = _veraenderung(a["gesamt"], b["gesamt"])
    kt = treiber(kostenarten, True)
    for x in kt:
        x["anteil_am_anstieg"] = _prozent(x["delta"], gesamt["delta"]) if gesamt["delta"] > 0 else None
    vergleichbar_abs = [x for x in kostenarten if x["vorjahr"] is not None and x["aktuell"] is not None]
    return {
        "vorjahr": a["jahr"], "aktuell": b["jahr"], "einschraenkungen": einschraenkungen,
        "eingeschraenkt_vergleichbar": bool(einschraenkungen),
        "gesamt": gesamt,
        "je_einheit": _veraenderung(a["je_einheit"], b["je_einheit"]) if a["je_einheit"] and b["je_einheit"] else None,
        "je_m2": _veraenderung(a["je_m2"], b["je_m2"]) if a["je_m2"] and b["je_m2"] else None,
        "kostenarten": kostenarten, "lieferanten": lieferanten, "kategorien": kategorien,
        "kostentreiber": kt, "senkungen": treiber(kostenarten, False),
        "versorger_steigerungen": treiber(lieferanten, True),
        "groesste_absolut": sorted(vergleichbar_abs, key=lambda x: (-abs(x["delta"]), x["id"]))[: r.top_anzahl],
        "groesste_prozentual": sorted([x for x in vergleichbar_abs if x["prozent"] is not None],
                                      key=lambda x: (-abs(x["prozent"]), x["id"]))[: r.top_anzahl],
        "zahlungen": {k: _veraenderung(a["zahlungen"][k], b["zahlungen"][k]) for k in ("vorauszahlungen", "kosten", "abrechnungsspitze")},
    }


def _balken(jahr: dict) -> tuple[list, list]:
    arten = sorted(jahr["kostenarten"].values(), key=lambda x: (-x["betrag"], x["id"]))
    if len(arten) > MAX_BALKEN:
        rest = sum((x["betrag"] for x in arten[MAX_BALKEN - 1:]), NULL)
        return [x["bezeichnung"] for x in arten[: MAX_BALKEN - 1]] + ["Übrige Kostenarten"], [x["betrag"] for x in arten[: MAX_BALKEN - 1]] + [rest]
    return [x["bezeichnung"] for x in arten], [x["betrag"] for x in arten]


def diagramme(jahre: list[dict], vergleich: dict | None) -> list[dict]:
    aktuell = jahre[-1]
    kat, werte = _balken(aktuell)
    d = [{"id": "kostenarten", "typ": "balken", "titel": f"Kosten je Kostenart {aktuell['jahr']}", "kategorien": kat,
          "reihen": [{"name": aktuell["jahr"], "werte": werte}]}]
    if vergleich:
        vorjahr = jahre[-2]
        ids = [x["id"] for x in sorted(vergleich["kostenarten"], key=lambda x: (-(x["aktuell"] or NULL), x["id"]))][:MAX_BALKEN]
        namen = {x["id"]: x["bezeichnung"] for x in vergleich["kostenarten"]}
        d.append({"id": "vergleich", "typ": "gruppiert", "titel": f"{vorjahr['jahr']} und {aktuell['jahr']} je Kostenart",
                  "kategorien": [namen[i] for i in ids],
                  "reihen": [{"name": vorjahr["jahr"], "werte": [vorjahr["kostenarten"].get(i, {}).get("betrag", NULL) for i in ids]},
                             {"name": aktuell["jahr"], "werte": [aktuell["kostenarten"].get(i, {}).get("betrag", NULL) for i in ids]}]})
        gr = vergleich["groesste_absolut"]
        d.append({"id": "veraenderungen", "typ": "abweichung", "titel": f"Größte Veränderungen gegenüber {vorjahr['jahr']}",
                  "kategorien": [x["bezeichnung"] for x in gr], "reihen": [{"name": "Veränderung", "werte": [x["delta"] for x in gr]}]})
    if len(jahre) >= 3:
        d.append({"id": "verlauf", "typ": "linie", "titel": "Mehrjahresverlauf", "kategorien": [j["jahr"] for j in jahre],
                  "reihen": [{"name": "Gesamtkosten", "werte": [j["gesamt"] for j in jahre]}]
                  + [{"name": KATEGORIE_TEXT[k], "werte": [j["kategorien"][k] for j in jahre]}
                     for k in KATEGORIE_TEXT if any(j["kategorien"][k] for j in jahre)]})
    d.append({"id": "vz_kosten", "typ": "gruppiert", "titel": "Vorauszahlungen und tatsächliche Kosten der Mietverhältnisse",
              "kategorien": [j["jahr"] for j in jahre],
              "reihen": [{"name": "Vorauszahlungen", "werte": [j["zahlungen"]["vorauszahlungen"] for j in jahre]},
                         {"name": "Tatsächliche Kosten", "werte": [j["zahlungen"]["kosten"] for j in jahre]}]})
    for kid in aktuell["verbrauch"]:
        reihe = [j["verbrauch"].get(kid) for j in jahre]
        if len(jahre) >= 2 and all(v is not None and v["vollstaendig"] for v in reihe):
            d.append({"id": f"verbrauch_{kid}", "typ": "linie" if len(jahre) >= 3 else "gruppiert",
                      "titel": f"Verbrauch {aktuell['verbrauch'][kid]['bezeichnung']} (laut Erfassung)", "einheit": "",
                      "kategorien": [j["jahr"] for j in jahre], "reihen": [{"name": "Verbrauch", "werte": [v["summe"] for v in reihe]}]})
    return d


def fakten_und_text(jahre: list[dict], vergleich: dict | None, konfig: Reportkonfiguration) -> tuple[list, list, list]:
    """Strukturierte Fakten, Datenqualitätshinweise und Zusammenfassung aus festen Vorlagen (kein Sprachmodell)."""
    a = jahre[-1]
    anm = defaultdict(list)
    for x in konfig.anmerkungen:
        if x.freigegeben:
            anm[x.kostenart].append(x.text)
    fakten = [{"code": "gesamtkosten", "jahr": a["jahr"], "betrag": a["gesamt"]}]
    text = []
    if vergleich is None:
        fakten.append({"code": "ausgangsbasis", "jahr": a["jahr"]})
        text.append(f"Gesamtkosten {a['jahr']}: {_eur(a['gesamt'])}. Dies ist das erste ausgewertete Jahr und dient als Ausgangsbasis; "
                    "eine Entwicklung oder ein Trend lässt sich daraus noch nicht ablesen.")
    else:
        g = vergleich["gesamt"]
        fakten.append({"code": "veraenderung_gesamt", "delta": g["delta"], "prozent": g["prozent"]})
        text.append(f"Gesamtkosten {a['jahr']}: {_eur(a['gesamt'])}, gegenüber {vergleich['vorjahr']} {_eur(g['delta'])} ({_pz(g['prozent'])}).")
        if vergleich["eingeschraenkt_vergleichbar"]:
            text.append("Der Vergleich ist eingeschränkt aussagekräftig: " + " ".join(vergleich["einschraenkungen"]))
        for x in vergleich["kostentreiber"]:
            fakten.append({"code": "kostentreiber", "kostenart": x["id"], "delta": x["delta"], "prozent": x["prozent"]})
            satz = f"Kostentreiber {x['bezeichnung']}: {_eur(x['delta'])} ({_pz(x['prozent'])})"
            if x.get("anteil_am_anstieg") is not None:
                satz += f", {str(x['anteil_am_anstieg']).replace('.', ',')} % des Gesamtanstiegs"
            text.append(satz + ".")
            for t in anm.get(x["id"], []):
                text.append(f"Erläuterung zu {x['bezeichnung']} (fachlich freigegeben): {t}")
        for x in vergleich["versorger_steigerungen"]:
            fakten.append({"code": "versorger_steigerung", "lieferant": x["id"], "delta": x["delta"], "prozent": x["prozent"]})
            text.append(f"Deutliche Steigerung bei {x['id']}: {_eur(x['delta'])} ({_pz(x['prozent'])}).")
        for x in vergleich["senkungen"]:
            fakten.append({"code": "kostensenkung", "kostenart": x["id"], "delta": x["delta"], "prozent": x["prozent"]})
            text.append(f"Kostensenkung {x['bezeichnung']}: {_eur(x['delta'])} ({_pz(x['prozent'])}).")
        if not vergleich["kostentreiber"] and not vergleich["senkungen"]:
            text.append("Keine Kostenart überschreitet die festgelegten Schwellen für einen Kostentreiber oder eine deutliche Senkung.")
        if vergleich["kostentreiber"]:
            text.append("Im Folgejahr beobachten: " + ", ".join(x["bezeichnung"] for x in vergleich["kostentreiber"]) + ". "
                        "Bei anhaltender Entwicklung kann eine vertiefende Controlling- oder Sonderprüfung sinnvoll sein.")
    z = a["zahlungen"]
    text.append(f"Vorauszahlungen der Mietverhältnisse {_eur(z['vorauszahlungen'])}, tatsächliche Kosten {_eur(z['kosten'])}, "
                f"Abrechnungsspitze insgesamt {_eur(z['abrechnungsspitze'])} (Nachzahlungen {_eur(z['nachzahlungen'])}, "
                f"Guthaben {_eur(z['guthaben'])}).")
    if a["bereiche"]["leerstand"]:
        text.append(f"Auf Leerstand entfielen {_eur(a['bereiche']['leerstand'])}, die der Eigentümer trägt.")

    qualitaet = []
    if not a["flaeche_belastbar"]:
        qualitaet.append("Kennzahl je m² nicht ausgewiesen: Flächenangaben fehlen oder sind null"
                         + (f" ({', '.join(a['flaeche_fehlt'])})." if a["flaeche_fehlt"] else "."))
    for kid, v in a["verbrauch"].items():
        if not v["vollstaendig"]:
            qualitaet.append(f"Verbrauchswerte {v['bezeichnung']} unvollständig; kein Verbrauchsverlauf.")
    if len(jahre) >= 2:
        for kid, v in a["verbrauch"].items():
            if any(kid not in j["verbrauch"] or not j["verbrauch"][kid]["vollstaendig"] for j in jahre):
                qualitaet.append(f"Verbrauchsverlauf {v['bezeichnung']}: nicht in allen Jahren vollständig erfasst, daher keine Verbrauchsgrafik.")
    if a["heizkosten_ohne_kategorie"]:
        qualitaet.append("Heizkostenarten ohne Zuordnung zu Heizung oder Warmwasser: " + ", ".join(a["heizkosten_ohne_kategorie"]) + ".")
    if len(jahre) < 3:
        qualitaet.append("Mehrjahresverlauf erst ab drei ausgewerteten Jahren (aktuelles Jahr und mindestens zwei Vergleichsjahre).")
    return fakten, qualitaet, text


def bericht(staende: list[tuple[dict, str]], konfig: Reportkonfiguration) -> dict:
    """Erzeugt den Kostenverlaufsbericht aus Berechnungsständen (je Jahr ein endgültiger Stand) mit ihrem Status."""
    if not staende:
        raise Reportfehler("Kein Berechnungsstand übergeben.")
    for stand, status in staende:
        grundlage_pruefen(stand, status)
    objekte = {(s["ergebnis"]["objekt"]["name"], s["ergebnis"]["objekt"]["anschrift"]) for s, _ in staende}
    if len(objekte) > 1:
        raise Reportfehler("Die Berechnungsstände gehören zu verschiedenen Objekten.")
    staende = sorted(staende, key=lambda x: x[0]["ergebnis"]["abrechnungszeitraum"]["von"])
    jahre = [jahreswerte(s) for s, _ in staende]
    if len({j["jahr"] for j in jahre}) != len(jahre):
        raise Reportfehler("Ein Abrechnungsjahr ist mehrfach enthalten.")
    for x, y in zip(jahre, jahre[1:]):
        if date.fromisoformat(x["zeitraum"]["bis"]) >= date.fromisoformat(y["zeitraum"]["von"]):
            raise Reportfehler("Abrechnungszeiträume überschneiden sich.")
    vergleich = vergleiche(jahre[-2], jahre[-1], konfig) if len(jahre) >= 2 else None
    verlauf = [vergleiche(x, y, konfig)["einschraenkungen"] for x, y in zip(jahre, jahre[1:])]
    fakten, qualitaet, text = fakten_und_text(jahre, vergleich, konfig)
    return {
        "report": "kostenverlauf", "report_regeln_version": REPORT_REGELN_VERSION,
        "grundlage": [grundlage(s, st) for s, st in staende], "regeln": konfig.regeln, "regeln_text": konfig.regeln.beschreibung(),
        "objekt": staende[-1][0]["ergebnis"]["objekt"], "jahre": jahre, "vergleich": vergleich,
        "einschraenkungen_verlauf": verlauf, "trend_moeglich": len(jahre) >= 2, "mehrjahresverlauf": len(jahre) >= 3,
        "diagramme": diagramme(jahre, vergleich), "fakten": fakten, "datenqualitaet": qualitaet,
        "zusammenfassung": text, "zusammenfassung_status": "entwurf",
        "hinweis": "Betriebswirtschaftliche Orientierung, keine Steuer- oder Rechtsberatung. "
                   "Texte und Auffälligkeiten werden vor der Bereitstellung fachlich geprüft.",
    }
