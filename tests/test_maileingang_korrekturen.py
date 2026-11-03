"""Regressionstests zu B4 (Wiederaufnahme), W4 (Dubletten je Auftrag) und H1 (signierte Anhangsliste).

Nur erfundene Daten und Test-PDFs aus tests/beispiel/. Kein Mailversand (locmem), kein Postfach.
"""
import hashlib
import json
import time
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from beispiel import pdf_muster
from kundenportal import belege, tresor
from kundenportal.models import Auftrag, Benutzer, Dokument, Eingangsdatei, Freigabe, Kunde, Maileingang, Objekt
from tests.test_portal import PASSWORT, anmelden
from tests.test_portal_betriebskosten import GEHEIM, JPG, META, datei, post_eingang, umgebung, welt  # noqa: F401

pytestmark = pytest.mark.django_db

A = pdf_muster.beleg("Rechnung A (fiktiv)")
B = pdf_muster.beleg("Rechnung B (fiktiv)")


class Abbruch(BaseException):
    """Simuliert einen harten Prozessabbruch (kein gewöhnlicher Fehler, wird nirgends abgefangen)."""


def nachricht(mid, anhaenge, **extra):
    return dict(message_id=mid, absender=META["absender"], betreff=META["betreff"], text="Hinweis (fiktiv)",
                empfangen=None, anhaenge=[belege.Anhang(n, d) for n, d in anhaenge], kanal="webhook", **extra)


def dokumente(auftrag):
    return Dokument.objects.filter(auftrag=auftrag, geloescht__isnull=True).count()


# --- B4: Abbruch und Wiederaufnahme -----------------------------------------------------

def test_verwaister_eintrag_aus_dem_pruefbericht_wird_fortgesetzt(client, welt):
    # Nachstellung: Metadatensatz mit Status „neu“ ohne gesicherte Anhänge (Abbruch im alten Ablauf)
    Maileingang.objects.create(message_id=META["message_id"], absender=META["absender"], betreff=META["betreff"])
    r = post_eingang(client, META, [("a.pdf", A)])
    e = Maileingang.objects.get()
    assert r.status_code == 200 and r.json()["status"] == "verarbeitet" and r.json()["gesichert"] is True
    assert e.status == "verarbeitet" and dokumente(welt["a25"]) == 1


def test_abbruch_nach_sicherung_vor_verarbeitung(client, welt, monkeypatch):
    def abbrechen(pk):
        belege._anspruch(pk)  # Anspruch genommen, dann „stirbt“ der Prozess
        raise Abbruch()

    monkeypatch.setattr(belege, "verarbeiten", abbrechen)
    with pytest.raises(Abbruch):
        post_eingang(client, META, [("a.pdf", A), ("b.pdf", B)])
    monkeypatch.undo()
    e = Maileingang.objects.get()
    assert e.roh_gespeichert and e.status == "in_bearbeitung" and e.dateien.filter(status="empfangen").count() == 2
    assert dokumente(welt["a25"]) == 0
    # Erneute Zustellung, solange der Anspruch läuft: gesichert, keine Doppelverarbeitung
    r = post_eingang(client, META, [("a.pdf", A), ("b.pdf", B)])
    assert r.status_code == 202 and r.json()["gesichert"] is True and dokumente(welt["a25"]) == 0
    # Anspruch verfällt → Wiederaufnahme übernimmt genau einmal vollständig
    Maileingang.objects.filter(pk=e.pk).update(bearbeitung_bis=timezone.now() - timedelta(seconds=1))
    assert belege.wiederaufnehmen() == (1, 0)
    e.refresh_from_db()
    assert e.status == "verarbeitet" and dokumente(welt["a25"]) == 2 and e.belegvorgang.seiten.count() == 2
    assert belege.wiederaufnehmen() == (0, 0)
    r = post_eingang(client, META, [("a.pdf", A), ("b.pdf", B)])
    assert r.status_code == 200 and r.json()["neu"] is False and dokumente(welt["a25"]) == 2


