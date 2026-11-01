"""Virenscan über einen laufenden clamd (ClamAV als eigener Dienst, nur über das Netzprotokoll angesprochen)."""
from __future__ import annotations

import socket
import struct

from django.conf import settings


class ScanFehler(Exception):
    """Scanner nicht erreichbar oder Antwort unklar: Upload wird abgelehnt (fail closed)."""


def _verbindung() -> socket.socket:
    if settings.CLAMD_SOCKET:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(settings.CLAMD_SOCKET)
    else:
        s = socket.create_connection((settings.CLAMD_HOST, settings.CLAMD_PORT))
    s.settimeout(120)
    return s


def pruefen(daten: bytes) -> tuple[bool, str]:
    """(sauber, Befund). Wirft ScanFehler, wenn kein eindeutiges Ergebnis vorliegt."""
    if settings.VIRENSCAN == "aus":
        return True, "nicht geprüft"
    try:
        with _verbindung() as s:
            s.sendall(b"zINSTREAM\0")
            for i in range(0, len(daten), 64 * 1024):
                teil = daten[i : i + 64 * 1024]
                s.sendall(struct.pack("!L", len(teil)) + teil)
            s.sendall(struct.pack("!L", 0))
            antwort = b""
            while not antwort.endswith(b"\0"):
                stueck = s.recv(4096)
                if not stueck:
                    break
                antwort += stueck
    except OSError as e:
        raise ScanFehler(f"Virenscanner nicht erreichbar: {e.__class__.__name__}") from e
    text = antwort.rstrip(b"\0").decode("utf-8", "replace")
    if text.endswith("OK"):
        return True, "sauber"
    if text.endswith("FOUND"):
        return False, text.split(":", 1)[-1].replace("FOUND", "").strip()
    raise ScanFehler(f"Unklare Antwort des Virenscanners: {text[:80]}")
