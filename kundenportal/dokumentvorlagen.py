"""Bibliothek der eigenen Zahlenwerk-Dokumentvorlagen (Wohnraumvermietung).

Die PDFs und Markdown-Fassungen stammen unverändert aus dem Obsidian-Vault
(`Projekte/Server und Kundenportal/Vorlagen/2026-11-01/`). Jede Auslieferung
prüft die SHA-256-Prüfsumme gegen `manifest.json`; weicht sie ab, wird nichts
ausgeliefert. Die Texte unten sind kurze Auszüge aus den Markdown-Fassungen,
ohne neue rechtliche Aussagen.

Blankovorlagen enthalten keine personenbezogenen Werte. Ausgefüllte Dokumente
werden hier weder erzeugt noch gespeichert.
"""
from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

STAND = "2026-11-01"
VERZEICHNIS = Path(__file__).resolve().parent / "dokumentvorlagen_dateien" / STAND


class Pruefsummenfehler(Exception):
    """Die Datei entspricht nicht dem freigegebenen Stand."""


@dataclass(frozen=True)
class Fassung:
    """Eine herunterladbare Fassung: das Original oder ein Seitenauszug daraus."""

    schluessel: str
    titel: str
    beschreibung: str
    seiten: tuple[int, ...] | None = None  # None = Originaldatei unverändert; sonst 1-basierte Seiten in Reihenfolge


@dataclass(frozen=True)
class Vorlage:
    id: str
    titel: str
    datei: str
    seiten: int
    sha256: str
    felder: tuple[dict, ...]
    zweck: str
    einsatz: str
    hinweise: tuple[str, ...]
    weitergeben: str
    fassungen: tuple[Fassung, ...] = field(default_factory=tuple)
    phasen: tuple[tuple[str, str], ...] = ()

    @property
    def markdown(self) -> str:
        return self.datei.removesuffix(".pdf") + ".md"


