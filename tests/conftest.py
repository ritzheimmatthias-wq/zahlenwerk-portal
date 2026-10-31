from pathlib import Path

import pytest

BEISPIEL = Path(__file__).parent / "beispiel"


@pytest.fixture
def beispiel() -> Path:
    return BEISPIEL
