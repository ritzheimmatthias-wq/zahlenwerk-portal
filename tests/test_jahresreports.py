"""Jahresreports und Reproduzierbarkeit: Soll/Ist-Abgleich mit ausschließlich künstlichen Daten.

Alle Sollwerte sind von Hand nachgerechnet (Kommentare). 2025 hat 365 Tage.
"""
import copy
import io
import json
from datetime import date, datetime
from decimal import Decimal as D

import pytest

from betriebskosten.eingabe import aus_dict
from betriebskosten.kostenverlauf import bericht
from betriebskosten.rechnen import berechne
from betriebskosten.reportablage import ReportAblage
from betriebskosten.reportregeln import Reportkonfiguration, konfiguration_aus_dict
from betriebskosten.version import Ablage, Freigabefehler, als_dict, kanonisch
from betriebskosten.vorauszahlung import Reportfehler, analysiere

ZEIT = datetime(2026, 11, 2, 12, 0)
STANDARD = Reportkonfiguration()


def ka(id, schluessel, heiz=False, kategorie="sonstige", bezeichnung=None):
    return {"id": id, "bezeichnung": bezeichnung or id.capitalize(), "schluessel": schluessel, "pool": "alle",
            "umlagefaehig_bestaetigt": True, "heizkosten": heiz, "kategorie": kategorie}


def endgueltig(pfad, daten, name="ablage"):
    ablage = Ablage(pfad / name)
    v, _ = ablage.speichern(berechne(aus_dict(daten)), zeit=ZEIT, person="pruefer")
    for s in ("fachlich_geprueft", "eigentuemer_freigegeben", "endgueltig"):
        ablage.status_setzen(v, s, person="pruefer", zeit=ZEIT)
    return ablage, ablage.lesen(v), ablage.status(v)


def _pdf_text(pdf: bytes) -> str:
    from pypdf import PdfReader

    return " ".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)


# --- Testobjekt Vorauszahlungen -------------------------------------------------------

def fall_vz():
    """Vier Wohnungen à 100 m². Grundsteuer 6.000 € nach Fläche × Tage, Heizung laut Messdienst.

    WE1 N: Nachzahlung, WE2 G: Guthaben, WE3 U: nahezu ausgeglichen,
    WE4: A zieht zum 30.06. aus (181 Tage), E zieht zum 01.07. ein (184 Tage).
    """
    def mv(id, einheit, bk, hk, monat_bk, monat_hk, **extra):
        return {"id": id, "einheit": einheit, "mieter": f"Mietpartei {id} (fiktiv)", "umlageart": "vorauszahlung",
                "vorauszahlungen": {"betriebskosten": bk, "heizkosten": hk},
                "vz_monatlich": {"betriebskosten": monat_bk, "heizkosten": monat_hk}, **extra}
    return {
        "objekt": {"name": "Vorauszahlungshaus (fiktiv)", "anschrift": "Testweg 2, 12345 Musterstadt", "vermieter": "Erika Eigentümerin (fiktiv)"},
        "abrechnung": {"von": date(2025, 1, 1), "bis": date(2025, 12, 31)},
        "einheiten": [{"id": f"WE{i}", "abschnitte": [{"nutzung": "wohnen", "flaeche": "100"}]} for i in range(1, 5)],
        "mietverhaeltnisse": [
            mv("N", "WE1", "1200.00", "600.00", "100.00", "50.00"),
            mv("G", "WE2", "2400.00", "600.00", "200.00", "50.00"),
            mv("U", "WE3", "1476.00", "600.00", "123.00", "50.00"),
            mv("A", "WE4", "600.00", "240.00", "100.00", "40.00", bis=date(2025, 6, 30)),
            mv("E", "WE4", "660.00", "240.00", "110.00", "40.00", von=date(2025, 7, 1)),
        ],
        "kostenarten": [ka("grundsteuer", "flaeche"), ka("heizung", "messdienst", heiz=True, kategorie="heizung")],
        "belege": [{"id": "GS", "lieferant": "Stadtkasse", "rechnungsnummer": "GS-25",
                    "positionen": [{"kostenart": "grundsteuer", "brutto": "6000.00"}]}],
        "messdienst": [{"id": "MD", "anbieter": "Messdienst Beispiel", "gesamt_brutto": "2280.00", "werte": [
            {"einheit": "WE1", "kostenart": "heizung", "brutto": "600.00"},
            {"einheit": "WE2", "kostenart": "heizung", "brutto": "480.00"},
            {"einheit": "WE3", "kostenart": "heizung", "brutto": "600.00"},
            {"einheit": "WE4", "kostenart": "heizung", "brutto": "400.00", "mietverhaeltnis": "A"},
            {"einheit": "WE4", "kostenart": "heizung", "brutto": "200.00", "mietverhaeltnis": "E"}]}],
    }


def report_vz(tmp_path, daten=None, konfig=STANDARD):
    _, stand, status = endgueltig(tmp_path, daten or fall_vz())
    return json.loads(kanonisch(analysiere(stand, status, konfig)))


def art(r, mid, a):
    m = next(x for x in r["mietverhaeltnisse"] if x["id"] == mid)
    return next(x for x in m["arten"] if x["art"] == a)


def test_nachzahlung_mit_erhoehung(tmp_path):
    r = report_vz(tmp_path)
    bk = art(r, "N", "betriebskosten")
    # Kosten 6.000 × 100/400 = 1.500; VZ 1.200; Spitze +300; 1.500 / 12 = 125,00; aktuell 100 → +25 > Grenze 5
    assert (bk["kosten"], bk["vorauszahlungen"], bk["abrechnungsspitze"], bk["ergebnis"]) == ("1500.00", "1200.00", "300.00", "Nachzahlung")
    assert (bk["monatskosten"], bk["aktuell_monatlich"], bk["differenz_monatlich"], bk["bagatellgrenze"]) == ("125.00", "100.00", "25.00", "5.00")
    assert (bk["empfehlung"], bk["vorschlag_monatlich"]) == ("erhoehen", "125.00")
    assert "Erhöhung um 25,00 €" in bk["begruendung"]
    hk = art(r, "N", "heizkosten")
    assert (hk["kosten"], hk["monatskosten"], hk["empfehlung"], hk["vorschlag_monatlich"]) == ("600.00", "50.00", "unveraendert", "50.00")


