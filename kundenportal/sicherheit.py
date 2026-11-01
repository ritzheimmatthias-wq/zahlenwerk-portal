"""Einmal-Codes, Einladungs-Token, kurzlebige Download-Links und IBAN-Prüfung."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.utils import timezone

from .models import AnmeldeCode, Benutzer, Einladung


def _hash(wert: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), wert.encode(), hashlib.sha256).hexdigest()


# --- Anmeldecode per E-Mail -------------------------------------------------

def neuer_code(benutzer: Benutzer) -> str:
    AnmeldeCode.objects.filter(benutzer=benutzer, verbraucht=False).update(verbraucht=True)
    code = f"{secrets.randbelow(1_000_000):06d}"
    AnmeldeCode.objects.create(
        benutzer=benutzer,
        code_hash=_hash(f"{benutzer.pk}:{code}"),
        gueltig_bis=timezone.now() + timedelta(minutes=settings.CODE_GUELTIG_MINUTEN),
    )
    return code


def pruefe_code(benutzer: Benutzer, code: str) -> bool:
    eintrag = (
        AnmeldeCode.objects.filter(benutzer=benutzer, verbraucht=False, gueltig_bis__gt=timezone.now())
        .order_by("-angelegt")
        .first()
    )
    if eintrag is None:
        return False
    eintrag.versuche += 1
    ok = hmac.compare_digest(eintrag.code_hash, _hash(f"{benutzer.pk}:{code.strip()}"))
    if ok or eintrag.versuche >= settings.CODE_MAX_VERSUCHE:
        eintrag.verbraucht = True
    eintrag.save(update_fields=["versuche", "verbraucht"])
    return ok


# --- Einladung ----------------------------------------------------------------

def neue_einladung(benutzer: Benutzer) -> str:
    Einladung.objects.filter(benutzer=benutzer, eingeloest__isnull=True).update(gueltig_bis=timezone.now())
    token = secrets.token_urlsafe(32)
    Einladung.objects.create(
        benutzer=benutzer,
        token_hash=_hash(token),
        gueltig_bis=timezone.now() + timedelta(days=settings.EINLADUNG_GUELTIG_TAGE),
    )
    return token


def finde_einladung(token: str) -> Einladung | None:
    e = Einladung.objects.select_related("benutzer").filter(token_hash=_hash(token)).first()
    return e if e and e.gueltig else None


# --- Download-Links -------------------------------------------------------------

_SALZ = "kundenportal.download"


def download_token(benutzer_id: int, dokument_id: int) -> str:
    return signing.dumps({"b": benutzer_id, "d": dokument_id}, salt=_SALZ)


def lies_download_token(token: str) -> tuple[int, int] | None:
    try:
        d = signing.loads(token, salt=_SALZ, max_age=settings.DOWNLOAD_LINK_SEKUNDEN)
    except signing.BadSignature:
        return None
    return int(d["b"]), int(d["d"])


# --- IBAN -------------------------------------------------------------------------

def iban_normal(iban: str) -> str:
    return "".join(iban.split()).upper()


def iban_gueltig(iban: str) -> bool:
    s = iban_normal(iban)
    if len(s) < 15 or len(s) > 34 or not s[:2].isalpha() or not s[2:4].isdigit() or not s.isalnum():
        return False
    if s.startswith("DE") and len(s) != 22:
        return False
    zahl = "".join(str(int(c, 36)) for c in s[4:] + s[:4])
    return int(zahl) % 97 == 1
