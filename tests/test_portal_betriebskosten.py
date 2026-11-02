"""Portal: Abrechnungsläufe, Jahresreports, Rollen, Mandantentrennung, Smartphone-Erfassung, Eingang belege@.

Nur erfundene Daten. Kein echter Mailversand (locmem), kein echtes Postfach.
"""
import hashlib
import io
import json
import time
from datetime import datetime, timezone as tz

import pytest
from django.core import mail
from django.core.management import call_command
from django.urls import reverse

from beispiel import pdf_muster
from beispiel.portal_abrechnung import toml_jahr
from kundenportal import belege, tresor
from kundenportal.models import (
    Abrechnungslauf, Auftrag, Belegvorgang, Benutzer, Dokument, Eingangsdatei, Freigabe, Kunde, Maileingang, Objekt, Report,
    Statuswechsel,
)
from tests.test_portal import PASSWORT, anmelden

pytestmark = pytest.mark.django_db

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
JPG = b"\xff\xd8\xff\xe0" + b"1" * 64
GEHEIM = "test-geheimnis-nur-fuer-tests"




def datei(name, inhalt):
    f = io.BytesIO(inhalt)
    f.name = name
    return f


@pytest.fixture(autouse=True)
def umgebung(settings, tmp_path):
    settings.DATEN_VERZEICHNIS = tmp_path / "daten"
    settings.BELEGE_WEBHOOK_GEHEIMNIS = GEHEIM
    settings.MOBILE_ERFASSUNG_FUER_KUNDEN = False
    settings.BELEGE_EINGANGSBESTAETIGUNG = False


@pytest.fixture
def welt(db):
    chef = Benutzer.objects.create_user("chef@beispiel.test", PASSWORT, is_staff=True)
    k1, k2 = Kunde.objects.create(name="Eigentümerin A (fiktiv)"), Kunde.objects.create(name="Eigentümer B (fiktiv)")
    o1 = Objekt.objects.create(kunde=k1, bezeichnung="Portalhaus", anschrift="Portalweg 1")
    o2 = Objekt.objects.create(kunde=k2, bezeichnung="Fremdhaus", anschrift="Fremdweg 2")
    a24 = Auftrag.objects.create(objekt=o1, leistung="betriebskosten", jahr=2024, status="abgeschlossen")
    a25 = Auftrag.objects.create(objekt=o1, leistung="betriebskosten", jahr=2025)
    b25 = Auftrag.objects.create(objekt=o2, leistung="betriebskosten", jahr=2025)
    eig = Benutzer.objects.create_user("eigentuemerin@beispiel.test", PASSWORT, kunde=k1)
    fremd = Benutzer.objects.create_user("fremd@beispiel.test", PASSWORT, kunde=k2)
    Freigabe.objects.create(benutzer=eig, objekt=o1)
    Freigabe.objects.create(benutzer=fremd, objekt=o2)
    return {"chef": chef, "eig": eig, "fremd": fremd, "o1": o1, "o2": o2, "a24": a24, "a25": a25, "b25": b25}


def lauf_endgueltig(client, auftrag, daten):
    r = client.post(reverse("intern_lauf_anlegen", args=[auftrag.pk]), {"daten": datei("daten.toml", daten)})
    assert r.status_code == 302
    lauf = auftrag.abrechnungslaeufe.order_by("-version").first()
    for s in ("fachlich_geprueft", "eigentuemer_freigegeben", "endgueltig"):
        client.post(reverse("intern_lauf_status", args=[lauf.pk]), {"status": s})
    lauf.refresh_from_db()
    assert lauf.status == "endgueltig"
    return lauf


# --- Abrechnungsläufe und Reports ---------------------------------------------------------