def test_guthaben_mit_absenkung(tmp_path):
    r = report_vz(tmp_path)
    bk, hk = art(r, "G", "betriebskosten"), art(r, "G", "heizkosten")
    assert (bk["abrechnungsspitze"], bk["ergebnis"], bk["empfehlung"], bk["vorschlag_monatlich"]) == ("-900.00", "Guthaben", "absenken", "125.00")
    # Heizung 480 / 12 = 40,00; aktuell 50 → −10 jenseits der Grenze max(5,00; 2,50)
    assert (hk["monatskosten"], hk["empfehlung"], hk["vorschlag_monatlich"]) == ("40.00", "absenken", "40.00")


def test_nahezu_ausgeglichen_bleibt_unveraendert(tmp_path):
    bk = art(report_vz(tmp_path), "U", "betriebskosten")
    # Kosten 1.500, VZ 1.476 → Spitze +24; 125,00 gegen 123,00 → +2,00 ≤ Grenze max(5,00; 6,15)
    assert (bk["abrechnungsspitze"], bk["differenz_monatlich"], bk["bagatellgrenze"]) == ("24.00", "2.00", "6.15")
    assert (bk["empfehlung"], bk["vorschlag_monatlich"]) == ("unveraendert", "123.00")


def test_teilzeitraum_und_mieterwechsel(tmp_path):
    r = report_vz(tmp_path)
    a = next(m for m in r["mietverhaeltnisse"] if m["id"] == "A")
    e = next(m for m in r["mietverhaeltnisse"] if m["id"] == "E")
    assert (a["nutzungstage"], e["nutzungstage"]) == (181, 184)
    # Auszug: Kosten 6.000 × 181/1.460 = 743,84 (Restcent), keine künftige Vorauszahlung
    assert art(r, "A", "betriebskosten")["kosten"] == "743.84"
    assert {x["empfehlung"] for x in a["arten"]} == {"keine"}
    assert "endete am 30.06.2025" in a["arten"][0]["begruendung"]
    # Einzug: 756,16 × 365 / (184 × 12) = 125,00; aktuell 110 → erhöhen auf 125
    bk = art(r, "E", "betriebskosten")
    assert (bk["kosten"], bk["vorauszahlungen"], bk["abrechnungsspitze"], bk["monatskosten"]) == ("756.16", "660.00", "96.16", "125.00")
    assert (bk["empfehlung"], bk["vorschlag_monatlich"]) == ("erhoehen", "125.00")
    # Heizung 200 × 365 / 2.208 = 33,06; aktuell 40 → absenken, kaufmännisch auf 33,00
    hk = art(r, "E", "heizkosten")
    assert (hk["monatskosten"], hk["empfehlung"], hk["vorschlag_monatlich"]) == ("33.06", "absenken", "33.00")
    assert any("Jahreszeitliche Schwankungen" in h for h in e["hinweise"])
    # Summe der Spitzen = Saldo der Abrechnung je Mietverhältnis
    for m in r["mietverhaeltnisse"]:
        assert m["summe"]["abrechnungsspitze"] == m["abgleich_saldo"]


def test_kurzer_zeitraum_ohne_belastbare_empfehlung(tmp_path):
    konfig = konfiguration_aus_dict({"regeln": {"mindest_nutzungstage": 200}})
    bk = art(report_vz(tmp_path, konfig=konfig), "E", "betriebskosten")
    assert bk["empfehlung"] == "keine" and bk["vorschlag_monatlich"] is None
    assert "weniger als 200 Tage" in bk["begruendung"]


def test_betriebskostenpauschale_ohne_empfehlung(tmp_path):
    d = fall_vz()
    d["mietverhaeltnisse"][0]["umlageart"] = "pauschale"
    erg = berechne(aus_dict(d))
    assert "pauschale" in {w.code for w in erg.gelb}
    r = report_vz(tmp_path, d)
    m = next(x for x in r["mietverhaeltnisse"] if x["id"] == "N")
    assert {x["empfehlung"] for x in m["arten"]} == {"pauschale"}
    assert all(x["vorschlag_monatlich"] is None for x in m["arten"])
    # Kein Abrechnungsschreiben an den Pauschalmieter
    from betriebskosten.ausgabe import schreibe_lauf

    namen = {p.name for p in schreibe_lauf(erg, tmp_path / "aus")}
    assert "abrechnung_N.pdf" not in namen and "abrechnung_G.pdf" in namen


@pytest.mark.parametrize("aenderung,grund", [
    ({"umlageart": ""}, "Vorauszahlung oder Pauschale"),
    ({"vz_monatlich": {}}, "monatliche Vorauszahlung ist nicht erfasst"),
])
def test_unvollstaendige_daten_sperren_empfehlung(tmp_path, aenderung, grund):
    d = fall_vz()
    d["mietverhaeltnisse"][2].update(aenderung)
    bk = art(report_vz(tmp_path, d), "U", "betriebskosten")
    assert bk["empfehlung"] == "keine" and bk["vorschlag_monatlich"] is None and grund in bk["begruendung"]


def test_nur_endgueltige_version(tmp_path):
    ablage = Ablage(tmp_path)
    v, _ = ablage.speichern(berechne(aus_dict(fall_vz())), zeit=ZEIT, person="pruefer")
    ablage.status_setzen(v, "fachlich_geprueft", person="pruefer", zeit=ZEIT)
    with pytest.raises(Reportfehler, match="endgültig"):
        analysiere(ablage.lesen(v), ablage.status(v), STANDARD)


