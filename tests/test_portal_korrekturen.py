"""Regressionstests zu den Befunden B1, W1, W2 und W6 aus dem Prüfbericht vom 02.11.2026.

Ausschließlich erfundene Daten (tests/beispiel/). Kein Mailversand, keine echte Datenbank.
"""
import pytest
from django.db import DatabaseError, connection, transaction
from django.urls import reverse

from beispiel.portal_abrechnung import toml_jahr
from kundenportal import abrechnung
from kundenportal.models import Abrechnungslauf, Report, Statuswechsel, Unveraenderlich
from tests.test_portal import anmelden
from tests.test_portal_betriebskosten import datei, lauf_endgueltig, umgebung, welt  # noqa: F401

pytestmark = pytest.mark.django_db


def _import(client, auftrag, daten):
    return client.post(reverse("intern_lauf_anlegen", args=[auftrag.pk]), {"daten": datei("d.toml", daten)}, follow=True)


def _freigegebener_report(client, welt):
    anmelden(client, "chef@beispiel.test")
    lauf = lauf_endgueltig(client, welt["a25"], toml_jahr(2025, welt["o1"].kennung))
    client.post(reverse("intern_report_erstellen", args=[welt["o1"].pk]), {"art": "vorauszahlung", "lauf": lauf.pk})
    r = Report.objects.get()
    for s in ("fachlich_geprueft", "freigegeben"):
        client.post(reverse("intern_report_status", args=[r.pk]), {"status": s})
    r.refresh_from_db()
    assert r.status == "freigegeben"
    client.post(reverse("abmelden"))
    return lauf, r


# --- B1: Import fest an Objekt, Auftrag und Zeitraum gebunden ---------------------------

def test_import_fremdes_objekt_und_falsches_jahr_abgewiesen(client, welt):
    anmelden(client, "chef@beispiel.test")
    # Nachstellung aus dem Prüfbericht: Daten „Portalhaus“ 2024 beim Auftrag „Fremdhaus“ 2025
    r = _import(client, welt["b25"], toml_jahr(2024, welt["o1"].kennung))
    assert "anderen Objekt" in r.content.decode() and not Abrechnungslauf.objects.exists()
    r = _import(client, welt["b25"], toml_jahr(2025, welt["o1"].kennung))
    assert "anderen Objekt" in r.content.decode() and not Abrechnungslauf.objects.exists()
    # Fehlende Kennung
    ohne = toml_jahr(2025, welt["o1"].kennung).replace(f'portal_id = "{welt["o1"].kennung}"\n'.encode(), b"")
    r = _import(client, welt["a25"], ohne)
    assert "portal_id fehlt" in r.content.decode() and not Abrechnungslauf.objects.exists()
    # Falsches Jahr für den Auftrag
    r = _import(client, welt["a25"], toml_jahr(2024, welt["o1"].kennung))
    assert "der Auftrag gilt für 2025" in r.content.decode() and not Abrechnungslauf.objects.exists()
    # Kalenderjahr-Objekt mit abweichendem Zeitraum
    r = _import(client, welt["a25"], toml_jahr(2025, welt["o1"].kennung, von="2024-07-01").replace(b"bis = 2025-12-31", b"bis = 2025-06-30"))
    assert "nach Kalenderjahr" in r.content.decode() and not Abrechnungslauf.objects.exists()
    # Richtig: eigenes Objekt, eigenes Jahr
    r = _import(client, welt["a25"], toml_jahr(2025, welt["o1"].kennung))
    lauf = Abrechnungslauf.objects.get()
    assert lauf.auftrag == welt["a25"] and lauf.stand["zuordnung"]["objekt_kennung"] == str(welt["o1"].kennung)


def test_abweichendes_wirtschaftsjahr_erlaubt(client, welt):
    welt["o1"].wirtschaftsjahr = "01.07. bis 30.06."
    welt["o1"].save()
    anmelden(client, "chef@beispiel.test")
    daten = toml_jahr(2025, welt["o1"].kennung, von="2024-07-01").replace(b"bis = 2025-12-31", b"bis = 2025-06-30")
    _import(client, welt["a25"], daten)
    assert Abrechnungslauf.objects.filter(auftrag=welt["a25"]).count() == 1