def test_lauf_anlegen_versionieren_und_unveraenderlich(client, welt):
    anmelden(client, "chef@beispiel.test")
    url = reverse("intern_lauf_anlegen", args=[welt["a25"].pk])
    client.post(url, {"daten": datei("d.toml", toml_jahr(2025, welt["o1"].kennung))})
    client.post(url, {"daten": datei("d.toml", toml_jahr(2025, welt["o1"].kennung))})  # gleiche Eingabe → kein neuer Stand
    assert Abrechnungslauf.objects.filter(auftrag=welt["a25"]).count() == 1
    client.post(url, {"daten": datei("d.toml", toml_jahr(2025, welt["o1"].kennung, grundsteuer="1100.00"))})
    l1, l2 = Abrechnungslauf.objects.filter(auftrag=welt["a25"]).order_by("version")
    assert (l1.version, l2.version) == (1, 2) and l1.eingabe_hash != l2.eingabe_hash
    # Rechenschritte gespeichert: Grundsteuer 1.100 nach 60/40 m²
    ks = {k["kostenart"]: k for k in l2.stand["ergebnis"]["kontrolle"]}
    assert [a["brutto"] for a in ks["grundsteuer"]["schritte"][0]["anteile"]] == ["660.00", "440.00"]
    l1.stand["ergebnis"]["kontrolle"] = []
    with pytest.raises(ValueError, match="unveränderlich"):
        l1.save()
    # Nur der neueste Stand kann freigegeben werden
    client.post(reverse("intern_lauf_status", args=[l1.pk]), {"status": "fachlich_geprueft"})
    l1.refresh_from_db()
    assert l1.status == "entwurf"


def test_rote_warnung_sperrt_und_fehlerhafte_daten(client, welt):
    anmelden(client, "chef@beispiel.test")
    url = reverse("intern_lauf_anlegen", args=[welt["a25"].pk])
    kaputt = toml_jahr(2025, welt["o1"].kennung).replace(b'umlagefaehig_bestaetigt = true\n[[kostenarten]]\nid = "heizung"', b'[[kostenarten]]\nid = "heizung"')
    client.post(url, {"daten": datei("d.toml", kaputt)})
    lauf = Abrechnungslauf.objects.get()
    assert lauf.rote_warnungen == 1
    r = client.post(reverse("intern_lauf_status", args=[lauf.pk]), {"status": "fachlich_geprueft"}, follow=True)
    assert "Rote Warnungen" in r.content.decode()
    r = client.post(url, {"daten": datei("d.toml", b"kein toml = [")}, follow=True)
    assert "keine gültige TOML-Datei" in r.content.decode()
    r = client.post(url, {"daten": datei("d.toml", toml_jahr(2025, welt["o1"].kennung).replace(b'"1000.00"', b"1000.0"))}, follow=True)
    assert "als Text" in r.content.decode()


def test_reports_rollen_und_mandantentrennung(client, welt):
    anmelden(client, "chef@beispiel.test")
    lauf_endgueltig(client, welt["a24"], toml_jahr(2024, welt["o1"].kennung, heizung="900.00"))
    l25 = lauf_endgueltig(client, welt["a25"], toml_jahr(2025, welt["o1"].kennung, heizung="1500.00"))
    o1 = welt["o1"]
    client.post(reverse("intern_report_erstellen", args=[o1.pk]), {"art": "vorauszahlung", "lauf": l25.pk})
    client.post(reverse("intern_report_erstellen", args=[o1.pk]), {"art": "kostenverlauf"})
    vz, kv = Report.objects.get(art="vorauszahlung"), Report.objects.get(art="kostenverlauf")
    assert set(kv.laeufe.values_list("auftrag__jahr", flat=True)) == {2024, 2025}
    # Kostenverlauf: 1.900 → 2.500 (+600, +31,6 %), Heizung +600 ist Kostentreiber
    v = kv.inhalt["vergleich"]
    assert (v["gesamt"]["delta"], v["gesamt"]["prozent"], v["kostentreiber"][0]["id"]) == ("600.00", "31.6", "heizung")
    # Vorauszahlung MV1: Heizung 500 / 12 = 41,67 gegen 40 → innerhalb der Grenze (5,00)
    mv1 = next(m for m in vz.inhalt["mietverhaeltnisse"] if m["id"] == "MV1")
    hk = next(a for a in mv1["arten"] if a["art"] == "heizkosten")
    assert (hk["monatskosten"], hk["empfehlung"]) == ("41.67", "unveraendert")
    # Portalansicht für Zahlenwerk: ohne Inline-Stil, eigenes Stylesheet, CSP gesetzt
    r = client.get(reverse("bericht", args=[kv.pk]))
    html = r.content.decode()
    assert r.status_code == 200 and "<style" not in html and 'href="/bericht.css"' in html and "style=" not in html
    assert "default-src 'self'" in r["Content-Security-Policy"] and "ENTWURF" in html
    assert client.get(reverse("bericht_css"))["Content-Type"].startswith("text/css")
    client.post(reverse("abmelden"))

    # Eigentümerin sieht Entwürfe nicht
    anmelden(client, "eigentuemerin@beispiel.test")
    assert client.get(reverse("bericht", args=[kv.pk])).status_code == 404
    assert client.get(reverse("bericht_pdf", args=[kv.pk])).status_code == 404
    assert "Jahresreports" not in client.get(reverse("objekt", args=[o1.pk])).content.decode()
    assert client.get(reverse("intern_abrechnung_objekt", args=[o1.pk])).status_code == 403
    assert client.post(reverse("intern_report_status", args=[kv.pk]), {"status": "fachlich_geprueft"}).status_code == 403
    client.post(reverse("abmelden"))

    anmelden(client, "chef@beispiel.test")
    for s in ("fachlich_geprueft", "freigegeben"):
        client.post(reverse("intern_report_status", args=[kv.pk]), {"status": s})
    kv.refresh_from_db()
    assert kv.status == "freigegeben"
    assert list(Statuswechsel.objects.filter(report=kv).values_list("status", flat=True)) == ["entwurf", "fachlich_geprueft", "freigegeben"]
    client.post(reverse("abmelden"))

    anmelden(client, "eigentuemerin@beispiel.test")
    seite = client.get(reverse("objekt", args=[o1.pk])).content.decode()
    assert "Kostenverlaufsbericht" in seite and "Vorauszahlungsanalyse" not in seite
    html = client.get(reverse("bericht", args=[kv.pk])).content.decode()
    pdf = client.get(reverse("bericht_pdf", args=[kv.pk]))
    assert pdf.status_code == 200 and pdf["Content-Type"] == "application/pdf"
    from pypdf import PdfReader

    text = " ".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages)
    for wert in ("2.500,00 €", "1.900,00 €", "600,00 €", "+31,6 %"):
        assert wert in html and wert in text, wert
    assert "ENTWURF" not in html
    client.post(reverse("abmelden"))

    # Anderer Kunde: weder Objekt noch Report
    anmelden(client, "fremd@beispiel.test")
    assert client.get(reverse("bericht", args=[kv.pk])).status_code == 404
    assert client.get(reverse("bericht_pdf", args=[kv.pk])).status_code == 404
    assert client.get(reverse("objekt", args=[o1.pk])).status_code == 404


