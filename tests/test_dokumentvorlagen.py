"""Dokumentvorlagen im Kundenbereich, ausschließlich mit erfundenen Werten."""
import hashlib
import io
import logging
import re

import pytest
from django.core import mail
from django.test import Client
from django.urls import reverse
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject

from kundenportal import dokumentvorlagen as dv
from kundenportal.models import Auftrag, Benutzer, Checklistenpunkt, Dokument, Freigabe, Kunde, Objekt, Protokoll
from kundenportal.sicherheit import download_token

pytestmark = pytest.mark.django_db

PASSWORT = "Beispiel-Passwort-2026!"
TITEL = [
    "Betriebskostenvorauszahlung anpassen",
    "Wohnungsgeberbestätigung",
    "Vorabnahme einer Mietwohnung",
    "Eingang einer Mieterkündigung bestätigen",
    "Indexmiete anpassen",
    "An eine vereinbarte Mietstaffel erinnern",
    "Bescheinigung über Mietzahlungen",
    "Wohnungsübergabe und Rückgabe",
    "Mieterselbstauskunft in drei Phasen",
]
# Erfundene Werte: Umlaute, lange Namen, mehrzeilige Angaben
NAME = "Jürgen-Ömer Müller-Lüdenscheidt-SSeßler (frei erfunden)"
MEHRZEILIG = "Gärtnerstraße 12a, 3. OG links\n12345 Beispielstadt-Süd\nzu Händen Frau Ä. Übel"


@pytest.fixture(autouse=True)
def datenordner(settings, tmp_path):
    settings.DATEN_VERZEICHNIS = tmp_path / "daten"


def _ein_wert(widget_ap) -> str:
    """Einschaltwert einer Checkbox aus ihren Darstellungen."""
    namen = [k for k in widget_ap["/N"].keys() if k != "/Off"]
    return namen[0]


def ausfuellen(daten: bytes) -> tuple[bytes, dict[str, str]]:
    leser = PdfReader(io.BytesIO(daten))
    schreiber = PdfWriter(clone_from=leser)
    erwartet: dict[str, str] = {}
    for seite in schreiber.pages:
        werte = {}
        for a in seite.get("/Annots") or []:
            w = a.get_object()
            if w.get("/Subtype") != "/Widget":
                continue
            name = w.get("/T") or w.get("/Parent", {}).get("/T")
            typ = w.get("/FT") or w.get("/Parent", {}).get("/FT")
            if typ == "/Tx":
                mehr = int(w.get("/Ff", 0)) & 4096
                werte[name] = (MEHRZEILIG if mehr else f"{NAME} · {name}")
            elif typ == "/Btn":
                werte[name] = _ein_wert(w["/AP"])
        schreiber.update_page_form_field_values(seite, werte, auto_regenerate=False)
        erwartet.update(werte)
    schreiber.set_need_appearances_writer(True)
    puffer = io.BytesIO()
    schreiber.write(puffer)
    return puffer.getvalue(), erwartet


def felder(daten: bytes) -> dict:
    return PdfReader(io.BytesIO(daten)).get_fields() or {}


# --- Dateistand -------------------------------------------------------------------

def test_neun_eigene_vorlagen_mit_pruefsumme():
    vorlagen = dv.alle()
    assert [v.titel for v in vorlagen] == TITEL
    for v in vorlagen:
        daten = (dv.VERZEICHNIS / v.datei).read_bytes()
        assert hashlib.sha256(daten).hexdigest() == v.sha256
        assert dv.original(v) == daten
        assert len(PdfReader(io.BytesIO(daten)).pages) == v.seiten


def test_keine_fremden_dateien_oder_marken():
    namen = sorted(p.name for p in dv.VERZEICHNIS.iterdir())
    erwartet = sorted([v.datei for v in dv.alle()] + [v.markdown for v in dv.alle()] + ["manifest.json", "technische-pruefung.json"])
    assert namen == erwartet
    for p in dv.VERZEICHNIS.iterdir():
        assert b"immocloud" not in p.read_bytes().lower()


