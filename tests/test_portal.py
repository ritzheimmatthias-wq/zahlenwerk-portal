"""Ablauf des Kundenportals mit erfundenen Daten: Anlegen, Einladung, Anmeldung, Upload, Prüfung, Löschung."""
import io
import os
import re
import socket
import threading
import zipfile
from datetime import timedelta

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from kundenportal import tresor
from kundenportal.dateipruefung import Abgelehnt, pruefe_datei, sicherer_name
from kundenportal.loeschung import loeschlauf
from kundenportal.models import Auftrag, Benutzer, Checklistenpunkt, Dokument, Freigabe, Kunde, Objekt, Protokoll
from kundenportal.sicherheit import download_token, iban_gueltig

pytestmark = pytest.mark.django_db

PASSWORT = "Beispiel-Passwort-2026!"
CSV = "Buchungstag;Betrag\n02.01.2025;310,00\n".encode()


@pytest.fixture(autouse=True)
def datenordner(settings, tmp_path):
    settings.DATEN_VERZEICHNIS = tmp_path / "daten"
    return settings.DATEN_VERZEICHNIS


@pytest.fixture
def chef(db):
    return Benutzer.objects.create_user("chef@beispiel.test", PASSWORT, is_staff=True)


def anmelden(client, email, passwort=PASSWORT):
    mail.outbox.clear()
    r = client.post(reverse("anmelden"), {"email": email, "passwort": passwort})
    assert r.status_code == 302 and r.url == reverse("anmelden_code"), r.content[:500]
    code = re.search(r"Anmeldecode lautet: (\d{6})", mail.outbox[-1].body).group(1)
    r = client.post(reverse("anmelden_code"), {"code": code})
    assert r.status_code == 302
    return r


def anlegen_daten(**extra):
    d = {
        "kunde_art": "weg", "kunde_name": "WEG Gartenweg 5 (Beispiel)", "ansprechpartner": "Beirat",
        "email": "beirat@beispiel.test", "bezeichnung": "WEG Gartenweg 5", "anschrift": "Gartenweg 5",
        "ort": "12345 Musterstadt", "objektart": "weg", "baujahr": "1972", "wirtschaftsjahr": "Kalenderjahr",
        "heizung": "", "leistung": "weg_jahresabrechnung", "jahr": "2025", "einladen": "on",
        "konto-TOTAL_FORMS": "2", "konto-INITIAL_FORMS": "0",
        "konto-0-bezeichnung": "Girokonto", "konto-0-art": "giro", "konto-0-iban": "DE89 3704 0044 0532 0130 00",
        "konto-1-bezeichnung": "Rücklage", "konto-1-art": "ruecklage", "konto-1-iban": "",
        "einheit-TOTAL_FORMS": "2", "einheit-INITIAL_FORMS": "0",
        "einheit-0-nummer": "WE 1", "einheit-0-eigentuemer": "Eigentümer A", "einheit-0-mea": "500",
        "einheit-0-hausgeld": "280,50", "einheit-0-ab": "", "einheit-0-iban": "",
        "einheit-1-nummer": "WE 2", "einheit-1-eigentuemer": "", "einheit-1-mea": "", "einheit-1-hausgeld": "",
        "einheit-1-ab": "", "einheit-1-iban": "",
    }
    d.update(extra)
    return d


@pytest.fixture
def kunde_angemeldet(client, chef):
    """Legt als Zahlenwerk ein Objekt an, löst die Einladung ein und meldet den Kunden an."""
    anmelden(client, chef.email)
    mail.outbox.clear()
    r = client.post(reverse("intern_anlegen"), anlegen_daten())
    assert r.status_code == 302, r.content.decode()[:2000]
    a = Auftrag.objects.get()
    einladung = next(m for m in mail.outbox if "Zugang zum Kundenportal" in m.subject)
    token = re.search(r"/einladung/([^/\s]+)/", einladung.body).group(1)
    client.post(reverse("abmelden"))
    r = client.post(reverse("einladung", args=[token]), {"new_password1": PASSWORT, "new_password2": PASSWORT})
    assert r.status_code == 302
    anmelden(client, "beirat@beispiel.test")
    return a


# --- Anlegen und Einladung ----------------------------------------------------------

