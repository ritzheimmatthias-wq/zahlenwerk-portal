"""Report 1: Vorauszahlungs- und Abrechnungsspitzenanalyse je Mietverhältnis.

Grundlage ist ausschließlich ein gespeicherter, endgültig freigegebener
Berechnungsstand (`Ablage.lesen`). Es wird nichts neu verteilt: Kosten,
Vorauszahlungen und Salden werden unverändert aus dem Stand übernommen und nur
für die Empfehlung auf einen Monat umgerechnet. Alle Schritte sind
deterministisch; ein Sprachmodell ist nicht beteiligt.

Die Empfehlung ist ein fachlicher Vorschlag. Der Report ändert keine
Vertragsdaten, erzeugt kein Anpassungsschreiben und versendet nichts.
"""
from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from .modell import NULL, cent
from .reportregeln import REPORT_REGELN_VERSION, Reportkonfiguration
from .version import stand_fehler

ARTEN = ("betriebskosten", "heizkosten")
ART_TEXT = {"betriebskosten": "Betriebskosten", "heizkosten": "Heiz- und Warmwasserkosten"}
EMPFEHLUNG_TEXT = {
    "unveraendert": "Vorauszahlung unverändert lassen",
    "erhoehen": "Vorauszahlung erhöhen",
    "absenken": "Vorauszahlung absenken",
    "keine": "Keine belastbare Empfehlung möglich",
    "pauschale": "Keine automatische Empfehlung (Betriebskostenpauschale)",
}


class Reportfehler(Exception):
    pass


def _d(x) -> Decimal | None:
    return None if x is None else Decimal(str(x))


def _tage(z: dict) -> int:
    return (date.fromisoformat(z["bis"]) - date.fromisoformat(z["von"])).days + 1


def _eur(b: Decimal) -> str:
    s = f"{abs(b):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'-' if b < 0 else ''}{s} €"


def volles_jahr(z: dict) -> bool:
    """True, wenn der Abrechnungszeitraum genau zwölf Monate umfasst (auch abweichendes Wirtschaftsjahr)."""
    von, bis = date.fromisoformat(z["von"]), date.fromisoformat(z["bis"])
    try:
        ende = von.replace(year=von.year + 1)
    except ValueError:  # 29.02.
        ende = date(von.year + 1, 3, 1)
    return (ende - bis).days == 1


def grundlage_pruefen(stand: dict, status: str) -> None:
    fehler = stand_fehler(stand)
    if fehler:
        raise Reportfehler(f"Der Berechnungsstand ist nicht unverfälscht ({'; '.join(fehler)}). Kein Report.")
    if status != "endgueltig":
        raise Reportfehler(f"Version {stand['version']} hat den Status „{status or 'unbekannt'}“. "
                           "Reports werden nur aus der endgültig freigegebenen Abrechnungsversion erzeugt.")
    if stand.get("rote_warnungen"):
        raise Reportfehler("Der Berechnungsstand enthält rote Warnungen.")


def grundlage(stand: dict, status: str) -> dict:
    return {"version": stand["version"], "status": status, "eingabe_hash": stand["eingabe_hash"],
            "ergebnis_hash": stand.get("ergebnis_hash", ""), "regeln_version": stand["ergebnis"]["regeln_version"],
            "abrechnungszeitraum": stand["ergebnis"]["abrechnungszeitraum"], "objekt": stand["ergebnis"]["objekt"]["name"]}


def _art_der_zeile(z: dict, vz_arten: set) -> str:
    return "heizkosten" if z["heizkosten"] and "heizkosten" in vz_arten else "betriebskosten"


def _aenderungseffekt(k, zeilen: list, faktor: Decimal, darstellung: str, kontrolle: dict) -> tuple[Decimal | None, str]:
    """Jährlicher Effekt einer freigegebenen Kostenänderung auf das Mietverhältnis (hochgerechnet auf 12 Monate)."""
    betroffen = [z for z in zeilen if z["kostenart"] == k.kostenart]
    if not betroffen:
        return None, "Kostenart im Mietverhältnis nicht enthalten"
    anteil = sum((_d(z["betrag"]) for z in betroffen), NULL) * faktor
    if k.art == "prozent":
        return anteil * k.wert, f"{k.wert * 100:+.1f} % auf den hochgerechneten Anteil {_eur(cent(anteil))}"
    if darstellung == "gewerbe_netto":
        return None, "Betragsänderung bei Nettodarstellung nicht automatisch umgerechnet; bitte als Prozentwert erfassen"
    gesamt = _d(kontrolle[k.kostenart]["gesamt_brutto"])
    if not gesamt:
        return None, "Gesamtkosten der Kostenart sind null"
    quote = sum((_d(z["anteil_brutto"]) for z in betroffen), NULL) / gesamt
    return k.wert * quote * faktor, f"{_eur(k.wert)} objektweit × Anteil {quote * 100:.2f} %"