def test_abbruch_nach_einzelnem_anhang_in_der_verarbeitung(client, welt, monkeypatch):
    echt = belege.datei_ablegen
    aufrufe = []

    def zweiter_bricht_ab(*a, **k):
        aufrufe.append(1)
        if len(aufrufe) == 2:
            raise Abbruch()
        return echt(*a, **k)

    monkeypatch.setattr(belege, "datei_ablegen", zweiter_bricht_ab)
    with pytest.raises(Abbruch):
        post_eingang(client, META, [("a.pdf", A), ("b.pdf", B)])
    monkeypatch.undo()
    # Die Übernahme ist eine Transaktion: kein halber Stand, beide Anhänge weiterhin gesichert
    e = Maileingang.objects.get()
    assert dokumente(welt["a25"]) == 0 and e.dateien.filter(status="empfangen").count() == 2
    Maileingang.objects.filter(pk=e.pk).update(bearbeitung_bis=timezone.now() - timedelta(seconds=1))
    r = post_eingang(client, META, [("a.pdf", A), ("b.pdf", B)])
    assert r.status_code == 200 and r.json()["dateien"]["uebernommen"] == 2 and dokumente(welt["a25"]) == 2


def test_abbruch_waehrend_der_sicherung(client, welt, monkeypatch):
    echt = tresor.ablegen
    aufrufe = []

    def zweiter_bricht_ab(*a, **k):
        aufrufe.append(1)
        if len(aufrufe) == 2:
            raise Abbruch()
        return echt(*a, **k)

    monkeypatch.setattr(tresor, "ablegen", zweiter_bricht_ab)
    with pytest.raises(Abbruch):
        post_eingang(client, META, [("a.pdf", A), ("b.pdf", B)])
    monkeypatch.undo()
    assert not Maileingang.objects.exists()  # nichts halb Gesichertes; Zustellung gilt nicht als übernommen
    r = post_eingang(client, META, [("a.pdf", A), ("b.pdf", B)])
    assert r.status_code == 200 and r.json()["neu"] is True and dokumente(welt["a25"]) == 2


def test_parallele_zustellung_nur_einmal_verarbeitet(welt):
    # Zwei Zustellungen derselben Message-ID: nur ein Lauf erhält den Bearbeitungsanspruch
    e1, neu1 = belege.annehmen(**nachricht("<parallel@x>", [("a.pdf", A)]))
    e2, neu2 = belege.annehmen(**nachricht("<parallel@x>", [("a.pdf", A)]))
    assert e1.pk == e2.pk and (neu1, neu2) == (True, False) and e1.dateien.count() == 1
    assert belege._anspruch(e1.pk)  # Token des gewinnenden Laufs
    assert belege._anspruch(e2.pk) == ""  # kein Token für den zweiten Lauf
    assert belege.verarbeiten(e2.pk).status == "in_bearbeitung" and dokumente(welt["a25"]) == 0
    Maileingang.objects.filter(pk=e1.pk).update(bearbeitung_bis=timezone.now() - timedelta(seconds=1))
    assert belege.verarbeiten(e1.pk).status == "verarbeitet"
    assert belege.verarbeiten(e2.pk).status == "verarbeitet" and dokumente(welt["a25"]) == 1


def test_fehler_bleibt_wiederholbar_und_quarantaene_moeglich(client, welt, monkeypatch):
    from kundenportal import virenscan

    def kaputt(_):
        raise virenscan.ScanFehler("aus")

    monkeypatch.setattr(virenscan, "pruefen", kaputt)
    r = post_eingang(client, META, [("a.pdf", A), ("makro.docx", b"PK\x03\x04kaputt")])
    assert r.status_code == 500 and r.json()["gesichert"] is True
    e = Maileingang.objects.get()
    assert e.status == "fehler" and e.dateien.filter(status="empfangen").count() == 2
    monkeypatch.undo()
    out = __import__("io").StringIO()
    call_command("belege_wiederaufnahme", stdout=out)
    e.refresh_from_db()
    assert "1 fortgesetzt" in out.getvalue() and e.status == "quarantaene" and e.versuche == 2
    assert e.dateien.get(originalname="makro.docx").status == "quarantaene" and dokumente(welt["a25"]) == 1


def test_imap_markiert_erst_nach_sicherung(welt, settings, monkeypatch):
    from kundenportal.management.commands import belege_imap_abruf
    from tests.test_portal_betriebskosten import FalschesImap

    settings.BELEGE_IMAP_BENUTZER, settings.BELEGE_IMAP_PASSWORT = "belege@beispiel.test", "nur-test"
    FalschesImap.markiert = []
    monkeypatch.setattr(belege_imap_abruf.Command, "verbindung", FalschesImap)
    monkeypatch.setattr(belege_imap_abruf, "annehmen", lambda **n: (_ for _ in ()).throw(OSError("Speicher voll")))
    call_command("belege_imap_abruf", stdout=__import__("io").StringIO(), stderr=__import__("io").StringIO())
    assert FalschesImap.markiert == [] and not Maileingang.objects.exists()


