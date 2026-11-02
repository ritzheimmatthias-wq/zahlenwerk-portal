"""Belegeingang: Upload, Smartphone-Erfassung und das Postfach belege@.

Alle Wege nutzen dieselbe Prüfung: Inhalt statt Endung, Virenscan,
Verschlüsselung je Datei, Dublettenprüfung über SHA-256. Eine OCR-Auswertung
gibt es noch nicht; eingegangene Belege prüft Zahlenwerk.

Maileingang (Zielweg laut Konzept vom 02.11.2026):
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
from dataclasses import dataclass
from datetime import datetime

from django.conf import settings
from django.db import transaction
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
    if vorgang.status != "offen":
        raise Abgelehnt("Dieser Beleg ist bereits abgeschlossen.")
    if not 1 <= seite <= MAX_SEITEN:
        raise Abgelehnt(f"Ein Beleg kann höchstens {MAX_SEITEN} Seiten haben.")
    vorhanden = vorgang.seiten.filter(seite=seite, geloescht__isnull=True).first()
    if vorhanden:
        if sha256 and vorhanden.sha256 == sha256:
            return vorhanden, False
        raise Abgelehnt(f"Seite {seite} ist bereits übertragen. Bitte als neue Seite senden.")
    name = sicherer_name(datei.name)
    if name.rsplit(".", 1)[-1].lower() not in BILDTYPEN:
        raise Abgelehnt("Bitte ein Foto (JPG oder PNG) oder ein PDF senden.")
    dok = datei_ablegen(datei, auftrag=vorgang.auftrag, punkt=None, richtung="kunde", benutzer=benutzer,
                        hinweis=f"Beleg {vorgang.eingang_id} Seite {seite}", belegvorgang=vorgang, seite=seite)
    if sha256 and dok.sha256 != sha256:
        protokoll("belegseite_pruefsumme", benutzer, f"dokument:{dok.pk}")
    return dok, True


def vorgang_abschliessen(vorgang: Belegvorgang, benutzer) -> None:
    if vorgang.status != "offen":
        return
    if not vorgang.seiten.filter(geloescht__isnull=True).exists():
        raise Abgelehnt("Der Beleg hat noch keine Seite.")
    vorgang.status, vorgang.abgeschlossen = "eingegangen", timezone.now()
    vorgang.save(update_fields=["status", "abgeschlossen"])
    protokoll("belegvorgang_eingegangen", benutzer, f"vorgang:{vorgang.pk}", f"seiten:{vorgang.seiten.count()}")
    mails.intern_neue_unterlagen(vorgang.auftrag)


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


def _datei_speichern(eingang: Maileingang, a: Anhang, status: str, grund: str = "", speichern: bool = True) -> Eingangsdatei:
    d = Eingangsdatei(maileingang=eingang, originalname=sicherer_name(a.name), groesse=len(a.daten),
                      sha256=hashlib.sha256(a.daten).hexdigest(), status=status, grund=grund[:300])
    if speichern:
        d.schluessel = tresor.ablegen(d.speicher_id, a.daten)
    d.save()
    return d


def verarbeite_eingang(*, message_id: str, absender: str, betreff: str, text: str, empfangen: datetime | None,
                       anhaenge: list[Anhang], kanal: str) -> tuple[Maileingang, bool]:
    """Nimmt eine Nachricht aus belege@ an. Gibt (Eingang, neu) zurück; Dubletten werden nicht erneut verarbeitet."""
    message_id = (message_id or "").strip()[:300]
    if not message_id:
        raise ValueError("Message-ID fehlt")
    with transaction.atomic():
        eingang, neu = Maileingang.objects.get_or_create(message_id=message_id, defaults={
            "kanal": kanal, "absender": (absender or "")[:254].lower(), "betreff": (betreff or "")[:300],
            "text": (text or "")[:5000], "empfangen": empfangen})
    if not neu and eingang.status != "fehler":
        protokoll("maileingang_dublette", None, f"eingang:{eingang.pk}")
        return eingang, False
    eingang.versuche += 1
    eingang.save(update_fields=["versuche"])
    try:
        with transaction.atomic():
            _verarbeiten(eingang, anhaenge)
    except Exception as e:  # nichts verlieren: Eintrag bleibt, Status fehler, n8n wiederholt
        eingang.status, eingang.fehler = "fehler", f"{type(e).__name__}: {str(e)[:400]}"
        eingang.save(update_fields=["status", "fehler"])
        protokoll("maileingang_fehler", None, f"eingang:{eingang.pk}", eingang.fehler)
        raise
    return eingang, True


def _verarbeiten(eingang: Maileingang, anhaenge: list[Anhang]) -> None:
    if len(anhaenge) > MAX_ANHAENGE:
        raise ValueError(f"Mehr als {MAX_ANHAENGE} Anhänge")
    auftrag, grund = _zuordnen(eingang.absender, eingang.betreff)
    vorgang = None
    if auftrag:
        vorgang = Belegvorgang.objects.create(auftrag=auftrag, quelle="email", status="offen",
                                              hinweis=f"E-Mail: {eingang.betreff}"[:1000])
    quarantaene = False
    seite = 0
    for a in anhaenge:
        sha = hashlib.sha256(a.daten).hexdigest()
        if Eingangsdatei.objects.filter(sha256=sha).exclude(maileingang=eingang).exists() or \
                (auftrag and Dokument.objects.filter(auftrag=auftrag, sha256=sha, geloescht__isnull=True).exists()):
            _datei_speichern(eingang, a, "dublette", "Datei bereits eingegangen", speichern=False)
            continue
        if len(a.daten) > settings.MAX_DATEIGROESSE:
            _datei_speichern(eingang, a, "quarantaene", "größer als 50 MB", speichern=False)
            quarantaene = True
            continue
        try:
            pruefe_datei(sicherer_name(a.name), a.daten)
            sauber, befund = virenscan.pruefen(a.daten)
        except Abgelehnt as e:
            _datei_speichern(eingang, a, "quarantaene", str(e))
            quarantaene = True
            continue
        if not sauber:
            _datei_speichern(eingang, a, "quarantaene", f"Virenfund: {befund}", speichern=False)
            protokoll("virenfund", None, f"eingang:{eingang.pk}", befund)
            quarantaene = True
            continue
        if not auftrag:
            _datei_speichern(eingang, a, "zuordnung_offen", grund)
            continue
        from django.core.files.uploadedfile import SimpleUploadedFile

        seite += 1
        dok = datei_ablegen(SimpleUploadedFile(a.name, a.daten), auftrag=auftrag, punkt=None, richtung="kunde",
                            benutzer=None, hinweis=f"E-Mail-Eingang {eingang.eingang_id}", belegvorgang=vorgang, seite=seite)
        d = _datei_speichern(eingang, a, "uebernommen", speichern=False)
        d.dokument = dok
        d.save(update_fields=["dokument"])
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
    eingang.save(update_fields=["auftrag", "belegvorgang", "status", "fehler"])
    protokoll("maileingang", None, f"eingang:{eingang.pk}", f"status:{eingang.status} anhaenge:{len(anhaenge)}")
    if auftrag:
        mails.intern_neue_unterlagen(auftrag)
        if settings.BELEGE_EINGANGSBESTAETIGUNG:
            mails.eingang_bestaetigen(eingang)


@transaction.atomic
def eingangsdatei_zuordnen(datei: Eingangsdatei, auftrag: Auftrag, benutzer) -> Dokument:
    """Zahlenwerk ordnet einen Anhang ohne Zuordnung einem Auftrag zu (nach Sichtprüfung)."""
    if datei.status != "zuordnung_offen" or not datei.schluessel:
        raise Abgelehnt("Diese Datei kann nicht zugeordnet werden.")
    from django.core.files.uploadedfile import SimpleUploadedFile

    daten = tresor.lesen(datei.speicher_id, datei.schluessel)
    vorgang = Belegvorgang.objects.create(auftrag=auftrag, quelle="email", status="eingegangen", erstellt_von=benutzer,
                                          abgeschlossen=timezone.now(), hinweis=f"E-Mail: {datei.maileingang.betreff}"[:1000])
    dok = datei_ablegen(SimpleUploadedFile(datei.originalname, daten), auftrag=auftrag, punkt=None, richtung="kunde",
                        benutzer=benutzer, hinweis=f"E-Mail-Eingang {datei.maileingang.eingang_id}", belegvorgang=vorgang, seite=1)
    tresor.entfernen(datei.speicher_id)
    datei.status, datei.dokument, datei.schluessel = "uebernommen", dok, None
    datei.save(update_fields=["status", "dokument", "schluessel"])
    protokoll("eingangsdatei_zugeordnet", benutzer, f"eingangsdatei:{datei.pk}", f"auftrag:{auftrag.pk}")
    return dok


def meta_lesen(meta: str) -> dict:
    d = json.loads(meta)
    if not isinstance(d, dict):
        raise ValueError("meta ist kein Objekt")
    return d
