import shutil
import subprocess
from dataclasses import replace
from datetime import date

import pytest

from auswertung.__main__ import main
from auswertung.camt import lese_camt_datei
from auswertung.einschaetzung import erstelle_einschaetzung
from auswertung.pdf_bericht import als_html, als_pdf
from auswertung.stammdaten import lade_stammdaten

STAND = date(2026, 11, 1)


@pytest.fixture
def daten(beispiel):
    sd = lade_stammdaten(beispiel / "stammdaten.toml")
    auszuege = lese_camt_datei(beispiel / "giro_2025.xml") + lese_camt_datei(beispiel / "ruecklage_2025.xml")
    return erstelle_einschaetzung(auszuege, sd), sd


def test_steckbrief_aus_stammdaten(beispiel):
    sd = lade_stammdaten(beispiel / "stammdaten.toml")
    assert list(sd.steckbrief)[:2] == ["Anschrift", "Baujahr"]
    assert sd.steckbrief["Heizung"] == "Gas-Zentralheizung"


def test_html_enthaelt_steckbrief_und_einschaetzung(daten):
    e, sd = daten
    h = als_html(e, sd, STAND)
    assert "Erste Einschätzung · WEG Lindenstraße 12–14 (Beispiel) · 2025" in h
    assert "Stand 01.11.2026" in h
    assert "Entwurf · vorläufig, zu prüfen" in h
    assert "Lindenstraße 12–14, 12345 Musterstadt" in h
    assert "28.956,00 €" in h  # Hausgeld Soll
    assert "WE 2: Hausgeld Aug nicht eingegangen." in h
    assert "Rücklagenkonto" in h and "fehlt: Aug" in h


def test_html_maskiert_fremde_inhalte(daten):
    e, sd = daten
    sd = replace(sd, steckbrief={"Notiz": '<img src="http://angreifer.example/x.png">'})
    h = als_html(e, sd, STAND)
    assert "<img" not in h
    assert "&lt;img src=&quot;http://angreifer.example/x.png&quot;&gt;" in h


def test_html_ohne_telefonnummer(daten):
    import re

    h = als_html(*daten, STAND)
    assert "tel:" not in h
    assert not re.search(r"\+49|\(0\d|\b0\d{2,4}[ /]\d{3,}", h)


def test_pdf_wird_erzeugt(daten, tmp_path):
    pdf = als_pdf(*daten, STAND)
    assert pdf.startswith(b"%PDF")
    if shutil.which("pdftotext"):
        ziel = tmp_path / "b.pdf"
        ziel.write_bytes(pdf)
        text = subprocess.run(["pdftotext", "-layout", str(ziel), "-"], capture_output=True, text=True, check=True).stdout
        assert "Erste Einschätzung" in text
        assert "Steckbrief" in text
        assert "Hausgeld je Einheit" in text
        assert "Seite 1 von" in text


def test_pdf_laedt_nichts_nach(daten, monkeypatch):
    e, sd = daten
    sd = replace(sd, steckbrief={"Bild": "x"})
    import auswertung.pdf_bericht as pb

    aufrufe = []
    original = pb._kein_nachladen

    def merken(url):
        aufrufe.append(url)
        return original(url)

    monkeypatch.setattr(pb, "_kein_nachladen", merken)
    monkeypatch.setattr(pb, "als_html", lambda *a, **k: '<html><body><img src="http://angreifer.example/x.png">Test</body></html>')
    assert pb.als_pdf(e, sd, STAND).startswith(b"%PDF")
    assert aufrufe == ["http://angreifer.example/x.png"]


def test_kommandozeile_schreibt_pdf(beispiel, tmp_path, capsys):
    ziel = tmp_path / "bericht.pdf"
    rc = main([
        "--stammdaten", str(beispiel / "stammdaten.toml"),
        "--pdf", str(ziel), "--stand", "2026-11-01",
        str(beispiel / "giro_2025.xml"), str(beispiel / "ruecklage_2025.xml"),
    ])
    assert rc == 0
    assert ziel.read_bytes().startswith(b"%PDF")
    assert "PDF-Bericht geschrieben" in capsys.readouterr().err