def test_freigegebene_kostenaenderung_als_annahme(tmp_path):
    konfig = konfiguration_aus_dict({"kostenaenderungen": [
        {"id": "K1", "kostenart": "grundsteuer", "art": "prozent", "wert": "0.10", "begruendung": "Neuer Grundsteuerbescheid (fiktiv)",
         "beleg": "GS-26", "freigegeben_von": "Zahlenwerk (fiktiv)", "freigegeben_am": date(2026, 1, 10)},
        {"id": "K2", "kostenart": "grundsteuer", "art": "betrag", "wert": "400.00", "begruendung": "Noch nicht geprüft"},
    ]})
    bk = art(report_vz(tmp_path, konfig=konfig), "N", "betriebskosten")
    k1, k2 = bk["annahmen"]
    # 1.500 × 10 % = 150 je Jahr = 12,50 je Monat; Bedarf 137,50 → kaufmännisch 138,00
    assert (k1["beruecksichtigt"], k1["jahr"], k1["monat"]) == (True, "150.00", "12.50")
    assert (k2["beruecksichtigt"], k2["jahr"]) == (False, None) and "nicht freigegeben" in k2["berechnung"]
    assert (bk["annahmen_monat"], bk["monatsbedarf"], bk["vorschlag_monatlich"]) == ("12.50", "137.50", "138.00")
    # Freigegebener Betrag: 400 € objektweit × 25 % Anteil = 100 € je Jahr
    konfig2 = konfiguration_aus_dict({"kostenaenderungen": [
        {"id": "K3", "kostenart": "grundsteuer", "art": "betrag", "wert": "400.00", "begruendung": "Bescheid",
         "freigegeben_von": "Zahlenwerk (fiktiv)", "freigegeben_am": date(2026, 1, 10)}]})
    assert art(report_vz(tmp_path / "b", konfig=konfig2), "N", "betriebskosten")["annahmen"][0]["jahr"] == "100.00"


def test_regeln_konfigurierbar_und_sichtbar(tmp_path):
    konfig = konfiguration_aus_dict({"regeln": {"rundungsart": "aufrunden", "rundung_auf": "5.00", "bagatelle_betrag": "30.00",
                                                "bagatelle_prozent": "0"}})
    r = report_vz(tmp_path, konfig=konfig)
    assert art(r, "N", "betriebskosten")["empfehlung"] == "unveraendert"  # +25 ≤ 30
    assert art(r, "E", "heizkosten")["empfehlung"] == "unveraendert"
    assert r["regeln"]["bagatelle_betrag"] == "30.00" and any("30.00 €" in t for t in r["regeln_text"])
    konfig = konfiguration_aus_dict({"regeln": {"rundungsart": "aufrunden", "rundung_auf": "5.00"}})
    assert art(report_vz(tmp_path / "b", konfig=konfig), "E", "heizkosten")["vorschlag_monatlich"] == "35.00"


def test_gewerbe_netto_im_vorauszahlungsreport(tmp_path):
    from tests.test_betriebskosten import fall_gewerbe

    d = fall_gewerbe()
    for m in d["mietverhaeltnisse"]:
        m["umlageart"] = "vorauszahlung"
    d["mietverhaeltnisse"][1]["vz_monatlich"] = {"betriebskosten": "150.00"}
    r = report_vz(tmp_path, d)
    g = art(r, "G1", "betriebskosten")
    # Netto: Kosten 1.900, VZ 1.800, Spitze 100; 1.900 / 12 = 158,33 gegen 150 → erhöhen auf 158
    assert (g["kosten"], g["vorauszahlungen"], g["abrechnungsspitze"], g["monatskosten"]) == ("1900.00", "1800.00", "100.00", "158.33")
    assert (g["empfehlung"], g["vorschlag_monatlich"]) == ("erhoehen", "158.00")
    assert art(r, "W1", "betriebskosten")["empfehlung"] == "keine"  # aktuelle Vorauszahlung fehlt


# --- Kostenverlauf ---------------------------------------------------------------------

def fall_jahr(jahr, muell, wasser, heizung, zusatz_einheit=False, flaeche_we1="50"):
    tage = (date(jahr, 12, 31) - date(jahr, 1, 1)).days + 1
    einheiten = [{"id": "WE1", "abschnitte": [{"nutzung": "wohnen", "flaeche": flaeche_we1}]},
                 {"id": "WE2", "abschnitte": [{"nutzung": "wohnen", "flaeche": "70"}]},
                 {"id": "WE3", "abschnitte": [{"nutzung": "wohnen", "flaeche": "80"}]}]
    mvs = [{"id": f"MV{i}", "einheit": f"WE{i}", "mieter": f"Mietpartei {i} (fiktiv)", "personen": i, "umlageart": "vorauszahlung",
            "verbrauch": {"wasser": str(10 * i)}, "vorauszahlungen": {"betriebskosten": "1000.00", "heizkosten": "500.00"}}
           for i in (1, 2, 3)]
    werte = [("WE1", "400.00"), ("WE2", "500.00"), ("WE3", str(D(heizung) - 900))]
    if zusatz_einheit:
        einheiten.append({"id": "WE4", "abschnitte": [{"von": date(jahr, 7, 1), "nutzung": "wohnen", "flaeche": "40"}]})
        mvs.append({"id": "MV4", "einheit": "WE4", "mieter": "Mietpartei 4 (fiktiv)", "von": date(jahr, 7, 1), "personen": 1,
                    "umlageart": "vorauszahlung", "verbrauch": {"wasser": "5"}, "vorauszahlungen": {"betriebskosten": "300.00"}})
    return {
        "objekt": {"name": "Verlaufshaus (fiktiv)", "anschrift": "Zeitreihe 3, 12345 Musterstadt", "vermieter": "Erika Eigentümerin (fiktiv)"},
        "abrechnung": {"von": date(jahr, 1, 1), "bis": date(jahr, 12, 31)},
        "einheiten": einheiten, "mietverhaeltnisse": mvs,
        "kostenarten": [ka("grundsteuer", "flaeche"), ka("muell", "personen"), ka("wasser", "verbrauch", kategorie="kaltwasser"),
                        ka("heizung", "messdienst", heiz=True, kategorie="heizung")],
        "belege": [{"id": f"GS{jahr}", "lieferant": "Stadtkasse", "rechnungsnummer": f"GS-{jahr}", "positionen": [{"kostenart": "grundsteuer", "brutto": "1000.00"}]},
                   {"id": f"M{jahr}", "lieferant": "Entsorgung GmbH", "rechnungsnummer": f"M-{jahr}", "positionen": [{"kostenart": "muell", "brutto": muell}]},
                   {"id": f"W{jahr}", "lieferant": "Wasserwerk", "rechnungsnummer": f"W-{jahr}", "positionen": [{"kostenart": "wasser", "brutto": wasser}]}],
        "messdienst": [{"id": f"MD{jahr}", "anbieter": "Messdienst Beispiel", "gesamt_brutto": heizung,
                        "werte": [{"einheit": e, "kostenart": "heizung", "brutto": b} for e, b in werte]}],
    }, tage


