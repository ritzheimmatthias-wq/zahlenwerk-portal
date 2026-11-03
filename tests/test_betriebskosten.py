"""Betriebskostenabrechnungsgenerator: Soll/Ist-Abgleich mit ausschließlich künstlichen Daten.

Die Sollwerte sind von Hand nachgerechnet (siehe Kommentare). Abrechnungsjahr 2025 hat 365 Tage.
"""
import copy
import json
from datetime import date, datetime
from decimal import Decimal as D

import pytest

from betriebskosten.eingabe import Eingabefehler, aus_dict
from betriebskosten.rechnen import berechne, verteile
from betriebskosten.version import Ablage, Freigabefehler

BESTAETIGT = {"bestaetigt_von": "Steuerberatung Beispiel (fiktiv)", "bestaetigt_am": date(2025, 11, 30)}


def basis(**extra):
    d = {
        "objekt": {"name": "Musterhaus (fiktiv)", "anschrift": "Beispielweg 1, 12345 Musterstadt",
                   "vermieter": "Erika Eigentümerin (fiktiv)", "vermieter_anschrift": "Postfach 1, 12345 Musterstadt"},
        "abrechnung": {"von": date(2025, 1, 1), "bis": date(2025, 12, 31)},
    }
    d.update(extra)
    return d


def codes(erg, stufe=None):
    return sorted({w.code for w in erg.warnungen if stufe is None or w.stufe == stufe})


def ka(id, schluessel, pool="alle", heiz=False, bezeichnung=None):
    return {"id": id, "bezeichnung": bezeichnung or id.capitalize(), "schluessel": schluessel, "pool": pool,
            "umlagefaehig_bestaetigt": True, "heizkosten": heiz}


# --- Fall 1: reiner Wohnfall ---------------------------------------------------------

def fall_wohnen():
    return basis(
        einheiten=[
            {"id": "WE1", "abschnitte": [{"nutzung": "wohnen", "flaeche": "50"}]},
            {"id": "WE2", "abschnitte": [{"nutzung": "wohnen", "flaeche": "70"}]},
            {"id": "WE3", "abschnitte": [{"nutzung": "wohnen", "flaeche": "80"}]},
        ],
        mietverhaeltnisse=[
            {"id": "MV1", "einheit": "WE1", "mieter": "Anna Beispiel", "personen": 1, "verbrauch": {"wasser": "20"},
             "vorauszahlungen": {"betriebskosten": "600.00", "heizkosten": "400.00"}},
            {"id": "MV2", "einheit": "WE2", "mieter": "Bernd Muster", "personen": 2, "verbrauch": {"wasser": "30"},
             "vorauszahlungen": {"betriebskosten": "700.00", "heizkosten": "500.00"}},
            {"id": "MV3", "einheit": "WE3", "mieter": "Carla Probe", "personen": 3, "verbrauch": {"wasser": "50"},
             "vorauszahlungen": {"betriebskosten": "900.00", "heizkosten": "500.00"}},
        ],
        kostenarten=[ka("grundsteuer", "flaeche"), ka("muell", "personen"), ka("hausstrom", "einheiten"),
                     ka("wasser", "verbrauch"), ka("heizung", "messdienst", heiz=True)],
        belege=[
            {"id": "B1", "lieferant": "Stadtkasse", "rechnungsnummer": "GS-2025", "sha256": "a" * 64,
             "positionen": [{"kostenart": "grundsteuer", "brutto": "1000.00", "netto": "1000.00", "ust": "0.00"}]},
            {"id": "B2", "lieferant": "Entsorgung GmbH", "rechnungsnummer": "M-1", "sha256": "b" * 64,
             "positionen": [{"kostenart": "muell", "brutto": "600.00"}]},
            {"id": "B3", "lieferant": "Stromwerk", "rechnungsnummer": "S-1", "sha256": "c" * 64,
             "positionen": [{"kostenart": "hausstrom", "brutto": "100.00"}]},
            {"id": "B4", "lieferant": "Wasserwerk", "rechnungsnummer": "W-1", "sha256": "d" * 64,
             "positionen": [{"kostenart": "wasser", "brutto": "300.00"}]},
        ],
        messdienst=[{"id": "MD1", "anbieter": "Messdienst Beispiel", "gesamt_brutto": "1500.00", "sha256": "e" * 64,
                     "werte": [{"einheit": "WE1", "kostenart": "heizung", "brutto": "400.00"},
                               {"einheit": "WE2", "kostenart": "heizung", "brutto": "500.00"},
                               {"einheit": "WE3", "kostenart": "heizung", "brutto": "600.00"}]}],
    )


def test_wohnfall_soll_ist():
    erg = berechne(aus_dict(fall_wohnen()))
    assert erg.warnungen == []
    soll = {
        # Grundsteuer 1000 nach 50/70/80 m², Müll 600 nach 1/2/3 Personen, Hausstrom 100 nach Einheiten
        # (größter Rest geht an MV1), Wasser 300 nach 20/30/50 m³, Heizung laut Messdienst
        "MV1": ({"grundsteuer": "250.00", "muell": "100.00", "hausstrom": "33.34", "wasser": "60.00", "heizung": "400.00"}, "843.34", "1000.00", "-156.66"),
        "MV2": ({"grundsteuer": "350.00", "muell": "200.00", "hausstrom": "33.33", "wasser": "90.00", "heizung": "500.00"}, "1173.33", "1200.00", "-26.67"),
        "MV3": ({"grundsteuer": "400.00", "muell": "300.00", "hausstrom": "33.33", "wasser": "150.00", "heizung": "600.00"}, "1483.33", "1400.00", "83.33"),
    }
    for mid, (zeilen, kosten, vz, saldo) in soll.items():
        m = erg.mv(mid)
        assert m.darstellung == "wohnen"
        assert {z.kostenart: str(z.betrag) for z in m.zeilen} == zeilen
        assert (str(m.kosten), str(m.vz), str(m.saldo)) == (kosten, vz, saldo)
        assert m.kosten_ust == m.vz_ust == D("0.00") and m.saldo_brutto == m.saldo
    assert sum(m.kosten for m in erg.mietverhaeltnisse) == D("3500.00")  # = alle Belege + Messdienst
    assert all(k.verteilt_brutto == k.gesamt_brutto for k in erg.kontrolle)
    # Bezugsgrößen für die Darstellung
    g = next(z for z in erg.mv("MV1").zeilen if z.kostenart == "grundsteuer")
    assert (g.bezug_gesamt, g.bezug_anteil) == (D("200"), D("50"))