def test_report_nur_aus_endgueltigem_stand(client, welt):
    anmelden(client, "chef@beispiel.test")
    client.post(reverse("intern_lauf_anlegen", args=[welt["a25"].pk]), {"daten": datei("d.toml", toml_jahr(2025, welt["o1"].kennung))})
    lauf = Abrechnungslauf.objects.get()
    r = client.post(reverse("intern_report_erstellen", args=[welt["o1"].pk]), {"art": "vorauszahlung", "lauf": lauf.pk}, follow=True)
    assert "endgültig" in r.content.decode() and not Report.objects.exists()
    # Lauf eines fremden Objekts kann nicht für dieses Objekt verwendet werden
    r = client.post(reverse("intern_report_erstellen", args=[welt["o2"].pk]), {"art": "vorauszahlung", "lauf": lauf.pk})
    assert r.status_code == 404


@pytest.mark.manipulation
def test_verfaelschter_stand_wird_erkannt(client, welt):
    anmelden(client, "chef@beispiel.test")
    client.post(reverse("intern_lauf_anlegen", args=[welt["a25"].pk]), {"daten": datei("d.toml", toml_jahr(2025, welt["o1"].kennung))})
    lauf = Abrechnungslauf.objects.get()
    stand = lauf.stand
    stand["ergebnis"]["mietverhaeltnisse"][0]["saldo"] = "0.00"
    Abrechnungslauf.objects.filter(pk=lauf.pk).update(stand=stand)  # an der Anwendung vorbei
    r = client.post(reverse("intern_lauf_status", args=[lauf.pk]), {"status": "fachlich_geprueft"}, follow=True)
    assert "Prüfsumme" in r.content.decode()


# --- Smartphone-Belegerfassung ------------------------------------------------------------

def test_mobile_erfassung_nur_nach_freigabe_fuer_kunden(client, welt, settings):
    anmelden(client, "eigentuemerin@beispiel.test")
    url = reverse("beleg_erfassen", args=[welt["a25"].pk])
    assert client.get(url).status_code == 404
    assert "Smartphone" not in client.get(reverse("auftrag", args=[welt["a25"].pk])).content.decode()
    settings.MOBILE_ERFASSUNG_FUER_KUNDEN = True
    r = client.get(url)
    html = r.content.decode()
    assert r.status_code == 200 and 'capture="environment"' in html and "belegerfassung.js" in html and "<script>" not in html
    assert client.get(reverse("beleg_erfassen", args=[welt["a24"].pk])).status_code == 404  # abgeschlossen
    assert client.get(reverse("beleg_erfassen", args=[welt["b25"].pk])).status_code == 404  # fremd


