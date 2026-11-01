"""Erzeugt erfundene Beispieldaten für Tests (keine echten Personen, Konten oder Beträge).

Aufruf: python tests/beispiel/erzeuge.py   (schreibt die Dateien neben dieses Skript)
"""
from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape

HIER = Path(__file__).parent
NS = "urn:iso:std:iso:20022:tech:xsd:camt.053.001.08"
GIRO = "DE99999999990000004711"
RUECKLAGE = "DE99999999990000000815"
JAHR = 2025

EINHEITEN = [  # Nummer, Soll, Eigentümer-IBAN
    ("WE 1", "310.00", "DE99000000000000000001"),
    ("WE 2", "275.00", "DE99000000000000000002"),
    ("WE 3", "342.00", "DE99000000000000000003"),
    ("WE 4", "298.00", "DE99000000000000000004"),
    ("WE 5", "265.00", "DE99000000000000000005"),
    ("WE 6", "330.00", "DE99000000000000000006"),
    ("WE 7", "288.00", "DE99000000000000000007"),
    ("WE 8", "305.00", "DE99000000000000000008"),
]
WE8_NEU = "DE99000000000000000088"  # Eigentümerwechsel zum 01.07.


def buchungen_giro() -> list[tuple[date, Decimal, str, str, str]]:
    b = []
    for m in range(1, 13):
        for nr, soll, iban in EINHEITEN:
            betrag = Decimal(soll)
            name = f"Eigentuemer {nr[-1]} (Beispiel)"
            if nr == "WE 2" and m == 8:
                continue
            if nr == "WE 5" and m in (11, 12):
                continue
            if nr == "WE 5" and m == 10:
                betrag = Decimal("200.00")
            if nr == "WE 8" and m >= 7:
                iban, name = WE8_NEU, "Neue Eigentuemerin 8 (Beispiel)"
            b.append((date(JAHR, m, 3), betrag, name, iban, f"Hausgeld {nr} {m:02d}/{JAHR}"))
            if nr == "WE 4" and m == 4:
                b.append((date(JAHR, m, 4), betrag, name, iban, f"Hausgeld {nr} {m:02d}/{JAHR}"))
        b.append((date(JAHR, m, 10), Decimal("-1235.00"), "Stadtwerke Musterstadt (Beispiel)", "DE99100000000000000100", f"Abschlag Strom Gas {m:02d}/{JAHR}"))
        b.append((date(JAHR, m, 15), Decimal("-400.00"), "WEG Beispiel Ruecklage", RUECKLAGE, "Zufuehrung Erhaltungsruecklage"))
        b.append((date(JAHR, m, 28), Decimal("-17.90"), "Beispielbank", "", "Kontofuehrungsentgelt"))
        if m % 3 == 0:
            b.append((date(JAHR, m, 20), Decimal("-612.40"), "Wasserversorgung Beispiel", "DE99100000000000000200", "Abschlag Wasser Abwasser"))
    b.append((date(JAHR, 2, 12), Decimal("-3410.00"), "Versicherung Beispiel AG", "DE99100000000000000300", "Gebaeudeversicherung 2025"))
    b.append((date(JAHR, 5, 7), Decimal("-890.25"), "Heizung Muster (Beispiel)", "DE99100000000000000400", "Wartung Heizungsanlage RE 2025-117"))
    b.append((date(JAHR, 9, 19), Decimal("-486.00"), "Firma Muster GmbH (Beispiel)", "DE99100000000000000500", "RE 2025-301"))
    b.append((date(JAHR, 9, 22), Decimal("250.00"), "", "", "Gutschrift"))
    return sorted(b)


def buchungen_ruecklage() -> list[tuple[date, Decimal, str, str, str]]:
    return [(date(JAHR, m, 15), Decimal("400.00"), "WEG Beispiel Giro", GIRO, "Zufuehrung Erhaltungsruecklage") for m in range(1, 13)]


def _bal(code: str, betrag: Decimal, tag: date) -> str:
    ind = "CRDT" if betrag >= 0 else "DBIT"
    return (f"<Bal><Tp><CdOrPrtry><Cd>{code}</Cd></CdOrPrtry></Tp><Amt Ccy=\"EUR\">{abs(betrag):.2f}</Amt>"
            f"<CdtDbtInd>{ind}</CdtDbtInd><Dt><Dt>{tag.isoformat()}</Dt></Dt></Bal>")


def _ntry(tag: date, betrag: Decimal, name: str, iban: str, zweck: str) -> str:
    ind = "CRDT" if betrag > 0 else "DBIT"
    rolle, konto = ("Dbtr", "DbtrAcct") if betrag > 0 else ("Cdtr", "CdtrAcct")
    partei = f"<{rolle}><Pty><Nm>{escape(name)}</Nm></Pty></{rolle}>" if name else ""
    kto = f"<{konto}><Id><IBAN>{iban}</IBAN></Id></{konto}>" if iban else ""
    return (f"<Ntry><Amt Ccy=\"EUR\">{abs(betrag):.2f}</Amt><CdtDbtInd>{ind}</CdtDbtInd><Sts><Cd>BOOK</Cd></Sts>"
            f"<BookgDt><Dt>{tag.isoformat()}</Dt></BookgDt><ValDt><Dt>{tag.isoformat()}</Dt></ValDt>"
            f"<NtryDtls><TxDtls><RltdPties>{partei}{kto}</RltdPties>"
            f"<RmtInf><Ustrd>{escape(zweck)}</Ustrd></RmtInf></TxDtls></NtryDtls></Ntry>")


