import json
from decimal import Decimal

import pytest

from auswertung.__main__ import main
from auswertung.bericht import als_dict, als_markdown
from auswertung.camt import lese_camt_datei
from auswertung.einschaetzung import erstelle_einschaetzung
from auswertung.stammdaten import lade_stammdaten


@pytest.fixture
def ergebnis(beispiel):
    sd = lade_stammdaten(beispiel / "stammdaten.toml")
    auszuege = lese_camt_datei(beispiel / "giro_2025.xml") + lese_camt_datei(beispiel / "ruecklage_2025.xml")
    return erstelle_einschaetzung(auszuege, sd)


def zeile(ergebnis, nr):
    return next(h for h in ergebnis.hausgeld if h.einheit == nr)


def test_hausgeld_status(ergebnis):
    assert zeile(ergebnis, "WE 1").status == ["ok"] * 12
    assert zeile(ergebnis, "WE 2").status[7] == "fehlt"
    assert zeile(ergebnis, "WE 4").status[3] == "doppelt"
    we5 = zeile(ergebnis, "WE 5")
    assert we5.status[9:] == ["teil", "fehlt", "fehlt"]
    assert we5.abweichung == Decimal("-595.00")


def test_eigentuemerwechsel_wird_ueber_neue_iban_zugeordnet(ergebnis):
    we8 = zeile(ergebnis, "WE 8")
    assert we8.status == ["ok"] * 12
    assert "Neue Eigentümerin" in we8.eigentuemer


def test_summen(ergebnis):
    assert sum(h.soll_jahr for h in ergebnis.hausgeld) == Decimal("28956.00")
    assert sum(h.ist_jahr for h in ergebnis.hausgeld) == Decimal("28384.00")


def test_umbuchung_ruecklage_ist_keine_ausgabe(ergebnis):
    namen = {g.name for g in ergebnis.gruppen}
    assert "Versorger" in namen
    alle_empfaenger = {n for g in ergebnis.gruppen for n in g.empfaenger}
    assert "WEG Beispiel Ruecklage" not in alle_empfaenger


def test_ausgaben_gruppen(ergebnis):
    g = {x.name: x for x in ergebnis.gruppen}
    assert g["Versorger"].summe == Decimal("-17269.60")
    assert g["Bank und Gebühren"].anzahl == 12
    assert g["Nicht zugeordnet"].anzahl == 1


def test_saldenkette_und_fehlender_monat(ergebnis):
    k = {x.art: x for x in ergebnis.konten}
    assert k["Girokonto"].fehlende_monate == [] and k["Girokonto"].kettenfehler == []
    assert k["Rücklagenkonto"].fehlende_monate == [8]
    assert len(k["Rücklagenkonto"].kettenfehler) == 1
    assert k["Rücklagenkonto"].ende == Decimal("47100.00")


def test_hinweise_kritisch_zuerst(ergebnis):
    assert ergebnis.hinweise[0][0] == "kritisch"
    assert ergebnis.hinweise[-1][0] == "info"


def test_bericht_ohne_volle_iban(ergebnis):
    md = als_markdown(ergebnis)
    assert "28.956,00 €" in md
    assert "DE99999999990000004711" not in md
    d = als_dict(ergebnis)
    assert d["status"] == "zu_pruefen"
    json.dumps(d)


def test_kommandozeile(beispiel, capsys):
    rc = main(["--stammdaten", str(beispiel / "stammdaten.toml"), "--json",
               str(beispiel / "giro_2025.xml"), str(beispiel / "ruecklage_2025.xml")])
    assert rc == 0
    d = json.loads(capsys.readouterr().out)
    assert d["jahr"] == 2025