def test_falsche_pruefsumme_liefert_nichts(monkeypatch, tmp_path):
    v = dv.finde("01")
    kopie = tmp_path / v.datei
    kopie.write_bytes(dv.original(v) + b"\n% veraendert")
    monkeypatch.setattr(dv, "VERZEICHNIS", tmp_path)
    with pytest.raises(dv.Pruefsummenfehler):
        dv.fassung(v, "vollstaendig")


def test_blankovorlagen_ohne_werte_und_ohne_skripte():
    for v in dv.alle():
        daten = dv.original(v)
        assert not re.search(rb"/(JavaScript|JS|OpenAction|AA|Launch|SubmitForm|ImportData)\b", daten), v.id
        f = felder(daten)
        assert set(f) == {x["name"] for x in v.felder}
        for name, feld in f.items():
            assert feld.get("/V") in (None, "", "/Off"), (v.id, name)
        info = PdfReader(io.BytesIO(daten)).metadata
        assert info.get("/Author") == "Zahlenwerk Immobilien"


def test_feldnamen_eindeutig_auch_beim_zusammenfuehren():
    alle_namen = [x["name"] for v in dv.alle() for x in v.felder]
    assert len(alle_namen) == len(set(alle_namen)) == 141
    for v in dv.alle():
        assert all(x["name"].startswith(f"d{v.id}_") for x in v.felder)
    schreiber = PdfWriter()
    for v in dv.alle():
        schreiber.append(PdfReader(io.BytesIO(dv.original(v))))
    puffer = io.BytesIO()
    schreiber.write(puffer)
    zusammen = puffer.getvalue()
    assert set(felder(zusammen)) == set(alle_namen)
    gefuellt, erwartet = ausfuellen(zusammen)
    f = felder(gefuellt)
    for name, wert in erwartet.items():
        assert f[name].get("/V") == wert, name


# --- Ausfüllen, Speichern, erneut Öffnen ------------------------------------------

@pytest.mark.parametrize("vid", [f"{i:02d}" for i in range(1, 10)])
def test_ausfuellen_speichern_und_wieder_oeffnen(vid):
    v = dv.finde(vid)
    gefuellt, erwartet = ausfuellen(dv.original(v))
    assert len(erwartet) == len(v.felder)
    wieder = PdfReader(io.BytesIO(gefuellt))
    assert len(wieder.pages) == v.seiten
    f = wieder.get_fields()
    for name, wert in erwartet.items():
        assert f[name].get("/V") == wert, name
    # Das Original bleibt unberührt und ohne Werte
    assert all(x.get("/V") in (None, "", "/Off") for x in felder(dv.original(v)).values())
    # Zweiter Durchgang: erneut speichern, Werte bleiben
    puffer = io.BytesIO()
    PdfWriter(clone_from=wieder).write(puffer)
    assert felder(puffer.getvalue())[next(iter(erwartet))].get("/V") == erwartet[next(iter(erwartet))]


def _feldseiten(v):
    return {x["name"]: x["page"] for x in v.felder}


def test_fassung_zum_weitergeben_ohne_anwendungshilfe():
    for v in dv.alle():
        if v.id == "09":
            continue
        daten, name = dv.fassung(v, "formular")
        leser = PdfReader(io.BytesIO(daten))
        assert len(leser.pages) == v.seiten - 1
        assert "Anwendung und Prüfung" not in (leser.pages[-1].extract_text() or "")
        assert set(leser.get_fields() or {}) == {n for n, s in _feldseiten(v).items() if s < v.seiten}
        assert name.endswith(".pdf") and "ohne Anwendungshilfe" in name
        gefuellt, erwartet = ausfuellen(daten)
        assert all(felder(gefuellt)[n].get("/V") == w for n, w in erwartet.items())