# --- W4: Dubletten je Auftrag und Berechtigung ---------------------------------------------

def _zweiter_kunde():
    k = Kunde.objects.create(name="Eigentümer C (fiktiv)")
    o = Objekt.objects.create(kunde=k, bezeichnung="Drittes Haus", anschrift="Drittweg 3")
    a = Auftrag.objects.create(objekt=o, leistung="betriebskosten", jahr=2025)
    b = Benutzer.objects.create_user("dritter@beispiel.test", PASSWORT, kunde=k)
    Freigabe.objects.create(benutzer=b, objekt=o)
    return a


def test_gleiche_datei_bei_zwei_kunden(client, welt):
    a3 = _zweiter_kunde()
    post_eingang(client, META, [("a.pdf", A)])
    r = post_eingang(client, dict(META, message_id="<c@x>", absender="dritter@beispiel.test"), [("a.pdf", A)])
    assert r.json()["status"] == "verarbeitet" and r.json()["dateien"]["uebernommen"] == 1
    assert dokumente(welt["a25"]) == 1 and dokumente(a3) == 1
    d1, d2 = Dokument.objects.get(auftrag=welt["a25"]), Dokument.objects.get(auftrag=a3)
    assert d1.speicher_id != d2.speicher_id  # getrennt verschlüsselte Ablage, keine gemeinsame Datei


def test_gleiche_datei_bei_zwei_auftraegen_desselben_kunden(client, welt):
    zweiter = Auftrag.objects.create(objekt=welt["o1"], leistung="rechnungspruefung", jahr=2025)
    post_eingang(client, dict(META, betreff=f"A-{welt['a25'].pk}"), [("a.pdf", A)])
    r = post_eingang(client, dict(META, message_id="<z@x>", betreff=f"Auftrag {zweiter.pk}"), [("a.pdf", A)])
    assert r.json()["dateien"]["uebernommen"] == 1 and dokumente(welt["a25"]) == 1 and dokumente(zweiter) == 1


def test_erneuter_versand_nach_offener_zuordnung(client, welt):
    Auftrag.objects.create(objekt=welt["o1"], leistung="rechnungspruefung", jahr=2025)  # zwei offene Aufträge
    r = post_eingang(client, dict(META, message_id="<o1@x>", betreff="ohne Nummer"), [("a.pdf", A)])
    assert r.json()["status"] == "zuordnung_offen"
    r = post_eingang(client, dict(META, message_id="<o2@x>", betreff=f"A-{welt['a25'].pk}"), [("a.pdf", A)])
    assert r.json()["status"] == "verarbeitet" and r.json()["dateien"]["uebernommen"] == 1 and dokumente(welt["a25"]) == 1
    # Spätere Zuordnung des offenen Anhangs zum selben Auftrag legt kein zweites Dokument an
    offen = Eingangsdatei.objects.get(maileingang__message_id="<o1@x>")
    anmelden(client, "chef@beispiel.test")
    client.post(reverse("intern_eingangsdatei_zuordnen", args=[offen.pk]), {"auftrag": welt["a25"].pk})
    offen.refresh_from_db()
    assert offen.status == "dublette" and offen.dokument.auftrag == welt["a25"] and dokumente(welt["a25"]) == 1


def test_echte_wiederholung_im_selben_auftrag(client, welt):
    post_eingang(client, META, [("a.pdf", A)])
    r = post_eingang(client, dict(META, message_id="<w@x>"), [("nochmal.pdf", A), ("b.pdf", B)])
    j = r.json()
    assert (j["dateien"]["uebernommen"], j["dateien"]["dublette"]) == (1, 1) and dokumente(welt["a25"]) == 2
    d = Eingangsdatei.objects.get(maileingang__message_id="<w@x>", originalname="nochmal.pdf")
    assert d.status == "dublette" and d.dokument == Dokument.objects.get(auftrag=welt["a25"], sha256=hashlib.sha256(A).hexdigest())


def test_quarantaene_blockiert_spaetere_uebernahme_nicht(client, welt, monkeypatch):
    from kundenportal import virenscan

    monkeypatch.setattr(virenscan, "pruefen", lambda d: (False, "Test-Befund (fiktiv)"))
    post_eingang(client, dict(META, message_id="<v1@x>"), [("a.pdf", A)])
    assert Eingangsdatei.objects.get().status == "quarantaene"
    monkeypatch.undo()
    r = post_eingang(client, dict(META, message_id="<v2@x>"), [("a.pdf", A)])
    assert r.json()["dateien"]["uebernommen"] == 1 and dokumente(welt["a25"]) == 1