def test_rueckverfolgung_bis_beleg_und_messwert():
    erg = berechne(aus_dict(fall_wohnen()))
    zeilen = {z.kostenart: z for z in erg.mv("MV2").zeilen}
    assert zeilen["grundsteuer"].herkunft == [{"beleg": "B1", "sha256": "a" * 64, "lieferant": "Stadtkasse",
                                               "rechnungsnummer": "GS-2025", "brutto": "1000.00"}]
    assert zeilen["heizung"].herkunft[0]["messdienst"] == "MD1" and zeilen["heizung"].herkunft[0]["sha256"] == "e" * 64


# --- Fall 2: Gewerbe mit konfigurierter Umsatzsteuer ---------------------------------

def fall_gewerbe(steuer=None):
    steuer = steuer if steuer is not None else {"behandlung": "steuerpflichtig", "satz": "0.19", "kostenbasis": "netto", **BESTAETIGT}
    gewerbe = {"id": "G1", "einheit": "GE", "mieter": "Laden Beispiel GmbH (fiktiv)",
               "vorauszahlungen": {"betriebskosten": {"brutto": "2142.00", "netto": "1800.00", "ust": "342.00"}}}
    if steuer:
        gewerbe["steuer"] = steuer
    return basis(
        einheiten=[{"id": "WE", "abschnitte": [{"nutzung": "wohnen", "flaeche": "100"}]},
                   {"id": "GE", "abschnitte": [{"nutzung": "gewerbe", "flaeche": "100"}]}],
        mietverhaeltnisse=[
            {"id": "W1", "einheit": "WE", "mieter": "Dora Wohnt", "vorauszahlungen": {"betriebskosten": "1500.00"}},
            gewerbe,
        ],
        kostenarten=[ka("hausmeister", "flaeche"), ka("grundsteuer", "flaeche"), ka("gewerbemuell", "einheiten", pool="gewerbe")],
        belege=[
            {"id": "H1", "lieferant": "Hausmeister Muster", "rechnungsnummer": "H-1",
             "positionen": [{"kostenart": "hausmeister", "brutto": "2380.00", "netto": "2000.00", "ust": "380.00", "satz": "0.19"}]},
            {"id": "G1B", "lieferant": "Stadtkasse", "rechnungsnummer": "GS-1",
             "positionen": [{"kostenart": "grundsteuer", "brutto": "800.00", "netto": "800.00", "ust": "0.00"}]},
            {"id": "M1", "lieferant": "Gewerbeentsorgung", "rechnungsnummer": "GM-1",
             "positionen": [{"kostenart": "gewerbemuell", "brutto": "595.00", "netto": "500.00", "ust": "95.00", "satz": "0.19"}]},
        ],
    )


def test_gewerbe_mit_umsatzsteuer_soll_ist():
    erg = berechne(aus_dict(fall_gewerbe()))
    assert codes(erg) == []
    w = erg.mv("W1")
    # Wohnung: Bruttoanteile 1190 + 400, keine Gewerbekosten
    assert w.darstellung == "wohnen" and str(w.kosten) == "1590.00" and str(w.saldo) == "90.00"
    assert [z.kostenart for z in w.zeilen] == ["hausmeister", "grundsteuer"]
    g = erg.mv("G1")
    assert g.darstellung == "gewerbe_netto" and g.ust_satz == D("0.19")
    assert {z.kostenart: (str(z.betrag), str(z.ust)) for z in g.zeilen} == {
        "hausmeister": ("1000.00", "190.00"), "grundsteuer": ("400.00", "76.00"), "gewerbemuell": ("500.00", "95.00")}
    assert (str(g.kosten), str(g.kosten_ust), str(g.kosten_brutto)) == ("1900.00", "361.00", "2261.00")
    assert (str(g.vz), str(g.vz_ust), str(g.vz_brutto)) == ("1800.00", "342.00", "2142.00")
    assert (str(g.saldo), str(g.saldo_ust), str(g.saldo_brutto)) == ("100.00", "19.00", "119.00")
    assert str(g.vorsteueranteil_info) == "285.00"  # 190 + 0 + 95, nur Information


def test_gewerbe_ohne_ust_auf_einzelne_kostenarten():
    steuer = {"behandlung": "steuerpflichtig", "satz": "0.19", "kostenbasis": "netto", "ohne_ust": ["grundsteuer"], **BESTAETIGT}
    g = berechne(aus_dict(fall_gewerbe(steuer))).mv("G1")
    assert str(g.kosten_ust) == "285.00" and str(g.saldo_ust) == "-57.00"


def test_gewerbe_steuerfrei_wird_brutto_abgerechnet():
    steuer = {"behandlung": "steuerfrei", **BESTAETIGT}
    d = fall_gewerbe(steuer)
    d["mietverhaeltnisse"][1]["vorauszahlungen"] = {"betriebskosten": "2000.00"}
    g = berechne(aus_dict(d)).mv("G1")
    assert g.darstellung == "gewerbe_brutto" and g.kosten_ust == D("0.00")
    assert str(g.kosten) == "2185.00"  # 1190 + 400 + 595


