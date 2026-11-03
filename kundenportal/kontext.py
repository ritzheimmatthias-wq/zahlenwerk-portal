from django.conf import settings


def portal(request):
    return {"vorlagen_fuer_kunden": settings.VORLAGEN_FUER_KUNDEN}