def analysiere(stand: dict, status: str, konfig: Reportkonfiguration) -> dict:
    """Erzeugt den Inhalt des Vorauszahlungsreports als JSON-fähiges, deterministisches Dict."""
    grundlage_pruefen(stand, status)
    r = konfig.regeln
    erg, eingabe = stand["ergebnis"], stand["eingabe"]
    jahr = erg["abrechnungszeitraum"]
    jahrestage = _tage(jahr)
    mv_eingabe = {m["id"]: m for m in eingabe["mietverhaeltnisse"]}
    kontrolle = {k["kostenart"]: k for k in erg["kontrolle"]}
    warnungen = erg["warnungen"]

    ergebnisse = []
    for m in erg["mietverhaeltnisse"]:
        me = mv_eingabe[m["id"]]
        tage = _tage(m["nutzungszeitraum"])
        darstellung = m["darstellung"]
        umlageart = me.get("umlageart", "")
        beendet = date.fromisoformat(me["zeitraum"]["bis"]) < date.fromisoformat(jahr["bis"])
        vz_arten = {v["art"] for v in m["vorauszahlungen"]}
        hinweise = [w["text"] for w in warnungen if w["bezug"] in (m["id"], m["einheit"]) and w["stufe"] == "gelb"]
        sperre = []
        if any(w["stufe"] == "rot" and w["bezug"] in (m["id"], m["einheit"]) for w in warnungen):
            sperre.append("Rote Warnung im Berechnungsstand")
        if darstellung == "gesperrt":
            sperre.append("Abrechnung des Mietverhältnisses gesperrt")
        if umlageart == "":
            sperre.append("Vertragliche Einordnung (Vorauszahlung oder Pauschale) ist nicht erfasst")
        if beendet:
            sperre.append(f"Mietverhältnis endete am {date.fromisoformat(me['zeitraum']['bis']):%d.%m.%Y}; keine künftige Vorauszahlung")
        if not volles_jahr(jahr):
            # Ohne belegte fachliche Regel keine Hochrechnung eines verkürzten Abrechnungsjahres (Befund W5).
            sperre.append(f"Verkürztes Abrechnungsjahr ({jahrestage} Tage): Die Kosten lassen sich nicht belastbar "
                          "auf zwölf Monate hochrechnen, insbesondere Heizkosten schwanken jahreszeitlich")
        if tage < r.mindest_nutzungstage:
            sperre.append(f"Nutzungszeitraum {tage} Tage, weniger als {r.mindest_nutzungstage} Tage: Hochrechnung nicht belastbar")
        faktor = Decimal(jahrestage) / Decimal(tage)
        if tage < jahrestage and any(z["heizkosten"] for z in m["zeilen"]):
            hinweise.append("Teilzeitraum: Die Hochrechnung erfolgt nach Tagen. Jahreszeitliche Schwankungen der Heizkosten sind nicht berücksichtigt.")
        if darstellung == "gewerbe_netto":
            hinweise.append("Gewerbe mit Umsatzsteuer: Beträge netto; die Umsatzsteuer richtet sich nach der bestätigten Konfiguration.")

        arten = []
        for art in ARTEN:
            zeilen = [z for z in m["zeilen"] if _art_der_zeile(z, vz_arten) == art]
            vz = next((v for v in m["vorauszahlungen"] if v["art"] == art), None)
            if not zeilen and vz is None:
                continue
            kosten = sum((_d(z["betrag"]) for z in zeilen), NULL)
            geleistet = _d(vz["betrag"]) if vz else NULL
            spitze = kosten - geleistet
            monat = (kosten * Decimal(jahrestage) / (Decimal(tage) * 12)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            aktuell = _d(me.get("vz_monatlich", {}).get(art))
            annahmen = []
            effekt_jahr = NULL
            for k in konfig.kostenaenderungen:
                if not any(z["kostenart"] == k.kostenart for z in zeilen):
                    continue
                wert, erlaeuterung = _aenderungseffekt(k, zeilen, faktor, darstellung, kontrolle)
                beruecksichtigt = k.freigegeben and wert is not None
                if not k.freigegeben:
                    erlaeuterung = "nicht freigegeben, daher nicht berücksichtigt"
                annahmen.append({"id": k.id, "kostenart": k.kostenart, "begruendung": k.begruendung, "beleg": k.beleg,
                                 "gilt_ab": k.gilt_ab, "berechnung": erlaeuterung, "beruecksichtigt": beruecksichtigt,
                                 "jahr": cent(wert) if beruecksichtigt else None,
                                 "monat": cent(wert / 12) if beruecksichtigt else None,
                                 "freigegeben_von": k.freigegeben_von, "freigegeben_am": k.freigegeben_am})
                if beruecksichtigt:
                    effekt_jahr += cent(wert)
            annahme_monat = sum((a["monat"] for a in annahmen if a["beruecksichtigt"]), NULL)
            ziel = monat + annahme_monat
            art_sperre = list(sperre)
            if aktuell is None and umlageart != "pauschale":
                art_sperre.append("Aktuelle monatliche Vorauszahlung ist nicht erfasst")
            eintrag = {"art": art, "bezeichnung": ART_TEXT[art], "vorauszahlungen": geleistet, "kosten": kosten,
                       "abrechnungsspitze": spitze, "ergebnis": "Nachzahlung" if spitze > 0 else ("Guthaben" if spitze < 0 else "ausgeglichen"),
                       "monatskosten": monat, "annahmen": annahmen, "annahmen_monat": annahme_monat,
                       "monatsbedarf": ziel, "aktuell_monatlich": aktuell, "differenz_monatlich": None,
                       "bagatellgrenze": None, "vorschlag_monatlich": None, "empfehlung": "keine", "sperrgruende": art_sperre,
                       "begruendung": ""}
            if umlageart == "pauschale":
                eintrag["empfehlung"] = "pauschale"
                eintrag["begruendung"] = (f"Laut Stammdaten ist eine Betriebskostenpauschale vereinbart. Kosten {_eur(kosten)} "
                                          f"stehen gezahlten Beträgen von {_eur(geleistet)} gegenüber (Differenz {_eur(spitze)}). "
                                          "Eine Anpassung setzt eine gesonderte fachliche und vertragliche Prüfung voraus.")
            elif art_sperre:
                eintrag["begruendung"] = "Empfehlung gesperrt: " + "; ".join(art_sperre) + "."
            else:
                diff = ziel - aktuell
                grenze = max(r.bagatelle_betrag, cent(aktuell * r.bagatelle_prozent))
                vorschlag = r.runde(ziel)
                if abs(diff) <= grenze or vorschlag == aktuell:
                    empf, vorschlag = "unveraendert", aktuell
                else:
                    empf = "erhoehen" if vorschlag > aktuell else "absenken"
                eintrag.update(differenz_monatlich=diff, bagatellgrenze=grenze, vorschlag_monatlich=vorschlag, empfehlung=empf)
                teile = [f"Kostenanteil {_eur(kosten)} für {tage} Tage ergibt {_eur(monat)} je Monat"]
                if annahme_monat:
                    teile.append(f"zuzüglich freigegebener Kostenänderungen {_eur(annahme_monat)} je Monat, zusammen {_eur(ziel)}")
                teile.append(f"aktuelle Vorauszahlung {_eur(aktuell)} je Monat, Abweichung {_eur(diff)}")
                if empf == "unveraendert":
                    teile.append(f"innerhalb der Bagatellgrenze von {_eur(grenze)} oder ohne Änderung nach Rundung")
                else:
                    teile.append(f"Vorschlag {_eur(vorschlag)} je Monat ({'Erhöhung' if empf == 'erhoehen' else 'Absenkung'} um {_eur(abs(vorschlag - aktuell))})")
                eintrag["begruendung"] = "; ".join(teile) + "."
            arten.append(eintrag)

        ergebnisse.append({
            "id": m["id"], "mieter": m["mieter"], "einheit": m["einheit"], "einheit_bezeichnung": m["einheit_bezeichnung"],
            "nutzung": m["nutzung"], "darstellung": darstellung, "umlageart": umlageart,
            "nutzungszeitraum": m["nutzungszeitraum"], "nutzungstage": tage, "jahrestage": jahrestage,
            "arten": arten,
            "summe": {"vorauszahlungen": sum((a["vorauszahlungen"] for a in arten), NULL),
                      "kosten": sum((a["kosten"] for a in arten), NULL),
                      "abrechnungsspitze": sum((a["abrechnungsspitze"] for a in arten), NULL)},
            "abgleich_saldo": _d(m["saldo"]),
            "hinweise": hinweise,
        })
    for e in ergebnisse:  # Abgleich mit der Abrechnung: Summe der Spitzen = Saldo (in der Darstellung des Mietverhältnisses)
        if e["summe"]["abrechnungsspitze"] != e["abgleich_saldo"]:
            raise Reportfehler(f"{e['id']}: Abrechnungsspitze {e['summe']['abrechnungsspitze']} weicht vom Saldo {e['abgleich_saldo']} ab.")
    return {"report": "vorauszahlung", "report_regeln_version": REPORT_REGELN_VERSION, "grundlage": grundlage(stand, status),
            "regeln": r, "regeln_text": r.beschreibung(), "mietverhaeltnisse": ergebnisse,
            "hinweis": "Fachliche Empfehlung. Es erfolgt keine Vertragsänderung, kein Anpassungsschreiben und kein Versand an Mieter."}
