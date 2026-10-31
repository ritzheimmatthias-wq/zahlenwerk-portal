from decimal import Decimal

import pytest

from auswertung.camt import CamtFehler, lese_camt, lese_camt_datei


def test_giro_liest_zwoelf_auszuege_mit_salden(beispiel):
    auszuege = lese_camt_datei(beispiel / "giro_2025.xml")
    assert len(auszuege) == 12
    jan = auszuege[0]
    assert jan.konto_iban == "DE99999999990000004711"
    assert jan.anfangssaldo == Decimal("18420.55")
    assert jan.anfangssaldo + sum(b.betrag for b in jan.buchungen) == jan.endsaldo


def test_gegenpartei_aus_sicht_des_kontoinhabers(beispiel):
    jan = lese_camt_datei(beispiel / "giro_2025.xml")[0]
    eingang = next(b for b in jan.buchungen if "WE 1" in b.zweck)
    assert eingang.betrag == Decimal("310.00")
    assert eingang.gegen_iban == "DE99000000000000000001"
    ausgang = next(b for b in jan.buchungen if "Strom" in b.zweck)
    assert ausgang.betrag == Decimal("-1235.00")
    assert ausgang.gegen_name.startswith("Stadtwerke")


def test_vorgemerkte_umsaetze_werden_ignoriert():
    xml = b"""<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.052.001.02"><BkToCstmrAcctRpt><Rpt>
    <Acct><Id><IBAN>DE99 9999 9999 9999 9999 99</IBAN></Id></Acct>
    <Ntry><Amt Ccy="EUR">10.00</Amt><CdtDbtInd>CRDT</CdtDbtInd><Sts>BOOK</Sts><BookgDt><Dt>2025-01-02</Dt></BookgDt>
      <NtryDtls><TxDtls><RltdPties><Dbtr><Nm>A</Nm></Dbtr></RltdPties><RmtInf><Ustrd>x</Ustrd></RmtInf></TxDtls></NtryDtls></Ntry>
    <Ntry><Amt Ccy="EUR">5.00</Amt><CdtDbtInd>DBIT</CdtDbtInd><Sts>PDNG</Sts><BookgDt><Dt>2025-01-03</Dt></BookgDt></Ntry>
    </Rpt></BkToCstmrAcctRpt></Document>"""
    (a,) = lese_camt(xml)
    assert a.konto_iban == "DE99999999999999999999"
    assert [b.betrag for b in a.buchungen] == [Decimal("10.00")]
    assert a.buchungen[0].gegen_name == "A"


def test_keine_camt_datei():
    with pytest.raises(CamtFehler):
        lese_camt(b"<html></html>")
    with pytest.raises(CamtFehler):
        lese_camt(b"kein xml")


def test_schaedliches_xml_wird_abgelehnt():
    bombe = b"""<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;">]>
    <Document><Stmt>&lol2;</Stmt></Document>"""
    with pytest.raises(Exception) as info:
        lese_camt(bombe)
    assert "Entities" in type(info.value).__name__ or "entit" in str(info.value).lower()