JAHRE = {2023: ("600.00", "300.00", "1500.00"), 2024: ("660.00", "300.00", "1500.00"), 2025: ("690.00", "330.00", "2100.00")}


def staende(tmp_path, jahre, **extra):
    erg = []
    for j in jahre:
        d, _ = fall_jahr(j, *JAHRE[j], **(extra if j == jahre[-1] else {}))
        _, stand, status = endgueltig(tmp_path, d, f"ablage{j}")
        erg.append((stand, status))
    return erg


def kv(tmp_path, jahre, konfig=STANDARD, **extra):
    return json.loads(kanonisch(bericht(staende(tmp_path, jahre, **extra), konfig)))


def test_ein_jahr_ist_ausgangsbasis_ohne_trend(tmp_path):
    r = kv(tmp_path, [2025])
    j = r["jahre"][0]
    # 1.000 + 690 + 330 + 2.100 = 4.120; je m² 4.120 / 200 = 20,60; je Einheit 4.120 / 3 = 1.373,33
    assert (j["gesamt"], j["je_m2"], j["je_einheit"]) == ("4120.00", "20.60", "1373.33")
    assert j["kategorien"] == {"heizung": "2100.00", "warmwasser": "0.00", "kaltwasser": "330.00", "sonstige": "1690.00"}
    assert j["lieferanten"] == {"Messdienst Beispiel": "2100.00", "Stadtkasse": "1000.00", "Entsorgung GmbH": "690.00", "Wasserwerk": "330.00"}
    assert r["vergleich"] is None and not r["trend_moeglich"]
    assert {d["id"] for d in r["diagramme"]} == {"kostenarten", "vz_kosten"}
    text = " ".join(r["zusammenfassung"])
    assert "Ausgangsbasis" in text and "Kostentreiber" not in text and "gegenüber" not in text
    # Zahlungen: VZ 3 × 1.500 = 4.500, Kosten 4.120 → Spitze −380. Je Mietverhältnis:
    # MV1 250 + 115 + 55 + 400 = 820 (−680), MV2 350 + 230 + 110 + 500 = 1.190 (−310), MV3 400 + 345 + 165 + 1.200 = 2.110 (+610)
    assert j["zahlungen"] == {"vorauszahlungen": "4500.00", "kosten": "4120.00", "abrechnungsspitze": "-380.00",
                              "nachzahlungen": "610.00", "guthaben": "-990.00"}


def test_zwei_jahre_mit_kostentreiber_beim_versorger(tmp_path):
    r = kv(tmp_path, [2024, 2025])
    v = r["vergleich"]
    # 3.460 → 4.120: +660, 660 / 3.460 = 19,08 % → 19,1 %
    assert (v["gesamt"]["delta"], v["gesamt"]["prozent"]) == ("660.00", "19.1")
    assert [(x["id"], x["delta"], x["prozent"], x["anteil_am_anstieg"]) for x in v["kostentreiber"]] == [("heizung", "600.00", "40.0", "90.9")]
    assert [(x["id"], x["delta"]) for x in v["versorger_steigerungen"]] == [("Messdienst Beispiel", "600.00")]
    muell = next(x for x in v["kostenarten"] if x["id"] == "muell")
    assert (muell["delta"], muell["prozent"]) == ("30.00", "4.5")  # 660 → 690, unter den Schwellen
    assert not v["eingeschraenkt_vergleichbar"]
    assert {d["id"] for d in r["diagramme"]} == {"kostenarten", "vergleich", "veraenderungen", "vz_kosten", "verbrauch_wasser"}
    vergl = next(d for d in r["diagramme"] if d["id"] == "vergleich")
    assert vergl["kategorien"][0] == "Heizung" and vergl["reihen"][0]["werte"][0] == "1500.00" and vergl["reihen"][1]["werte"][0] == "2100.00"
    assert not r["mehrjahresverlauf"]
    assert any("Kostentreiber Heizung: 600,00 € (+40,0 %)" in t for t in r["zusammenfassung"])


def test_kein_kostentreiber_ohne_schwelle(tmp_path):
    r = kv(tmp_path, [2023, 2024])
    assert r["vergleich"]["kostentreiber"] == [] and r["vergleich"]["versorger_steigerungen"] == []
    assert any("Keine Kostenart überschreitet" in t for t in r["zusammenfassung"])


def test_mehrere_jahre_mit_linienverlauf(tmp_path):
    r = kv(tmp_path, [2025, 2023, 2024])  # Reihenfolge der Übergabe egal
    assert [j["jahr"] for j in r["jahre"]] == ["2023", "2024", "2025"]
    linie = next(d for d in r["diagramme"] if d["id"] == "verlauf")
    assert linie["kategorien"] == ["2023", "2024", "2025"]
    assert linie["reihen"][0] == {"name": "Gesamtkosten", "werte": ["3400.00", "3460.00", "4120.00"]}
    assert r["mehrjahresverlauf"] and r["vergleich"]["vorjahr"] == "2024"


def test_freigegebene_anmerkung_einzige_ursache(tmp_path):
    konfig = konfiguration_aus_dict({"anmerkungen": [
        {"kostenart": "heizung", "text": "Neuer Wärmeliefervertrag ab 01.01.2025 (fiktiv).", "freigegeben_von": "Zahlenwerk", "freigegeben_am": date(2026, 2, 1)},
        {"kostenart": "heizung", "text": "Unbestätigte Vermutung."}]})
    text = " ".join(kv(tmp_path, [2024, 2025], konfig)["zusammenfassung"])
    assert "Neuer Wärmeliefervertrag" in text and "Unbestätigte Vermutung" not in text


def test_geaenderte_einheitenstruktur_eingeschraenkt_vergleichbar(tmp_path):
    r = kv(tmp_path, [2024, 2025], zusatz_einheit=True)
    j = r["jahre"][-1]
    # 3 + 184/365 = 3,50 Einheiten; 200 + 40 × 184/365 = 220,16 m²
    assert (j["einheiten_mittel"], j["flaeche_mittel"], j["strukturwechsel"]) == ("3.50", "220.16", ["WE4"])
    v = r["vergleich"]
    assert v["eingeschraenkt_vergleichbar"]
    assert any("Einheitenstruktur geändert" in x for x in v["einschraenkungen"])
    assert any("Eingeschränkt" in t or "eingeschränkt" in t for t in r["zusammenfassung"])