# --- H1: signierte Anhangsliste als Multimenge --------------------------------------------

def _post_roh(client, liste, dateien):
    meta = dict(META, anhaenge=[{"name": n, "sha256": hashlib.sha256(d).hexdigest()} for n, d in liste])
    text = json.dumps(meta)
    zeit = str(int(time.time()))
    return client.post(reverse("eingang_belege"), {"meta": text, "anhang": [datei(n, d) for n, d in dateien]},
                       HTTP_X_ZAHLENWERK_ZEIT=zeit, HTTP_X_ZAHLENWERK_SIGNATUR=belege.signatur(GEHEIM, zeit, text))


@pytest.mark.parametrize("liste,dateien", [
    ([("a.pdf", A)], [("a.pdf", A), ("a.pdf", A), ("a.pdf", A)]),  # ein signierter Anhang, zwei Kopien zusätzlich
    ([("a.pdf", A), ("b.pdf", B)], [("a.pdf", A)]),  # fehlender Anhang
    ([("a.pdf", A)], [("a.pdf", A), ("b.pdf", B)]),  # zusätzlicher Anhang
    ([("a.pdf", A)], [("a.pdf", B)]),  # inhaltlich verändert
    ([("a.pdf", A)], [("x.pdf", A)]),  # anderer Name
])
def test_anhaenge_muessen_exakt_zur_signierten_liste_passen(client, welt, liste, dateien):
    r = _post_roh(client, liste, dateien)
    assert r.status_code == 400 and not Maileingang.objects.exists()


def test_signierte_liste_mit_doppeltem_anhang_wird_angenommen_wenn_exakt(client, welt):
    r = _post_roh(client, [("a.pdf", A), ("a.pdf", A)], [("a.pdf", A), ("a.pdf", A)])
    assert r.status_code == 200 and r.json()["dateien"] == {"uebernommen": 1, "dublette": 1, "quarantaene": 0, "zuordnung_offen": 0}


def test_signatur_und_zeitfenster_unveraendert_streng(client, welt):
    assert post_eingang(client, META, [("a.pdf", A)], sig="0" * 64).status_code == 403
    assert post_eingang(client, META, [("a.pdf", A)], zeit=time.time() - 301).status_code == 403
    assert post_eingang(client, META, [("a.pdf", A)], zeit=time.time() + 301).status_code == 403
    assert not Maileingang.objects.exists()


# --- N2 (Nachprüfung 02.11.2026): verspäteter Lauf nach Ablauf seines Anspruchs ------------

def _zustand(eingang):
    eingang.refresh_from_db()
    dateien = sorted((d.pk, d.status, d.dokument_id) for d in eingang.dateien.all())
    inhalte = sorted(tresor.lesen(d.speicher_id, d.schluessel) for d in Dokument.objects.filter(geloescht__isnull=True))
    return (eingang.status, eingang.auftrag_id, eingang.belegvorgang_id, eingang.fehler, dateien,
            Dokument.objects.filter(geloescht__isnull=True).count(), inhalte)


def _neue_unterlagen(mail_outbox):
    return [m for m in mail_outbox if m.subject.startswith("Neue Unterlagen")]