def test_anlegen_erzeugt_kunde_objekt_auftrag_und_checkliste(client, chef):
    anmelden(client, chef.email)
    r = client.post(reverse("intern_anlegen"), anlegen_daten())
    assert r.status_code == 302
    o = Objekt.objects.get()
    assert o.kunde.name == "WEG Gartenweg 5 (Beispiel)"
    assert [k.iban for k in o.konten.all()] == ["DE89370400440532013000"]
    e = o.einheiten.get()
    assert (e.nummer, str(e.hausgeld), str(e.mea)) == ("WE 1", "280.50", "500.0000")
    a = o.auftraege.get()
    assert a.punkte.count() == 7 and a.punkte.first().status == "offen"
    b = Benutzer.objects.get(email="beirat@beispiel.test")
    assert not b.has_usable_password() and Freigabe.objects.filter(benutzer=b, objekt=o).exists()
    einladung = mail.outbox[-1]
    assert "/einladung/" in einladung.body and "Passwort" in einladung.body


def test_anlegen_prueft_iban(client, chef):
    anmelden(client, chef.email)
    r = client.post(reverse("intern_anlegen"), anlegen_daten(**{"konto-0-iban": "DE89370400440532013001"}))
    assert r.status_code == 200 and "IBAN ungültig" in r.content.decode()
    assert not Objekt.objects.exists()


def test_anlegen_mit_vergebener_adresse(client, chef):
    anmelden(client, chef.email)
    client.post(reverse("intern_anlegen"), anlegen_daten())
    r = client.post(reverse("intern_anlegen"), anlegen_daten(kunde_name="Anderer Kunde"))
    assert r.status_code == 200 and "gehört bereits" in r.content.decode()
    assert Objekt.objects.count() == 1
    r = client.post(reverse("intern_anlegen"), anlegen_daten(email=chef.email))
    assert "gehört bereits" in r.content.decode()


def test_weiteres_objekt_fuer_bestehenden_kunden(client, chef):
    anmelden(client, chef.email)
    client.post(reverse("intern_anlegen"), anlegen_daten())
    k = Kunde.objects.get()
    r = client.post(reverse("intern_anlegen"), anlegen_daten(bestehender_kunde=k.pk, kunde_name="", email="", bezeichnung="Zweites Objekt"))
    assert r.status_code == 302
    b = Benutzer.objects.get(email="beirat@beispiel.test")
    assert set(b.freigaben.values_list("objekt__bezeichnung", flat=True)) == {"WEG Gartenweg 5", "Zweites Objekt"}


def test_einladung_nur_einmal_gueltig(client, chef):
    anmelden(client, chef.email)
    client.post(reverse("intern_anlegen"), anlegen_daten())
    token = re.search(r"/einladung/([^/\s]+)/", mail.outbox[-1].body).group(1)
    client.post(reverse("abmelden"))
    client.post(reverse("einladung", args=[token]), {"new_password1": PASSWORT, "new_password2": PASSWORT})
    assert client.get(reverse("einladung", args=[token])).status_code == 410


def test_einladung_laeuft_ab(client, chef):
    anmelden(client, chef.email)
    client.post(reverse("intern_anlegen"), anlegen_daten())
    token = re.search(r"/einladung/([^/\s]+)/", mail.outbox[-1].body).group(1)
    Benutzer.objects.get(email="beirat@beispiel.test").einladungen.update(gueltig_bis=timezone.now() - timedelta(seconds=1))
    assert client.get(reverse("einladung", args=[token])).status_code == 410


# --- Anmeldung -------------------------------------------------------------------------

def test_ohne_anmeldung_nur_anmeldeseite(client):
    assert client.get("/").url == reverse("anmelden")
    assert client.get(reverse("intern")).url == reverse("anmelden")


def test_ohne_code_keine_anmeldung(client, chef):
    client.post(reverse("anmelden"), {"email": chef.email, "passwort": PASSWORT})
    assert client.get(reverse("intern")).status_code == 302


def test_falscher_code_wird_nach_fuenf_versuchen_ungueltig(client, chef):
    client.post(reverse("anmelden"), {"email": chef.email, "passwort": PASSWORT})
    code = re.search(r"(\d{6})", mail.outbox[-1].body).group(1)
    falsch = "000000" if code != "000000" else "111111"
    for _ in range(5):
        client.post(reverse("anmelden_code"), {"code": falsch})
    r = client.post(reverse("anmelden_code"), {"code": code})
    assert r.status_code == 200  # richtiger Code danach nicht mehr gültig
    assert client.get(reverse("intern")).status_code == 302