@pytest.mark.manipulation  # verfälscht an der Anwendung vorbei; unter PostgreSQL Trigger aus
def test_kein_freigabefaehiger_lauf_oder_report_bei_fremdzuordnung(client, welt):
    anmelden(client, "chef@beispiel.test")
    fremd = lauf_endgueltig(client, welt["b25"], toml_jahr(2025, welt["o2"].kennung))
    eigen = lauf_endgueltig(client, welt["a24"], toml_jahr(2024, welt["o1"].kennung))
    # An der Anwendung vorbei: fremden Lauf einem Auftrag des Portalhauses unterschieben
    Abrechnungslauf.objects.filter(pk=fremd.pk).update(auftrag=welt["a25"])
    fremd.refresh_from_db()
    assert "Stand gehört zu einem anderen Objekt" in abrechnung.lauf_fehler(fremd)
    r = client.post(reverse("intern_report_erstellen", args=[welt["o1"].pk]), {"art": "kostenverlauf"}, follow=True)
    assert "nicht unverfälscht" in r.content.decode() and not Report.objects.exists()
    r = client.post(reverse("intern_report_erstellen", args=[welt["o1"].pk]), {"art": "vorauszahlung", "lauf": fremd.pk}, follow=True)
    assert "nicht unverfälscht" in r.content.decode() and not Report.objects.exists()
    assert not abrechnung.lauf_fehler(eigen)


# --- W1: gesamten Abrechnungssnapshot validieren -----------------------------------------

@pytest.mark.parametrize("aendern,erwartet", [
    (lambda s: s["eingabe"]["mietverhaeltnisse"][0]["vz_monatlich"].__setitem__("betriebskosten", "10000.00"), "Eingabe-Prüfsumme"),
    (lambda s: s["ergebnis"]["mietverhaeltnisse"][0].__setitem__("saldo", "0.00"), "Ergebnis-Prüfsumme"),
    (lambda s: s.__setitem__("gelbe_warnungen", 7), "Warnungszähler"),
    (lambda s: s.__setitem__("ergebnis_hash", "0" * 64), "Ergebnis-Prüfsumme"),
])
@pytest.mark.manipulation  # verfälscht an der Anwendung vorbei; unter PostgreSQL Trigger aus
def test_veraenderter_stand_sperrt_freigabe_und_report(client, welt, aendern, erwartet):
    anmelden(client, "chef@beispiel.test")
    lauf = lauf_endgueltig(client, welt["a25"], toml_jahr(2025, welt["o1"].kennung))
    stand = lauf.stand
    aendern(stand)
    Abrechnungslauf.objects.filter(pk=lauf.pk).update(stand=stand)
    r = client.post(reverse("intern_report_erstellen", args=[welt["o1"].pk]), {"art": "vorauszahlung", "lauf": lauf.pk}, follow=True)
    assert erwartet in r.content.decode() and not Report.objects.exists()


@pytest.mark.manipulation  # verfälscht an der Anwendung vorbei; unter PostgreSQL Trigger aus
def test_falsche_datenbankfelder_sperren(client, welt):
    anmelden(client, "chef@beispiel.test")
    _import(client, welt["a25"], toml_jahr(2025, welt["o1"].kennung))
    lauf = Abrechnungslauf.objects.get()
    Abrechnungslauf.objects.filter(pk=lauf.pk).update(rote_warnungen=3)
    r = client.post(reverse("intern_lauf_status", args=[lauf.pk]), {"status": "fachlich_geprueft"}, follow=True)
    assert "Datenbankfelder passen nicht" in r.content.decode()
    lauf.refresh_from_db()
    assert lauf.status == "entwurf"


@pytest.mark.manipulation  # verfälscht an der Anwendung vorbei; unter PostgreSQL Trigger aus
def test_beschaedigter_altstand_nicht_ueber_identischen_import(client, welt):
    anmelden(client, "chef@beispiel.test")
    daten = toml_jahr(2025, welt["o1"].kennung)
    _import(client, welt["a25"], daten)
    lauf = Abrechnungslauf.objects.get()
    stand = lauf.stand
    stand["ergebnis"]["mietverhaeltnisse"][0]["saldo"] = "0.00"
    Abrechnungslauf.objects.filter(pk=lauf.pk).update(stand=stand)
    r = _import(client, welt["a25"], daten)
    html = r.content.decode()
    assert "nicht unverfälscht" in html and "unverändert (gleiche Eingabe)" not in html


# --- W2: Reportintegrität bei HTML und PDF -----------------------------------------------