# Kurztexte je Vorlage, wörtlich oder sinngleich aus den Markdown-Fassungen.
_TEXTE: dict[str, dict] = {
    "01": {
        "zweck": "Mitteilung an die Mietpartei, mit der die vereinbarten monatlichen Betriebskostenvorauszahlungen nach der Betriebskostenabrechnung gemäß § 560 Absatz 4 BGB angepasst werden.",
        "einsatz": "Nur für vereinbarte Vorauszahlungen nach erfolgter formell und inhaltlich zutreffender Abrechnung. Für eine Pauschale gilt ein anderer Ablauf.",
        "hinweise": (
            "Eine Senkung ist ebenso möglich wie eine Erhöhung. Bei streitigen Abrechnungsposten zunächst bereinigen.",
            "Nicht schlicht einen Nachzahlungsbetrag durch zwölf teilen: Darin können ausgebliebene Zahlungen enthalten sein. Keine pauschalen Sicherheitszuschläge.",
            "§ 560 Absatz 4 BGB enthält keine starre Übernächster-Monat-Frist. Zugang und einen erst künftig fälligen Zahlungstermin dokumentieren; keine rückwirkende Anpassung.",
        ),
    },
    "02": {
        "zweck": "Bestätigung des tatsächlichen Einzugs nach § 19 Bundesmeldegesetz zur Vorlage bei der Meldebehörde.",
        "einsatz": "Wenn meldepflichtige Personen eingezogen sind. Bestätigt wird der tatsächliche Einzug, nicht der Vertragsbeginn.",
        "hinweise": (
            "Die Bestätigung ist innerhalb der Meldefrist von zwei Wochen nach dem Einzug zu erteilen.",
            "Bei abweichendem Eigentümer verlangt § 19 Absatz 3 BMG dessen Namen. Keine Ausweisnummern oder Geburtsdaten ergänzen.",
            "Eine gewöhnlich per Portal übermittelte PDF ist nicht automatisch das elektronische Behördenverfahren nach § 19 Absatz 4 BMG.",
        ),
    },
    "03": {
        "zweck": "Vorläufige Zustandsaufnahme vor der endgültigen Rückgabe einer Mietwohnung.",
        "einsatz": "Bei einer Begehung vor dem Rückgabetermin. Das Protokoll allein bestätigt weder die Rückgabe der Wohnung noch einen Vertragsabschluss über Arbeiten oder Kosten.",
        "hinweise": (
            "Nur beobachtbaren Zustand beschreiben. Ursachen, Verantwortlichkeit und geschuldete Arbeiten nicht ungeprüft festlegen.",
            "Keine automatische Renovierungs-, Endrenovierungs- oder Rückbaupflicht ankreuzen.",
            "Eine Begehung verschiebt keine Verjährungsfrist. Werden Besitz und Schlüssel bereits zurückgegeben, den Zeitpunkt gesondert dokumentieren und § 548 BGB prüfen.",
        ),
    },
    "04": {
        "zweck": "Bestätigung, dass eine Kündigung der Mietpartei eingegangen ist.",
        "einsatz": "Nach Eingang einer Mieterkündigung. Genau eine Variante auswählen: nur Eingang bestätigt oder geprüfter Beendigungszeitpunkt.",
        "hinweise": (
            "Ein Enddatum nur nach Prüfung der Kündigung, des Zugangs, sämtlicher Mietparteien, der Form und einschlägiger Vertragsbindungen bestätigen.",
            "Für eine gewöhnliche Wohnraumkündigung genügt eine einfache E-Mail oder Portalnachricht der Mieterseite nicht; die gesetzliche Schriftform ist zu beachten.",
            "Rückgabe und rechtliche Vertragsbeendigung können zeitlich auseinanderfallen.",
        ),
    },
    "05": {
        "zweck": "Erklärung zur Änderung der Nettokaltmiete auf Grundlage einer vereinbarten Indexmiete nach § 557b BGB.",
        "einsatz": "Nur bei wirksamer schriftlicher Indexvereinbarung und mindestens einem Jahr unveränderter Miete. Dieses Muster führt keine Indexmiete neu ein.",
        "hinweise": (
            "Beide Monatswerte aus derselben veröffentlichten VPI-Reihe mit demselben Basisjahr verwenden.",
            "Mit voller Rechengenauigkeit rechnen und erst den Euro-Endbetrag auf Cent runden. Betriebskostenvorauszahlungen nicht mitindexieren.",
            "Die geänderte Miete ist mit Beginn des übernächsten Monats nach Zugang der Erklärung zu zahlen.",
        ),
    },
    "06": {
        "zweck": "Erinnerung an die im Mietvertrag bereits schriftlich vereinbarte nächste Mietstaffel.",
        "einsatz": "Wenn eine Staffel wirksam vereinbart ist. Das Schreiben setzt keine neue Staffel fest und ändert die Vereinbarung nicht.",
        "hinweise": (
            "Der jeweilige Mietbetrag oder die Erhöhung muss als Geldbetrag feststehen; jede Staffel muss mindestens ein Jahr unverändert bleiben.",
            "In Gebieten mit Mietpreisbremse ist jede einzelne Staffel nach § 557a Absatz 4 BGB zu prüfen.",
            "Bei fehlerhafter oder unklarer Vereinbarung dieses Erinnerungsschreiben nicht als Reparatur verwenden.",
        ),
    },
    "07": {
        "zweck": "Freiwillige, auf einen genannten Zeitraum und Prüfstand beschränkte Bescheinigung über Mietzahlungen auf Wunsch der Mietpartei.",
        "einsatz": "Nur nach Abgleich mit Buchhaltung und Streitstand ausstellen. Nach Prüfung genau eine Aussage auswählen.",
        "hinweise": (
            "Die Aussage betrifft ausschließlich das geprüfte Mietkonto und den ausgewiesenen Zeitraum. Sie bestätigt keine durchgehend pünktlichen Zahlungen.",
            "Diese begrenzte Zahlungsauskunft ersetzt das weitreichende Versprechen einer umfassenden Mietschuldenfreiheit.",
            "Eine Rückstandsbescheinigung nicht an Dritte senden; die Mietpartei entscheidet über ihre Vorlage.",
        ),
    },
    "08": {
        "zweck": "Zustandsaufnahme bei Einzug oder Rückgabe einer Mietwohnung mit Räumen, Zählern und Schlüsseln.",
        "einsatz": "Bei der Übergabe an die Mietpartei oder bei der Rückgabe. Zutreffend Einzug oder Auszug und Rückgabe auswählen.",
        "hinweise": (
            "Zustand, Ort und Umfang beschreiben und Foto-ID zuordnen. Nicht geprüft bedeutet keine Feststellung. Normale Abnutzung ist nicht automatisch ein Schaden.",
            "Keine globale Aussage wie mangelfrei oder alle Ansprüche erledigt verwenden, wenn Bereiche nicht geprüft oder Positionen offen sind.",
            "§ 548 BGB: sechsmonatige Verjährung bestimmter Ersatzansprüche ab tatsächlichem Rückerhalt. Schlüssel- und Besitzlage dokumentieren.",
        ),
    },
    "09": {
        "zweck": "Sparsame Selbstauskunft für Mietinteressenten, getrennt nach dem Stand des Vermietungsverfahrens.",
        "einsatz": "Je künftiger Vertragspartei ein eigenes Formular. Phasen B und C erst beim dort genannten Anlass freigeben.",
        "hinweise": (
            "Die Datenschutzinformationen vor Erhebung vom verantwortlichen Vermieter vor Ausgabe vollständig ausfüllen. Kein Blankohinweis darf an Bewerber ausgegeben werden.",
            "Das Gesamtdokument darf nicht dazu verleiten, schon zur Besichtigung alles auszufüllen. Nachweise erst bei Auswahl.",
            "Datenschutzinformationen sind keine Einwilligung und benötigen keine Zwangsunterschrift.",
        ),
        "phasen": (
            ("Phase A · Besichtigung", "Nur zur Organisation einer Besichtigung. Bitte noch keine Gehalts-, Bonitäts- oder Ausweiskopien."),
            ("Phase B · Konkretes Anmietungsinteresse", "Erst ausfüllen, nachdem Interesse an der Anmietung dieser konkreten Wohnung erklärt wurde. Die Datenschutzinformationen müssen bereits vorliegen."),
            ("Phase C · Ausgewählte Vertragspartei", "Erst nach dokumentierter Auswahl und unmittelbar vor Vertragsschluss. Nur erforderliche Nachweise anfordern."),
        ),
    },
}