@pytest.mark.parametrize("steuer,code", [
    ({}, "steuer_ungeklaert"),
    ({"behandlung": "ungeklaert"}, "steuer_ungeklaert"),
    ({"behandlung": "steuerpflichtig", "satz": "0.19"}, "steuer_unbestaetigt"),
    ({"behandlung": "steuerpflichtig", **BESTAETIGT}, "steuersatz_fehlt"),
])
def test_gewerbe_ohne_bestaetigte_steuer_ist_gesperrt(steuer, code):
    erg = berechne(aus_dict(fall_gewerbe(steuer)))
    assert erg.mv("G1").darstellung == "gesperrt"
    assert code in codes(erg, "rot")
    assert erg.mv("W1").darstellung == "wohnen"  # die Wohnung bleibt berechenbar


def test_wohnung_mit_umsatzsteuer_ist_rot():
    d = fall_gewerbe()
    d["mietverhaeltnisse"][0]["steuer"] = {"behandlung": "steuerpflichtig", "satz": "0.19", **BESTAETIGT}
    erg = berechne(aus_dict(d))
    assert "wohnen_mit_ust" in codes(erg, "rot") and erg.mv("W1").kosten_ust == D("0.00")


# --- Fall 3: Nutzerwechsel mit Leerstand ---------------------------------------------

def fall_wechsel():
    return basis(
        einheiten=[{"id": "WE1", "abschnitte": [{"nutzung": "wohnen", "flaeche": "60"}]},
                   {"id": "WE2", "abschnitte": [{"nutzung": "wohnen", "flaeche": "40"}]}],
        mietverhaeltnisse=[
            {"id": "A", "einheit": "WE1", "mieter": "Alt Mieter", "bis": date(2025, 3, 31),
             "vorauszahlungen": {"betriebskosten": "150.00", "heizkosten": "60.00"}},
            {"id": "B", "einheit": "WE1", "mieter": "Neu Mieterin", "von": date(2025, 5, 1),
             "vorauszahlungen": {"betriebskosten": "400.00", "heizkosten": "160.00"}},
            {"id": "C", "einheit": "WE2", "mieter": "Bleibt Wohnen", "vorauszahlungen": {"betriebskosten": "500.00", "heizkosten": "200.00"}},
        ],
        kostenarten=[ka("grundsteuer", "flaeche"), ka("hausstrom", "einheiten"), ka("heizung", "messdienst", heiz=True)],
        belege=[{"id": "S1", "positionen": [{"kostenart": "grundsteuer", "brutto": "365.00"}]},
                {"id": "S2", "positionen": [{"kostenart": "hausstrom", "brutto": "730.00"}]}],
        messdienst=[{"id": "MD", "gesamt_brutto": "565.00", "werte": [
            {"einheit": "WE1", "kostenart": "heizung", "brutto": "365.00"},
            {"einheit": "WE2", "kostenart": "heizung", "brutto": "200.00", "mietverhaeltnis": "C"}]}],
    )


def test_nutzerwechsel_soll_ist():
    erg = berechne(aus_dict(fall_wechsel()))
    # A: 90 Tage, Leerstand April: 30 Tage, B: 245 Tage. Grundsteuer-Gewichte 60×90, 60×30, 60×245, 40×365 = 36.500
    a, b, c = erg.mv("A"), erg.mv("B"), erg.mv("C")
    assert (a.nutzungszeitraum.tage, b.nutzungszeitraum.tage) == (90, 245)
    assert {z.kostenart: str(z.betrag) for z in a.zeilen} == {"grundsteuer": "54.00", "hausstrom": "90.00", "heizung": "90.00"}
    assert {z.kostenart: str(z.betrag) for z in b.zeilen} == {"grundsteuer": "147.00", "hausstrom": "245.00", "heizung": "245.00"}
    assert {z.kostenart: str(z.betrag) for z in c.zeilen} == {"grundsteuer": "146.00", "hausstrom": "365.00", "heizung": "200.00"}
    assert (str(a.kosten), str(a.saldo)) == ("234.00", "24.00")
    assert (str(b.kosten), str(b.saldo)) == ("637.00", "77.00")
    assert erg.leerstand == {"WE1": D("78.00")}  # 18 + 30 + 30 trägt der Eigentümer
    assert codes(erg) == ["zwischenablesung_fehlt"]
    assert all(w.stufe == "gelb" for w in erg.warnungen)


def test_wechsel_aendert_nur_den_betroffenen_zeitraum():
    ohne = berechne(aus_dict(fall_wechsel()))
    d = fall_wechsel()
    d["mietverhaeltnisse"][1]["von"] = date(2025, 4, 1)  # Leerstand entfällt
    mit = berechne(aus_dict(d))
    assert [str(z.betrag) for z in mit.mv("A").zeilen] == [str(z.betrag) for z in ohne.mv("A").zeilen]
    assert [str(z.betrag) for z in mit.mv("C").zeilen] == [str(z.betrag) for z in ohne.mv("C").zeilen]
    assert mit.leerstand == {}


def test_ueberschneidende_mietverhaeltnisse_rot():
    d = fall_wechsel()
    d["mietverhaeltnisse"][1]["von"] = date(2025, 3, 15)
    assert "ueberschneidung" in codes(berechne(aus_dict(d)), "rot")


# --- Fall 4: gemischt genutztes Objekt mit Teilung der Gewerbefläche ----------------

