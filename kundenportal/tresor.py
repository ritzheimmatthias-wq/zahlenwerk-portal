"""Verschlüsselte Ablage: jede Datei mit eigenem AES-256-GCM-Schlüssel.

Der Dateischlüssel wird mit dem Hauptschlüssel (PORTAL_MASTER_KEY) verschlüsselt
in der Datenbank gespeichert. Die Speicher-ID ist als Zusatzdaten gebunden, damit
Schlüssel und Datei nicht vertauscht werden können. Wird der Schlüssel gelöscht,
ist die Datei auch in Sicherungen nicht mehr lesbar.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings


class TresorFehler(Exception):
    pass


def _hauptschluessel() -> bytes:
    k = base64.b64decode(settings.MASTER_KEY)
    if len(k) != 32:
        raise TresorFehler("PORTAL_MASTER_KEY muss 32 Byte (base64) lang sein.")
    return k


def _pfad(speicher_id: UUID) -> Path:
    h = speicher_id.hex
    return Path(settings.DATEN_VERZEICHNIS) / h[:2] / f"{h}.bin"


def ablegen(speicher_id: UUID, daten: bytes) -> bytes:
    """Verschlüsselt und speichert die Datei. Gibt den verpackten Dateischlüssel zurück."""
    aad = speicher_id.bytes
    schluessel = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    inhalt = nonce + AESGCM(schluessel).encrypt(nonce, daten, aad)
    pfad = _pfad(speicher_id)
    pfad.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = pfad.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        f.write(inhalt)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, pfad)
    n2 = os.urandom(12)
    return n2 + AESGCM(_hauptschluessel()).encrypt(n2, schluessel, aad)


def lesen(speicher_id: UUID, verpackt: bytes) -> bytes:
    aad = speicher_id.bytes
    verpackt = bytes(verpackt)
    try:
        schluessel = AESGCM(_hauptschluessel()).decrypt(verpackt[:12], verpackt[12:], aad)
        roh = _pfad(speicher_id).read_bytes()
        return AESGCM(schluessel).decrypt(roh[:12], roh[12:], aad)
    except FileNotFoundError as e:
        raise TresorFehler("Datei nicht vorhanden") from e
    except Exception as e:  # InvalidTag u. a.
        raise TresorFehler("Datei oder Schlüssel beschädigt") from e


def entfernen(speicher_id: UUID) -> None:
    try:
        _pfad(speicher_id).unlink()
    except FileNotFoundError:
        pass
