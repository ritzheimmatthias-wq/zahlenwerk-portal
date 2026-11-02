"""Erfundene, harmlose Test-PDFs für die Upload-Prüfung.

Die Dateien werden nur erzeugt und geprüft, nie geöffnet oder ausgeführt. Die JavaScript-Muster
enthalten ausschließlich `void(0);`.
"""
from __future__ import annotations

import struct
import zlib

SEITE = [
    b"<</Type/Catalog/Pages 2 0 R>>",
    b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
    b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]/Contents 4 0 R>>",
    b"<</Length 21>>stream\nBT /F1 12 Tf ET\nq Q\nendstream",
]


def klassisch(objekte: list[bytes]) -> bytes:
    """PDF mit korrekter Querverweistabelle; Objekt 1 ist der Katalog."""
    aus = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    lagen = []
    for i, body in enumerate(objekte, 1):
        lagen.append(len(aus))
        aus += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(aus)
    aus += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objekte) + 1)
    for lage in lagen:
        aus += b"%010d 00000 n \n" % lage
    aus += b"trailer\n<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(objekte) + 1, xref)
    return bytes(aus)


def leer() -> bytes:
    return klassisch(SEITE)


def mit_javascript(escaped: bool = False) -> bytes:
    """Katalog mit OpenAction-JavaScript; mit `escaped` nur als Hex-Escape (/Java#53cript, /J#53)."""
    katalog = b"<</Type/Catalog/Pages 2 0 R/OpenAction 5 0 R>>"
    aktion = (b"<</Type/Action/S/Java#53cript/J#53(void\\(0\\);)>>" if escaped
              else b"<</Type/Action/S/JavaScript/JS(void\\(0\\);)>>")
    return klassisch([katalog, *SEITE[1:], aktion])


def mit_start(name: bytes = b"/Launch") -> bytes:
    katalog = b"<</Type/Catalog/Pages 2 0 R/OpenAction 5 0 R>>"
    return klassisch([katalog, *SEITE[1:], b"<</Type/Action/S " + name + b"/F(harmlos.txt)>>"])


def mit_objektstrom(aktion: bytes) -> bytes:
    """PDF 1.5: die Aktion (Objekt 5) liegt komprimiert in einem Objektstrom (Objekt 6),
    Querverweise als komprimierter Querverweisstrom (Objekt 7)."""
    katalog = b"<</Type/Catalog/Pages 2 0 R/OpenAction 5 0 R>>"
    direkt = [katalog, *SEITE[1:]]
    kopf = b"5 0 "
    roh = kopf + aktion
    strom = zlib.compress(roh)
    objstm = (b"<</Type/ObjStm/N 1/First %d/Filter/FlateDecode/Length %d>>stream\n" % (len(kopf), len(strom))
              + strom + b"\nendstream")
    aus = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    lagen = []
    for i, body in enumerate(direkt + [None, objstm], 1):
        if body is None:
            lagen.append(None)
            continue
        lagen.append(len(aus))
        aus += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref_lage = len(aus)
    zeilen = [struct.pack(">BIH", 0, 0, 65535)]
    for i, lage in enumerate(lagen, 1):
        zeilen.append(struct.pack(">BIH", 2, 6, 0) if lage is None else struct.pack(">BIH", 1, lage, 0))
    zeilen.append(struct.pack(">BIH", 1, xref_lage, 0))
    daten = zlib.compress(b"".join(zeilen))
    groesse = len(zeilen)
    aus += (b"%d 0 obj\n<</Type/XRef/Size %d/W[1 4 2]/Root 1 0 R/Filter/FlateDecode/Length %d>>stream\n"
            % (groesse - 1, groesse, len(daten)) + daten + b"\nendstream\nendobj\n")
    aus += b"startxref\n%d\n%%%%EOF\n" % xref_lage
    return bytes(aus)


def beschaedigt() -> bytes:
    return b"%PDF-1.7\n1 0 obj<</Type/Catalog/Pages 9 0 R>>\nkeine Struktur\n"
