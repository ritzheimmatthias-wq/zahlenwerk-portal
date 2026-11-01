"""Zugangsregeln und Sicherheitsköpfe für alle Seiten."""
from __future__ import annotations

from django.conf import settings
from django.http import HttpResponseForbidden
from django.shortcuts import redirect

OFFEN = ("/anmelden/", "/einladung/", "/static/", "/download/")


class Zugang:
    """Ohne Anmeldung nur Anmeldung und Einladung; /intern/ nur für Zahlenwerk."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        pfad = request.path
        if not pfad.startswith(OFFEN):
            if not request.user.is_authenticated:
                return redirect(settings.LOGIN_URL)
            if pfad.startswith("/intern/") and not request.user.is_staff:
                return HttpResponseForbidden("Kein Zugriff.")
        return self.get_response(request)


class Sicherheitskopf:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        antwort = self.get_response(request)
        antwort.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
        )
        antwort.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if not request.path.startswith("/static/"):
            antwort.setdefault("Cache-Control", "no-store")
        return antwort