def test_sperre_nach_fehlversuchen(client, chef):
    for _ in range(5):
        client.post(reverse("anmelden"), {"email": chef.email, "passwort": "falsch-falsch"})
    r = client.post(reverse("anmelden"), {"email": chef.email, "passwort": PASSWORT})
    assert r.status_code == 200 and "Zu viele Fehlversuche" in r.content.decode()


def test_code_mail_ohne_weitere_inhalte(client, chef):
    client.post(reverse("anmelden"), {"email": chef.email, "passwort": PASSWORT})
    assert mail.outbox[-1].to == [chef.email]
    assert "Anmeldecode" in mail.outbox[-1].subject


# --- Rechte ------------------------------------------------------------------------------

def test_kunde_hat_keinen_zugriff_auf_intern(client, kunde_angemeldet):
    assert client.get(reverse("intern")).status_code == 403


def test_kunde_sieht_nur_eigene_auftraege(client, kunde_angemeldet):
    fremd_kunde = Kunde.objects.create(name="Anderer Kunde")
    fremd = Objekt.objects.create(kunde=fremd_kunde, bezeichnung="Fremdes Objekt", anschrift="X")
    fa = Auftrag.objects.create(objekt=fremd, leistung="ankauf", jahr=2025)
    assert client.get(reverse("auftrag", args=[fa.pk])).status_code == 404
    assert client.get(reverse("objekt", args=[fremd.pk])).status_code == 404
    seite = client.get("/").content.decode()
    assert "WEG Gartenweg 5" in seite and "Fremdes Objekt" not in seite
    assert "Guten Tag" in seite


# --- Upload, Verschlüsselung, Download ----------------------------------------------------

def hochladen(client, a, name="auszug.csv", inhalt=CSV, **extra):
    punkt = a.punkte.first()
    daten = {"punkt": punkt.pk, "zeitraum": "2025", "hinweis": "", "berechtigt": "on",
             "datei": _datei(name, inhalt)}
    daten.update(extra)
    return client.post(reverse("auftrag", args=[a.pk]), daten)


def _datei(name, inhalt):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(name, inhalt)


def test_upload_wird_verschluesselt_gespeichert(client, kunde_angemeldet, datenordner):
    a = kunde_angemeldet
    mail.outbox.clear()
    r = hochladen(client, a)
    assert r.status_code == 302
    d = Dokument.objects.get()
    assert d.dateityp == "csv" and d.groesse == len(CSV)
    dateien = [p for p in datenordner.rglob("*.bin")]
    assert len(dateien) == 1
    roh = dateien[0].read_bytes()
    assert b"Buchungstag" not in roh and b"310,00" not in roh
    assert tresor.lesen(d.speicher_id, d.schluessel) == CSV
    assert Checklistenpunkt.objects.get(pk=d.punkt_id).status == "eingereicht"
    assert Protokoll.objects.filter(aktion="upload").exists()


def test_download_nur_mit_recht_und_kurzlebigem_link(client, kunde_angemeldet, settings):
    a = kunde_angemeldet
    hochladen(client, a)
    d = Dokument.objects.get()
    r = client.get(reverse("dokument", args=[d.pk]))
    assert r.status_code == 302 and "/download/" in r.url
    inhalt = client.get(r.url)
    assert inhalt.status_code == 200 and inhalt.content == CSV
    assert "attachment" in inhalt["Content-Disposition"]
    # Link eines anderen Benutzers gilt nicht
    assert client.get(reverse("download", args=[download_token(99999, d.pk)])).status_code == 404
    # abgelaufener Link
    settings.DOWNLOAD_LINK_SEKUNDEN = -1
    assert client.get(r.url).status_code == 404


def test_ungeeignete_dateien_werden_abgelehnt(client, kunde_angemeldet):
    a = kunde_angemeldet
    r = hochladen(client, a, "rechnung.pdf", b"MZ\x90\x00 kein pdf")
    assert r.status_code == 200 and "Ausführbare Dateien" in r.content.decode()
    r = hochladen(client, a, "archiv.zip", b"PK\x03\x04")
    assert "nicht erlaubt" in r.content.decode()
    assert not Dokument.objects.exists()


def test_upload_ohne_bestaetigung_abgelehnt(client, kunde_angemeldet):
    r = hochladen(client, kunde_angemeldet, berechtigt="")
    assert r.status_code == 200 and not Dokument.objects.exists()


def _office(namen: dict[str, bytes]) -> bytes:
    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w", zipfile.ZIP_DEFLATED) as z:
        for n, inhalt in namen.items():
            z.writestr(n, inhalt)
    return puffer.getvalue()


TYPES = b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'


