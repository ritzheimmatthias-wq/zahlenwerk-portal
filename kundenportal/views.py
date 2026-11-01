from __future__ import annotations

import hashlib
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import dokumentvorlagen, mails, tresor, virenscan
from .dateipruefung import Abgelehnt, ERLAUBT_TEXT, pruefe_datei, sicherer_name
from .forms import (
    AnlegenForm, AnmeldeForm, CodeForm, EinheitFormSet, ErgebnisForm, KontoFormSet, PasswortForm, PunktForm, UploadForm,
)
from .models import (
    Auftrag, Benutzer, Checklistenpunkt, Dokument, Einheit, Freigabe, Konto, Kunde, Objekt, Protokoll, protokoll,
)
from .sicherheit import (
    download_token, finde_einladung, lies_download_token, neue_einladung, neuer_code, pruefe_code,
)
from .vorlagen import VORLAGEN

VORLAEUFIG = "vorlaeufig_benutzer"


def fehler_403(request, exception=None):
    return render(request, "kundenportal/fehler.html", {"titel": "Kein Zugriff"}, status=403)


def fehler_404(request, exception=None):
    return render(request, "kundenportal/fehler.html", {"titel": "Nicht gefunden"}, status=404)


# --- Anmeldung ------------------------------------------------------------------

def anmelden(request):
    if request.user.is_authenticated:
        return redirect("intern" if request.user.is_staff else "start")
    form = AnmeldeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].lower()
        b = Benutzer.objects.filter(email=email).first()
        if b and b.vorlaeufig_gesperrt:
            form.add_error(None, "Zu viele Fehlversuche. Bitte versuchen Sie es in 15 Minuten erneut.")
        else:
            ok = authenticate(request, email=email, password=form.cleaned_data["passwort"])
            if ok is None:
                if b:
                    b.fehlversuche += 1
                    if b.fehlversuche >= settings.ANMELDUNG_MAX_FEHLVERSUCHE:
                        b.gesperrt_bis = timezone.now() + timedelta(minutes=settings.ANMELDUNG_SPERRE_MINUTEN)
                        b.fehlversuche = 0
                        protokoll("anmeldung_gesperrt", b)
                    b.save(update_fields=["fehlversuche", "gesperrt_bis"])
                form.add_error(None, "E-Mail-Adresse oder Passwort stimmen nicht.")
            else:
                request.session.cycle_key()
                request.session[VORLAEUFIG] = ok.pk
                mails.code_senden(ok, neuer_code(ok))
                return redirect("anmelden_code")
    return render(request, "kundenportal/anmelden.html", {"form": form})


def anmelden_code(request):
    pk = request.session.get(VORLAEUFIG)
    b = Benutzer.objects.filter(pk=pk, is_active=True).first() if pk else None
    if b is None:
        return redirect("anmelden")
    form = CodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if pruefe_code(b, form.cleaned_data["code"]):
            b.fehlversuche = 0
            b.save(update_fields=["fehlversuche"])
            del request.session[VORLAEUFIG]
            login(request, b, backend="django.contrib.auth.backends.ModelBackend")
            protokoll("anmeldung", b)
            return redirect("intern" if b.is_staff else "start")
        form.add_error("code", "Der Code ist falsch oder abgelaufen. Fordern Sie bei Bedarf einen neuen Code an.")
    return render(request, "kundenportal/code.html", {"form": form})


@require_POST
def anmelden_code_neu(request):
    pk = request.session.get(VORLAEUFIG)
    b = Benutzer.objects.filter(pk=pk, is_active=True).first() if pk else None
    if b is None:
        return redirect("anmelden")
    letzter = b.codes.order_by("-angelegt").first()
    if letzter and letzter.angelegt > timezone.now() - timedelta(seconds=60):
        messages.info(request, "Bitte warten Sie eine Minute, bevor Sie einen neuen Code anfordern.")
    else:
        mails.code_senden(b, neuer_code(b))
        messages.info(request, "Wir haben Ihnen einen neuen Code geschickt.")
    return redirect("anmelden_code")


@require_POST
def abmelden(request):
    logout(request)
    return redirect("anmelden")