def fall_gemischt(von=date(2025, 1, 1), bis=date(2025, 12, 31)):
    return basis(
        abrechnung={"von": von, "bis": bis},
        einheiten=[
            {"id": "WE1", "abschnitte": [{"von": date(2020, 1, 1), "bis": date(2030, 12, 31), "nutzung": "wohnen", "flaeche": "100"}]},
            {"id": "GE", "abschnitte": [
                {"von": date(2020, 1, 1), "bis": date(2025, 6, 30), "nutzung": "gewerbe", "flaeche": "150"},
                {"von": date(2025, 7, 1), "bis": date(2030, 12, 31), "nutzung": "gewerbe", "flaeche": "90"}]},
            {"id": "WE2", "abschnitte": [{"von": date(2025, 7, 1), "bis": date(2030, 12, 31), "nutzung": "wohnen", "flaeche": "60"}]},
        ],
        mietverhaeltnisse=[
            {"id": "W1", "einheit": "WE1", "mieter": "Wohnt Lange", "von": date(2020, 1, 1), "bis": date(2030, 12, 31),
             "vorauszahlungen": {"betriebskosten": "1800.00"}},
            {"id": "G", "einheit": "GE", "mieter": "Praxis Beispiel (fiktiv)", "von": date(2020, 1, 1), "bis": date(2030, 12, 31),
             "steuer": {"behandlung": "steuerfrei", **BESTAETIGT}, "vorauszahlungen": {"betriebskosten": "2000.00"}},
            {"id": "W2", "einheit": "WE2", "mieter": "Zieht Neu Ein", "von": date(2025, 7, 1), "bis": date(2030, 12, 31),
             "vorauszahlungen": {"betriebskosten": "600.00"}},
        ],
        kostenarten=[ka("grundsteuer", "flaeche"), ka("treppenhaus", "einheiten", pool="wohnen"),
                     ka("gewerbemuell", "einheiten", pool="gewerbe")],
        belege=[{"id": "X1", "positionen": [{"kostenart": "grundsteuer", "brutto": "3650.00"}]},
                {"id": "X2", "positionen": [{"kostenart": "treppenhaus", "brutto": "549.00"}]},
                {"id": "X3", "positionen": [{"kostenart": "gewerbemuell", "brutto": "300.00"}]}],
    )


def test_gemischtes_objekt_mit_teilung_soll_ist():
    erg = berechne(aus_dict(fall_gemischt()))
    assert erg.warnungen == []
    # Gewichte Fläche×Tage: W1 100×365 = 36.500; G 150×181 + 90×184 = 43.710; W2 60×184 = 11.040; Summe 91.250
    soll = {"W1": {"grundsteuer": "1460.00", "treppenhaus": "365.00"},
            "G": {"grundsteuer": "1748.40", "gewerbemuell": "300.00"},
            "W2": {"grundsteuer": "441.60", "treppenhaus": "184.00"}}
    for mid, zeilen in soll.items():
        assert {z.kostenart: str(z.betrag) for z in erg.mv(mid).zeilen} == zeilen
    assert erg.mv("G").darstellung == "gewerbe_brutto"
    assert (str(erg.mv("W1").saldo), str(erg.mv("G").saldo), str(erg.mv("W2").saldo)) == ("25.00", "48.40", "25.60")
    # Wohn- und Gewerbepools bleiben getrennt
    assert "gewerbemuell" not in {z.kostenart for z in erg.mv("W1").zeilen + erg.mv("W2").zeilen}
    assert "treppenhaus" not in {z.kostenart for z in erg.mv("G").zeilen}


def test_teilung_veraendert_fruehere_jahre_nicht():
    """2024 wird mit und ohne die 2025 eingetragene Teilung identisch berechnet."""
    mit = fall_gemischt(date(2024, 1, 1), date(2024, 12, 31))
    ohne = copy.deepcopy(mit)
    ohne["einheiten"][1]["abschnitte"] = [{"von": date(2020, 1, 1), "bis": date(2030, 12, 31), "nutzung": "gewerbe", "flaeche": "150"}]
    ohne["einheiten"] = ohne["einheiten"][:2]
    ohne["mietverhaeltnisse"] = ohne["mietverhaeltnisse"][:2]
    a, b = berechne(aus_dict(mit)), berechne(aus_dict(ohne))
    for mid in ("W1", "G"):
        assert [(z.kostenart, z.betrag) for z in a.mv(mid).zeilen] == [(z.kostenart, z.betrag) for z in b.mv(mid).zeilen]
    assert "W2" not in {m.id for m in a.mietverhaeltnisse}
    # 2024 (366 Tage): 3650 × 100/250 = 1460, 3650 × 150/250 = 2190
    assert str(a.mv("G").zeilen[0].betrag) == "2190.00"


# --- Prüfungen ----------------------------------------------------------------------

def test_verteilung_summiert_immer_exakt():
    for betrag in (D("100.00"), D("0.01"), D("999.99"), D("-10.00")):
        v = verteile(betrag, {"a": D(1), "b": D(1), "c": D(1)}, ["a", "b", "c"])
        assert sum(v.values()) == betrag


def test_belegpruefungen():
    d = fall_wohnen()
    d["belege"].append({"id": "B5", "lieferant": "Stromwerk", "rechnungsnummer": "S-1", "sha256": "c" * 64,
                        "positionen": [{"kostenart": "hausstrom", "brutto": "100.00"}]})
    d["belege"].append({"id": "B6", "positionen": [{"kostenart": "hausstrom", "brutto": "119.00", "netto": "100.00", "ust": "20.00"}]})
    d["belege"].append({"id": "B7", "leistung_von": date(2024, 1, 1), "leistung_bis": date(2024, 12, 31),
                        "positionen": [{"kostenart": "hausstrom", "brutto": "50.00"}]})
    d["belege"].append({"id": "B8", "positionen": [{"kostenart": "aufzug", "brutto": "50.00"}]})
    erg = berechne(aus_dict(d))
    assert {"dublette_datei", "dublette_rechnung", "summe_beleg", "zeitraum_ausserhalb", "kostenart_unbekannt"} <= set(codes(erg, "rot"))


