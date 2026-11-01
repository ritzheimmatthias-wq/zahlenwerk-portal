from django.urls import path

from . import views

urlpatterns = [
    path("", views.start, name="start"),
    path("anmelden/", views.anmelden, name="anmelden"),
    path("anmelden/code/", views.anmelden_code, name="anmelden_code"),
    path("anmelden/code/neu/", views.anmelden_code_neu, name="anmelden_code_neu"),
    path("abmelden/", views.abmelden, name="abmelden"),
    path("einladung/<str:token>/", views.einladung, name="einladung"),
    path("objekt/<int:pk>/", views.objekt, name="objekt"),
    path("auftrag/<int:pk>/", views.auftrag, name="auftrag"),
    path("dokument/<int:pk>/", views.dokument, name="dokument"),
    path("download/<str:token>/", views.download, name="download"),
    path("intern/", views.intern, name="intern"),
    path("intern/anlegen/", views.intern_anlegen, name="intern_anlegen"),
    path("intern/auftrag/<int:pk>/", views.intern_auftrag, name="intern_auftrag"),
    path("intern/auftrag/<int:pk>/punkt/", views.intern_punkt, name="intern_punkt"),
    path("intern/auftrag/<int:pk>/ergebnis/", views.intern_ergebnis, name="intern_ergebnis"),
    path("intern/auftrag/<int:pk>/abschliessen/", views.intern_abschliessen, name="intern_abschliessen"),
    path("intern/auftrag/<int:pk>/loeschen/", views.intern_loeschen, name="intern_loeschen"),
    path("intern/punkt/<int:pk>/", views.intern_punkt_status, name="intern_punkt_status"),
    path("intern/dokument/<int:pk>/pruefen/", views.intern_pruefen, name="intern_pruefen"),
    path("intern/benutzer/<int:pk>/einladen/", views.intern_einladen, name="intern_einladen"),
    path("intern/benutzer/<int:pk>/sperren/", views.intern_sperren, name="intern_sperren"),
]