def _seitenliste(seiten: tuple[int, ...]) -> str:
    if len(seiten) == 1:
        return f"Seite {seiten[0]}"
    if list(seiten) == list(range(seiten[0], seiten[-1] + 1)):
        return f"Seiten {seiten[0]} bis {seiten[-1]}"
    return "Seiten " + " und ".join(str(s) for s in seiten)


def _fassungen(vid: str, seiten: int) -> tuple[tuple[Fassung, ...], str]:
    letzte = seiten
    formular = tuple(range(1, letzte))
    voll = Fassung("vollstaendig", "Vollständige Fassung", f"Alle {seiten} Seiten mit der Anwendungshilfe auf Seite {letzte}, zum Lesen und Vorbereiten.")
    if vid == "09":
        f = (
            voll,
            Fassung("phase-a", "Phase A mit Datenschutzinformationen",
                    "Datenschutzinformationen vor Erhebung (Seite 4) und Phase A (Seite 1). Die Datenschutzseite vor Ausgabe vollständig ausfüllen.",
                    (4, 1)),
            Fassung("phase-b", "Phase B", "Nur Seite 2. Die Datenschutzinformationen müssen bereits vorliegen.", (2,)),
            Fassung("phase-c", "Phase C", "Nur Seite 3, unmittelbar vor Vertragsschluss.", (3,)),
        )
        text = ("Weitergeben werden je Phase nur die passenden Seiten: zuerst die vollständig ausgefüllten "
                "Datenschutzinformationen (Seite 4) mit Phase A (Seite 1), später Phase B (Seite 2) und Phase C (Seite 3). "
                f"Seite {letzte} ist die Anwendungshilfe und wird nicht weitergegeben.")
        return f, text
    f = (
        voll,
        Fassung("formular", "Zum Weitergeben ohne Anwendungshilfe",
                f"{_seitenliste(formular)}, ohne die Anwendungshilfe.", formular),
    )
    umfang = "wird nur Seite 1" if len(formular) == 1 else f"werden nur die {_seitenliste(formular)}"
    text = f"Weitergegeben {umfang}. Seite {letzte} ist die Anwendungshilfe und wird nicht mit dem Schreiben versandt."
    return f, text


@lru_cache(maxsize=1)
def _manifest() -> list[dict]:
    return json.loads((VERZEICHNIS / "manifest.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def alle() -> tuple[Vorlage, ...]:
    ergebnis = []
    for e in _manifest():
        t = _TEXTE[e["id"]]
        fassungen, weitergeben = _fassungen(e["id"], e["pages"])
        ergebnis.append(Vorlage(
            id=e["id"], titel=e["title"], datei=e["file"], seiten=e["pages"], sha256=e["sha256"],
            felder=tuple(e["fields"]), zweck=t["zweck"], einsatz=t["einsatz"], hinweise=t["hinweise"],
            weitergeben=weitergeben, fassungen=fassungen, phasen=t.get("phasen", ()),
        ))
    return tuple(ergebnis)


def finde(vid: str) -> Vorlage | None:
    return next((v for v in alle() if v.id == vid), None)


def original(v: Vorlage) -> bytes:
    """Liest das PDF und prüft es gegen die Prüfsumme im Manifest."""
    daten = (VERZEICHNIS / v.datei).read_bytes()
    if hashlib.sha256(daten).hexdigest() != v.sha256:
        raise Pruefsummenfehler(v.id)
    return daten


def markdown(v: Vorlage) -> bytes:
    return (VERZEICHNIS / v.markdown).read_bytes()


def fassung(v: Vorlage, schluessel: str) -> tuple[bytes, str] | None:
    """Liefert (PDF, Dateiname) der gewünschten Fassung oder None."""
    f = next((x for x in v.fassungen if x.schluessel == schluessel), None)
    if f is None:
        return None
    daten = original(v)
    if f.seiten is None:
        return daten, v.datei
    from pypdf import PdfReader, PdfWriter

    leser = PdfReader(io.BytesIO(daten))
    schreiber = PdfWriter()
    schreiber.append(leser, pages=[s - 1 for s in f.seiten])
    schreiber.add_metadata({"/Title": f"{v.titel} · {f.titel}", "/Author": "Zahlenwerk Immobilien"})
    puffer = io.BytesIO()
    schreiber.write(puffer)
    return puffer.getvalue(), f"{v.datei.removesuffix('.pdf')} - {f.titel}.pdf"