def einladung(request, token: str):
    e = finde_einladung(token)
    if e is None:
        return render(request, "kundenportal/fehler.html", {
            "titel": "Einladung ungültig",
            "text": "Dieser Link ist abgelaufen oder wurde bereits verwendet. Bitte fordern Sie bei Zahlenwerk einen neuen Link an.",
        }, status=410)
    form = PasswortForm(e.benutzer, request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            form.save()
            e.eingeloest = timezone.now()
            e.save(update_fields=["eingeloest"])
            protokoll("einladung_eingeloest", e.benutzer)
        messages.success(request, "Ihr Passwort ist gespeichert. Bitte melden Sie sich jetzt an.")
        return redirect("anmelden")
    return render(request, "kundenportal/einladung.html", {"form": form, "email": e.benutzer.email})


# --- Rechte -----------------------------------------------------------------------

def _objekte(user):
    if user.is_staff:
        return Objekt.objects.all()
    return Objekt.objects.filter(freigaben__benutzer=user, kunde=user.kunde)


def _auftrag(user, pk: int) -> Auftrag:
    a = get_object_or_404(Auftrag.objects.select_related("objekt"), pk=pk)
    if not _objekte(user).filter(pk=a.objekt_id).exists():
        raise Http404
    return a


def _sichtbare_dokumente(user, auftrag: Auftrag):
    return auftrag.dokumente.filter(geloescht__isnull=True)


# --- Ablage -----------------------------------------------------------------------

def _ablegen(datei, *, auftrag, punkt, richtung, benutzer, zeitraum="", hinweis="") -> Dokument:
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
        zeitraum=zeitraum, hinweis=hinweis, hochgeladen_von=benutzer,
    )
    dok.schluessel = tresor.ablegen(dok.speicher_id, daten)
    dok.save()
    protokoll("upload", benutzer, f"dokument:{dok.pk}", f"auftrag:{auftrag.pk} typ:{dok.dateityp} groesse:{dok.groesse}")
    return dok


# --- Kundensicht ------------------------------------------------------------------

def start(request):
    if request.user.is_staff:
        return redirect("intern")
    kacheln = []
    for o in _objekte(request.user).prefetch_related("auftraege__punkte"):
        auftraege = list(o.auftraege.all())
        kacheln.append({
            "objekt": o,
            "auftraege": [(a, *a.fortschritt()) for a in auftraege],
            "nachreichen": sum(1 for a in auftraege for p in a.punkte.all() if p.status == "nachreichen"),
            "neu": Dokument.objects.filter(auftrag__objekt=o, richtung="ergebnis", gelesen__isnull=True, geloescht__isnull=True).count(),
        })
    return render(request, "kundenportal/start.html", {"kacheln": kacheln})


def objekt(request, pk: int):
    o = get_object_or_404(_objekte(request.user), pk=pk)
    return render(request, "kundenportal/objekt.html", {"o": o, "auftraege": [(a, *a.fortschritt()) for a in o.auftraege.all()]})


def auftrag(request, pk: int):
    a = _auftrag(request.user, pk)
    offen = a.status != "abgeschlossen"
    form = UploadForm(request.POST or None, request.FILES or None, auftrag=a)
    if request.method == "POST":
        if not offen:
            raise Http404
        if form.is_valid():
            punkt = form.cleaned_data["punkt"]
            try:
                _ablegen(form.cleaned_data["datei"], auftrag=a, punkt=punkt, richtung="kunde", benutzer=request.user,
                         zeitraum=form.cleaned_data["zeitraum"], hinweis=form.cleaned_data["hinweis"])
            except Abgelehnt as e:
                form.add_error("datei", str(e))
            else:
                punkt.status = "eingereicht"
                punkt.kommentar = ""
                punkt.save(update_fields=["status", "kommentar"])
                mails.intern_neue_unterlagen(a)
                messages.success(request, "Die Datei ist eingegangen und verschlüsselt gespeichert. Zahlenwerk prüft die Unterlage.")
                return redirect("auftrag", pk=a.pk)
    fertig, gesamt = a.fortschritt()
    dok = _sichtbare_dokumente(request.user, a)
    return render(request, "kundenportal/auftrag.html", {
        "a": a, "form": form, "offen": offen, "fertig": fertig, "gesamt": gesamt, "erlaubt": ERLAUBT_TEXT,
        "ergebnisse": dok.filter(richtung="ergebnis"), "eigene": dok.filter(richtung="kunde"),
    })


def dokument(request, pk: int):
    """Prüft das Recht und leitet auf einen 5 Minuten gültigen Link um."""
    d = get_object_or_404(Dokument, pk=pk, geloescht__isnull=True)
    _auftrag(request.user, d.auftrag_id)
    return redirect("download", token=download_token(request.user.pk, d.pk))


