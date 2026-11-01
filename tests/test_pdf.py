import shutil
from datetime import date
from decimal import Decimal

import pytest

from auswertung.pdf_import import PdfFehler, lese_volksbank_text


def test_volksbank_layout(beispiel):
    a = lese_volksbank_text((beispiel / "volksbank_auszug.txt").read_text(encoding="utf-8"), "beispiel")
    assert a.konto_iban == "DE99999999990000004711"
    assert a.anfangssaldo == Decimal("1000.00")
    assert a.endsaldo == Decimal("-342.90")
    assert [b.betrag for b in a.buchungen] == [Decimal("310.00"), Decimal("-1235.00"), Decimal("-400.00"), Decimal("-17.90")]
    gutschrift, lastschrift, ueberweisung, abschluss = a.buchungen
    assert gutschrift.gegen_name == "Eigentuemer 1 (Beispiel)"
    assert gutschrift.zweck == "Hausgeld WE 1 12/2025"
    assert lastschrift.gegen_name == "STADTWERKE"  # Name in Großbuchstaben bleibt erhalten
    assert "CRED" in lastschrift.zweck and "Bitte beachten" not in lastschrift.zweck
    assert ueberweisung.gegen_name == "WEG Beispiel Ruecklage"
    assert abschluss.gegen_name == "" and abschluss.datum == date(2025, 12, 30)


def test_fehlende_zeile_faellt_in_der_saldenpruefung_auf(beispiel):
    text = (beispiel / "volksbank_auszug.txt").read_text(encoding="utf-8")
    kaputt = text.replace("            30.12. 31.12. Abschluss PN:905", "            30.12.31.12. Abschluss PN:905")
    with pytest.raises(PdfFehler, match="Saldenprüfung"):
        lese_volksbank_text(kaputt)


def test_jahreswechsel():
    text = """    alter Kontostand vom 30.12.2025      100,00 H
            31.12. 31.12. Abschluss PN:905                         1,00 S
            02.01. 02.01. Sepa-Gutschrift PN:801                  11,00 H
                          Beispiel
    neuer Kontostand vom 05.01.2026      110,00 H
"""
    a = lese_volksbank_text(text)
    assert [b.datum for b in a.buchungen] == [date(2025, 12, 31), date(2026, 1, 2)]


def test_ohne_salden():
    with pytest.raises(PdfFehler):
        lese_volksbank_text("kein Auszug")


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="pdftotext nicht installiert")
def test_pdf_ohne_text(tmp_path):
    from auswertung.pdf_import import lese_pdf_datei
    leer = tmp_path / "leer.pdf"
    leer.write_bytes(b"%PDF-1.1\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj "
                     b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 10 10]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF")
    with pytest.raises(PdfFehler):
        lese_pdf_datei(leer)
