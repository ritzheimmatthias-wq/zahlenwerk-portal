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

from .version import Ablage, Freigabefehler, kanonisch

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
        if vorhanden and self.lesen(art, vorhanden[-1])["inhalt_hash"] == h:
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

    def status(self, art: str, nummer: int) -> str:
        st = [e["status"] for e in self.ablage.audit()
              if e["ereignis"] == "report_status" and e["report"] == art and e["nummer"] == nummer]
        return st[-1] if st else ""

    def status_setzen(self, art: str, nummer: int, neu: str, *, person: str, zeit: datetime, notiz: str = "") -> None:
        if neu not in REPORT_STATUS:
            raise Freigabefehler(f"Unbekannter Status {neu}")
        if nummer not in self.nummern(art):
            raise Freigabefehler(f"Report {art} Nr. {nummer} gibt es nicht")
        alt = self.status(art, nummer)
        if alt == REPORT_STATUS[-1] or REPORT_STATUS.index(neu) != REPORT_STATUS.index(alt) + 1:
            raise Freigabefehler(f"Von {alt} ist dieser Schritt nicht möglich")
        if nummer != self.nummern(art)[-1]:
            raise Freigabefehler("Nur der neueste Report kann freigegeben werden")
        if not person:
            raise Freigabefehler("Freigabe braucht eine verantwortliche Person")
        snapshot = self.lesen(art, nummer)
        if inhalt_hash(snapshot["inhalt"]) != snapshot["inhalt_hash"]:
            raise Freigabefehler("Report-Inhalt passt nicht zur gespeicherten Prüfsumme")
        self.ablage._audit({"ereignis": "report_status", "report": art, "nummer": nummer, "status": neu,
                            "person": person, "zeit": zeit.isoformat(), "notiz": notiz})

    def download_erlaubt(self, art: str, nummer: int) -> bool:
        return self.status(art, nummer) == "freigegeben"