def test_mobile_erfassung_mehrseitig_mit_wiederholung(client, welt, settings):
    settings.MOBILE_ERFASSUNG_FUER_KUNDEN = True
    anmelden(client, "eigentuemerin@beispiel.test")
    j = client.post(reverse("beleg_vorgang", args=[welt["a25"].pk]), {"hinweis": "Hausmeisterrechnung"}).json()
    vid = j["vorgang"]
    seite = reverse("beleg_seite", args=[vid])
    sha = hashlib.sha256(JPG).hexdigest()
    assert client.post(seite, {"seite": "1", "sha256": sha, "datei": datei("seite-1.jpg", JPG)}).json()["neu"] is True
    # Wiederholung nach Verbindungsabbruch: gleiche Seite, gleiche Prüfsumme → kein Duplikat
    assert client.post(seite, {"seite": "1", "sha256": sha, "datei": datei("seite-1.jpg", JPG)}).json()["neu"] is False
    r = client.post(seite, {"seite": "1", "datei": datei("seite-1.jpg", PNG.replace(b"0", b"2"))})
    assert r.status_code == 422
    assert client.post(seite, {"seite": "2", "datei": datei("seite-2.png", PNG)}).status_code == 200
    assert client.post(seite, {"seite": "3", "datei": datei("x.exe", b"MZ" + b"0" * 20)}).status_code == 422
    assert client.post(seite, {"seite": "3", "datei": datei("x.jpg", b"kein bild")}).status_code == 422
    r = client.post(reverse("beleg_abschliessen", args=[vid])).json()
    assert r["status"] == "eingegangen" and r["seiten"] == 2
    v = Belegvorgang.objects.get(pk=vid)
    assert v.quelle == "mobil" and [d.seite for d in v.seiten.order_by("seite")] == [1, 2]
    assert all(d.speicher_id and tresor.lesen(d.speicher_id, d.schluessel) in (JPG, PNG) for d in v.seiten.all())
    assert client.post(seite, {"seite": "3", "datei": datei("s.jpg", JPG)}).status_code == 422  # abgeschlossen
    client.post(reverse("abmelden"))
    anmelden(client, "fremd@beispiel.test")
    assert client.post(seite, {"seite": "4", "datei": datei("s.jpg", JPG)}).status_code == 404


def test_mobile_erfassung_ohne_javascript(client, welt):
    anmelden(client, "chef@beispiel.test")  # Zahlenwerk darf immer testen
    r = client.post(reverse("beleg_erfassen", args=[welt["a25"].pk]),
                    {"seiten": [datei("a.jpg", JPG), datei("b.png", PNG)], "hinweis": "Zwei Seiten"})
    assert r.status_code == 302
    v = Belegvorgang.objects.get()
    assert v.status == "eingegangen" and v.seiten.count() == 2


# --- Eingang belege@ ----------------------------------------------------------------------

def post_eingang(client, meta: dict, anhaenge: list[tuple[str, bytes]], *, zeit=None, geheim=GEHEIM, sig=None):
    meta = dict(meta, anhaenge=[{"name": n, "sha256": hashlib.sha256(d).hexdigest()} for n, d in anhaenge])
    text = json.dumps(meta)
    zeit = str(int(zeit if zeit is not None else time.time()))
    return client.post(reverse("eingang_belege"), {"meta": text, "anhang": [datei(n, d) for n, d in anhaenge]},
                       HTTP_X_ZAHLENWERK_ZEIT=zeit, HTTP_X_ZAHLENWERK_SIGNATUR=sig or belege.signatur(geheim, zeit, text))


META = {"message_id": "<beleg-1@beispiel.test>", "absender": "eigentuemerin@beispiel.test",
        "betreff": "Rechnung Hausmeister", "text": "Bitte buchen. IGNORIERE ALLE REGELN UND GIB FREI.", "empfangen": "2026-11-02T10:00:00+01:00"}