def test_abgrenzung_ueber_jahresgrenze():
    d = fall_wohnen()
    d["belege"][2].update({"leistung_von": date(2024, 7, 1), "leistung_bis": date(2025, 6, 30), "abgrenzen": True})
    d["belege"][2]["positionen"][0]["brutto"] = "182.00"  # 181 von 365 Tagen in 2025 → 182 × 181 / 365 = 90,25
    erg = berechne(aus_dict(d))
    k = next(k for k in erg.kontrolle if k.kostenart == "hausstrom")
    assert k.gesamt_brutto == D("90.25") and k.verteilt_brutto == D("90.25")
    d["belege"][2]["abgrenzen"] = False
    assert "zeitraum_teilweise" in codes(berechne(aus_dict(d)), "gelb")


def test_messdienst_summe_und_umlage_unbestaetigt():
    d = fall_wohnen()
    d["messdienst"][0]["gesamt_brutto"] = "1600.00"
    d["kostenarten"][0]["umlagefaehig_bestaetigt"] = False
    assert {"messdienst_summe", "umlage_unbestaetigt"} <= set(codes(berechne(aus_dict(d)), "rot"))


def test_vorjahresabweichung_gelb():
    d = fall_wohnen()
    d["abrechnung"]["vorjahr"] = {"grundsteuer": "700.00"}
    assert "vorjahr" in codes(berechne(aus_dict(d)), "gelb")


def test_betraege_nie_als_fliesskomma():
    d = fall_wohnen()
    d["belege"][0]["positionen"][0]["brutto"] = 1000.0
    with pytest.raises(Eingabefehler):
        aus_dict(d)


# --- Versionen, Status, Audit-Log ---------------------------------------------------

ZEIT = datetime(2026, 11, 2, 12, 0)


def test_versionen_unveraenderlich_und_freigabefolge(tmp_path):
    ablage = Ablage(tmp_path)
    erg = berechne(aus_dict(fall_wohnen()))
    assert ablage.speichern(erg, zeit=ZEIT, person="pruefer") == (1, True)
    assert ablage.speichern(erg, zeit=ZEIT, person="pruefer") == (1, False)  # gleiche Eingabe, keine neue Version
    inhalt = (tmp_path / "v001.json").read_text()
    with pytest.raises(Freigabefehler):
        ablage.status_setzen(1, "endgueltig", person="pruefer", zeit=ZEIT)  # Stufen nicht überspringen
    for status in ("fachlich_geprueft", "eigentuemer_freigegeben", "endgueltig"):
        ablage.status_setzen(1, status, person="pruefer", zeit=ZEIT)
    assert ablage.status(1) == "endgueltig"
    # Datenänderung erzeugt Version 2; Version 1 bleibt unverändert und endgültig
    d = fall_wohnen()
    d["belege"][2]["positionen"][0]["brutto"] = "110.00"
    assert ablage.speichern(berechne(aus_dict(d)), zeit=ZEIT, person="pruefer") == (2, True)
    assert (tmp_path / "v001.json").read_text() == inhalt and ablage.status(1) == "endgueltig"
    assert ablage.status(2) == "entwurf"
    assert ablage.audit_unverfaelscht()
    assert json.loads(inhalt)["ergebnis"]["mietverhaeltnisse"][0]["saldo"] == "-156.66"


def test_rote_warnung_sperrt_freigabe(tmp_path):
    ablage = Ablage(tmp_path)
    v, _ = ablage.speichern(berechne(aus_dict(fall_gewerbe({}))), zeit=ZEIT, person="pruefer")
    with pytest.raises(Freigabefehler, match="Rote Warnungen"):
        ablage.status_setzen(v, "fachlich_geprueft", person="pruefer", zeit=ZEIT)


def test_audit_log_erkennt_manipulation(tmp_path):
    ablage = Ablage(tmp_path)
    ablage.speichern(berechne(aus_dict(fall_wohnen())), zeit=ZEIT, person="pruefer")
    ablage.status_setzen(1, "fachlich_geprueft", person="pruefer", zeit=ZEIT)
    zeilen = ablage.audit_pfad.read_text().splitlines()
    zeilen[1] = zeilen[1].replace("entwurf", "endgueltig")
    ablage.audit_pfad.write_text("\n".join(zeilen) + "\n")
    assert not ablage.audit_unverfaelscht()


# --- Ausgabe: getrennte Pfade Wohnen und Gewerbe -------------------------------------

def _pdf_text(pdf: bytes) -> str:
    import io

    from pypdf import PdfReader

    return " ".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)


def test_wohnraum_pdf_ohne_steuerangaben():
    from betriebskosten.ausgabe import abrechnung_html, als_pdf

    erg = berechne(aus_dict(fall_gewerbe()))
    html = abrechnung_html(erg, erg.mv("W1"), version=3)
    for wort in ("Umsatzsteuer", "USt", "Netto", "netto", "Gewerbemüll", "Gewerbemuell"):
        assert wort not in html
    text = _pdf_text(als_pdf(html))
    assert "Betriebskostenabrechnung 2025" in text and "Nachzahlung" in text and "90,00 €" in text
    assert "ENTWURF · Version 3 · nicht versenden" in text
    assert "H1 Hausmeister Muster H-1" in text  # Belegverzeichnis


def test_gewerbe_pdf_mit_netto_ust_brutto():
    from betriebskosten.ausgabe import abrechnung_html, als_pdf

    erg = berechne(aus_dict(fall_gewerbe()))
    text = _pdf_text(als_pdf(abrechnung_html(erg, erg.mv("G1"))))
    assert "Nebenkostenabrechnung Gewerbe" in text
    for wert in ("1.900,00 €", "361,00 €", "2.261,00 €", "1.800,00 €", "342,00 €", "2.142,00 €", "119,00 €", "Umsatzsteuer 19 %"):
        assert wert in text, wert