@pytest.mark.manipulation  # verfälscht an der Anwendung vorbei; unter PostgreSQL Trigger aus
def test_veraenderter_report_wird_nicht_ausgeliefert(client, welt):
    from betriebskosten.reportablage import inhalt_hash

    _, r = _freigegebener_report(client, welt)
    anmelden(client, "eigentuemerin@beispiel.test")
    assert client.get(reverse("bericht", args=[r.pk])).status_code == 200
    inhalt = r.inhalt
    inhalt["mietverhaeltnisse"][0]["mieter"] = "MANIPULIERT (fiktiv)"
    Report.objects.filter(pk=r.pk).update(inhalt=inhalt)
    for name in ("bericht", "bericht_pdf"):
        antwort = client.get(reverse(name, args=[r.pk]))
        assert antwort.status_code == 404 and b"MANIPULIERT" not in antwort.content
    assert "Vorauszahlungsanalyse" not in client.get(reverse("objekt", args=[welt["o1"].pk])).content.decode()
    # Auch mit neu berechneter Prüfsumme: der Statusverlauf verankert die freigegebene Prüfsumme
    Report.objects.filter(pk=r.pk).update(inhalt_hash=inhalt_hash(inhalt))
    assert client.get(reverse("bericht", args=[r.pk])).status_code == 404
    assert client.get(reverse("bericht_pdf", args=[r.pk])).status_code == 404
    client.post(reverse("abmelden"))
    anmelden(client, "chef@beispiel.test")
    assert client.get(reverse("bericht", args=[r.pk])).status_code == 403
    client.post(reverse("abmelden"))
    anmelden(client, "fremd@beispiel.test")
    assert client.get(reverse("bericht", args=[r.pk])).status_code == 404
    from kundenportal.models import Protokoll
    assert Protokoll.objects.filter(aktion="report_integritaet", bezug=f"report:{r.pk}").exists()
    assert "MANIPULIERT" not in " ".join(Protokoll.objects.values_list("details", flat=True))


@pytest.mark.manipulation  # verfälscht an der Anwendung vorbei; unter PostgreSQL Trigger aus
def test_report_mit_veraenderter_grundlage_wird_gesperrt(client, welt):
    lauf, r = _freigegebener_report(client, welt)
    stand = lauf.stand
    stand["ergebnis"]["mietverhaeltnisse"][0]["saldo"] = "0.00"
    Abrechnungslauf.objects.filter(pk=lauf.pk).update(stand=stand)
    anmelden(client, "eigentuemerin@beispiel.test")
    assert client.get(reverse("bericht_pdf", args=[r.pk])).status_code == 404


# --- W6: Statusverlauf unveränderlich ----------------------------------------------------

def test_statuswechsel_nicht_aenderbar_oder_loeschbar(client, welt):
    anmelden(client, "chef@beispiel.test")
    lauf = lauf_endgueltig(client, welt["a25"], toml_jahr(2025, welt["o1"].kennung))
    w = lauf.statuswechsel.order_by("id").first()
    w.notiz = "umgeschrieben"
    with pytest.raises(Unveraenderlich):
        w.save()
    with pytest.raises(Unveraenderlich):
        w.delete()
    with pytest.raises(Unveraenderlich):
        Statuswechsel.objects.filter(lauf=lauf).update(notiz="umgeschrieben")
    with pytest.raises(Unveraenderlich):
        Statuswechsel.objects.filter(lauf=lauf).delete()
    with pytest.raises(Unveraenderlich):
        Statuswechsel.objects.bulk_update([w], ["notiz"])
    # Auch an der Anwendung vorbei per SQL: Trigger in der Datenbank
    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as c:
        c.execute("UPDATE kundenportal_statuswechsel SET notiz = 'umgeschrieben' WHERE id = %s", [w.pk])
    assert Statuswechsel.objects.get(pk=w.pk).notiz == ""
    assert not abrechnung.lauf_fehler(lauf)


@pytest.mark.manipulation  # verfälscht an der Anwendung vorbei; unter PostgreSQL Trigger aus
def test_geloeschter_oder_eingeschobener_statuswechsel_sperrt_folgeaktionen(client, welt):
    anmelden(client, "chef@beispiel.test")
    lauf = lauf_endgueltig(client, welt["a25"], toml_jahr(2025, welt["o1"].kennung))
    letzter = lauf.statuswechsel.order_by("-id").first()
    with connection.cursor() as c:  # SQLite erlaubt DELETE für die Testdatenbank; PostgreSQL sperrt es (siehe Bericht)
        c.execute("DELETE FROM kundenportal_statuswechsel WHERE id = %s", [letzter.pk])
    assert "Aktueller Status passt nicht zum Statusverlauf" in abrechnung.lauf_fehler(lauf)
    r = client.post(reverse("intern_report_erstellen", args=[welt["o1"].pk]), {"art": "vorauszahlung", "lauf": lauf.pk}, follow=True)
    assert "nicht unverfälscht" in r.content.decode() and not Report.objects.exists()