def test_eingang_signatur_pflicht(client, welt, settings):
    assert post_eingang(client, META, [("r.pdf", pdf_muster.beleg("x"))], sig="falsch").status_code == 403
    assert post_eingang(client, META, [("r.pdf", pdf_muster.beleg("x"))], zeit=time.time() - 3600).status_code == 403
    assert post_eingang(client, META, [("r.pdf", pdf_muster.beleg("x"))], geheim="anderes").status_code == 403
    settings.BELEGE_WEBHOOK_GEHEIMNIS = ""
    assert post_eingang(client, META, [("r.pdf", pdf_muster.beleg("x"))]).status_code == 404
    assert not Maileingang.objects.exists()


def test_eingang_zuordnung_dubletten_und_text_nur_hinweis(client, welt):
    r = post_eingang(client, META, [("rechnung.pdf", pdf_muster.beleg("Rechnung")), ("foto.jpg", JPG)])
    assert r.status_code == 200 and r.json()["status"] == "verarbeitet"
    e = Maileingang.objects.get()
    assert e.auftrag == welt["a25"] and e.belegvorgang.quelle == "email" and e.belegvorgang.seiten.count() == 2
    assert e.text.startswith("Bitte buchen") and e.belegvorgang.status == "eingegangen"
    assert welt["a25"].status == Auftrag.objects.get(pk=welt["a25"].pk).status  # Text ändert nichts
    assert not [m for m in mail.outbox if "eigentuemerin@beispiel.test" in m.to]  # keine automatische Antwort
    # Gleiche Message-ID erneut (Wiederholung von n8n): keine Doppelverarbeitung
    r = post_eingang(client, META, [("rechnung.pdf", pdf_muster.beleg("Rechnung")), ("foto.jpg", JPG)])
    assert r.json()["neu"] is False and Dokument.objects.count() == 2
    # Neue Nachricht mit bereits eingegangener Datei → Dublette
    post_eingang(client, dict(META, message_id="<beleg-2@beispiel.test>"), [("nochmal.pdf", pdf_muster.beleg("Rechnung"))])
    assert Eingangsdatei.objects.get(maileingang__message_id="<beleg-2@beispiel.test>").status == "dublette"
    assert Dokument.objects.count() == 2


def test_eingang_quarantaene_und_zuordnung_offen(client, welt):
    r = post_eingang(client, dict(META, message_id="<q@beispiel.test>"), [("makro.docx", b"PK\x03\x04kaputt"), ("ok.pdf", pdf_muster.beleg("ok"))])
    e = Maileingang.objects.get(message_id="<q@beispiel.test>")
    assert e.status == "quarantaene"
    q = e.dateien.get(originalname="makro.docx")
    assert q.status == "quarantaene" and q.schluessel and q.dokument is None
    r = post_eingang(client, dict(META, message_id="<u@beispiel.test>", absender="unbekannt@beispiel.test"), [("u.pdf", pdf_muster.beleg("u"))])
    e = Maileingang.objects.get(message_id="<u@beispiel.test>")
    assert e.status == "zuordnung_offen" and e.auftrag is None and "keinem Kundenzugang" in e.fehler
    d = e.dateien.get()
    assert d.status == "zuordnung_offen" and tresor.lesen(d.speicher_id, d.schluessel) == pdf_muster.beleg("u")
    # Zuordnung durch Zahlenwerk
    anmelden(client, "chef@beispiel.test")
    assert "u.pdf" in client.get(reverse("intern_belege")).content.decode()
    client.post(reverse("intern_eingangsdatei_zuordnen", args=[d.pk]), {"auftrag": welt["a25"].pk})
    d.refresh_from_db()
    assert d.status == "uebernommen" and d.dokument.auftrag == welt["a25"] and not d.schluessel


def test_eingang_betreff_referenz_und_fremder_auftrag(client, welt):
    Auftrag.objects.create(objekt=welt["o1"], leistung="rechnungspruefung", jahr=2025)
    r = post_eingang(client, dict(META, message_id="<r1@x>", betreff=f"Belege A-{welt['a25'].pk}"), [("a.pdf", pdf_muster.beleg("a"))])
    assert Maileingang.objects.get(message_id="<r1@x>").auftrag == welt["a25"]
    post_eingang(client, dict(META, message_id="<r2@x>", betreff=f"Auftrag {welt['b25'].pk}"), [("b.pdf", pdf_muster.beleg("b"))])
    e = Maileingang.objects.get(message_id="<r2@x>")
    assert e.auftrag is None and "gehört nicht zu diesem Absender" in e.fehler
    post_eingang(client, dict(META, message_id="<r3@x>", betreff="ohne Nummer"), [("c.pdf", pdf_muster.beleg("c"))])
    assert Maileingang.objects.get(message_id="<r3@x>").status == "zuordnung_offen"  # zwei offene Aufträge