def download(request, token: str):
    daten = lies_download_token(token)
    if daten is None:
        raise Http404
    benutzer_id, dok_id = daten
    if not request.user.is_authenticated or request.user.pk != benutzer_id:
        raise Http404
    d = get_object_or_404(Dokument, pk=dok_id, geloescht__isnull=True)
    _auftrag(request.user, d.auftrag_id)
    inhalt = tresor.lesen(d.speicher_id, d.schluessel)
    if d.richtung == "ergebnis" and not request.user.is_staff and d.gelesen is None:
        d.gelesen = timezone.now()
        d.save(update_fields=["gelesen"])
    protokoll("download", request.user, f"dokument:{d.pk}")
    antwort = HttpResponse(inhalt, content_type="application/octet-stream")
    antwort["Content-Disposition"] = f"attachment; filename*=UTF-8''{_url_name(d.originalname)}"
    return antwort


def _url_name(name: str) -> str:
    from urllib.parse import quote

    return quote(name, safe="")


# --- Dokumentvorlagen ------------------------------------------------------------

def _vorlagen_sichtbar(user) -> bool:
    return user.is_staff or settings.VORLAGEN_FUER_KUNDEN


def vorlagen(request):
    if not _vorlagen_sichtbar(request.user):
        raise Http404
    return render(request, "kundenportal/vorlagen.html", {
        "vorlagen": dokumentvorlagen.alle(), "stand": "01.11.2026",
        "vorschau": not settings.VORLAGEN_FUER_KUNDEN,
    })


def vorlage_datei(request, vid: str, art: str):
    """Blankovorlage ausliefern: nur nach Anmeldung, nur geprüfter Dateistand, ohne Personendaten."""
    if not _vorlagen_sichtbar(request.user):
        raise Http404
    v = dokumentvorlagen.finde(vid)
    if v is None:
        raise Http404
    try:
        if art == "markdown":
            inhalt, name, typ = dokumentvorlagen.markdown(v), v.markdown, "text/markdown; charset=utf-8"
        else:
            ergebnis = dokumentvorlagen.fassung(v, art)
            if ergebnis is None:
                raise Http404
            (inhalt, name), typ = ergebnis, "application/pdf"
    except dokumentvorlagen.Pruefsummenfehler:
        protokoll("vorlage_pruefsumme_falsch", request.user, f"vorlage:{v.id}")
        raise Http404 from None
    protokoll("vorlage_download", request.user, f"vorlage:{v.id}", art)
    antwort = HttpResponse(inhalt, content_type=typ)
    antwort["Content-Disposition"] = f"attachment; filename*=UTF-8''{_url_name(name)}"
    return antwort


# --- Interner Bereich -------------------------------------------------------------

