"""E-Mails enthalten nie Inhalte, nur einen Hinweis und einen Link."""
from __future__ import annotations

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse

from .models import Auftrag, Benutzer

GRUSS = "\n\nZahlenwerk Immobilien\nhttps://zahlenwerk-immobilien.de"


def _url(pfad: str) -> str:
    return settings.BASIS_URL + pfad


def code_senden(benutzer: Benutzer, code: str) -> None:
    send_mail(
        "Ihr Anmeldecode für das Kundenportal",
        f"Guten Tag,\n\nIhr Anmeldecode lautet: {code}\n\nEr ist {settings.CODE_GUELTIG_MINUTEN} Minuten gültig. "
        "Wenn Sie sich nicht gerade anmelden, können Sie diese E-Mail ignorieren." + GRUSS,
        None,
        [benutzer.email],
    )


def einladung_senden(benutzer: Benutzer, token: str) -> None:
    send_mail(
        "Ihr Zugang zum Kundenportal von Zahlenwerk Immobilien",
        "Guten Tag,\n\nfür Ihren Auftrag haben wir Ihnen einen Zugang zum Kundenportal eingerichtet. "
        "Über diesen persönlichen Link vergeben Sie Ihr Passwort:\n\n"
        f"{_url(reverse('einladung', args=[token]))}\n\n"
        f"Der Link ist {settings.EINLADUNG_GUELTIG_TAGE} Tage gültig. Danach melden Sie sich mit Ihrer "
        "E-Mail-Adresse, Ihrem Passwort und einem Code an, den wir Ihnen bei jeder Anmeldung per E-Mail senden." + GRUSS,
        None,
        [benutzer.email],
    )


def intern_neue_unterlagen(auftrag: Auftrag) -> None:
    if not settings.MAIL_INTERN:
        return
    send_mail(
        f"Neue Unterlagen zu Auftrag {auftrag.pk}",
        f"Neue Unterlagen zu Auftrag {auftrag.pk}:\n{_url(reverse('intern_auftrag', args=[auftrag.pk]))}",
        None,
        [settings.MAIL_INTERN],
    )


def kunde_hinweis(auftrag: Auftrag, empfaenger: list[str], anlass: str) -> None:
    """anlass: 'nachreichen' oder 'ergebnis'. Kein Inhalt, nur Hinweis und Link."""
    if not empfaenger:
        return
    text = {
        "nachreichen": "zu Ihrem Auftrag gibt es eine Bitte um Nachreichung.",
        "ergebnis": "zu Ihrem Auftrag liegt ein neues Dokument von Zahlenwerk bereit.",
    }[anlass]
    send_mail(
        "Neuigkeit im Kundenportal",
        f"Guten Tag,\n\n{text} Bitte melden Sie sich im Kundenportal an:\n\n"
        f"{_url(reverse('auftrag', args=[auftrag.pk]))}" + GRUSS,
        None,
        empfaenger,
    )
