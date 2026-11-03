"""Versionierte Report-Snapshots neben den Abrechnungsständen.

Ein Snapshot hält Abrechnungsversion(en) mit Prüfsummen, Regeln, Annahmen,
Erstellungszeitpunkt und den vollständigen Report-Inhalt. Dateien werden
exklusiv angelegt (`reports/vorauszahlung_r001.json`, …) und nie überschrieben.
Erstellung und Statuswechsel stehen im gemeinsamen Audit-Log der Ablage.

Status: entwurf → fachlich_geprueft → freigegeben. Erst ein freigegebener
Report darf dem Eigentümer zum Download bereitgestellt werden.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from .version import Ablage, Freigabefehler, kanonisch, statusfolge

REPORT_STATUS = ("entwurf", "fachlich_geprueft", "freigegeben")
REPORTARTEN = ("vorauszahlung", "kostenverlauf")


def inhalt_hash(inhalt: dict) -> str:
    return hashlib.sha256(kanonisch(inhalt).encode()).hexdigest()


class ReportAblage:
    def __init__(self, ablage: Ablage):
        self.ablage = ablage
        self.pfad = ablage.pfad / "reports"
        self.pfad.mkdir(exist_ok=True)

    def nummern(self, art: str) -> list[int]:
        return sorted(int(p.stem.rsplit("_r", 1)[1]) for p in self.pfad.glob(f"{art}_r[0-9][0-9][0-9].json"))

    def lesen(self, art: str, nummer: int) -> dict:
        return json.loads((self.pfad / f"{art}_r{nummer:03d}.json").read_text(encoding="utf-8"))

    def speichern(self, inhalt: dict, *, zeit: datetime, person: str) -> tuple[int, bool]:
        art = inhalt["report"]
        if art not in REPORTARTEN:
            raise ValueError(f"Unbekannte Reportart {art}")
        daten = json.loads(kanonisch(inhalt))
        h = inhalt_hash(daten)
        vorhanden = self.nummern(art)
        if vorhanden and self.lesen(art, vorhanden[-1]).get("inhalt_hash") == h:
            self.pruefen(art, vorhanden[-1])  # beschädigten Altstand nicht als identisch zurückgeben
            return vorhanden[-1], False
        nummer = (vorhanden[-1] + 1) if vorhanden else 1
        snapshot = {"report": art, "nummer": nummer, "erstellt": zeit.isoformat(), "erstellt_von": person,
                    "grundlage": daten["grundlage"], "report_regeln_version": daten["report_regeln_version"],
                    "regeln": daten["regeln"], "inhalt_hash": h, "inhalt": daten}
        with open(self.pfad / f"{art}_r{nummer:03d}.json", "x", encoding="utf-8") as f:
            f.write(json.dumps(snapshot, ensure_ascii=False, indent=1, sort_keys=True))
        self.ablage._audit({"ereignis": "report_erstellt", "report": art, "nummer": nummer, "inhalt_hash": h,
                            "grundlage": daten["grundlage"], "person": person, "zeit": zeit.isoformat()})
        self.ablage._audit({"ereignis": "report_status", "report": art, "nummer": nummer, "status": "entwurf",
                            "person": person, "zeit": zeit.isoformat()})
        return nummer, True

    def pruefen(self, art: str, nummer: int) -> dict:
        """Liest einen Snapshot und prüft Inhalt, Kopfdaten und Audit-Log. Wirft Freigabefehler.

        Prüft auch die vollständige Statusfolge (entwurf → fachlich_geprueft → freigegeben) und über das
        Kettenende den letzten Audit-Eintrag.
        Schutzgrenze: Prüfsummen und Audit-Kette erkennen nachträgliche Änderungen, aber nicht, wenn jemand
        mit Schreibrecht auf die Ablage Snapshot, gesamtes Audit-Log und Kettenende konsistent neu schreibt.
        """
        if nummer not in self.nummern(art):
            raise Freigabefehler(f"Report {art} Nr. {nummer} gibt es nicht")
        fehler = []
        try:
            snapshot = self.lesen(art, nummer)
            inhalt = snapshot["inhalt"]
            if inhalt_hash(inhalt) != snapshot["inhalt_hash"]:
                fehler.append("Inhalt passt nicht zur Prüfsumme")
            if (snapshot["report"], snapshot["nummer"]) != (art, nummer) or inhalt.get("report") != art:
                fehler.append("Kopfdaten passen nicht zur Datei")
            for feld in ("grundlage", "report_regeln_version", "regeln"):
                if kanonisch(snapshot[feld]) != kanonisch(inhalt[feld]):
                    fehler.append(f"{feld} weicht vom Inhalt ab")
        except (OSError, ValueError, KeyError, TypeError):
            raise Freigabefehler(f"Report {art} Nr. {nummer} ist nicht lesbar. Weitere Schritte gesperrt.") from None
        if not self.ablage.audit_unverfaelscht():
            fehler.append("Audit-Log ist beschädigt")
        erstellt = [e for e in self.ablage.audit()
                    if e.get("ereignis") == "report_erstellt" and e.get("report") == art and e.get("nummer") == nummer]
        if len(erstellt) != 1 or erstellt[0].get("inhalt_hash") != snapshot["inhalt_hash"]:
            fehler.append("Snapshot weicht vom Audit-Log ab")
        _, folgefehler = statusfolge(self._statusereignisse(art, nummer), REPORT_STATUS)
        fehler.extend(folgefehler)
        if fehler:
            raise Freigabefehler(f"Report {art} Nr. {nummer} ist nicht unverfälscht ({'; '.join(fehler)}). Gesperrt.")
        return snapshot

    def freigegeben(self, art: str, nummer: int) -> dict:
        """Geprüfter Snapshot eines freigegebenen Reports, sonst Freigabefehler. Für Ausgabe und Download."""
        snapshot = self.pruefen(art, nummer)
        if self.status(art, nummer) != "freigegeben":
            raise Freigabefehler(f"Report {art} Nr. {nummer} ist nicht freigegeben")
        return snapshot

    def _statusereignisse(self, art: str, nummer: int) -> list[dict]:
        return [e for e in self.ablage.audit()
                if e.get("ereignis") == "report_status" and e.get("report") == art and e.get("nummer") == nummer]

    def status(self, art: str, nummer: int) -> str:
        """Aktueller Status aus der vollständig geprüften Folge. Unzulässige Folge → Freigabefehler."""
        ereignisse = self._statusereignisse(art, nummer)
        if not ereignisse:
            return ""
        status, fehler = statusfolge(ereignisse, REPORT_STATUS)
        if fehler:
            raise Freigabefehler(f"Report {art} Nr. {nummer}: {'; '.join(fehler)}. Gesperrt.")
        return status

    def status_setzen(self, art: str, nummer: int, neu: str, *, person: str, zeit: datetime, notiz: str = "") -> None:
        if neu not in REPORT_STATUS:
            raise Freigabefehler(f"Unbekannter Status {neu}")
        if nummer not in self.nummern(art):
            raise Freigabefehler(f"Report {art} Nr. {nummer} gibt es nicht")
        self.pruefen(art, nummer)
        alt = self.status(art, nummer)
        if alt == REPORT_STATUS[-1] or REPORT_STATUS.index(neu) != REPORT_STATUS.index(alt) + 1:
            raise Freigabefehler(f"Von {alt} ist dieser Schritt nicht möglich")
        if nummer != self.nummern(art)[-1]:
            raise Freigabefehler("Nur der neueste Report kann freigegeben werden")
        if not person:
            raise Freigabefehler("Freigabe braucht eine verantwortliche Person")
        self.ablage._audit({"ereignis": "report_status", "report": art, "nummer": nummer, "status": neu,
                            "person": person, "zeit": zeit.isoformat(), "notiz": notiz})

    def download_erlaubt(self, art: str, nummer: int) -> bool:
        try:
            self.freigegeben(art, nummer)
        except Freigabefehler:
            return False
        return True