def test_unzulaessiger_statussprung_wird_erkannt(client, welt):
    anmelden(client, "chef@beispiel.test")
    client.post(reverse("intern_lauf_anlegen", args=[welt["a25"].pk]), {"daten": datei("d.toml", toml_jahr(2025, welt["o1"].kennung))})
    lauf = Abrechnungslauf.objects.get()
    # Statusfeld an der Anwendung vorbei auf „endgültig“ setzen und passenden Eintrag einschieben
    Abrechnungslauf.objects.filter(pk=lauf.pk).update(status="endgueltig")
    w = Statuswechsel(lauf=lauf, status="endgueltig", bezug_hash=abrechnung._anker_lauf(lauf),
                      vorher_hash=lauf.statuswechsel.order_by("-id").first().eintrag_hash)
    w.eintrag_hash = w.berechne_hash()
    w.save()
    lauf.refresh_from_db()
    assert "Statusverlauf ist lückenhaft oder in falscher Reihenfolge" in abrechnung.lauf_fehler(lauf)
    r = client.post(reverse("intern_report_erstellen", args=[welt["o1"].pk]), {"art": "vorauszahlung", "lauf": lauf.pk}, follow=True)
    assert "nicht unverfälscht" in r.content.decode()


def test_gueltige_ereignisfolge(client, welt):
    lauf, r = _freigegebener_report(client, welt)
    assert list(lauf.statuswechsel.order_by("id").values_list("status", flat=True)) == [
        "entwurf", "fachlich_geprueft", "eigentuemer_freigegeben", "endgueltig"]
    assert not abrechnung.lauf_fehler(lauf) and not abrechnung.report_fehler(r)
    ketten = list(r.statuswechsel.order_by("id"))
    assert ketten[0].vorher_hash == "" and all(b.vorher_hash == a.eintrag_hash for a, b in zip(ketten, ketten[1:]))


# --- W7 (Serverseite): Abschluss idempotent, Wiederholung ohne zweite Meldung -------------

def test_mobiler_abschluss_idempotent_ohne_zweite_benachrichtigung(client, welt, settings, django_capture_on_commit_callbacks):
    import hashlib

    from django.core import mail

    from kundenportal.models import Belegvorgang
    from tests.test_portal_betriebskosten import JPG

    settings.MOBILE_ERFASSUNG_FUER_KUNDEN = True
    settings.MAIL_INTERN = "intern@beispiel.test"
    anmelden(client, "eigentuemerin@beispiel.test")
    vid = client.post(reverse("beleg_vorgang", args=[welt["a25"].pk])).json()["vorgang"]
    seite, abschluss = reverse("beleg_seite", args=[vid]), reverse("beleg_abschliessen", args=[vid])
    sha = hashlib.sha256(JPG).hexdigest()
    assert client.post(seite, {"seite": "1", "sha256": sha, "datei": datei("s1.jpg", JPG)}).status_code == 200
    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        erst = client.post(abschluss).json()
    assert erst["abgeschlossen"] and not erst["bereits_abgeschlossen"] and erst["seiten"] == 1
    # Antwort verloren, Browser fragt nach Neustart erneut: gleicher Zustand, keine zweite Meldung
    with django_capture_on_commit_callbacks(execute=True) as rueckrufe:
        zweit = client.post(abschluss).json()
    assert rueckrufe == []
    assert zweit["abgeschlossen"] and zweit["bereits_abgeschlossen"] and zweit["status"] == "eingegangen"
    assert len([m for m in mail.outbox if m.subject.startswith("Neue Unterlagen")]) == 1
    # Dieselbe Seite nach Abschluss erneut gesendet: unschädlich; eine neue Seite dagegen nicht
    r = client.post(seite, {"seite": "1", "sha256": sha, "datei": datei("s1.jpg", JPG)})
    assert r.status_code == 200 and r.json()["neu"] is False
    assert client.post(seite, {"seite": "2", "datei": datei("s2.jpg", JPG)}).status_code == 422
    v = Belegvorgang.objects.get(pk=vid)
    assert Belegvorgang.objects.count() == 1 and v.seiten.count() == 1
