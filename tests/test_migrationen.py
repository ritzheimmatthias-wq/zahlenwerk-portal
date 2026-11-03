"""Migrationen: leere Datenbank bis zum Endstand und 0001 → 0002 → 0003 mit erfundenen Altdaten.

Läuft mit SQLite (Standard) und, wenn PORTAL_TEST_POSTGRES=1 gesetzt ist, gegen eine temporäre
PostgreSQL-Datenbank (CI-Job „postgres“). Keine echten Daten.
"""
from datetime import datetime, timezone

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db(transaction=True)

APP = "kundenportal"


def _migrieren(ziel):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(ziel)
    if ziel[0][1] is None:
        return None
    executor.loader.build_graph()
    return executor.loader.project_state(ziel).apps


def _endstand():
    executor = MigrationExecutor(connection)
    return executor.loader.graph.leaf_nodes(APP)


def test_leere_datenbank_bis_endstand():
    _migrieren([(APP, None)])
    with connection.cursor() as c:
        tabellen = connection.introspection.table_names(c)
    assert "kundenportal_objekt" not in tabellen
    _migrieren(_endstand())
    with connection.cursor() as c:
        tabellen = connection.introspection.table_names(c)
        spalten = {s.name for s in connection.introspection.get_table_description(c, "kundenportal_objekt")}
    assert {"kundenportal_statuswechsel", "kundenportal_eingangsdatei", "kundenportal_maileingang"} <= set(tabellen)
    assert "kennung" in spalten


def test_altdaten_von_0001_ueber_0002_bis_0003():
    apps = _migrieren([(APP, "0001_anfang")])
    Kunde, Objekt = apps.get_model(APP, "Kunde"), apps.get_model(APP, "Objekt")
    k = Kunde.objects.create(name="Altkunde (fiktiv)")
    o1 = Objekt.objects.create(kunde=k, bezeichnung="Althaus 1", anschrift="Altweg 1")
    o2 = Objekt.objects.create(kunde=k, bezeichnung="Althaus 2", anschrift="Altweg 2")

    apps = _migrieren([(APP, "0002_betriebskosten_belege")])
    Auftrag = apps.get_model(APP, "Auftrag")
    Lauf, Wechsel = apps.get_model(APP, "Abrechnungslauf"), apps.get_model(APP, "Statuswechsel")
    Maileingang, Eingangsdatei = apps.get_model(APP, "Maileingang"), apps.get_model(APP, "Eingangsdatei")
    a = Auftrag.objects.create(objekt_id=o1.pk, leistung="betriebskosten", jahr=2025)
    lauf = Lauf.objects.create(auftrag=a, version=1, status="fachlich_geprueft", eingabe_hash="a" * 64,
                               ergebnis_hash="b" * 64, regeln_version="1", rote_warnungen=0, gelbe_warnungen=0,
                               stand={"alt": True}, erstellt=datetime(2025, 3, 1, tzinfo=timezone.utc))
    for s in ("entwurf", "fachlich_geprueft"):
        Wechsel.objects.create(lauf=lauf, status=s)
    m = Maileingang.objects.create(message_id="<alt@beispiel.test>", status="verarbeitet")
    Eingangsdatei.objects.create(maileingang=m, originalname="alt.pdf", groesse=10, status="uebernommen")

    apps = _migrieren(_endstand())
    Objekt, Wechsel = apps.get_model(APP, "Objekt"), apps.get_model(APP, "Statuswechsel")
    kennungen = list(Objekt.objects.values_list("kennung", flat=True))
    assert len(kennungen) == 2 and None not in kennungen and len(set(kennungen)) == 2
    kette = list(Wechsel.objects.filter(lauf_id=lauf.pk).order_by("id"))
    assert kette[0].vorher_hash == "" and kette[1].vorher_hash == kette[0].eintrag_hash
    assert all(len(w.eintrag_hash) == 64 and w.bezug_hash == "" for w in kette)  # Altbestand ohne Inhaltsbezug

    # Kettenwerte der Migration entsprechen dem Modell der Anwendung
    from kundenportal.models import Statuswechsel

    for w in Statuswechsel.objects.filter(lauf_id=lauf.pk):
        assert w.berechne_hash() == w.eintrag_hash
    # Schutz ist angelegt: Statuswechsel lassen sich nicht mehr ändern
    with pytest.raises(Exception):
        with connection.cursor() as c:
            c.execute("UPDATE kundenportal_statuswechsel SET notiz = 'x' WHERE lauf_id = %s", [lauf.pk])
    assert o1.pk != o2.pk
