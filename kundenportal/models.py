"""Datenmodell des Kundenportals.

Die Datenbank hält nur Stammdaten, Status und Dateiverweise. Dateiinhalte liegen
verschlüsselt im Datenverzeichnis, jede Datei mit eigenem Schlüssel.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models
from django.utils import timezone


class Kunde(models.Model):
    ARTEN = [
        ("weg", "Eigentümergemeinschaft (WEG)"),
        ("privat", "Privater Eigentümer"),
        ("verwaltung", "Hausverwaltung"),
        ("unternehmen", "Unternehmen"),
    ]
    name = models.CharField(max_length=200)
    art = models.CharField(max_length=20, choices=ARTEN, default="weg")
    ansprechpartner = models.CharField(max_length=200, blank=True)
    angelegt = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Kunden"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class BenutzerManager(UserManager):
    def create_user(self, email, password=None, **extra):
        email = self.normalize_email(email).lower()
        return super().create_user(username=email, email=email, password=password, **extra)

    def create_superuser(self, email, password=None, **extra):
        email = self.normalize_email(email).lower()
        return super().create_superuser(username=email, email=email, password=password, **extra)


class Benutzer(AbstractUser):
    """Anmeldung per E-Mail. Mitarbeiter von Zahlenwerk haben is_staff, Kunden einen Kunden."""

    email = models.EmailField(unique=True)
    kunde = models.ForeignKey(Kunde, null=True, blank=True, on_delete=models.CASCADE, related_name="benutzer")
    fehlversuche = models.PositiveIntegerField(default=0)
    gesperrt_bis = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []
    objects = BenutzerManager()

    def save(self, *args, **kwargs):
        self.email = (self.email or "").lower()
        self.username = self.email
        super().save(*args, **kwargs)

    @property
    def vorlaeufig_gesperrt(self) -> bool:
        return bool(self.gesperrt_bis and self.gesperrt_bis > timezone.now())


class Einladung(models.Model):
    benutzer = models.ForeignKey(Benutzer, on_delete=models.CASCADE, related_name="einladungen")
    token_hash = models.CharField(max_length=64, unique=True)
    gueltig_bis = models.DateTimeField()
    eingeloest = models.DateTimeField(null=True, blank=True)
    angelegt = models.DateTimeField(auto_now_add=True)

    @property
    def gueltig(self) -> bool:
        return self.eingeloest is None and self.gueltig_bis > timezone.now()


class AnmeldeCode(models.Model):
    benutzer = models.ForeignKey(Benutzer, on_delete=models.CASCADE, related_name="codes")
    code_hash = models.CharField(max_length=64)
    gueltig_bis = models.DateTimeField()
    versuche = models.PositiveIntegerField(default=0)
    verbraucht = models.BooleanField(default=False)
    angelegt = models.DateTimeField(auto_now_add=True)


class Objekt(models.Model):
    ARTEN = [("weg", "WEG"), ("mfh", "Mehrfamilienhaus"), ("wgh", "Wohn- und Geschäftshaus"), ("gewerbe", "Gewerbe")]
    kunde = models.ForeignKey(Kunde, on_delete=models.CASCADE, related_name="objekte")
    bezeichnung = models.CharField(max_length=200)
    anschrift = models.CharField(max_length=200)
    ort = models.CharField("PLZ und Ort", max_length=120, blank=True)
    objektart = models.CharField(max_length=10, choices=ARTEN, default="weg")
    baujahr = models.CharField(max_length=10, blank=True)
    wirtschaftsjahr = models.CharField(max_length=40, default="Kalenderjahr")
    heizung = models.CharField(max_length=60, blank=True)
    steckbrief = models.JSONField(default=dict, blank=True)  # weitere freie Angaben
    angelegt = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Objekte"
        ordering = ["bezeichnung"]

    def __str__(self) -> str:
        return self.bezeichnung


class Konto(models.Model):
    ARTEN = [("giro", "Girokonto"), ("ruecklage", "Rücklagenkonto"), ("sonstiges", "Sonstiges")]
    objekt = models.ForeignKey(Objekt, on_delete=models.CASCADE, related_name="konten")
    bezeichnung = models.CharField(max_length=100)
    art = models.CharField(max_length=10, choices=ARTEN, default="giro")
    iban = models.CharField(max_length=34)

    class Meta:
        verbose_name_plural = "Konten"


class Einheit(models.Model):
    objekt = models.ForeignKey(Objekt, on_delete=models.CASCADE, related_name="einheiten")
    nummer = models.CharField(max_length=20)
    eigentuemer = models.CharField(max_length=200, blank=True)
    mea = models.DecimalField("Miteigentumsanteil", max_digits=10, decimal_places=4, null=True, blank=True)
    hausgeld = models.DecimalField("Hausgeld je Monat", max_digits=10, decimal_places=2, null=True, blank=True)
    ab = models.DateField(null=True, blank=True)
    iban = models.CharField("IBAN Eigentümer", max_length=34, blank=True)

    class Meta:
        verbose_name_plural = "Einheiten"
        ordering = ["nummer", "ab"]


class Freigabe(models.Model):
    """Welcher Benutzer welches Objekt sieht."""

    benutzer = models.ForeignKey(Benutzer, on_delete=models.CASCADE, related_name="freigaben")
    objekt = models.ForeignKey(Objekt, on_delete=models.CASCADE, related_name="freigaben")
    angelegt = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("benutzer", "objekt")]


class Auftrag(models.Model):
    LEISTUNGEN = [
        ("weg_jahresabrechnung", "WEG-Jahresabrechnung"),
        ("betriebskosten", "Betriebskostenabrechnung"),
        ("rechnungspruefung", "Rechnungsprüfung"),
        ("sonderpruefung", "Sonderprüfung WEG oder Mietshaus"),
        ("ankauf", "Ankauf-Check"),
    ]
    STATUS = [
        ("angelegt", "angelegt"),
        ("unterlagen", "Unterlagen"),
        ("in_arbeit", "in Arbeit"),
        ("abgeschlossen", "abgeschlossen"),
    ]
    objekt = models.ForeignKey(Objekt, on_delete=models.CASCADE, related_name="auftraege")
    leistung = models.CharField(max_length=30, choices=LEISTUNGEN)
    jahr = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=STATUS, default="unterlagen")
    dauerauftrag = models.BooleanField(default=False)
    vertragsende = models.DateField(null=True, blank=True)
    abgeschlossen = models.DateTimeField(null=True, blank=True)
    angelegt = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Aufträge"
        ordering = ["-angelegt"]

    def __str__(self) -> str:
        return f"{self.get_leistung_display()} {self.jahr}"

    @property
    def loeschdatum(self):
        """90 Tage nach Abschluss, bei Daueraufträgen 90 Tage nach Vertragsende."""
        frist = timedelta(days=settings.LOESCHFRIST_TAGE)
        if self.dauerauftrag:
            return (self.vertragsende + frist) if self.vertragsende else None
        return (self.abgeschlossen.date() + frist) if self.abgeschlossen else None

    def fortschritt(self) -> tuple[int, int]:
        punkte = list(self.punkte.all())
        fertig = sum(1 for p in punkte if p.status in ("eingereicht", "geprueft"))
        return fertig, len(punkte)


class Checklistenpunkt(models.Model):
    KATEGORIEN = [
        ("bank", "Bank"),
        ("buchhaltung", "Buchhaltung"),
        ("abrechnungen", "Abrechnungen"),
        ("rechtliches", "Rechtliches"),
        ("stammdaten", "Stammdaten"),
        ("sonstiges", "Sonstiges"),
    ]
    STATUS = [("offen", "offen"), ("eingereicht", "eingereicht"), ("geprueft", "geprüft"), ("nachreichen", "nachreichen")]
    auftrag = models.ForeignKey(Auftrag, on_delete=models.CASCADE, related_name="punkte")
    text = models.CharField(max_length=300)
    kategorie = models.CharField(max_length=20, choices=KATEGORIEN, default="sonstiges")
    status = models.CharField(max_length=20, choices=STATUS, default="offen")
    kommentar = models.CharField(max_length=500, blank=True)
    reihenfolge = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["reihenfolge", "id"]

    def __str__(self) -> str:
        return self.text


class Dokument(models.Model):
    RICHTUNGEN = [("kunde", "vom Kunden"), ("ergebnis", "von Zahlenwerk")]
    SCAN = [("sauber", "sauber"), ("nicht_geprueft", "nicht geprüft")]
    auftrag = models.ForeignKey(Auftrag, on_delete=models.CASCADE, related_name="dokumente")
    punkt = models.ForeignKey(Checklistenpunkt, null=True, blank=True, on_delete=models.SET_NULL, related_name="dokumente")
    richtung = models.CharField(max_length=10, choices=RICHTUNGEN, default="kunde")
    originalname = models.CharField(max_length=255)
    dateityp = models.CharField(max_length=10)
    groesse = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    speicher_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    schluessel = models.BinaryField()  # Dateischlüssel, mit dem Hauptschlüssel verschlüsselt
    scan = models.CharField(max_length=20, choices=SCAN)
    zeitraum = models.CharField(max_length=100, blank=True)
    hinweis = models.CharField(max_length=1000, blank=True)
    hochgeladen_von = models.ForeignKey(Benutzer, null=True, on_delete=models.SET_NULL)
    hochgeladen = models.DateTimeField(auto_now_add=True)
    geprueft = models.DateTimeField(null=True, blank=True)
    gelesen = models.DateTimeField(null=True, blank=True)  # Ergebnis vom Kunden abgerufen
    geloescht = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-hochgeladen"]


class Protokoll(models.Model):
    """Was, wann, wer, ohne Inhalte."""

    zeit = models.DateTimeField(auto_now_add=True)
    benutzer = models.ForeignKey(Benutzer, null=True, blank=True, on_delete=models.SET_NULL)
    aktion = models.CharField(max_length=60)
    bezug = models.CharField(max_length=100, blank=True)
    details = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-zeit"]
        verbose_name_plural = "Protokoll"


def protokoll(aktion: str, benutzer=None, bezug: str = "", details: str = "") -> None:
    Protokoll.objects.create(
        aktion=aktion,
        benutzer=benutzer if getattr(benutzer, "pk", None) else None,
        bezug=bezug[:100],
        details=details[:500],
    )