@pytest.mark.parametrize("name,inhalt,erwartet", [
    ("a.pdf", b"%PDF-1.7\n1 0 obj<<>>endobj", "pdf"),
    ("a.png", b"\x89PNG\r\n\x1a\n" + b"0" * 20, "png"),
    ("a.JPG", b"\xff\xd8\xff\xe0" + b"0" * 20, "jpg"),
    ("a.docx", _office({"[Content_Types].xml": TYPES, "word/document.xml": b"<w/>"}), "docx"),
    ("a.xlsx", _office({"[Content_Types].xml": TYPES, "xl/workbook.xml": b"<w/>"}), "xlsx"),
    ("a.xml", b'<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02"/>', "xml"),
])
def test_erlaubte_dateien(name, inhalt, erwartet):
    assert pruefe_datei(name, inhalt).dateityp == erwartet


@pytest.mark.parametrize("name,inhalt,text", [
    ("a.pdf", b"%PDF-1.4 /OpenAction << /S /JavaScript >>", "ausführbare Inhalte"),
    ("a.pdf", b"kein pdf", "kein gültiges PDF"),
    ("a.docx", _office({"[Content_Types].xml": TYPES, "word/document.xml": b"x", "word/vbaProject.bin": b"x"}), "Makros"),
    ("a.xlsx", _office({"[Content_Types].xml": TYPES.replace(b"/>", b'><Default ContentType="application/vnd.ms-excel.sheet.macroEnabled"/></Types>'), "xl/a.xml": b"x"}), "Makros"),
    ("a.xlsx", _office({"[Content_Types].xml": TYPES, "xl/a.xml": b"0" * 3_000_000}), "stark gepackt"),
    ("a.docx", b"keine zip", "kein gültiges DOCX"),
    ("a.csv", b"PK\x03\x04abc", "Archiv"),
    ("a.csv", b"a;b\x00c", "Binärdaten"),
    ("a.png", b"GIF89a", "kein gültiges PNG"),
    ("a.xml", b"<!DOCTYPE x [<!ENTITY a 'b'>]><x>&a;</x>", "ungültig"),
    ("a.xml", b"<andere/>", "nur XML-Kontoauszüge"),
    ("a.exe", b"MZ", "nicht erlaubt"),
    ("a.docm", b"x", "nicht erlaubt"),
    ("a.csv", b"", "leer"),
])
def test_abgelehnte_dateien(name, inhalt, text):
    with pytest.raises(Abgelehnt, match=text):
        pruefe_datei(name, inhalt)


def test_dateiname_wird_bereinigt():
    assert sicherer_name("../../etc/passwd") == "passwd"
    assert sicherer_name('C:\\x\\a"b<c>.pdf') == "a_b_c_.pdf"


# --- Virenscan -------------------------------------------------------------------------------

def _clamd(antwort: bytes, pfad: str):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(pfad)
    s.listen(1)

    def bedienen():
        conn, _ = s.accept()
        with conn:
            daten = b""
            while not daten.endswith(b"\0\0\0\0"):
                stueck = conn.recv(65536)
                if not stueck:
                    break
                daten += stueck
            conn.sendall(antwort)
        s.close()

    threading.Thread(target=bedienen, daemon=True).start()


@pytest.mark.parametrize("antwort,angenommen", [(b"stream: OK\0", True), (b"stream: Eicar-Test-Signature FOUND\0", False)])
def test_virenscan(client, kunde_angemeldet, settings, tmp_path, antwort, angenommen):
    pfad = str(tmp_path / "clamd.sock")
    settings.VIRENSCAN, settings.CLAMD_SOCKET = "clamd", pfad
    _clamd(antwort, pfad)
    r = hochladen(client, kunde_angemeldet)
    assert Dokument.objects.exists() is angenommen
    if angenommen:
        assert Dokument.objects.get().scan == "sauber"
    else:
        assert "Schadsoftware" in r.content.decode()
        assert Protokoll.objects.filter(aktion="virenfund").exists()


def test_virenscanner_nicht_erreichbar_lehnt_ab(client, kunde_angemeldet, settings, tmp_path):
    settings.VIRENSCAN, settings.CLAMD_SOCKET = "clamd", str(tmp_path / "fehlt.sock")
    r = hochladen(client, kunde_angemeldet)
    assert "nicht geprüft werden" in r.content.decode()
    assert not Dokument.objects.exists()


# --- Prüfung, Ergebnis, Abschluss, Löschung --------------------------------------------------

