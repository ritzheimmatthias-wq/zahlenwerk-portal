"""Rückfallweg für belege@: ungelesene Nachrichten per IMAP (SSL/TLS, Port 993) abholen.

Zugangsdaten nur aus Server-Secrets (PORTAL_BELEGE_IMAP_BENUTZER, PORTAL_BELEGE_IMAP_PASSWORT).
Eine Nachricht wird erst als gelesen markiert, wenn sie mit allen Anhängen verschlüsselt gesichert ist
(sichere Warteschlange). Schlägt schon die Sicherung fehl, bleibt sie ungelesen und wird beim nächsten
Lauf erneut versucht. Gesicherte, aber noch nicht verarbeitete Eingänge setzt jeder Lauf zuerst fort.
"""
from __future__ import annotations

import email
import imaplib
from email.policy import default as standard
from email.utils import parseaddr, parsedate_to_datetime

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from kundenportal.belege import Anhang, annehmen, verarbeiten, wiederaufnehmen


def nachricht_lesen(roh: bytes) -> dict:
    m = email.message_from_bytes(roh, policy=standard)
    text = m.get_body(preferencelist=("plain",))
    anhaenge = [Anhang(t.get_filename() or "anhang", t.get_payload(decode=True) or b"")
                for t in m.iter_attachments() if t.get_filename()]
    try:
        empfangen = parsedate_to_datetime(m["Date"]) if m["Date"] else None
    except (TypeError, ValueError):
        empfangen = None
    return {"message_id": (m["Message-ID"] or "").strip(), "absender": parseaddr(m["From"] or "")[1],
            "betreff": str(m["Subject"] or ""), "text": text.get_content() if text else "", "empfangen": empfangen,
            "anhaenge": anhaenge}


class Command(BaseCommand):
    help = "Holt ungelesene Nachrichten aus belege@ per IMAP ab (Rückfallweg zu n8n)."
    verbindung = imaplib.IMAP4_SSL  # in Tests austauschbar

    def handle(self, *args, **opts):
        if not (settings.BELEGE_IMAP_BENUTZER and settings.BELEGE_IMAP_PASSWORT):
            raise CommandError("IMAP-Zugang ist nicht eingerichtet (Server-Secrets fehlen).")
        fortgesetzt, offen = wiederaufnehmen()
        if fortgesetzt or offen:
            self.stdout.write(f"{fortgesetzt} gesicherte Eingänge fortgesetzt, {offen} weiterhin mit Fehler")
        imap = self.verbindung(settings.BELEGE_IMAP_HOST, settings.BELEGE_IMAP_PORT)
        imap.login(settings.BELEGE_IMAP_BENUTZER, settings.BELEGE_IMAP_PASSWORT)
        try:
            imap.select("INBOX")
            _, daten = imap.uid("SEARCH", None, "UNSEEN")
            uids = daten[0].split() if daten and daten[0] else []
            ok = fehler = 0
            for uid in uids:
                _, teile = imap.uid("FETCH", uid, "(BODY.PEEK[])")
                roh = next((t[1] for t in teile if isinstance(t, tuple)), b"")
                try:
                    n = nachricht_lesen(roh)
                    if not n["message_id"]:
                        raise ValueError("Message-ID fehlt")
                    eingang, _ = annehmen(kanal="imap", **n)
                except Exception as e:  # nicht gesichert: bleibt ungelesen, nächster Lauf versucht es erneut
                    fehler += 1
                    self.stderr.write(f"Nachricht {uid.decode()}: {type(e).__name__}")
                    continue
                if not eingang.roh_gespeichert:
                    fehler += 1
                    continue
                imap.uid("STORE", uid, "+FLAGS", "(\\Seen)")  # gesichert: Verlust ausgeschlossen
                ok += 1
                try:
                    verarbeiten(eingang.pk)
                except Exception as e:  # bleibt gesichert; Wiederaufnahme beim nächsten Lauf
                    self.stderr.write(f"Nachricht {uid.decode()}: gesichert, Verarbeitung später ({type(e).__name__})")
            self.stdout.write(f"{ok} gesichert, {fehler} mit Fehler")
        finally:
            imap.logout()
