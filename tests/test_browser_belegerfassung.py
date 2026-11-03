"""Browserprüfung der Smartphone-Belegerfassung (Befund W7) und des mobilen Layouts.

Läuft mit Playwright gegen einen lokalen Testserver, nur mit erfundenen Daten. Die mobile Erfassung
wird ausschließlich in dieser Testumgebung eingeschaltet. Kein echter Mailversand (locmem).

Ohne installiertes Playwright oder ohne startbaren Browser werden die Tests übersprungen; mit
PORTAL_BROWSERTESTS=1 (CI-Job „browser“) ist ein fehlender Browser dagegen ein Fehler.
Optional: PORTAL_TEST_CHROMIUM=<Pfad zur Chromium-Datei>.
"""
import os
import struct
import zlib

import pytest
from django.core import mail

from kundenportal import tresor
from kundenportal.models import Belegvorgang, Benutzer, Dokument, Freigabe, Kunde, Objekt, Auftrag
from tests.test_portal import PASSWORT, anmelden

pytestmark = pytest.mark.django_db(transaction=True)

PFLICHT = os.environ.get("PORTAL_BROWSERTESTS") == "1"
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")  # Playwright sync-API im selben Prozess

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None


def png(breite: int, hoehe: int, farbe=(200, 40, 40)) -> bytes:
    """Erfundenes, einfarbiges PNG (ohne Bildbibliothek)."""
    zeile = b"\x00" + bytes(farbe) * breite
    roh = zlib.compress(zeile * hoehe)

    def block(art, daten):
        return struct.pack(">I", len(daten)) + art + daten + struct.pack(">I", zlib.crc32(art + daten) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + block(b"IHDR", struct.pack(">IIBBBBB", breite, hoehe, 8, 2, 0, 0, 0))
            + block(b"IDAT", roh) + block(b"IEND", b""))