def test_nachreichen_mit_hinweis_ohne_inhalt(client, kunde_angemeldet, chef):
    a = kunde_angemeldet
    hochladen(client, a)
    d = Dokument.objects.get()
    client.post(reverse("abmelden"))
    anmelden(client, chef.email)
    mail.outbox.clear()
    client.post(reverse("intern_pruefen", args=[d.pk]), {"aktion": "nachreichen", "kommentar": "August fehlt"})
    p = Checklistenpunkt.objects.get(pk=d.punkt_id)
    assert (p.status, p.kommentar) == ("nachreichen", "August fehlt")
    hinweis = mail.outbox[-1]
    assert hinweis.to == ["beirat@beispiel.test"]
    assert "August fehlt" not in hinweis.body and "auszug.csv" not in hinweis.body


def test_ergebnis_bereitstellen_und_abrufen(client, kunde_angemeldet, chef):
    a = kunde_angemeldet
    client.post(reverse("abmelden"))
    anmelden(client, chef.email)
    client.post(reverse("intern_ergebnis", args=[a.pk]), {"datei": _datei("bericht.pdf", b"%PDF-1.7 Bericht")})
    d = Dokument.objects.get(richtung="ergebnis")
    client.post(reverse("abmelden"))
    anmelden(client, "beirat@beispiel.test")
    seite = client.get(reverse("auftrag", args=[a.pk])).content.decode()
    assert "bericht.pdf" in seite and "neu" in seite
    r = client.get(reverse("dokument", args=[d.pk]))
    assert client.get(r.url).content == b"%PDF-1.7 Bericht"
    d.refresh_from_db()
    assert d.gelesen is not None


def test_loeschjob_nach_frist(client, kunde_angemeldet, datenordner):
    a = kunde_angemeldet
    hochladen(client, a)
    a.status, a.abgeschlossen = "abgeschlossen", timezone.now()
    a.save()
    assert a.loeschdatum == (timezone.now() + timedelta(days=90)).date()
    assert loeschlauf() == 0  # Frist noch nicht erreicht
    assert loeschlauf(heute=a.loeschdatum) == 1
    d = Dokument.objects.get()
    assert d.geloescht and bytes(d.schluessel) == b""
    assert not list(datenordner.rglob("*.bin"))
    assert Protokoll.objects.filter(aktion="dokument_geloescht").exists()
    with pytest.raises(tresor.TresorFehler):
        tresor.lesen(d.speicher_id, d.schluessel)


def test_abgeschlossener_auftrag_nimmt_keine_uploads(client, kunde_angemeldet):
    a = kunde_angemeldet
    Auftrag.objects.filter(pk=a.pk).update(status="abgeschlossen", abgeschlossen=timezone.now())
    assert hochladen(client, a).status_code == 404


def test_sofortloeschung_braucht_bestaetigung(client, kunde_angemeldet, chef):
    a = kunde_angemeldet
    hochladen(client, a)
    client.post(reverse("abmelden"))
    anmelden(client, chef.email)
    client.post(reverse("intern_loeschen", args=[a.pk]), {})
    assert Dokument.objects.get().geloescht is None
    client.post(reverse("intern_loeschen", args=[a.pk]), {"bestaetigt": "ja"})
    assert Dokument.objects.get().geloescht is not None


# --- Sonstiges --------------------------------------------------------------------------------

def test_iban_pruefung():
    assert iban_gueltig("DE89 3704 0044 0532 0130 00")
    assert not iban_gueltig("DE89 3704 0044 0532 0130 01")
    assert not iban_gueltig("DE89")


def test_sicherheitskoepfe(client):
    r = client.get(reverse("anmelden"))
    assert "default-src 'self'" in r["Content-Security-Policy"]
    assert r["X-Frame-Options"] == "DENY"
    assert r["Cache-Control"] == "no-store"
    assert "fonts.googleapis" not in r.content.decode()


def test_weiterleitung_nur_intern(client, chef, kunde_angemeldet):
    client.post(reverse("abmelden"))
    anmelden(client, chef.email)
    b = Benutzer.objects.get(email="beirat@beispiel.test")
    r = client.post(reverse("intern_einladen", args=[b.pk]), {"weiter": "https://angreifer.example/"})
    assert r.url == reverse("intern")


def test_keine_telefonnummer_in_seiten(client, kunde_angemeldet):
    for url in ["/", reverse("auftrag", args=[kunde_angemeldet.pk])]:
        assert not re.search(r"\+49|tel:", client.get(url).content.decode())