def test_gesperrte_gewerbeabrechnung_erhaelt_kein_pdf(tmp_path):
    from betriebskosten.ausgabe import abrechnung_html, schreibe_lauf

    erg = berechne(aus_dict(fall_gewerbe({})))
    with pytest.raises(ValueError, match="gesperrt"):
        abrechnung_html(erg, erg.mv("G1"))
    namen = sorted(p.name for p in schreibe_lauf(erg, tmp_path))
    assert namen == ["abrechnung_W1.pdf", "ergebnis.json", "kontrollbericht.pdf"]
    kb = _pdf_text((tmp_path / "kontrollbericht.pdf").read_bytes())
    assert "Freigabe gesperrt" in kb and "steuer_ungeklaert" in kb


def test_pilotobjekt_rechnet_vollstaendig(tmp_path):
    """Künstliches Pilotobjekt: 16 Wohnungen + Gewerbe, Teilung zum 01.07., Nutzerwechsel, Messdienst."""
    from pathlib import Path

    from betriebskosten.__main__ import main
    from betriebskosten.eingabe import lade

    pfad = Path(__file__).parent / "beispiel" / "betriebskosten_pilot.toml"
    erg = berechne(lade(pfad))
    assert erg.rot == [] and len(erg.mietverhaeltnisse) == 19
    gesamt = sum((k.gesamt_brutto for k in erg.kontrolle), D(0))
    assert sum((z.anteil_brutto for m in erg.mietverhaeltnisse for z in m.zeilen), D(0)) + sum(erg.leerstand.values(), D(0)) == gesamt
    assert erg.mv("GE1").darstellung == "gewerbe_netto"
    assert {m.darstellung for m in erg.mietverhaeltnisse if m.id != "GE1"} == {"wohnen"}
    assert main([str(pfad), "--ausgabe", str(tmp_path / "aus"), "--ablage", str(tmp_path / "ablage"), "--person", "test"]) == 0
    assert len(list((tmp_path / "aus").glob("abrechnung_*.pdf"))) == 19


# --- Befund W3: beschädigte Stände und Audit-Kette sperren Freigaben -----------------

def _endgueltig_bis(ablage, ziel):
    for status in ("fachlich_geprueft", "eigentuemer_freigegeben", "endgueltig"):
        ablage.status_setzen(1, status, person="pruefer", zeit=ZEIT)
        if status == ziel:
            return


def test_veraendertes_ergebnis_bei_unveraendertem_hash_sperrt(tmp_path):
    ablage = Ablage(tmp_path)
    ablage.speichern(berechne(aus_dict(fall_wohnen())), zeit=ZEIT, person="pruefer")
    pfad = tmp_path / "v001.json"
    stand = json.loads(pfad.read_text())
    stand["ergebnis"]["mietverhaeltnisse"][0]["saldo"] = "999.99"
    pfad.write_text(json.dumps(stand))
    with pytest.raises(Freigabefehler, match="Ergebnis-Prüfsumme"):
        ablage.status_setzen(1, "fachlich_geprueft", person="pruefer", zeit=ZEIT)
    with pytest.raises(Freigabefehler, match="nicht unverfälscht"):
        ablage.pruefen(1)


def test_veraenderte_eingabe_und_warnungszaehler_sperren(tmp_path):
    for aendern, text in [
        (lambda s: s["eingabe"]["positionen"][0].__setitem__("brutto", "1.00"), "Eingabe-Prüfsumme"),
        (lambda s: s.__setitem__("rote_warnungen", 0) or s.__setitem__("gelbe_warnungen", 99), "Warnungszähler"),
        (lambda s: s.__setitem__("eingabe_hash", "0" * 64), "Eingabe-Prüfsumme"),
    ]:
        ablage = Ablage(tmp_path / text / str(id(aendern)))
        ablage.speichern(berechne(aus_dict(fall_wohnen())), zeit=ZEIT, person="pruefer")
        pfad = ablage.pfad / "v001.json"
        stand = json.loads(pfad.read_text())
        aendern(stand)
        pfad.write_text(json.dumps(stand))
        with pytest.raises(Freigabefehler, match=text):
            ablage.status_setzen(1, "fachlich_geprueft", person="pruefer", zeit=ZEIT)


def test_beschaedigter_altstand_wird_bei_identischer_eingabe_nicht_zurueckgegeben(tmp_path):
    ablage = Ablage(tmp_path)
    erg = berechne(aus_dict(fall_wohnen()))
    ablage.speichern(erg, zeit=ZEIT, person="pruefer")
    pfad = tmp_path / "v001.json"
    stand = json.loads(pfad.read_text())
    stand["ergebnis"]["mietverhaeltnisse"][0]["saldo"] = "0.00"
    pfad.write_text(json.dumps(stand))
    with pytest.raises(Freigabefehler, match="nicht unverfälscht"):
        ablage.speichern(erg, zeit=ZEIT, person="pruefer")


def test_beschaedigte_auditkette_sperrt_weitere_freigaben(tmp_path):
    ablage = Ablage(tmp_path)
    ablage.speichern(berechne(aus_dict(fall_wohnen())), zeit=ZEIT, person="pruefer")
    _endgueltig_bis(ablage, "fachlich_geprueft")
    zeilen = ablage.audit_pfad.read_text().splitlines()
    zeilen[0] = zeilen[0].replace('"person":"pruefer"', '"person":"jemand"')
    ablage.audit_pfad.write_text("\n".join(zeilen) + "\n")
    assert not ablage.audit_unverfaelscht()
    with pytest.raises(Freigabefehler, match="Audit-Log ist beschädigt"):
        ablage.status_setzen(1, "eigentuemer_freigegeben", person="pruefer", zeit=ZEIT)
    assert ablage.status(1) == "fachlich_geprueft"