def test_gemischtes_objekt_wohn_und_gewerbebereich(tmp_path):
    from tests.test_betriebskosten import fall_gemischt

    _, stand, status = endgueltig(tmp_path, fall_gemischt())
    j = json.loads(kanonisch(bericht([(stand, status)], STANDARD)))["jahre"][0]
    # Wohnen 1.460 + 365 + 441,60 + 184 = 2.450,60; Gewerbe 1.748,40 + 300 = 2.048,40; Summe 4.499
    assert j["bereiche"] == {"wohnen": "2450.60", "gewerbe": "2048.40", "leerstand": "0.00"}
    assert j["gesamt"] == "4499.00"


def test_fehlende_flaeche_und_verbrauch(tmp_path):
    r = kv(tmp_path, [2025], flaeche_we1="0")
    j = r["jahre"][0]
    assert j["je_m2"] is None and j["flaeche_belastbar"] is False and j["flaeche_fehlt"] == ["WE1"]
    assert any("je m² nicht ausgewiesen" in x for x in r["datenqualitaet"])
    # Verbrauch nur in einem der Jahre als Schlüssel erfasst → keine Verbrauchsgrafik
    a, _ = fall_jahr(2024, *JAHRE[2024])
    a["kostenarten"][2]["schluessel"] = "flaeche"
    _, s1, st1 = endgueltig(tmp_path, a, "x2024")
    b, _ = fall_jahr(2025, *JAHRE[2025])
    _, s2, st2 = endgueltig(tmp_path, b, "x2025")
    r2 = json.loads(kanonisch(bericht([(s1, st1), (s2, st2)], STANDARD)))
    assert "verbrauch_wasser" not in {d["id"] for d in r2["diagramme"]}
    assert any("Verbrauchsverlauf Wasser" in x for x in r2["datenqualitaet"])


def test_objekte_nicht_vermischen(tmp_path):
    (s1, st1), = staende(tmp_path, [2024])
    d, _ = fall_jahr(2025, *JAHRE[2025])
    d["objekt"]["name"] = "Anderes Haus (fiktiv)"
    _, s2, st2 = endgueltig(tmp_path, d, "fremd")
    with pytest.raises(Reportfehler, match="verschiedenen Objekten"):
        bericht([(s1, st1), (s2, st2)], STANDARD)


def test_kostenverlauf_stimmt_mit_abrechnung_ueberein(tmp_path):
    ((stand, status),) = staende(tmp_path, [2025])
    j = bericht([(stand, status)], STANDARD)["jahre"][0]
    erg = stand["ergebnis"]
    assert j["gesamt"] == sum(D(k["gesamt_brutto"]) for k in erg["kontrolle"])
    assert sum(j["lieferanten"].values()) == j["gesamt"] == sum(j["kategorien"].values()) == sum(j["bereiche"].values())
    assert j["zahlungen"]["abrechnungsspitze"] == sum(D(m["saldo_brutto"]) for m in erg["mietverhaeltnisse"])


# --- Snapshots, Freigabe, PDF ------------------------------------------------------------

def test_snapshot_versioniert_und_freigabe(tmp_path):
    ablage, stand, status = endgueltig(tmp_path, fall_vz())
    ra = ReportAblage(ablage)
    inhalt = analysiere(stand, status, STANDARD)
    assert ra.speichern(inhalt, zeit=ZEIT, person="pruefer") == (1, True)
    assert ra.speichern(analysiere(stand, status, STANDARD), zeit=ZEIT, person="pruefer") == (1, False)
    snap = ra.lesen("vorauszahlung", 1)
    assert snap["grundlage"]["version"] == 1 and snap["grundlage"]["ergebnis_hash"] == stand["ergebnis_hash"]
    assert snap["regeln"]["bagatelle_betrag"] == "5.00"
    assert not ra.download_erlaubt("vorauszahlung", 1)
    with pytest.raises(Freigabefehler):
        ra.status_setzen("vorauszahlung", 1, "freigegeben", person="pruefer", zeit=ZEIT)
    ra.status_setzen("vorauszahlung", 1, "fachlich_geprueft", person="pruefer", zeit=ZEIT)
    ra.status_setzen("vorauszahlung", 1, "freigegeben", person="pruefer", zeit=ZEIT)
    assert ra.download_erlaubt("vorauszahlung", 1) and ablage.audit_unverfaelscht()
    konfig = konfiguration_aus_dict({"regeln": {"bagatelle_betrag": "10.00"}})
    assert ra.speichern(analysiere(stand, status, konfig), zeit=ZEIT, person="pruefer") == (2, True)
    assert ra.status("vorauszahlung", 1) == "freigegeben" and ra.status("vorauszahlung", 2) == "entwurf"


def test_report_pdf_und_portal_zeigen_dieselben_werte(tmp_path):
    from betriebskosten.reportausgabe import report_pdf, vorauszahlung_html

    ablage, stand, status = endgueltig(tmp_path, fall_vz())
    ra = ReportAblage(ablage)
    ra.speichern(analysiere(stand, status, STANDARD), zeit=ZEIT, person="pruefer")
    snap = ra.lesen("vorauszahlung", 1)
    html = vorauszahlung_html(snap, "entwurf", "N")
    text = _pdf_text(report_pdf(snap, "entwurf", "N"))
    for wert in ("1.200,00 €", "1.500,00 €", "300,00 €", "125,00 €", "100,00 €", "Vorauszahlung erhöhen", "Nachzahlung",
                 "keine Vertragsänderung", "ENTWURF"):
        assert wert in html and wert in text, wert
    assert report_pdf(snap, "entwurf") == report_pdf(snap, "entwurf")  # byte-identisch
    assert "ENTWURF" not in _pdf_text(report_pdf(snap, "freigegeben", "N"))