def test_n2_gegenprobe_veralteter_lauf_ueberschreibt_nichts(welt, settings, monkeypatch, mailoutbox,
                                                            django_capture_on_commit_callbacks):
    # Nachstellung: Lauf A erhält den Anspruch und pausiert; der Anspruch verfällt; Lauf B verarbeitet
    # vollständig; danach setzt Lauf A fort.
    settings.MAIL_INTERN = "intern@beispiel.test"
    eingang, _ = belege.annehmen(**nachricht("<n2@beispiel.test>", [("a.pdf", A), ("b.pdf", B)]))
    echt_anspruch = belege._anspruch
    nachher = {}

    def anspruch_mit_pause(pk):
        token = echt_anspruch(pk)  # Lauf A
        Maileingang.objects.filter(pk=pk).update(bearbeitung_bis=timezone.now() - timedelta(seconds=1))
        monkeypatch.setattr(belege, "_anspruch", echt_anspruch)
        with django_capture_on_commit_callbacks(execute=True) as rueckrufe_b:
            belege.verarbeiten(pk)  # Lauf B übernimmt und wird fertig
        nachher["rueckrufe_b"] = len(rueckrufe_b)
        nachher["b"] = _zustand(eingang)
        nachher["mails"] = len(_neue_unterlagen(mailoutbox))
        return token

    monkeypatch.setattr(belege, "_anspruch", anspruch_mit_pause)
    # Äußere Erfassung ohne Ausführung: sie sieht B's Rückrufe (schon ausgeführt) und jeden weiteren von A.
    with django_capture_on_commit_callbacks(execute=False) as alle:
        ergebnis = belege.verarbeiten(eingang.pk)  # Lauf A setzt fort
    status, auftrag, vorgang, fehler, dateien, anzahl, inhalte = nachher["b"]
    assert status == "verarbeitet" and auftrag == welt["a25"].pk and vorgang is not None and anzahl == 2
    assert _zustand(eingang) == nachher["b"]  # Zuordnung, Dokumente, Dateistatus und Inhalte unverändert
    assert ergebnis.belegvorgang_id == vorgang and ergebnis.status == "verarbeitet"
    assert len(alle) == nachher["rueckrufe_b"]  # A hat keine Mail, kein Löschen und nichts sonst vorgemerkt
    assert len(_neue_unterlagen(mailoutbox)) == nachher["mails"] == 1  # keine zweite Benachrichtigung
    from kundenportal.models import Protokoll
    assert Protokoll.objects.filter(aktion="maileingang_veralteter_lauf").count() == 1


def test_n2_veralteter_lauf_nach_uebernahme_noch_in_bearbeitung(welt):
    # B hat übernommen, ist aber noch nicht fertig: A darf weder verarbeiten noch den Anspruch beenden
    eingang, _ = belege.annehmen(**nachricht("<n2b@beispiel.test>", [("a.pdf", A)]))
    token_a = belege._anspruch(eingang.pk)
    Maileingang.objects.filter(pk=eingang.pk).update(bearbeitung_bis=timezone.now() - timedelta(seconds=1))
    token_b = belege._anspruch(eingang.pk)
    assert token_a and token_b and token_a != token_b
    vorher = _zustand(eingang)
    belege._mit_anspruch_verarbeiten(eingang.pk, token_a)
    assert _zustand(eingang) == vorher and dokumente(welt["a25"]) == 0
    eingang.refresh_from_db()
    assert eingang.bearbeitung_token == token_b  # B behält seinen Anspruch
    belege._mit_anspruch_verarbeiten(eingang.pk, token_b)
    eingang.refresh_from_db()
    assert eingang.status == "verarbeitet" and dokumente(welt["a25"]) == 1 and eingang.bearbeitung_token == ""


def test_n2_abgelaufener_eigener_anspruch_verarbeitet_nicht(welt):
    eingang, _ = belege.annehmen(**nachricht("<n2c@beispiel.test>", [("a.pdf", A)]))
    token = belege._anspruch(eingang.pk)
    Maileingang.objects.filter(pk=eingang.pk).update(bearbeitung_bis=timezone.now() - timedelta(seconds=1))
    belege._mit_anspruch_verarbeiten(eingang.pk, token)
    eingang.refresh_from_db()
    assert eingang.status == "in_bearbeitung" and dokumente(welt["a25"]) == 0
    assert belege.wiederaufnehmen() == (1, 0)  # Wiederaufnahme übernimmt sauber
    eingang.refresh_from_db()
    assert eingang.status == "verarbeitet" and dokumente(welt["a25"]) == 1


def test_n2_fehler_eines_veralteten_laufs_ueberschreibt_den_status_nicht(welt, monkeypatch):
    eingang, _ = belege.annehmen(**nachricht("<n2d@beispiel.test>", [("a.pdf", A)]))
    token_a = belege._anspruch(eingang.pk)
    Maileingang.objects.filter(pk=eingang.pk).update(bearbeitung_bis=timezone.now() - timedelta(seconds=1))
    belege.verarbeiten(eingang.pk)  # B fertig
    vorher = _zustand(eingang)

    def kaputt(*a, **k):
        raise RuntimeError("simulierter Fehler")

    monkeypatch.setattr(Maileingang.objects, "select_for_update", kaputt)
    with pytest.raises(RuntimeError):
        belege._mit_anspruch_verarbeiten(eingang.pk, token_a)
    monkeypatch.undo()
    assert _zustand(eingang) == vorher  # weiterhin „verarbeitet“, nicht „fehler“