def jpeg_groesse(daten: bytes) -> tuple[int, int]:
    i = 2
    while i < len(daten):
        marker, laenge = daten[i + 1], struct.unpack(">H", daten[i + 2:i + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack(">HH", daten[i + 5:i + 9])
            return w, h
        i += 2 + laenge
    raise AssertionError("kein SOF-Block")


@pytest.fixture(scope="module")
def browser():
    if sync_playwright is None:
        if PFLICHT:
            pytest.fail("Playwright fehlt")
        pytest.skip("Playwright nicht installiert")
    with sync_playwright() as p:
        pfad = os.environ.get("PORTAL_TEST_CHROMIUM")
        try:
            b = p.chromium.launch(executable_path=pfad) if pfad else p.chromium.launch()
        except Exception as e:  # pragma: no cover
            if PFLICHT:
                raise
            pytest.skip(f"Browser nicht startbar: {e}")
        yield p, b, pfad
        b.close()


@pytest.fixture(autouse=True)
def umgebung(settings, tmp_path):
    settings.DATEN_VERZEICHNIS = tmp_path / "daten"
    settings.MOBILE_ERFASSUNG_FUER_KUNDEN = True  # nur in diesem Test
    settings.MAIL_INTERN = "intern@beispiel.test"


@pytest.fixture
def welt(db):
    k = Kunde.objects.create(name="Eigentümerin Browser (fiktiv)")
    o = Objekt.objects.create(kunde=k, bezeichnung="Browserhaus", anschrift="Testweg 3")
    a = Auftrag.objects.create(objekt=o, leistung="betriebskosten", jahr=2025)
    eig = Benutzer.objects.create_user("browser@beispiel.test", PASSWORT, kunde=k)
    Freigabe.objects.create(benutzer=eig, objekt=o)
    return {"a": a}


def sitzung(client, live_server, kontext):
    if "sessionid" not in client.cookies:
        anmelden(client, "browser@beispiel.test")
    kontext.add_cookies([{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": live_server.url}])


def erfassen(seite, live_server, auftrag, bilder):
    seite.goto(f"{live_server.url}/auftrag/{auftrag.pk}/beleg/")
    seite.set_input_files("#seiten", [{"name": f"foto-{i}.png", "mimeType": "image/png", "buffer": b}
                                      for i, b in enumerate(bilder, 1)])
    expect(seite.locator("#vorschau img")).to_have_count(len(bilder))


def status(seite, text, timeout=20000):
    # Locator-Prüfungen statt wait_for_function: die strenge CSP der Seite (kein eval) bleibt aktiv.
    expect(seite.locator("#status")).to_contain_text(text, timeout=timeout)


def mails_neue_unterlagen():
    return [m for m in mail.outbox if m.subject.startswith("Neue Unterlagen")]


def test_abbruch_vor_abschluss_und_neustart_des_browsers(browser, welt, client, live_server, tmp_path):
    p, _, pfad = browser
    profil = tmp_path / "profil"
    start = {"executable_path": pfad} if pfad else {}
    k = p.chromium.launch_persistent_context(str(profil), viewport={"width": 375, "height": 740}, **start)
    sitzung(client, live_server, k)
    s = k.new_page()
    erfassen(s, live_server, welt["a"], [png(60, 40), png(40, 60, (20, 90, 200))])
    s.route("**/abschliessen/", lambda r: r.abort())  # Netz bricht genau vor dem Abschluss ab
    mail.outbox.clear()
    s.click("#absenden")
    status(s, "Übertragung unterbrochen")
    v = Belegvorgang.objects.get()
    assert v.status == "offen" and v.seiten.count() == 2  # alle Seiten sind da, Abschluss fehlt
    k.close()  # Browser beenden

    k = p.chromium.launch_persistent_context(str(profil), viewport={"width": 375, "height": 740}, **start)
    # Sitzungscookie endet mit dem Browser; wie auf dem Smartphone neu anmelden. IndexedDB bleibt.
    sitzung(client, live_server, k)
    s = k.new_page()
    s.goto(f"{live_server.url}/auftrag/{welt['a'].pk}/beleg/")
    status(s, "noch nicht abgeschlossen")  # Alle Seiten waren „fertig“, trotzdem fortsetzen
    with s.expect_navigation(url=f"**/auftrag/{welt['a'].pk}/"):
        s.click("#absenden")
    v.refresh_from_db()
    assert v.status == "eingegangen" and v.seiten.count() == 2
    assert Belegvorgang.objects.count() == 1 and len(mails_neue_unterlagen()) == 1
    s.goto(f"{live_server.url}/auftrag/{welt['a'].pk}/beleg/")
    s.wait_for_timeout(500)
    assert s.evaluate("document.getElementById('status').textContent") == ""  # nichts mehr gespeichert
    k.close()


def test_server_schliesst_ab_aber_antwort_geht_verloren(browser, welt, client, live_server):
    _, b, _ = browser
    k = b.new_context(viewport={"width": 390, "height": 800})
    sitzung(client, live_server, k)
    s = k.new_page()
    erfassen(s, live_server, welt["a"], [png(50, 50)])

    def verlieren(route):
        route.fetch()  # Server verarbeitet den Abschluss …
        route.abort()  # … die Antwort erreicht das Smartphone aber nicht

    s.route("**/abschliessen/", verlieren)
    mail.outbox.clear()
    s.click("#absenden")
    status(s, "Übertragung unterbrochen")
    v = Belegvorgang.objects.get()
    assert v.status == "eingegangen" and len(mails_neue_unterlagen()) == 1

    s.unroute("**/abschliessen/")
    s2 = k.new_page()  # Seite neu öffnen: Vorgang ist noch gespeichert und wird abgeglichen
    s.close()
    s2.goto(f"{live_server.url}/auftrag/{welt['a'].pk}/beleg/")
    status(s2, "noch nicht abgeschlossen")
    with s2.expect_navigation(url=f"**/auftrag/{welt['a'].pk}/"):
        s2.click("#absenden")
    assert Belegvorgang.objects.count() == 1 and len(mails_neue_unterlagen()) == 1  # keine zweite Meldung
    assert Dokument.objects.count() == 1
    k.close()


def test_seite_wird_nach_verlorener_antwort_ohne_duplikat_wiederholt(browser, welt, client, live_server):
    _, b, _ = browser
    k = b.new_context(viewport={"width": 320, "height": 640})
    sitzung(client, live_server, k)
    s = k.new_page()
    erfassen(s, live_server, welt["a"], [png(30, 30), png(31, 31), png(32, 32)])
    zaehler = {"n": 0}

    def zweite_seite(route):
        zaehler["n"] += 1
        if b'name="seite"\r\n\r\n2\r\n' in (route.request.post_data_buffer or b"") and zaehler.get("verloren") is None:
            zaehler["verloren"] = True
            route.fetch()
            route.abort()
            return
        route.continue_()

    s.route("**/seite/", zweite_seite)
    with s.expect_navigation(url=f"**/auftrag/{welt['a'].pk}/", timeout=20000):
        s.click("#absenden")
    v = Belegvorgang.objects.get()
    assert v.status == "eingegangen" and zaehler["verloren"]
    assert sorted(v.seiten.values_list("seite", flat=True)) == [1, 2, 3]  # Seite 2 nur einmal
    k.close()


def test_drehen_zuschneiden_und_tastatur(browser, welt, client, live_server):
    _, b, _ = browser
    k = b.new_context(viewport={"width": 375, "height": 740})
    sitzung(client, live_server, k)
    s = k.new_page()
    erfassen(s, live_server, welt["a"], [png(200, 100)])
    drehen = s.get_by_role("button", name="Drehen")
    drehen.focus()
    s.keyboard.press("Enter")  # Bedienung per Tastatur
    hochkant = "(() => { const i = document.querySelector('#vorschau img'); return !!i && i.naturalHeight > i.naturalWidth; })()"
    for _ in range(50):
        if s.evaluate(hochkant):
            break
        s.wait_for_timeout(100)
    assert s.evaluate(hochkant)
    s.get_by_role("button", name="Zuschneiden").focus()
    s.keyboard.press("Space")
    regler = s.locator(".schnitt input[type=range]").first  # Rand oben
    assert regler.is_visible()
    regler.fill("20")
    regler.dispatch_event("change")
    s.wait_for_timeout(300)
    s.locator(".schnitt input[type=range]").last.focus()  # letztes Bedienelement der Seitenliste
    s.keyboard.press("Tab")
    assert s.evaluate("document.activeElement.id") == "absenden"
    with s.expect_navigation(url=f"**/auftrag/{welt['a'].pk}/"):
        s.keyboard.press("Enter")
    d = Dokument.objects.get()
    w, h = jpeg_groesse(tresor.lesen(d.speicher_id, d.schluessel))
    assert (w, h) == (80, 200)  # gedreht (100×200) und oben 20 % zugeschnitten (200 → 80 nach Drehung)
    k.close()


@pytest.mark.parametrize("breite", [320, 375, 390])
def test_layout_ohne_horizontalen_ueberlauf(browser, welt, client, live_server, breite):
    _, b, _ = browser
    k = b.new_context(viewport={"width": breite, "height": 740}, device_scale_factor=2, is_mobile=True, has_touch=True)
    sitzung(client, live_server, k)
    s = k.new_page()
    erfassen(s, live_server, welt["a"], [png(400, 300), png(300, 400)])
    s.get_by_role("button", name="Zuschneiden").first.click()
    for pfad in (f"/auftrag/{welt['a'].pk}/beleg/", f"/auftrag/{welt['a'].pk}/", "/"):
        if pfad != f"/auftrag/{welt['a'].pk}/beleg/":
            s.goto(live_server.url + pfad)
        masse = s.evaluate("({innen: window.innerWidth, doc: document.documentElement.scrollWidth, body: document.body.scrollWidth})")
        assert masse["innen"] == breite, masse  # tatsächliche Breite bestätigt
        assert masse["doc"] <= breite and masse["body"] <= breite, (pfad, masse)
    k.close()