def _weiter(request):
    ziel = request.POST.get("weiter", "")
    if ziel and url_has_allowed_host_and_scheme(ziel, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(ziel)
    return redirect("intern")


def intern(request):
    zu_pruefen = (
        Dokument.objects.filter(richtung="kunde", geloescht__isnull=True, geprueft__isnull=True)
        .select_related("auftrag__objekt", "punkt").order_by("hochgeladen")
    )
    auftraege = Auftrag.objects.select_related("objekt__kunde").prefetch_related("punkte").order_by("status", "-angelegt")
    heute = timezone.now().date()
    bald = [a for a in auftraege if a.loeschdatum and a.loeschdatum <= heute + timedelta(days=14)]
    return render(request, "kundenportal/intern/eingang.html", {
        "zu_pruefen": zu_pruefen,
        "auftraege": [(a, *a.fortschritt()) for a in auftraege],
        "warten": Checklistenpunkt.objects.filter(status="nachreichen").count(),
        "laufend": sum(1 for a in auftraege if a.status != "abgeschlossen"),
        "bald": len(bald),
        "protokoll": Protokoll.objects.select_related("benutzer")[:15],
    })


def intern_anlegen(request):
    try:
        zeilen = max(1, min(int(request.GET.get("einheiten", 8)), 200))
    except ValueError:
        zeilen = 8
    form = AnlegenForm(request.POST or None, initial={"jahr": timezone.now().year - 1})
    konten = KontoFormSet(request.POST or None, prefix="konto", initial=[
        {"bezeichnung": "Girokonto", "art": "giro"}, {"bezeichnung": "Rücklage", "art": "ruecklage"}, {}])
    einheiten = EinheitFormSet(request.POST or None, prefix="einheit", initial=[{"nummer": f"WE {i}"} for i in range(1, zeilen + 1)])
    if request.method == "POST" and form.is_valid() and konten.is_valid() and einheiten.is_valid():
        d = form.cleaned_data
        vorhanden = Benutzer.objects.filter(email=(d.get("email") or "").lower()).first()
        if vorhanden and (vorhanden.is_staff or vorhanden.kunde_id != getattr(d["bestehender_kunde"], "pk", None)):
            form.add_error("email", "Diese Adresse gehört bereits zu einem anderen Kunden oder zu Zahlenwerk.")
            return _anlegen_seite(request, form, konten, einheiten, zeilen)
        with transaction.atomic():
            kunde = d["bestehender_kunde"] or Kunde.objects.create(
                name=d["kunde_name"], art=d["kunde_art"] or "weg", ansprechpartner=d["ansprechpartner"])
            o = Objekt.objects.create(
                kunde=kunde, bezeichnung=d["bezeichnung"], anschrift=d["anschrift"], ort=d["ort"],
                objektart=d["objektart"], baujahr=d["baujahr"], wirtschaftsjahr=d["wirtschaftsjahr"], heizung=d["heizung"])
            for k in konten.cleaned_data:
                if k.get("iban"):
                    Konto.objects.create(objekt=o, bezeichnung=k.get("bezeichnung") or "Konto", art=k.get("art") or "giro", iban=k["iban"])
            for e in einheiten.cleaned_data:
                if e.get("nummer") and (e.get("eigentuemer") or e.get("hausgeld") or e.get("mea")):
                    Einheit.objects.create(objekt=o, nummer=e["nummer"], eigentuemer=e.get("eigentuemer", ""),
                                           mea=e.get("mea"), hausgeld=e.get("hausgeld"), ab=e.get("ab"), iban=e.get("iban", ""))
            a = Auftrag.objects.create(objekt=o, leistung=d["leistung"], jahr=d["jahr"])
            for i, (text, kat) in enumerate(VORLAGEN[d["leistung"]]):
                Checklistenpunkt.objects.create(auftrag=a, text=text, kategorie=kat, reihenfolge=i)
            neue = []
            if d.get("email"):
                b, neu = Benutzer.objects.get_or_create(email=d["email"].lower(), defaults={"kunde": kunde})
                if neu:
                    b.set_unusable_password()
                    b.save()
                    neue.append(b)
            empfaenger = list(kunde.benutzer.filter(is_active=True))
            for b in empfaenger:
                Freigabe.objects.get_or_create(benutzer=b, objekt=o)
            protokoll("objekt_angelegt", request.user, f"objekt:{o.pk}", f"auftrag:{a.pk}")
        if d.get("einladen"):
            for b in neue:
                mails.einladung_senden(b, neue_einladung(b))
                protokoll("einladung_gesendet", request.user, f"benutzer:{b.pk}")
        messages.success(request, f"{o.bezeichnung} ist angelegt." + (" Die Einladung ist versendet." if d.get("einladen") and neue else ""))
        return redirect("intern_auftrag", pk=a.pk)
    return _anlegen_seite(request, form, konten, einheiten, zeilen)


def _anlegen_seite(request, form, konten, einheiten, zeilen):
    return render(request, "kundenportal/intern/anlegen.html", {
        "form": form, "konten": konten, "einheiten": einheiten, "mehr": zeilen + 8, "vorlagen": {
            k: (dict(Auftrag.LEISTUNGEN)[k], [t for t, _ in v]) for k, v in VORLAGEN.items()},
    })


def intern_auftrag(request, pk: int):
    a = get_object_or_404(Auftrag.objects.select_related("objekt__kunde"), pk=pk)
    return render(request, "kundenportal/intern/auftrag.html", {
        "a": a, "punktform": PunktForm(), "ergebnisform": ErgebnisForm(),
        "dokumente": a.dokumente.filter(geloescht__isnull=True).select_related("punkt", "hochgeladen_von"),
        "benutzer": Benutzer.objects.filter(kunde=a.objekt.kunde),
        "fertig_gesamt": a.fortschritt(),
    })


def _kunden_mails(a: Auftrag) -> list[str]:
    return list(Benutzer.objects.filter(freigaben__objekt=a.objekt, is_active=True).values_list("email", flat=True))


@require_POST
def intern_pruefen(request, pk: int):
    d = get_object_or_404(Dokument, pk=pk, richtung="kunde", geloescht__isnull=True)
    aktion = request.POST.get("aktion")
    d.geprueft = timezone.now()
    d.save(update_fields=["geprueft"])
    if d.punkt:
        if aktion == "geprueft":
            d.punkt.status, d.punkt.kommentar = "geprueft", ""
        elif aktion == "nachreichen":
            d.punkt.status = "nachreichen"
            d.punkt.kommentar = (request.POST.get("kommentar") or "Bitte die Unterlage vollständig nachreichen.")[:500]
        d.punkt.save(update_fields=["status", "kommentar"])
    if aktion == "nachreichen":
        mails.kunde_hinweis(d.auftrag, _kunden_mails(d.auftrag), "nachreichen")
    protokoll(f"pruefung_{aktion}", request.user, f"dokument:{d.pk}")
    if request.POST.get("zurueck") == "auftrag":
        return redirect("intern_auftrag", pk=d.auftrag_id)
    return redirect("intern")


@require_POST
def intern_punkt(request, pk: int):
    a = get_object_or_404(Auftrag, pk=pk)
    form = PunktForm(request.POST)
    if form.is_valid():
        n = a.punkte.count()
        Checklistenpunkt.objects.create(auftrag=a, reihenfolge=n, **form.cleaned_data)
    return redirect("intern_auftrag", pk=a.pk)


@require_POST
def intern_punkt_status(request, pk: int):
    p = get_object_or_404(Checklistenpunkt, pk=pk)
    status = request.POST.get("status")
    if status in dict(Checklistenpunkt.STATUS):
        p.status = status
        p.kommentar = (request.POST.get("kommentar") or "")[:500] if status == "nachreichen" else ""
        p.save(update_fields=["status", "kommentar"])
        if status == "nachreichen":
            mails.kunde_hinweis(p.auftrag, _kunden_mails(p.auftrag), "nachreichen")
    return redirect("intern_auftrag", pk=p.auftrag_id)


@require_POST
def intern_ergebnis(request, pk: int):
    a = get_object_or_404(Auftrag, pk=pk)
    form = ErgebnisForm(request.POST, request.FILES)
    if form.is_valid():
        try:
            _ablegen(form.cleaned_data["datei"], auftrag=a, punkt=None, richtung="ergebnis", benutzer=request.user)
        except Abgelehnt as e:
            messages.error(request, str(e))
        else:
            mails.kunde_hinweis(a, _kunden_mails(a), "ergebnis")
            messages.success(request, "Dokument bereitgestellt. Der Kunde bekommt einen Hinweis per E-Mail.")
    return redirect("intern_auftrag", pk=a.pk)


@require_POST
def intern_abschliessen(request, pk: int):
    a = get_object_or_404(Auftrag, pk=pk)
    a.status, a.abgeschlossen = "abgeschlossen", timezone.now()
    a.save(update_fields=["status", "abgeschlossen"])
    protokoll("auftrag_abgeschlossen", request.user, f"auftrag:{a.pk}", f"loeschdatum:{a.loeschdatum}")
    messages.success(request, f"Auftrag abgeschlossen. Die Unterlagen werden am {a.loeschdatum:%d.%m.%Y} gelöscht.")
    return redirect("intern_auftrag", pk=a.pk)


@require_POST
def intern_einladen(request, pk: int):
    b = get_object_or_404(Benutzer, pk=pk, is_staff=False)
    mails.einladung_senden(b, neue_einladung(b))
    protokoll("einladung_gesendet", request.user, f"benutzer:{b.pk}")
    messages.success(request, f"Neue Einladung an {b.email} gesendet.")
    return _weiter(request)


@require_POST
def intern_sperren(request, pk: int):
    b = get_object_or_404(Benutzer, pk=pk, is_staff=False)
    b.is_active = not b.is_active
    b.save(update_fields=["is_active"])
    protokoll("zugang_" + ("entsperrt" if b.is_active else "gesperrt"), request.user, f"benutzer:{b.pk}")
    messages.success(request, f"Zugang {b.email} {'freigegeben' if b.is_active else 'gesperrt'}.")
    return _weiter(request)


@require_POST
def intern_loeschen(request, pk: int):
    """Sofortige Löschung aller Unterlagen eines Auftrags (nach Rückfrage im Formular)."""
    a = get_object_or_404(Auftrag, pk=pk)
    if request.POST.get("bestaetigt") != "ja":
        messages.error(request, "Bitte die Löschung ausdrücklich bestätigen.")
        return redirect("intern_auftrag", pk=a.pk)
    from .loeschung import loesche_dokumente

    n = loesche_dokumente(a.dokumente.filter(geloescht__isnull=True), request.user, "manuell")
    messages.success(request, f"{n} Dokumente gelöscht.")
    return redirect("intern_auftrag", pk=a.pk)