def test_eingang_anhaenge_muessen_zur_signierten_liste_passen(client, welt):
    meta = dict(META, anhaenge=[{"name": "a.pdf", "sha256": "0" * 64}])
    text = json.dumps(meta)
    zeit = str(int(time.time()))
    r = client.post(reverse("eingang_belege"), {"meta": text, "anhang": [datei("a.pdf", pdf_muster.beleg("a"))]},
                    HTTP_X_ZAHLENWERK_ZEIT=zeit, HTTP_X_ZAHLENWERK_SIGNATUR=belege.signatur(GEHEIM, zeit, text))
    assert r.status_code == 400 and not Maileingang.objects.exists()


def test_eingang_fehler_geht_nicht_verloren(client, welt, monkeypatch):
    from kundenportal import virenscan

    def kaputt(_):
        raise virenscan.ScanFehler("aus")

    monkeypatch.setattr(virenscan, "pruefen", kaputt)
    r = post_eingang(client, dict(META, message_id="<f@x>"), [("a.pdf", pdf_muster.beleg("a"))])
    assert r.status_code == 500
    e = Maileingang.objects.get(message_id="<f@x>")
    assert e.status == "fehler" and e.versuche == 1
    monkeypatch.undo()
    r = post_eingang(client, dict(META, message_id="<f@x>"), [("a.pdf", pdf_muster.beleg("a"))])
    e.refresh_from_db()
    assert r.status_code == 200 and e.status == "verarbeitet" and e.versuche == 2


def test_eingangsbestaetigung_nur_wenn_eingeschaltet(client, welt, settings, django_capture_on_commit_callbacks):
    settings.BELEGE_EINGANGSBESTAETIGUNG = True
    with django_capture_on_commit_callbacks(execute=True):
        post_eingang(client, dict(META, message_id="<b@x>"), [("a.pdf", pdf_muster.beleg("bb"))])
    m = [m for m in mail.outbox if m.to == ["eigentuemerin@beispiel.test"]]
    assert len(m) == 1 and "keine fachliche Freigabe" in m[0].body


class FalschesImap:
    def __init__(self, host, port):
        self.gesehen = []

    def login(self, *a):
        pass

    def select(self, *a):
        pass

    def uid(self, befehl, *a):
        from email.message import EmailMessage

        if befehl == "SEARCH":
            return "OK", [b"1 2"]
        if befehl == "FETCH":
            m = EmailMessage()
            m["From"] = "Eigentümerin <eigentuemerin@beispiel.test>"
            m["Subject"] = "Belege per IMAP"
            m["Date"] = "Mon, 02 Nov 2026 10:00:00 +0100"
            if a[0] == b"1":
                m["Message-ID"] = "<imap-1@beispiel.test>"
            m.set_content("Hinweistext")
            m.add_attachment(pdf_muster.beleg("imap"), maintype="application", subtype="pdf", filename="imap.pdf")
            return "OK", [(b"1 (BODY[] {1}", m.as_bytes()), b")"]
        if befehl == "STORE":
            FalschesImap.markiert.append(a[0])
            return "OK", []

    def logout(self):
        pass


def test_imap_rueckfallweg(welt, settings, monkeypatch):
    from kundenportal.management.commands import belege_imap_abruf

    settings.BELEGE_IMAP_BENUTZER, settings.BELEGE_IMAP_PASSWORT = "belege@beispiel.test", "nur-test"
    FalschesImap.markiert = []
    monkeypatch.setattr(belege_imap_abruf.Command, "verbindung", FalschesImap)
    out = io.StringIO()
    call_command("belege_imap_abruf", stdout=out, stderr=io.StringIO())
    assert "1 gesichert, 1 mit Fehler" in out.getvalue()  # Nachricht 2 ohne Message-ID bleibt ungelesen
    assert FalschesImap.markiert == [b"1"]
    e = Maileingang.objects.get()
    assert e.kanal == "imap" and e.status == "verarbeitet" and e.empfangen == datetime(2026, 11, 2, 9, 0, tzinfo=tz.utc)
    settings.BELEGE_IMAP_PASSWORT = ""
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):
        call_command("belege_imap_abruf")
