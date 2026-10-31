from decimal import Decimal

import pytest

from auswertung.csv_import import CsvFehler, CsvVorlage, lese_csv, lese_csv_datei


def test_csv_camt_export(beispiel):
    a = lese_csv_datei(beispiel / "giro_januar.csv")
    assert a.konto_iban == "DE99999999990000004711"
    assert [b.betrag for b in a.buchungen] == [Decimal("310.00"), Decimal("-1235.00")]  # vorgemerkt fehlt
    assert a.buchungen[0].gegen_iban == "DE99000000000000000001"
    assert a.anfangssaldo is None


def test_eigene_vorlage_und_fehlende_spalten():
    vorlage = CsvVorlage(datum="Datum", betrag="Betrag", gegen_name="Name", gegen_iban="IBAN", zweck="Zweck",
                         trennzeichen=",", zeichensatz="utf-8", datumsformat="%Y-%m-%d", dezimalkomma=False)
    daten = b"Datum,Betrag,Name,IBAN,Zweck\n2025-03-01,-12.50,Bank,,Entgelt\n"
    a = lese_csv(daten, vorlage, konto_iban="DE99 1")
    assert a.buchungen[0].betrag == Decimal("-12.50")
    with pytest.raises(CsvFehler):
        lese_csv(b"A,B\n1,2\n", vorlage)