def test_selbstauskunft_phasen_getrennt():
    v = dv.finde("09")
    seiten = _feldseiten(v)
    erwartung = {"phase-a": ([4, 1], "Datenschutzinformationen vor Erhebung"), "phase-b": ([2], "Phase B"), "phase-c": ([3], "Phase C")}
    for art, (nummern, ueberschrift) in erwartung.items():
        daten, _ = dv.fassung(v, art)
        leser = PdfReader(io.BytesIO(daten))
        assert len(leser.pages) == len(nummern)
        assert ueberschrift in leser.pages[0].extract_text()
        assert set(leser.get_fields() or {}) == {n for n, s in seiten.items() if s in nummern}
        texte = " ".join(p.extract_text() for p in leser.pages)
        assert "Anwendung und Prüfung" not in texte
    a = PdfReader(io.BytesIO(dv.fassung(v, "phase-a")[0]))
    assert "Phase A Besichtigung" in a.pages[1].extract_text()
    assert "Phase B" not in " ".join(p.extract_text() for p in a.pages).replace("Phasen B und C", "")


# --- Seiten und Rechte ------------------------------------------------------------

def _anmelden(client, email):
    mail.outbox.clear()
    r = client.post(reverse("anmelden"), {"email": email, "passwort": PASSWORT})
    assert r.status_code == 302
    code = re.search(r"Anmeldecode lautet: (\d{6})", mail.outbox[-1].body).group(1)
    assert client.post(reverse("anmelden_code"), {"code": code}).status_code == 302


def _kunde(nr: int):
    k = Kunde.objects.create(name=f"Beispielkunde {nr}", art="privat")
    b = Benutzer.objects.create_user(f"kunde{nr}@beispiel.test", PASSWORT, kunde=k)
    o = Objekt.objects.create(kunde=k, bezeichnung=f"Haus {nr}", anschrift=f"Musterweg {nr}", objektart="mfh")
    Freigabe.objects.create(benutzer=b, objekt=o)
    a = Auftrag.objects.create(objekt=o, leistung="betriebskosten", jahr=2025)
    p = Checklistenpunkt.objects.create(auftrag=a, text="Übergabeprotokoll")
    return b, a, p


def test_ohne_anmeldung_kein_zugriff(client):
    for url in (reverse("vorlagen"), reverse("vorlage_datei", args=["01", "vollstaendig"])):
        r = client.get(url)
        assert r.status_code == 302 and reverse("anmelden") in r.url


def test_kunden_sehen_bibliothek_erst_nach_freigabe(client, settings):
    settings.VORLAGEN_FUER_KUNDEN = False
    b, _, _ = _kunde(1)
    _anmelden(client, b.email)
    assert client.get(reverse("vorlagen")).status_code == 404
    assert client.get(reverse("vorlage_datei", args=["01", "vollstaendig"])).status_code == 404
    assert b"Vorlagen" not in client.get(reverse("start")).content


def test_zahlenwerk_sieht_vorschau(client, settings):
    settings.VORLAGEN_FUER_KUNDEN = False
    Benutzer.objects.create_user("chef@beispiel.test", PASSWORT, is_staff=True)
    _anmelden(client, "chef@beispiel.test")
    r = client.get(reverse("vorlagen"))
    assert r.status_code == 200
    assert "Vorschau." in r.content.decode()


def test_bibliothek_inhalt(client, settings):
    settings.VORLAGEN_FUER_KUNDEN = True
    b, _, _ = _kunde(1)
    _anmelden(client, b.email)
    assert b'href="/vorlagen/"' in client.get(reverse("start")).content
    html = client.get(reverse("vorlagen")).content.decode()
    for t in TITEL:
        assert t in html
    assert html.count(">Prüfstand 01.11.2026<") == 9
    assert html.count(">Wohnraumvermietung<") == 9
    assert "keine Beschlussvorlagen für Eigentümergemeinschaften (WEG)" in html
    for satz in ("rechnen aber nicht automatisch", "ersetzt keine gesetzlich vorgeschriebene Unterschrift",
                 "Anwendungshilfe und wird nicht", "Das Portal versendet nichts"):
        assert satz in html
    for phase in ("Phase A · Besichtigung", "Phase B · Konkretes Anmietungsinteresse", "Phase C · Ausgewählte Vertragspartei"):
        assert phase in html
    assert "Datenschutzinformationen (Seite 4) mit Phase A (Seite 1)" in html
    tief = html.lower()
    assert "anwaltlich geprüft" not in tief and "rechtssicher" not in tief and "garantiert" not in tief
    assert "Vorschau." not in html
    assert not re.search(r"\+49|\(0\d|\b0\d{2,4}[ /]\d{3,}", html)