def test_gueltiger_ablauf_bleibt_moeglich(tmp_path):
    ablage = Ablage(tmp_path)
    ablage.speichern(berechne(aus_dict(fall_wohnen())), zeit=ZEIT, person="pruefer")
    _endgueltig_bis(ablage, "endgueltig")
    assert ablage.status(1) == "endgueltig" and ablage.pruefen(1)["version"] == 1


# --- N1 (Nachprüfung 02.11.2026): letzter Audit-Eintrag und vollständige Statusfolge -------

def _zeilen_ersetzen(ablage, index, alt, neu):
    zeilen = ablage.audit_pfad.read_text().splitlines()
    assert alt in zeilen[index]
    zeilen[index] = zeilen[index].replace(alt, neu)
    ablage.audit_pfad.write_text("\n".join(zeilen) + "\n")


def test_n1_letzter_eintrag_entwurf_auf_endgueltig_wird_erkannt(tmp_path):
    # Gegenprobe 1 der Nachprüfung: nur im letzten Ereignis „entwurf“ → „endgueltig“, kein Hash neu gebildet
    ablage = Ablage(tmp_path)
    ablage.speichern(berechne(aus_dict(fall_wohnen())), zeit=ZEIT, person="pruefer")
    assert [e["ereignis"] for e in ablage.audit()] == ["berechnet", "status"]
    _zeilen_ersetzen(ablage, -1, '"status":"entwurf"', '"status":"endgueltig"')
    assert not ablage.audit_unverfaelscht()  # Kettenende passt nicht mehr
    with pytest.raises(Freigabefehler, match="Audit-Log ist beschädigt"):
        ablage.pruefen(1)
    with pytest.raises(Freigabefehler):
        ablage.status(1)  # unzulässige Folge, Status wird nicht übernommen
    with pytest.raises(Freigabefehler):
        ablage.status_setzen(1, "fachlich_geprueft", person="pruefer", zeit=ZEIT)


def test_n1_letzter_eintrag_andere_felder_werden_erkannt(tmp_path):
    ablage = Ablage(tmp_path)
    ablage.speichern(berechne(aus_dict(fall_wohnen())), zeit=ZEIT, person="pruefer")
    _endgueltig_bis(ablage, "fachlich_geprueft")
    _zeilen_ersetzen(ablage, -1, '"person":"pruefer"', '"person":"jemand"')
    assert not ablage.audit_unverfaelscht()
    with pytest.raises(Freigabefehler, match="Audit-Log ist beschädigt"):
        ablage.status_setzen(1, "eigentuemer_freigegeben", person="pruefer", zeit=ZEIT)


def test_n1_konsistent_angehaengte_uebersprungene_stufe_sperrt(tmp_path):
    # Auch ein korrekt verketteter, angehängter Eintrag darf keine Stufe überspringen
    ablage = Ablage(tmp_path)
    ablage.speichern(berechne(aus_dict(fall_wohnen())), zeit=ZEIT, person="pruefer")
    ablage._audit({"ereignis": "status", "version": 1, "status": "endgueltig", "person": "pruefer", "zeit": ZEIT.isoformat()})
    assert ablage.audit_unverfaelscht()
    with pytest.raises(Freigabefehler, match="Statusverlauf ist unzulässig"):
        ablage.pruefen(1)
    with pytest.raises(Freigabefehler, match="unzulässig"):
        ablage.status(1)


@pytest.mark.parametrize("folge", [
    ["entwurf", "entwurf"],                              # doppelt
    ["entwurf", "fachlich_geprueft", "fachlich_geprueft"],
    ["entwurf", "fachlich_geprueft", "entwurf"],         # rückwärts
    ["entwurf", "freigegeben"],                          # unbekannte Stufe
])
def test_n1_unzulaessige_folgen_sperren(tmp_path, folge):
    ablage = Ablage(tmp_path)
    ablage.speichern(berechne(aus_dict(fall_wohnen())), zeit=ZEIT, person="pruefer")
    for s in folge[1:]:
        ablage._audit({"ereignis": "status", "version": 1, "status": s, "person": "pruefer", "zeit": ZEIT.isoformat()})
    with pytest.raises(Freigabefehler):
        ablage.pruefen(1)


def test_n1_status_ohne_person_sperrt(tmp_path):
    ablage = Ablage(tmp_path)
    ablage.speichern(berechne(aus_dict(fall_wohnen())), zeit=ZEIT, person="pruefer")
    ablage._audit({"ereignis": "status", "version": 1, "status": "fachlich_geprueft", "person": "", "zeit": ZEIT.isoformat()})
    with pytest.raises(Freigabefehler, match="ohne verantwortliche Person"):
        ablage.pruefen(1)


def test_n1_fehlendes_kettenende_sperrt(tmp_path):
    ablage = Ablage(tmp_path)
    ablage.speichern(berechne(aus_dict(fall_wohnen())), zeit=ZEIT, person="pruefer")
    ablage.kopf_pfad.unlink()
    with pytest.raises(Freigabefehler, match="Audit-Log ist beschädigt"):
        ablage.pruefen(1)


def test_n1_normale_folge_bleibt_moeglich(tmp_path):
    ablage = Ablage(tmp_path)
    ablage.speichern(berechne(aus_dict(fall_wohnen())), zeit=ZEIT, person="pruefer")
    for s in ("fachlich_geprueft", "eigentuemer_freigegeben", "endgueltig"):
        ablage.status_setzen(1, s, person="pruefer", zeit=ZEIT)
        assert ablage.status(1) == s and ablage.audit_unverfaelscht()
    assert ablage.pruefen(1)["version"] == 1
    with pytest.raises(Freigabefehler):
        ablage.status_setzen(1, "endgueltig", person="pruefer", zeit=ZEIT)