def test_kostenverlauf_pdf_mit_diagrammen(tmp_path):
    from betriebskosten.reportausgabe import kostenverlauf_html, report_pdf

    st = staende(tmp_path, [2023, 2024, 2025])
    ablage = Ablage(tmp_path / "ablage2025")
    ra = ReportAblage(ablage)
    ra.speichern(bericht(st, STANDARD), zeit=ZEIT, person="pruefer")
    snap = ra.lesen("kostenverlauf", 1)
    html = kostenverlauf_html(snap, "entwurf")
    assert html.count("<svg") == len(snap["inhalt"]["diagramme"]) == 6
    assert "<script" not in html and "http://" not in html.replace("http://www.w3.org/2000/svg", "")
    text = _pdf_text(report_pdf(snap, "entwurf"))
    for wert in ("4.120,00 €", "3.460,00 €", "660,00 €", "+19,1 %", "Messdienst Beispiel", "Mehrjahresverlauf", "20,60 €"):
        assert wert in html and wert in text, wert


# --- Reproduzierbarkeit ---------------------------------------------------------------

def test_wiederholte_berechnung_ist_identisch(tmp_path):
    from tests.test_betriebskosten import fall_gemischt, fall_gewerbe, fall_wechsel, fall_wohnen

    for fall in (fall_wohnen, fall_gewerbe, fall_wechsel, fall_gemischt, fall_vz):
        a, b = berechne(aus_dict(fall())), berechne(aus_dict(copy.deepcopy(fall())))
        assert kanonisch(als_dict(a)) == kanonisch(als_dict(b))
    ablage = Ablage(tmp_path)
    ablage.speichern(berechne(aus_dict(fall_vz())), zeit=ZEIT, person="pruefer")
    assert ablage.nachrechnen(1, berechne(aus_dict(fall_vz())))
    d = fall_vz()
    d["belege"][0]["positionen"][0]["brutto"] = "6000.01"
    assert not ablage.nachrechnen(1, berechne(aus_dict(d)))


def test_rechenschritte_und_bezugsgroessen_gespeichert(tmp_path):
    from tests.test_betriebskosten import fall_wohnen

    ablage = Ablage(tmp_path)
    ablage.speichern(berechne(aus_dict(fall_wohnen())), zeit=ZEIT, person="pruefer")
    erg = ablage.lesen(1)["ergebnis"]
    assert erg["rundungsregeln"] and erg["regeln_version"] == "2026.11-2"
    k = {x["kostenart"]: x for x in erg["kontrolle"]}
    gs = k["grundsteuer"]["schritte"][0]
    # 50, 70, 80 m² × 365 Tage = 73.000 m²-Tage, Jahresmittel 200 m²
    assert (gs["summe_gewichte"], gs["bezugsgroesse_jahr"], gs["gewicht"]) == ("73000", "200.000000", "m² × Tage")
    assert [(a["empfaenger"], a["gewicht"], a["exakt_brutto"], a["brutto"]) for a in gs["anteile"]] == [
        ("mv:MV1", "18250", "250.000000", "250.00"), ("mv:MV2", "25550", "350.000000", "350.00"), ("mv:MV3", "29200", "400.000000", "400.00")]
    hs = k["hausstrom"]["schritte"][0]["anteile"]
    # 100 / 3 = 33,333333 je Einheit; der Restcent geht an den ersten Empfänger
    assert [(a["exakt_brutto"], a["brutto"], a["restcent"]) for a in hs] == [
        ("33.333333", "33.34", True), ("33.333333", "33.33", False), ("33.333333", "33.33", False)]
    assert k["grundsteuer"]["schritte"][0]["positionen"][0]["lieferant"] == "Stadtkasse"


def test_abrechnungs_pdf_byte_identisch(tmp_path):
    from betriebskosten.ausgabe import schreibe_lauf
    from tests.test_betriebskosten import fall_gewerbe

    a = schreibe_lauf(berechne(aus_dict(fall_gewerbe())), tmp_path / "a")
    b = schreibe_lauf(berechne(aus_dict(fall_gewerbe())), tmp_path / "b")
    assert [p.read_bytes() for p in a] == [p.read_bytes() for p in b]


def test_kein_sprachmodell_im_rechenweg():
    """Die Fachmodule importieren keine KI- oder Netzwerkbibliotheken."""
    import pathlib

    verboten = ("openai", "anthropic", "requests", "httpx", "urllib.request", "langchain", "socket")
    for p in (pathlib.Path(__file__).parent.parent / "betriebskosten").glob("*.py"):
        quelle = p.read_text(encoding="utf-8")
        for v in verboten:
            assert f"import {v}" not in quelle and f"from {v}" not in quelle, (p.name, v)


# --- Befund W3: Nachrechnen und Reportgrundlage prüfen den gespeicherten Inhalt -------

def _datei_aendern(pfad, aendern):
    daten = json.loads(pfad.read_text())
    aendern(daten)
    pfad.write_text(json.dumps(daten))


def test_nachrechnen_erkennt_veraenderten_ergebnisinhalt(tmp_path):
    ablage = Ablage(tmp_path)
    ablage.speichern(berechne(aus_dict(fall_vz())), zeit=ZEIT, person="pruefer")
    assert ablage.nachrechnen(1, berechne(aus_dict(fall_vz())))
    _datei_aendern(tmp_path / "v001.json", lambda s: s["ergebnis"]["mietverhaeltnisse"][0].__setitem__("saldo", "1.00"))
    assert not ablage.nachrechnen(1, berechne(aus_dict(fall_vz())))  # Hash unverändert, Inhalt nicht


def test_veraenderter_stand_ergibt_keinen_report(tmp_path):
    from betriebskosten.jahresreports import main

    ablage, stand, status = endgueltig(tmp_path, fall_vz())
    manipuliert = copy.deepcopy(stand)
    manipuliert["eingabe"]["mietverhaeltnisse"][0]["vz_monatlich"]["betriebskosten"] = "10000.00"
    with pytest.raises(Reportfehler, match="nicht unverfälscht"):
        analysiere(manipuliert, status, STANDARD)
    with pytest.raises(Reportfehler, match="nicht unverfälscht"):
        bericht([(manipuliert, status)], STANDARD)
    # Über die Ablage: Datei verändert → CLI erstellt keinen Report
    _datei_aendern(ablage.pfad / "v001.json",
                   lambda s: s["eingabe"]["mietverhaeltnisse"][0]["vz_monatlich"].__setitem__("betriebskosten", "10000.00"))
    assert main(["vorauszahlung", "--ablage", str(ablage.pfad), "--ausgabe", str(tmp_path / "aus")]) == 1
    assert not (ablage.pfad / "reports" / "vorauszahlung_r001.json").exists()


