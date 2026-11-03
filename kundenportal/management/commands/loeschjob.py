from django.core.management.base import BaseCommand

from kundenportal.loeschung import loeschlauf


class Command(BaseCommand):
    help = "Löscht Unterlagen, deren Löschfrist (90 Tage nach Abschluss bzw. Vertragsende) erreicht ist."

    def handle(self, *args, **opts):
        n = loeschlauf()
        self.stdout.write(f"{n} Dokumente gelöscht.")