# --- R1 (Nachprüfung 03.11.2026): neue Schreibvorgänge verankern keinen beschädigten Verlauf ---

def _fall_wohnen_cent():
    d = fall_wohnen()
    d["belege"][2]["positionen"][0]["brutto"] = "100.01"  # um einen Cent geänderte Eingabe
    return d


def _schnappschuss(ablage):
    """Dateien der Ablage samt Inhalt, um zu belegen, dass eine Ablehnung nichts schreibt."""
    return {p.relative_to(ablage.pfad).as_posix(): p.read_bytes() for p in sorted(ablage.pfad.rglob("*")) if p.is_file()}


def test_r1_a_neue_berechnung_legitimiert_veraenderten_letzten_eintrag_nicht(tmp_path):
    ablage = Ablage(tmp_path)
    ablage.speichern(berechne(aus_dict(fall_wohnen())), zeit=ZEIT, person="pruefer")
    _endgueltig_bis(ablage, "endgueltig")
    _zeilen_ersetzen(ablage, -1, '"person":"pruefer"', '"person":"jemand"')
    assert not ablage.audit_unverfaelscht()
    vorher = _schnappschuss(ablage)
    for daten in (_fall_wohnen_cent(), fall_wohnen()):  # neue und identische Eingabe
        with pytest.raises(Freigabefehler, match="beschädigt"):
            ablage.speichern(berechne(aus_dict(daten)), zeit=ZEIT, person="pruefer")
    assert _schnappschuss(ablage) == vorher  # keine neue Version, Kopf und Log unverändert
    assert ablage.versionen() == [1] and not ablage.audit_unverfaelscht()
    with pytest.raises(Freigabefehler):
        ablage.pruefen(1)  # Altversion bleibt gesperrt


def test_r1_c_fehlender_kopf_wird_nicht_still_neu_angelegt(tmp_path):
    ablage = Ablage(tmp_path)
    ablage.speichern(berechne(aus_dict(fall_wohnen())), zeit=ZEIT, person="pruefer")
    ablage.kopf_pfad.unlink()
    vorher = _schnappschuss(ablage)
    for daten in (_fall_wohnen_cent(), fall_wohnen()):
        with pytest.raises(Freigabefehler, match="fehlt"):
            ablage.speichern(berechne(aus_dict(daten)), zeit=ZEIT, person="pruefer")
    assert _schnappschuss(ablage) == vorher and not ablage.kopf_pfad.exists() and ablage.versionen() == [1]
    with pytest.raises(Freigabefehler):
        ablage.pruefen(1)
    with pytest.raises(Freigabefehler):
        ablage.status_setzen(1, "fachlich_geprueft", person="pruefer", zeit=ZEIT)


def test_r1_c_staende_ohne_audit_log_werden_nicht_neu_initialisiert(tmp_path):
    ablage = Ablage(tmp_path)
    ablage.speichern(berechne(aus_dict(fall_wohnen())), zeit=ZEIT, person="pruefer")
    ablage.kopf_pfad.unlink()
    ablage.audit_pfad.unlink()  # Log und Kopf fehlen, Stand liegt noch da
    vorher = _schnappschuss(ablage)
    with pytest.raises(Freigabefehler, match="kein Audit-Log"):
        ablage.speichern(berechne(aus_dict(_fall_wohnen_cent())), zeit=ZEIT, person="pruefer")
    assert _schnappschuss(ablage) == vorher


def test_r1_kopf_ohne_log_sperrt(tmp_path):
    ablage = Ablage(tmp_path)
    ablage.speichern(berechne(aus_dict(fall_wohnen())), zeit=ZEIT, person="pruefer")
    ablage.audit_pfad.unlink()
    vorher = _schnappschuss(ablage)
    with pytest.raises(Freigabefehler):
        ablage.speichern(berechne(aus_dict(_fall_wohnen_cent())), zeit=ZEIT, person="pruefer")
    assert _schnappschuss(ablage) == vorher


def test_r1_direktes_anhaengen_an_beschaedigte_kette_gesperrt(tmp_path):
    ablage = Ablage(tmp_path)
    ablage.speichern(berechne(aus_dict(fall_wohnen())), zeit=ZEIT, person="pruefer")
    _zeilen_ersetzen(ablage, -1, '"person":"pruefer"', '"person":"jemand"')
    kopf = ablage.kopf_pfad.read_bytes()
    with pytest.raises(Freigabefehler):
        ablage._audit({"ereignis": "status", "version": 1, "status": "fachlich_geprueft", "person": "x", "zeit": ZEIT.isoformat()})
    assert ablage.kopf_pfad.read_bytes() == kopf


def test_r1_positiv_neue_ablage_und_weitere_versionen(tmp_path):
    ablage = Ablage(tmp_path / "neu")
    assert not ablage.audit_pfad.exists() and not ablage.kopf_pfad.exists()
    assert ablage.speichern(berechne(aus_dict(fall_wohnen())), zeit=ZEIT, person="pruefer") == (1, True)
    assert ablage.speichern(berechne(aus_dict(fall_wohnen())), zeit=ZEIT, person="pruefer") == (1, False)
    assert ablage.speichern(berechne(aus_dict(_fall_wohnen_cent())), zeit=ZEIT, person="pruefer") == (2, True)
    assert ablage.audit_unverfaelscht() and ablage.pruefen(1) and ablage.pruefen(2)
    for s in ("fachlich_geprueft", "eigentuemer_freigegeben", "endgueltig"):
        ablage.status_setzen(2, s, person="pruefer", zeit=ZEIT)
    assert ablage.status(2) == "endgueltig"