def test_beschaedigte_auditkette_verhindert_report(tmp_path):
    from betriebskosten.jahresreports import main

    ablage, _, _ = endgueltig(tmp_path, fall_vz())
    zeilen = ablage.audit_pfad.read_text().splitlines()
    zeilen[1] = zeilen[1].replace('"person":"pruefer"', '"person":"jemand"')
    ablage.audit_pfad.write_text("\n".join(zeilen) + "\n")
    assert main(["vorauszahlung", "--ablage", str(ablage.pfad), "--ausgabe", str(tmp_path / "aus")]) == 1
    assert not (tmp_path / "aus").exists() or not list((tmp_path / "aus").glob("*.pdf"))


# --- Befund W2 (Dateiablage): freigegebene Reports vor Ausgabe erneut prüfen ----------

def _freigegebener_report(tmp_path):
    ablage, stand, status = endgueltig(tmp_path, fall_vz())
    ra = ReportAblage(ablage)
    ra.speichern(analysiere(stand, status, STANDARD), zeit=ZEIT, person="pruefer")
    for s in ("fachlich_geprueft", "freigegeben"):
        ra.status_setzen("vorauszahlung", 1, s, person="pruefer", zeit=ZEIT)
    assert ra.download_erlaubt("vorauszahlung", 1)
    return ablage, ra


def test_veraenderter_report_wird_nicht_ausgegeben(tmp_path):
    _, ra = _freigegebener_report(tmp_path)
    pfad = ra.pfad / "vorauszahlung_r001.json"
    _datei_aendern(pfad, lambda s: s["inhalt"]["mietverhaeltnisse"][0].__setitem__("mieter", "MANIPULIERT"))
    assert not ra.download_erlaubt("vorauszahlung", 1)
    with pytest.raises(Freigabefehler, match="nicht unverfälscht"):
        ra.freigegeben("vorauszahlung", 1)


def test_report_mit_neu_berechneter_pruefsumme_faellt_am_auditlog_auf(tmp_path):
    from betriebskosten.reportablage import inhalt_hash

    _, ra = _freigegebener_report(tmp_path)

    def aendern(s):
        s["inhalt"]["mietverhaeltnisse"][0]["mieter"] = "MANIPULIERT"
        s["inhalt_hash"] = inhalt_hash(s["inhalt"])
    _datei_aendern(ra.pfad / "vorauszahlung_r001.json", aendern)
    with pytest.raises(Freigabefehler, match="Audit-Log"):
        ra.freigegeben("vorauszahlung", 1)


def test_beschaedigte_auditkette_sperrt_reportfreigabe(tmp_path):
    ablage, stand, status = endgueltig(tmp_path, fall_vz())
    ra = ReportAblage(ablage)
    ra.speichern(analysiere(stand, status, STANDARD), zeit=ZEIT, person="pruefer")
    zeilen = ablage.audit_pfad.read_text().splitlines()
    zeilen[0] = zeilen[0].replace('"person":"pruefer"', '"person":"jemand"')
    ablage.audit_pfad.write_text("\n".join(zeilen) + "\n")
    with pytest.raises(Freigabefehler, match="Audit-Log ist beschädigt"):
        ra.status_setzen("vorauszahlung", 1, "fachlich_geprueft", person="pruefer", zeit=ZEIT)


# --- Befund W5: verkürztes Abrechnungsjahr --------------------------------------------

def fall_kurzjahr():
    d = fall_vz()
    d["abrechnung"] = {"von": date(2025, 7, 1), "bis": date(2025, 12, 31)}
    d["mietverhaeltnisse"] = [m for m in d["mietverhaeltnisse"] if m["id"] != "A"]
    md = d["messdienst"][0]
    md["werte"] = [w for w in md["werte"] if w.get("mietverhaeltnis") != "A"]
    md["gesamt_brutto"] = "1880.00"
    return d


def test_verkuerztes_abrechnungsjahr_ohne_empfehlung(tmp_path):
    from betriebskosten.vorauszahlung import volles_jahr

    r = report_vz(tmp_path, fall_kurzjahr())
    for m in r["mietverhaeltnisse"]:
        assert m["nutzungstage"] == 184
        for a in m["arten"]:
            assert a["empfehlung"] == "keine" and a["vorschlag_monatlich"] is None, (m["id"], a["art"])
            assert "Verkürztes Abrechnungsjahr (184 Tage)" in a["begruendung"]
    # Kosten bleiben unverändert aus der Abrechnung, keine Hochrechnung als belastbare Empfehlung
    for m in r["mietverhaeltnisse"]:
        assert m["summe"]["abrechnungsspitze"] == m["abgleich_saldo"]
    assert not volles_jahr({"von": "2025-07-01", "bis": "2025-12-31"})


def test_volles_jahr_schaltjahr_und_abweichendes_wirtschaftsjahr():
    from betriebskosten.vorauszahlung import volles_jahr

    assert volles_jahr({"von": "2025-01-01", "bis": "2025-12-31"})
    assert volles_jahr({"von": "2024-01-01", "bis": "2024-12-31"})  # 366 Tage
    assert volles_jahr({"von": "2024-07-01", "bis": "2025-06-30"})
    assert volles_jahr({"von": "2024-02-29", "bis": "2025-02-28"})
    assert not volles_jahr({"von": "2025-01-01", "bis": "2025-12-30"})
    assert not volles_jahr({"von": "2024-01-01", "bis": "2025-01-01"})


def test_schaltjahr_mit_mieterwechsel_bleibt_empfehlungsfaehig(tmp_path):
    d = fall_vz()
    d["abrechnung"] = {"von": date(2024, 1, 1), "bis": date(2024, 12, 31)}
    for m in d["mietverhaeltnisse"]:
        if m["id"] == "A":
            m["bis"] = date(2024, 6, 30)
        if m["id"] == "E":
            m["von"] = date(2024, 7, 1)
    r = report_vz(tmp_path, d)
    bk = art(r, "N", "betriebskosten")
    # volles Jahr mit 366 Tagen: 1.500 × 366 / (366 × 12) = 125,00
    assert (bk["monatskosten"], bk["empfehlung"]) == ("125.00", "erhoehen")
    e = art(r, "E", "betriebskosten")
    assert e["empfehlung"] != "keine" and "Verkürztes" not in e["begruendung"]