def test_alle_downloads_entsprechen_dem_freigegebenen_stand(client, settings):
    settings.VORLAGEN_FUER_KUNDEN = True
    b, _, _ = _kunde(1)
    _anmelden(client, b.email)
    for v in dv.alle():
        r = client.get(reverse("vorlage_datei", args=[v.id, "vollstaendig"]))
        assert r.status_code == 200 and r["Content-Type"] == "application/pdf"
        assert hashlib.sha256(r.content).hexdigest() == v.sha256
        assert r["Content-Disposition"].startswith("attachment; filename*=UTF-8''")
        assert r["Cache-Control"] == "no-store"
        for f in v.fassungen[1:]:
            r = client.get(reverse("vorlage_datei", args=[v.id, f.schluessel]))
            assert r.status_code == 200 and r.content.startswith(b"%PDF-")
        r = client.get(reverse("vorlage_datei", args=[v.id, "markdown"]))
        assert r.status_code == 200 and r.content == dv.markdown(v) and b"typ: dokumentvorlage" in r.content[:40]
    assert client.get(reverse("vorlage_datei", args=["10", "vollstaendig"])).status_code == 404
    assert client.get(reverse("vorlage_datei", args=["01", "phase-a"])).status_code == 404
    assert client.get(reverse("vorlage_datei", args=["..", "vollstaendig"])).status_code == 404
    eintraege = Protokoll.objects.filter(aktion="vorlage_download")
    assert eintraege.count() == 8 * 3 + 5  # je Vorlage vollständig, Auszug(e) und Text; 09 mit drei Phasen
    assert {e.bezug for e in eintraege} == {f"vorlage:{v.id}" for v in dv.alle()}


def test_fremde_ausgefuellte_dokumente_nicht_erreichbar(client, settings, caplog):
    """Zwei getrennte Testkonten: Kunde 2 erreicht das ausgefüllte Protokoll von Kunde 1 nicht."""
    settings.VORLAGEN_FUER_KUNDEN = True
    caplog.set_level(logging.DEBUG)
    b1, a1, p1 = _kunde(1)
    b2, a2, _ = _kunde(2)
    _anmelden(client, b1.email)
    blanko = client.get(reverse("vorlage_datei", args=["08", "formular"])).content
    gefuellt, _ = ausfuellen(blanko)
    from django.core.files.uploadedfile import SimpleUploadedFile

    r = client.post(reverse("auftrag", args=[a1.pk]), {
        "punkt": p1.pk, "zeitraum": "", "hinweis": "", "berechtigt": "on",
        "datei": SimpleUploadedFile("Übergabe Musterweg 1.pdf", gefuellt, content_type="application/pdf"),
    })
    assert r.status_code == 302, r.content.decode()[:1500]
    d = Dokument.objects.get(auftrag=a1)
    # Kunde 1 erhält genau seine Datei zurück
    r = client.get(reverse("dokument", args=[d.pk]))
    r = client.get(r.url)
    assert r.status_code == 200 and r.content == gefuellt
    eigener_link = reverse("download", args=[download_token(b1.pk, d.pk)])

    andere = Client()
    _anmelden(andere, b2.email)
    assert andere.get(reverse("dokument", args=[d.pk])).status_code == 404
    assert andere.get(eigener_link).status_code == 404
    assert andere.get(reverse("download", args=[download_token(b2.pk, d.pk)])).status_code == 404
    assert andere.get(reverse("auftrag", args=[a1.pk])).status_code == 404
    assert "Übergabe Musterweg 1" not in andere.get(reverse("auftrag", args=[a2.pk])).content.decode()
    assert not Dokument.objects.filter(auftrag=a2).exists()

    # Keine Personendaten in Protokoll oder Logs
    protokolltext = " ".join(f"{e.aktion} {e.bezug} {e.details}" for e in Protokoll.objects.all())
    for geheim in ("Müller", "Gärtnerstraße", "Übergabe Musterweg"):
        assert geheim not in protokolltext
        assert geheim not in caplog.text
