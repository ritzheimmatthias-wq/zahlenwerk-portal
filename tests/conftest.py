from pathlib import Path

import pytest

BEISPIEL = Path(__file__).parent / "beispiel"


@pytest.fixture
def beispiel() -> Path:
    return BEISPIEL


@pytest.fixture(autouse=True)
def _manipulation(request):
    """Für Tests mit @pytest.mark.manipulation: simuliert jemanden, der die Datenbank-Trigger umgeht
    (z. B. Datenbankverwalter). Nur unter PostgreSQL nötig; die Änderung gilt nur in der Test-Transaktion."""
    if request.node.get_closest_marker("manipulation") is None:
        yield
        return
    request.getfixturevalue("db")
    from django.db import connection

    if connection.vendor == "postgresql":
        with connection.cursor() as c:
            for tabelle in ("abrechnungslauf", "report", "statuswechsel"):
                c.execute(f"ALTER TABLE kundenportal_{tabelle} DISABLE TRIGGER USER")
    yield