# --- Byte-Wiederholung auch für den Kostenverlaufsbericht -----------------------------

def test_kostenverlauf_pdf_byte_identisch(tmp_path):
    from betriebskosten.reportausgabe import report_pdf

    pdfs = []
    for lauf in ("a", "b"):
        st = staende(tmp_path / lauf, [2023, 2024, 2025])
        ra = ReportAblage(Ablage(tmp_path / lauf / "ablage2025"))
        ra.speichern(bericht(st, STANDARD), zeit=ZEIT, person="pruefer")
        snap = ra.pruefen("kostenverlauf", 1)
        pdfs.append((snap["inhalt_hash"], report_pdf(snap, "freigegeben")))
    assert pdfs[0][0] == pdfs[1][0]
    assert pdfs[0][1] == pdfs[1][1]


# --- N1 (Nachprüfung 02.11.2026): Freigabestufen über den letzten Audit-Eintrag ------------

def _letzte_zeile(ablage, alt, neu):
    zeilen = ablage.audit_pfad.read_text().splitlines()
    assert alt in zeilen[-1]
    zeilen[-1] = zeilen[-1].replace(alt, neu)
    ablage.audit_pfad.write_text("\n".join(zeilen) + "\n")


def test_n1_gegenprobe_1_entwurf_als_endgueltig_ergibt_keinen_report(tmp_path):
    from betriebskosten.jahresreports import _endgueltig, main

    ablage = Ablage(tmp_path / "ablage")
    ablage.speichern(berechne(aus_dict(fall_vz())), zeit=ZEIT, person="pruefer")
    assert [e["ereignis"] for e in ablage.audit()] == ["berechnet", "status"]
    _letzte_zeile(ablage, '"status":"entwurf"', '"status":"endgueltig"')  # kein Hash neu gebildet
    assert not ablage.audit_unverfaelscht()
    with pytest.raises(Reportfehler):
        _endgueltig(ablage, None)
    with pytest.raises(Reportfehler):
        _endgueltig(ablage, 1)
    assert main(["vorauszahlung", "--ablage", str(ablage.pfad), "--ausgabe", str(tmp_path / "aus")]) == 1
    assert not (ablage.pfad / "reports" / "vorauszahlung_r001.json").exists()


def test_n1_konsistent_verketteter_sprung_auf_endgueltig_ergibt_keinen_report(tmp_path):
    from betriebskosten.jahresreports import _endgueltig, main

    ablage = Ablage(tmp_path / "ablage")
    ablage.speichern(berechne(aus_dict(fall_vz())), zeit=ZEIT, person="pruefer")
    ablage._audit({"ereignis": "status", "version": 1, "status": "endgueltig", "person": "pruefer", "zeit": ZEIT.isoformat()})
    assert ablage.audit_unverfaelscht()  # Kette und Kettenende korrekt, aber Stufen übersprungen
    with pytest.raises(Reportfehler, match="unzulässig|keine endgültige"):
        _endgueltig(ablage, None)
    with pytest.raises(Reportfehler, match="unzulässig"):
        _endgueltig(ablage, 1)
    assert main(["vorauszahlung", "--ablage", str(ablage.pfad), "--ausgabe", str(tmp_path / "aus")]) == 1


def test_n1_gegenprobe_2_report_entwurf_als_freigegeben_kein_download(tmp_path):
    ablage, stand, status = endgueltig(tmp_path, fall_vz())
    ra = ReportAblage(ablage)
    ra.speichern(analysiere(stand, status, STANDARD), zeit=ZEIT, person="pruefer")
    assert ra.status("vorauszahlung", 1) == "entwurf"
    _letzte_zeile(ablage, '"status":"entwurf"', '"status":"freigegeben"')
    assert not ablage.audit_unverfaelscht()
    assert not ra.download_erlaubt("vorauszahlung", 1)
    with pytest.raises(Freigabefehler):
        ra.freigegeben("vorauszahlung", 1)
    with pytest.raises(Freigabefehler):
        ra.status_setzen("vorauszahlung", 1, "fachlich_geprueft", person="pruefer", zeit=ZEIT)


@pytest.mark.parametrize("folge", [
    ["freigegeben"],                                   # Prüfung übersprungen
    ["fachlich_geprueft", "fachlich_geprueft"],        # doppelt
    ["fachlich_geprueft", "entwurf"],                  # rückwärts
    ["endgueltig"],                                    # unbekannte Stufe
])
def test_n1_konsistent_verkettete_unzulaessige_reportfolgen_sperren(tmp_path, folge):
    ablage, stand, status = endgueltig(tmp_path, fall_vz())
    ra = ReportAblage(ablage)
    ra.speichern(analysiere(stand, status, STANDARD), zeit=ZEIT, person="pruefer")
    for s in folge:
        ablage._audit({"ereignis": "report_status", "report": "vorauszahlung", "nummer": 1, "status": s,
                       "person": "pruefer", "zeit": ZEIT.isoformat()})
    assert ablage.audit_unverfaelscht()
    assert not ra.download_erlaubt("vorauszahlung", 1)
    with pytest.raises(Freigabefehler, match="unzulässig"):
        ra.pruefen("vorauszahlung", 1)


def test_n1_normale_report_freigabe_bleibt_moeglich(tmp_path):
    from betriebskosten.jahresreports import main

    ablage, _, _ = endgueltig(tmp_path, fall_vz())
    assert main(["vorauszahlung", "--ablage", str(ablage.pfad), "--ausgabe", str(tmp_path / "aus"), "--person", "pruefer"]) == 0
    ra = ReportAblage(ablage)
    assert not ra.download_erlaubt("vorauszahlung", 1)
    for s in ("fachlich_geprueft", "freigegeben"):
        ra.status_setzen("vorauszahlung", 1, s, person="pruefer", zeit=ZEIT)
    assert ra.download_erlaubt("vorauszahlung", 1) and ablage.audit_unverfaelscht()
