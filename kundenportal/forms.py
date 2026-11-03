from __future__ import annotations

from django import forms
from django.contrib.auth.forms import SetPasswordForm

from .models import Auftrag, Checklistenpunkt, Konto, Kunde, Objekt
from .sicherheit import iban_gueltig, iban_normal


class AnmeldeForm(forms.Form):
    email = forms.EmailField(label="E-Mail-Adresse", widget=forms.EmailInput(attrs={"autocomplete": "username"}))
    passwort = forms.CharField(label="Passwort", widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))


class CodeForm(forms.Form):
    code = forms.RegexField(
        label="Bestätigungscode",
        regex=r"^\s*\d{6}\s*$",
        error_messages={"invalid": "Bitte die sechs Ziffern aus der E-Mail eingeben."},
        widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code", "maxlength": 6}),
    )


class PasswortForm(SetPasswordForm):
    pass


class UploadForm(forms.Form):
    punkt = forms.ModelChoiceField(label="Checklistenpunkt", queryset=Checklistenpunkt.objects.none(), empty_label=None)
    zeitraum = forms.CharField(label="Zeitraum", max_length=100, required=False)
    datei = forms.FileField(label="Datei")
    hinweis = forms.CharField(label="Hinweis zur Datei (optional)", max_length=1000, required=False, widget=forms.Textarea(attrs={"rows": 3}))
    berechtigt = forms.BooleanField(label="Ich bin berechtigt, diese Unterlagen weiterzugeben.")

    def __init__(self, *args, auftrag: Auftrag, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["punkt"].queryset = auftrag.punkte.exclude(status="geprueft")


def _iban_feld(pflicht: bool) -> forms.CharField:
    return forms.CharField(label="IBAN", max_length=42, required=pflicht)


class KontoForm(forms.Form):
    bezeichnung = forms.CharField(max_length=100, required=False)
    art = forms.ChoiceField(choices=Konto.ARTEN, required=False)
    iban = _iban_feld(False)

    def clean_iban(self):
        v = self.cleaned_data.get("iban", "")
        if v and not iban_gueltig(v):
            raise forms.ValidationError("IBAN ungültig (Prüfziffer).")
        return iban_normal(v)


class EinheitForm(forms.Form):
    nummer = forms.CharField(max_length=20, required=False)
    eigentuemer = forms.CharField(max_length=200, required=False)
    mea = forms.DecimalField(max_digits=10, decimal_places=4, required=False, localize=True)
    hausgeld = forms.DecimalField(max_digits=10, decimal_places=2, required=False, localize=True)
    ab = forms.DateField(required=False, input_formats=["%d.%m.%Y", "%Y-%m-%d"])
    iban = _iban_feld(False)

    def clean_iban(self):
        v = self.cleaned_data.get("iban", "")
        if v and not iban_gueltig(v):
            raise forms.ValidationError("IBAN ungültig (Prüfziffer).")
        return iban_normal(v)


KontoFormSet = forms.formset_factory(KontoForm, extra=0)
EinheitFormSet = forms.formset_factory(EinheitForm, extra=0)


class AnlegenForm(forms.Form):
    bestehender_kunde = forms.ModelChoiceField(
        label="Bestehender Kunde", queryset=Kunde.objects.all(), required=False, empty_label="neuer Kunde"
    )
    kunde_art = forms.ChoiceField(label="Art des Kunden", choices=Kunde.ARTEN, required=False)
    kunde_name = forms.CharField(label="Name des Kunden", max_length=200, required=False)
    ansprechpartner = forms.CharField(label="Ansprechpartner", max_length=200, required=False)
    email = forms.EmailField(label="E-Mail für Einladung und Anmeldecode", required=False)

    bezeichnung = forms.CharField(label="Bezeichnung", max_length=200)
    anschrift = forms.CharField(label="Anschrift", max_length=200)
    ort = forms.CharField(label="PLZ und Ort", max_length=120, required=False)
    objektart = forms.ChoiceField(label="Objektart", choices=Objekt.ARTEN)
    baujahr = forms.CharField(label="Baujahr", max_length=10, required=False)
    wirtschaftsjahr = forms.CharField(label="Wirtschaftsjahr", max_length=40, initial="Kalenderjahr")
    heizung = forms.CharField(label="Heizung", max_length=60, required=False)

    leistung = forms.ChoiceField(label="Leistung", choices=Auftrag.LEISTUNGEN)
    jahr = forms.IntegerField(label="Jahr", min_value=2000, max_value=2100)
    einladen = forms.BooleanField(label="Einladung gleich senden", required=False, initial=True)

    def clean(self):
        d = super().clean()
        if not d.get("bestehender_kunde"):
            if not d.get("kunde_name"):
                self.add_error("kunde_name", "Name des Kunden fehlt.")
            if not d.get("email"):
                self.add_error("email", "E-Mail-Adresse fehlt.")
        return d


class PunktForm(forms.Form):
    text = forms.CharField(label="Neuer Checklistenpunkt", max_length=300)
    kategorie = forms.ChoiceField(label="Kategorie", choices=Checklistenpunkt.KATEGORIEN)


class ErgebnisForm(forms.Form):
    datei = forms.FileField(label="Dokument für den Kunden")
