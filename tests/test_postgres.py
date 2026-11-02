"""Datenbankschutz in PostgreSQL (Befund W6): Trigger aus Migration 0003.

Nur mit PORTAL_TEST_POSTGRES=1 gegen eine temporäre PostgreSQL-Instanz; sonst übersprungen.
Ausschließlich erfundene Daten.
"""
import pytest
from django.db import DatabaseError, connection, transaction
from django.urls import reverse

from beispiel.portal_abrechnung import toml_jahr
from kundenportal.models import Abrechnungslauf, Report, Statuswechsel
from tests.test_portal import anmelden
from tests.test_portal_betriebskosten import datei, lauf_endgueltig, umgebung, welt  # noqa: F401

pytestmark = [pytest.mark.django_db]


@pytest.fixture(autouse=True)
def nur_postgres():
    if connection.vendor != "postgresql":
        pytest.skip("nur mit PORTAL_TEST_POSTGRES=1")


def _roh(sql, werte):
    with transaction.atomic(), connection.cursor() as c:
        c.execute("SET CONSTRAINTS ALL IMMEDIATE")  # Fremdschlüssel sofort prüfen, nicht erst beim Commit
        c.execute(sql, werte)


@pytest.fixture
def stand(client, welt):
    anmelden(client, "chef@beispiel.test")
    lauf = lauf_endgueltig(client, welt["a25"], toml_jahr(2025, welt["o1"].kennung))
    client.post(reverse("intern_report_erstellen", args=[welt["o1"].pk]), {"art": "vorauszahlung", "lauf": lauf.pk})
    return lauf, Report.objects.get()


def test_statuswechsel_weder_aendern_noch_loeschen(stand):
    lauf, _ = stand
    w = lauf.statuswechsel.order_by("id").first()
    with pytest.raises(DatabaseError, match="unveraenderlich"):
        _roh("UPDATE kundenportal_statuswechsel SET status = 'endgueltig' WHERE id = %s", [w.pk])
    with pytest.raises(DatabaseError, match="unveraenderlich"):
        _roh("DELETE FROM kundenportal_statuswechsel WHERE id = %s", [w.pk])
    with pytest.raises(DatabaseError):
        _roh("DELETE FROM kundenportal_abrechnungslauf WHERE id = %s", [lauf.pk])  # Verlauf hängt am Lauf
    with pytest.raises(DatabaseError, match="unveraenderlich"):
        with transaction.atomic():
            lauf.delete()  # Django-Kaskade löscht den Verlauf per SQL und trifft den Trigger
    assert Statuswechsel.objects.filter(pk=w.pk).exists()


def test_lauf_und_report_nur_status_aenderbar(stand):
    lauf, r = stand
    with pytest.raises(DatabaseError, match="Nur der Status"):
        _roh("UPDATE kundenportal_abrechnungslauf SET stand = '{}'::jsonb WHERE id = %s", [lauf.pk])
    with pytest.raises(DatabaseError, match="Nur der Status"):
        _roh("UPDATE kundenportal_abrechnungslauf SET ergebnis_hash = %s WHERE id = %s", ["0" * 64, lauf.pk])
    with pytest.raises(DatabaseError, match="Nur der Status"):
        _roh("UPDATE kundenportal_report SET inhalt_hash = %s WHERE id = %s", ["0" * 64, r.pk])
    _roh("UPDATE kundenportal_report SET status = 'fachlich_geprueft' WHERE id = %s", [r.pk])  # erlaubt
    lauf.refresh_from_db()
    assert lauf.stand["eingabe"] and Abrechnungslauf.objects.get(pk=lauf.pk).ergebnis_hash != "0" * 64


@pytest.mark.django_db(transaction=True)  # echte Transaktionen: die Freigabe gilt nur bis zum Commit
def test_loeschung_nur_mit_ausdruecklicher_sitzungsfreigabe(stand):
    lauf, _ = stand
    w = lauf.statuswechsel.order_by("id").first()
    with transaction.atomic(), connection.cursor() as c:
        c.execute("SELECT set_config('zahlenwerk.loeschung', 'erlaubt', true)")  # nur diese Transaktion
        c.execute("DELETE FROM kundenportal_statuswechsel WHERE id = %s", [w.pk])
    assert not Statuswechsel.objects.filter(pk=w.pk).exists()
    w2 = lauf.statuswechsel.order_by("id").first()
    with pytest.raises(DatabaseError):
        _roh("DELETE FROM kundenportal_statuswechsel WHERE id = %s", [w2.pk])  # Freigabe galt nur einmal
