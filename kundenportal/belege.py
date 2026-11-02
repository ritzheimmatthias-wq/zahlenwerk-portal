"""Belegeingang: Upload, Smartphone-Erfassung und das Postfach belege@.

Alle Wege nutzen dieselbe Prüfung: Inhalt statt Endung, Virenscan,
Verschlüsselung je Datei, Dublettenprüfung über SHA-256. Eine OCR-Auswertung
gibt es noch nicht; eingegangene Belege prüft Zahlenwerk.

Maileingang (Zielweg laut Konzept vom 02.11.2026), zweistufig und wiederaufnehmbar:
1. `annehmen`: Nachricht und alle Anhänge verschlüsselt in einer Transaktion sichern. Erst dann
   bestätigt der Webhook bzw. setzt IMAP „gelesen“.
2. `verarbeiten`: befristeter Bearbeitungsanspruch, Prüfung, Zuordnung, Übernahme in einer Transaktion.
   Verwaiste oder fehlgeschlagene Eingänge setzt `wiederaufnehmen` (manage.py belege_wiederaufnahme) fort.

Hostinger löst für belege@ das Ereignis `message.received` aus → n8n prüft den
Webhook, holt Nachricht und Anhänge (Hostinger-Mail-Schnittstelle oder IMAP)
und übergibt sie signiert an `/eingang/belege/`. Ersatzweise holt
`manage.py belege_imap_abruf` die Nachrichten selbst per IMAP ab.
Der Nachrichtentext wird nur als Hinweis gespeichert und nie ausgeführt.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from . import mails, tresor, virenscan
from .dateipruefung import Abgelehnt, pruefe_datei, sicherer_name
from .models import Auftrag, Belegvorgang, Benutzer, Dokument, Eingangsdatei, Maileingang, Objekt, protokoll

MAX_SEITEN = 30
MAX_ANHAENGE = 20
SIGNATUR_TOLERANZ = 300  # Sekunden
REFERENZ = re.compile(r"\b(?:A|Auftrag)[\s\-#:]*(\d{1,9})\b", re.IGNORECASE)


# --- gemeinsame Ablage ------------------------------------------------------------

def datei_ablegen(datei, *, auftrag, punkt, richtung, benutzer, zeitraum="", hinweis="", **felder) -> Dokument:
    if datei.size > settings.MAX_DATEIGROESSE:
        raise Abgelehnt("Die Datei ist größer als 50 MB. Bitte teilen Sie sie auf.")
    name = sicherer_name(datei.name)
    daten = datei.read()
    ergebnis = pruefe_datei(name, daten)
    try:
        sauber, befund = virenscan.pruefen(daten)
    except virenscan.ScanFehler:
        protokoll("virenscan_fehler", benutzer, f"auftrag:{auftrag.pk}")
        raise Abgelehnt("Die Datei konnte gerade nicht geprüft werden. Bitte versuchen Sie es in einigen Minuten erneut.") from None
    if not sauber:
        protokoll("virenfund", benutzer, f"auftrag:{auftrag.pk}", befund)
        raise Abgelehnt("In der Datei wurde Schadsoftware gefunden. Sie wurde nicht gespeichert.")
    dok = Dokument(
        auftrag=auftrag, punkt=punkt, richtung=richtung, originalname=name, dateityp=ergebnis.dateityp,
        groesse=len(daten), sha256=hashlib.sha256(daten).hexdigest(),
        scan="sauber" if befund == "sauber" else "nicht_geprueft",
        zeitraum=zeitraum, hinweis=hinweis, hochgeladen_von=benutzer, **felder,
    )
    dok.schluessel = tresor.ablegen(dok.speicher_id, daten)
    dok.save()
    protokoll("upload", benutzer, f"dokument:{dok.pk}", f"auftrag:{auftrag.pk} typ:{dok.dateityp} groesse:{dok.groesse}")
    return dok


# --- Smartphone-Erfassung -----------------------------------------------------------

BILDTYPEN = {"jpg", "jpeg", "png", "pdf"}


def vorgang_anlegen(auftrag: Auftrag, benutzer, quelle: str, hinweis: str = "") -> Belegvorgang:
    v = Belegvorgang.objects.create(auftrag=auftrag, quelle=quelle, erstellt_von=benutzer, hinweis=hinweis[:1000])
    protokoll("belegvorgang_angelegt", benutzer, f"vorgang:{v.pk}", f"auftrag:{auftrag.pk} quelle:{quelle}")
    return v


def seite_ablegen(vorgang: Belegvorgang, datei, seite: int, benutzer, sha256: str = "") -> tuple[Dokument, bool]:
    """Legt eine Seite ab. Wiederholte Übertragung derselben Seite (gleiche Nummer und Prüfsumme) ist unschädlich."""
    if not 1 <= seite <= MAX_SEITEN:
        raise Abgelehnt(f"Ein Beleg kann höchstens {MAX_SEITEN} Seiten haben.")
    vorhanden = vorgang.seiten.filter(seite=seite, geloescht__isnull=True).first()
    if vorhanden and sha256 and vorhanden.sha256 == sha256:
        return vorhanden, False  # auch nach Abschluss: Wiederholung derselben Seite ist unschädlich
    if vorgang.status != "offen":
        raise Abgelehnt("Dieser Beleg ist bereits abgeschlossen.")
    if vorhanden:
        raise Abgelehnt(f"Seite {seite} ist bereits übertragen. Bitte als neue Seite senden.")
    name = sicherer_name(datei.name)
    if name.rsplit(".", 1)[-1].lower() not in BILDTYPEN:
        raise Abgelehnt("Bitte ein Foto (JPG oder PNG) oder ein PDF senden.")
    dok = datei_ablegen(datei, auftrag=vorgang.auftrag, punkt=None, richtung="kunde", benutzer=benutzer,
                        hinweis=f"Beleg {vorgang.eingang_id} Seite {seite}", belegvorgang=vorgang, seite=seite)
    if sha256 and dok.sha256 != sha256:
        protokoll("belegseite_pruefsumme", benutzer, f"dokument:{dok.pk}")
    return dok, True


def vorgang_abschliessen(vorgang: Belegvorgang, benutzer) -> bool:
    """Schließt einen Vorgang ab. Idempotent: ein wiederholter Aufruf (z. B. nach verlorener Antwort
    oder Neustart des Browsers) ändert nichts und benachrichtigt nicht erneut. Gibt zurück, ob dieser
    Aufruf den Vorgang abgeschlossen hat."""
    with transaction.atomic():
        gesperrt = Belegvorgang.objects.select_for_update().get(pk=vorgang.pk)
        if gesperrt.status != "offen":
            vorgang.status, vorgang.abgeschlossen = gesperrt.status, gesperrt.abgeschlossen
            return False
        if not gesperrt.seiten.filter(geloescht__isnull=True).exists():
            raise Abgelehnt("Der Beleg hat noch keine Seite.")
        gesperrt.status, gesperrt.abgeschlossen = "eingegangen", timezone.now()
        gesperrt.save(update_fields=["status", "abgeschlossen"])
        vorgang.status, vorgang.abgeschlossen = gesperrt.status, gesperrt.abgeschlossen
        protokoll("belegvorgang_eingegangen", benutzer, f"vorgang:{vorgang.pk}", f"seiten:{gesperrt.seiten.count()}")
        auftrag = gesperrt.auftrag
        transaction.on_commit(lambda: mails.intern_neue_unterlagen(auftrag))
    return True


# --- Maileingang ----------------------------------------------------------------------

class Signaturfehler(Exception):
    pass


def signatur(geheimnis: str, zeit: str, meta: str) -> str:
    return hmac.new(geheimnis.encode(), f"{zeit}.{meta}".encode(), hashlib.sha256).hexdigest()


def pruefe_signatur(zeit: str, meta: str, sig: str, *, jetzt: float | None = None) -> None:
    geheimnis = settings.BELEGE_WEBHOOK_GEHEIMNIS
    if not geheimnis:
        raise Signaturfehler("Eingang nicht eingerichtet")
    try:
        alter = abs((jetzt or time.time()) - int(zeit))
    except (TypeError, ValueError):
        raise Signaturfehler("Zeitstempel fehlt") from None
    if alter > SIGNATUR_TOLERANZ:
        raise Signaturfehler("Zeitstempel zu alt")
    if not hmac.compare_digest(signatur(geheimnis, zeit, meta), sig or ""):
        raise Signaturfehler("Signatur ungültig")


@dataclass
class Anhang:
    name: str
    daten: bytes


def _zuordnen(absender: str, betreff: str) -> tuple[Auftrag | None, str]:
    b = Benutzer.objects.filter(email=(absender or "").lower(), is_active=True, is_staff=False, kunde__isnull=False).first()
    if b is None:
        return None, "Absender ist keinem Kundenzugang zugeordnet"
    objekte = Objekt.objects.filter(freigaben__benutzer=b, kunde=b.kunde)
    offen = Auftrag.objects.filter(objekt__in=objekte).exclude(status="abgeschlossen")
    m = REFERENZ.search(betreff or "")
    if m:
        a = offen.filter(pk=int(m.group(1))).first()
        return (a, "") if a else (None, "Auftragsnummer im Betreff gehört nicht zu diesem Absender")
    if offen.count() == 1:
        return offen.first(), ""
    return None, "Mehrere oder keine offenen Aufträge; bitte zuordnen"


LEASE = timedelta(minutes=10)  # befristeter Bearbeitungsanspruch; danach darf ein anderer Lauf übernehmen
OFFEN = ("neu", "fehler")


@dataclass
class Eingangsergebnis:
    eingang: Maileingang
    neu: bool  # Nachricht zum ersten Mal angenommen
    gesichert: bool  # alle Anhänge verschlüsselt gespeichert (sichere Warteschlange)
    abgeschlossen: bool  # Verarbeitung beendet (verarbeitet, Zuordnung offen oder Quarantäne)


def annehmen(*, message_id: str, absender: str, betreff: str, text: str, empfangen: datetime | None,
             anhaenge: list[Anhang], kanal: str) -> tuple[Maileingang, bool]:
    """Stufe 1: Nachricht und alle Anhänge in einer Transaktion dauerhaft und verschlüsselt sichern.

    Erst danach gilt eine Nachricht als übernommen. Bricht der Prozess vorher ab, bleibt nichts zurück und
    die erneute Zustellung beginnt von vorn. Liegt bereits ein Eintrag ohne gesicherte Anhänge vor (älterer
    Stand oder Abbruch), werden die Anhänge der erneuten Zustellung gesichert.
    """
    message_id = (message_id or "").strip()[:300]
    if not message_id:
        raise ValueError("Message-ID fehlt")
    if len(anhaenge) > MAX_ANHAENGE:
        raise ValueError(f"Mehr als {MAX_ANHAENGE} Anhänge")
    with transaction.atomic():
        eingang, neu = Maileingang.objects.get_or_create(message_id=message_id, defaults={
            "kanal": kanal, "absender": (absender or "")[:254].lower(), "betreff": (betreff or "")[:300],
            "text": (text or "")[:5000], "empfangen": empfangen})
        eingang = Maileingang.objects.select_for_update().get(pk=eingang.pk)
        if not eingang.roh_gespeichert:
            eingang.dateien.filter(status="empfangen").delete()  # unvollständige Reste eines älteren Stands
            for i, a in enumerate(anhaenge):
                zu_gross = len(a.daten) > settings.MAX_DATEIGROESSE
                d = Eingangsdatei(maileingang=eingang, position=i, originalname=sicherer_name(a.name), groesse=len(a.daten),
                                  sha256=hashlib.sha256(a.daten).hexdigest(),
                                  status="quarantaene" if zu_gross else "empfangen", grund="größer als 50 MB" if zu_gross else "")
                if not zu_gross:
                    d.schluessel = tresor.ablegen(d.speicher_id, a.daten)
                d.save()
            eingang.roh_gespeichert = True
            eingang.save(update_fields=["roh_gespeichert"])
            protokoll("maileingang_gesichert", None, f"eingang:{eingang.pk}", f"anhaenge:{len(anhaenge)} kanal:{kanal}")
        elif not neu:
            protokoll("maileingang_wiederholt", None, f"eingang:{eingang.pk}", f"status:{eingang.status}")
    return eingang, neu


def _anspruch(pk: int) -> bool:
    """Atomarer, befristeter Bearbeitungsanspruch. Nur ein Lauf gewinnt; verwaiste Ansprüche verfallen."""
    jetzt = timezone.now()
    return bool(Maileingang.objects.filter(pk=pk, roh_gespeichert=True).filter(
        Q(status__in=OFFEN) | Q(status="in_bearbeitung", bearbeitung_bis__lt=jetzt) | Q(status="in_bearbeitung", bearbeitung_bis__isnull=True)
    ).update(status="in_bearbeitung", bearbeitung_bis=jetzt + LEASE, versuche=F("versuche") + 1))


def verarbeiten(pk: int) -> Maileingang:
    """Stufe 2: gesicherte Anhänge prüfen, zuordnen und übernehmen. Wiederholbar und idempotent."""
    if not _anspruch(pk):
        return Maileingang.objects.get(pk=pk)
    try:
        with transaction.atomic():
            eingang = Maileingang.objects.select_for_update().get(pk=pk)
            _verarbeiten(eingang)
    except Exception as e:  # nichts verlieren: Anhänge bleiben gesichert, Status fehler, erneut versuchbar
        fehler = f"{type(e).__name__}: {str(e)[:400]}"
        Maileingang.objects.filter(pk=pk).update(status="fehler", fehler=fehler, bearbeitung_bis=None)
        protokoll("maileingang_fehler", None, f"eingang:{pk}", fehler)
        raise
    return Maileingang.objects.get(pk=pk)


def verarbeite_eingang(**nachricht) -> Eingangsergebnis:
    """Annehmen und sofort verarbeiten. Bei einem Fehler der Verarbeitung bleibt die Nachricht gesichert."""
    eingang, neu = annehmen(**nachricht)
    eingang = verarbeiten(eingang.pk)
    return Eingangsergebnis(eingang, neu, eingang.roh_gespeichert, eingang.status in Maileingang.ABGESCHLOSSEN)


def wiederaufnehmen() -> tuple[int, int]:
    """Setzt gesicherte, aber nicht abgeschlossene Eingänge fort (neu, fehler, verfallener Anspruch)."""
    jetzt = timezone.now()
    offen = Maileingang.objects.filter(roh_gespeichert=True).filter(
        Q(status__in=OFFEN) | Q(status="in_bearbeitung", bearbeitung_bis__lt=jetzt)).values_list("pk", flat=True)
    ok = fehler = 0
    for pk in list(offen):
        try:
            verarbeiten(pk)
            ok += 1
        except Exception:
            fehler += 1
    return ok, fehler


def _nach_commit_entfernen(d: Eingangsdatei) -> None:
    speicher_id = d.speicher_id
    transaction.on_commit(lambda: tresor.entfernen(speicher_id))
    d.schluessel = None


def _verarbeiten(eingang: Maileingang) -> None:
    from django.core.files.uploadedfile import SimpleUploadedFile

    auftrag, grund = _zuordnen(eingang.absender, eingang.betreff)
    vorgang = None
    if auftrag:
        vorgang = Belegvorgang.objects.create(auftrag=auftrag, quelle="email", status="offen",
                                              hinweis=f"E-Mail: {eingang.betreff}"[:1000])
    quarantaene = eingang.dateien.filter(status="quarantaene").exists()
    seite = 0
    zaehler = {"uebernommen": 0, "dublette": 0, "quarantaene": 0, "zuordnung_offen": 0}
    for d in eingang.dateien.filter(status="empfangen").order_by("position", "id"):
        daten = tresor.lesen(d.speicher_id, d.schluessel)
        if hashlib.sha256(daten).hexdigest() != d.sha256:
            raise ValueError("Gesicherter Anhang passt nicht zu seiner Prüfsumme")
        # Dubletten nur fachlich je Auftrag (Befund W4): fremde Kunden oder offene Eingänge blockieren nichts.
        vorhanden = auftrag and Dokument.objects.filter(auftrag=auftrag, sha256=d.sha256, geloescht__isnull=True).first()
        if vorhanden:
            d.status, d.grund, d.dokument = "dublette", "Datei liegt in diesem Auftrag bereits vor", vorhanden
            _nach_commit_entfernen(d)
            d.save(update_fields=["status", "grund", "dokument", "schluessel"])
            zaehler["dublette"] += 1
            continue
        try:
            pruefe_datei(d.originalname, daten)
            sauber, befund = virenscan.pruefen(daten)
        except Abgelehnt as e:
            d.status, d.grund = "quarantaene", str(e)[:300]
            d.save(update_fields=["status", "grund"])
            quarantaene = True
            zaehler["quarantaene"] += 1
            continue
        if not sauber:
            d.status, d.grund = "quarantaene", f"Virenfund: {befund}"[:300]
            _nach_commit_entfernen(d)  # nur der Befund bleibt
            d.save(update_fields=["status", "grund", "schluessel"])
            protokoll("virenfund", None, f"eingang:{eingang.pk}", befund)
            quarantaene = True
            zaehler["quarantaene"] += 1
            continue
        if not auftrag:
            d.status, d.grund = "zuordnung_offen", grund[:300]
            d.save(update_fields=["status", "grund"])
            zaehler["zuordnung_offen"] += 1
            continue
        seite += 1
        dok = datei_ablegen(SimpleUploadedFile(d.originalname, daten), auftrag=auftrag, punkt=None, richtung="kunde",
                            benutzer=None, hinweis=f"E-Mail-Eingang {eingang.eingang_id}", belegvorgang=vorgang, seite=seite)
        d.status, d.dokument = "uebernommen", dok
        _nach_commit_entfernen(d)
        d.save(update_fields=["status", "dokument", "schluessel"])
        zaehler["uebernommen"] += 1
    if vorgang:
        if seite:
            vorgang.status, vorgang.abgeschlossen = "eingegangen", timezone.now()
            vorgang.save(update_fields=["status", "abgeschlossen"])
        else:
            vorgang.delete()
            vorgang = None
    eingang.auftrag, eingang.belegvorgang = auftrag, vorgang
    eingang.status = "quarantaene" if quarantaene else ("verarbeitet" if auftrag else "zuordnung_offen")
    eingang.fehler = "" if auftrag else grund
    eingang.bearbeitung_bis = None
    eingang.save(update_fields=["auftrag", "belegvorgang", "status", "fehler", "bearbeitung_bis"])
    protokoll("maileingang", None, f"eingang:{eingang.pk}",
              f"status:{eingang.status} " + " ".join(f"{k}:{v}" for k, v in zaehler.items()))
    if auftrag and seite:
        transaction.on_commit(lambda: mails.intern_neue_unterlagen(auftrag))
        if settings.BELEGE_EINGANGSBESTAETIGUNG:
            transaction.on_commit(lambda: mails.eingang_bestaetigen(eingang))


@transaction.atomic
def eingangsdatei_zuordnen(datei: Eingangsdatei, auftrag: Auftrag, benutzer) -> Dokument:
    """Zahlenwerk ordnet einen Anhang ohne Zuordnung einem Auftrag zu (nach Sichtprüfung)."""
    if datei.status != "zuordnung_offen" or not datei.schluessel:
        raise Abgelehnt("Diese Datei kann nicht zugeordnet werden.")
    from django.core.files.uploadedfile import SimpleUploadedFile

    daten = tresor.lesen(datei.speicher_id, datei.schluessel)
    vorhanden = Dokument.objects.filter(auftrag=auftrag, sha256=datei.sha256, geloescht__isnull=True).first()
    if vorhanden:  # liegt im Zielauftrag bereits vor: verknüpfen statt doppelt anlegen
        datei.status, datei.dokument, datei.grund = "dublette", vorhanden, "Datei liegt in diesem Auftrag bereits vor"
        _nach_commit_entfernen(datei)
        datei.save(update_fields=["status", "dokument", "grund", "schluessel"])
        protokoll("eingangsdatei_dublette", benutzer, f"eingangsdatei:{datei.pk}", f"auftrag:{auftrag.pk}")
        return vorhanden
    vorgang = Belegvorgang.objects.create(auftrag=auftrag, quelle="email", status="eingegangen", erstellt_von=benutzer,
                                          abgeschlossen=timezone.now(), hinweis=f"E-Mail: {datei.maileingang.betreff}"[:1000])
    dok = datei_ablegen(SimpleUploadedFile(datei.originalname, daten), auftrag=auftrag, punkt=None, richtung="kunde",
                        benutzer=benutzer, hinweis=f"E-Mail-Eingang {datei.maileingang.eingang_id}", belegvorgang=vorgang, seite=1)
    datei.status, datei.dokument = "uebernommen", dok
    _nach_commit_entfernen(datei)
    datei.save(update_fields=["status", "dokument", "schluessel"])
    protokoll("eingangsdatei_zugeordnet", benutzer, f"eingangsdatei:{datei.pk}", f"auftrag:{auftrag.pk}")
    return dok


def anhaenge_passen(meta: dict, anhaenge: list[Anhang]) -> bool:
    """Signierte Liste und hochgeladene Dateien müssen als Multimenge gleich sein (Befund H1):
    gleiche Anzahl, gleiche Namen, gleiche Prüfsummen. Zusätzliche Kopien, fehlende oder veränderte
    Anhänge werden abgewiesen."""
    liste = meta.get("anhaenge", [])
    if not isinstance(liste, list):
        return False
    erwartet = Counter((str(x["name"]), str(x["sha256"]).lower()) for x in liste)
    return erwartet == Counter((x.name, hashlib.sha256(x.daten).hexdigest()) for x in anhaenge)


def meta_lesen(meta: str) -> dict:
    d = json.loads(meta)
    if not isinstance(d, dict):
        raise ValueError("meta ist kein Objekt")
    return d