def camt(iban: str, start: Decimal, buchungen, ohne_monate=()) -> str:
    stmts = []
    saldo = start
    for m in range(1, 13):
        letzter = calendar.monthrange(JAHR, m)[1]
        im_monat = [b for b in buchungen if b[0].month == m]
        ende = saldo + sum((b[1] for b in im_monat), Decimal("0"))
        if m not in ohne_monate:
            stmts.append(
                f"<Stmt><Id>{iban[-4:]}-{JAHR}-{m:02d}</Id><ElctrncSeqNb>{m}</ElctrncSeqNb>"
                f"<FrToDt><FrDtTm>{JAHR}-{m:02d}-01T00:00:00</FrDtTm><ToDtTm>{JAHR}-{m:02d}-{letzter}T23:59:59</ToDtTm></FrToDt>"
                f"<Acct><Id><IBAN>{iban}</IBAN></Id><Ccy>EUR</Ccy></Acct>"
                + _bal("PRCD", saldo, date(JAHR, m, 1)) + _bal("CLBD", ende, date(JAHR, m, letzter))
                + "".join(_ntry(*b) for b in im_monat) + "</Stmt>"
            )
        saldo = ende
    return (f"<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<Document xmlns=\"{NS}\"><BkToCstmrStmt>"
            f"<GrpHdr><MsgId>BEISPIEL-{iban[-4:]}</MsgId><CreDtTm>{JAHR + 1}-01-05T08:00:00</CreDtTm></GrpHdr>"
            + "".join(stmts) + "</BkToCstmrStmt></Document>\n")


STAMMDATEN = f'''# Erfundenes Beispielobjekt für Tests
[objekt]
name = "WEG Lindenstraße 12–14 (Beispiel)"
jahr = {JAHR}

[steckbrief]
Anschrift = "Lindenstraße 12–14, 12345 Musterstadt"
Baujahr = "1972"
Verwaltung = "selbstverwaltet seit 2024"
Wirtschaftsjahr = "Kalenderjahr"
Heizung = "Gas-Zentralheizung"

[[konten]]
iban = "{GIRO}"
art = "Girokonto"

[[konten]]
iban = "{RUECKLAGE}"
art = "Rücklagenkonto"
''' + "".join(
    f'''
[[einheiten]]
nummer = "{nr}"
hausgeld_soll = "{soll}"
[[einheiten.eigentuemer]]
name = "Eigentümer {nr[-1]} (Beispiel)"
ibans = ["{iban}"]
''' + (f'''bis = {JAHR}-06-30
[[einheiten.eigentuemer]]
name = "Neue Eigentümerin 8 (Beispiel)"
ibans = ["{WE8_NEU}"]
von = {JAHR}-07-01
''' if nr == "WE 8" else "")
    for nr, soll, iban in EINHEITEN
) + '''
[[kostengruppen]]
gruppe = "Versorger"
muster = ["stadtwerke", "wasser", "strom", "gas", "fernwärme", "fernwaerme"]

[[kostengruppen]]
gruppe = "Handwerker und Wartung"
muster = ["wartung", "reparatur", "heizung", "sanitär", "elektro"]

[[kostengruppen]]
gruppe = "Versicherungen"
muster = ["versicherung"]

[[kostengruppen]]
gruppe = "Verwaltung"
muster = ["verwalter", "verwaltung"]

[[kostengruppen]]
gruppe = "Bank und Gebühren"
muster = ["kontofuehrung", "kontoführung", "entgelt", "gebühr"]
'''

CSV = """Auftragskonto;Buchungstag;Valutadatum;Buchungstext;Verwendungszweck;Beguenstigter/Zahlungspflichtiger;Kontonummer/IBAN;BIC (SWIFT-Code);Betrag;Waehrung;Info
DE99999999990000004711;03.01.25;03.01.25;GUTSCHR. UEBERWEISUNG;Hausgeld WE 1 01/2025;Eigentuemer 1 (Beispiel);DE99000000000000000001;BEISPIELXXX;310,00;EUR;Umsatz gebucht
DE99999999990000004711;10.01.25;10.01.25;LASTSCHRIFT;Abschlag Strom Gas 01/2025;Stadtwerke Musterstadt (Beispiel);DE99100000000000000100;BEISPIELXXX;-1.235,00;EUR;Umsatz gebucht
DE99999999990000004711;31.01.25;31.01.25;LASTSCHRIFT;Vorgemerkt;Beispiel;DE99100000000000000999;BEISPIELXXX;-9,99;EUR;Umsatz vorgemerkt
"""

if __name__ == "__main__":
    (HIER / "giro_2025.xml").write_text(camt(GIRO, Decimal("18420.55"), buchungen_giro()), encoding="utf-8")
    (HIER / "ruecklage_2025.xml").write_text(
        camt(RUECKLAGE, Decimal("42300.00"), buchungen_ruecklage(), ohne_monate=(8,)), encoding="utf-8")
    (HIER / "stammdaten.toml").write_text(STAMMDATEN, encoding="utf-8")
    (HIER / "giro_januar.csv").write_bytes(CSV.encode("cp1252"))
    print("Beispieldaten geschrieben.")
