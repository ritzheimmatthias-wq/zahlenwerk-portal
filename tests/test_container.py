"""Das Image muss alle Pakete enthalten, die pyproject.toml installieren will (Befund B2)."""
import re
import tomllib
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent


def test_dockerfile_kopiert_alle_pakete_vor_der_installation():
    pakete = tomllib.loads((WURZEL / "pyproject.toml").read_text())["tool"]["setuptools"]["packages"]
    zeilen = (WURZEL / "Dockerfile").read_text().splitlines()
    installation = next(i for i, z in enumerate(zeilen) if "pip install" in z)
    kopiert = {m.group(1) for z in zeilen[:installation] if (m := re.match(r"COPY\s+(\S+)\s+\./\1\s*$", z))}
    fehlend = sorted(set(pakete) - kopiert)
    assert not fehlend, f"Dockerfile kopiert diese Pakete nicht vor pip install: {fehlend}"


def test_ci_baut_das_image_und_fuehrt_den_smoke_test_aus():
    ci = (WURZEL / ".github" / "workflows" / "pruefung.yml").read_text()
    assert "docker build" in ci and "container_smoke.py" in ci and "--network none" in ci
    assert "docker push" not in ci and "docker login" not in ci
