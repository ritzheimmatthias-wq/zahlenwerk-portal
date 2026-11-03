"""Versionierte Berechnungsstände, Statusfolge und Audit-Log.

Jeder Lauf wird als unveränderliche Datei `v001.json`, `v002.json` … abgelegt
(exklusives Anlegen, kein Überschreiben). Neue Daten erzeugen eine neue Version.
Statuswechsel stehen nur im Audit-Log (`audit.jsonl`, nur Anhängen, jede Zeile
mit dem Hash der vorherigen), nie in der Versionsdatei selbst.

Statusfolge: entwurf → fachlich_geprueft → eigentuemer_freigegeben → endgueltig.
Solange rote Warnungen bestehen, ist nur „entwurf“ möglich.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

STATUS = ("entwurf", "fachlich_geprueft", "eigentuemer_freigegeben", "endgueltig")


class Freigabefehler(Exception):
    pass


def _json(o):
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if dataclasses.is_dataclass(o):
        return {f.name: getattr(o, f.name) for f in dataclasses.fields(o)}
    if isinstance(o, tuple):
        return list(o)
    if isinstance(o, set):
        return sorted(o)
    raise TypeError(type(o))


def kanonisch(o) -> str:
    return json.dumps(o, default=_json, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def eingabe_hash(daten) -> str:
    return hashlib.sha256(kanonisch(daten).encode()).hexdigest()


def als_dict(erg) -> dict:
    """Strukturierte Ergebnisdaten für Portal und Exporte (ohne die Eingabedaten selbst)."""
    roh = json.loads(kanonisch({
        "regeln_version": erg.regeln_version,
        "abrechnungszeitraum": erg.daten.zeitraum,
        "objekt": erg.daten.objekt,
        "mietverhaeltnisse": erg.mietverhaeltnisse,
        "kontrolle": erg.kontrolle,
        "leerstand": erg.leerstand,
        "warnungen": erg.warnungen,
    }))
    return roh


def ergebnis_hash(ergebnis: dict) -> str:
    """Prüfsumme der strukturierten Ergebnisdaten; identische Eingabe und Regeln ergeben denselben Wert."""
    return hashlib.sha256(kanonisch(ergebnis).encode()).hexdigest()


def stand_fehler(stand: dict) -> list[str]:
    """Prüft einen gespeicherten Stand gegen seinen eigenen Inhalt: Prüfsummen werden aus Eingabe
    und Ergebnis neu gebildet, Warnungszähler aus den gespeicherten Warnungen. Leere Liste = in Ordnung.

    Schutzgrenze: Wer Inhalt und alle Prüfsummen gemeinsam neu schreiben darf, wird hiermit allein nicht
    erkannt. Dafür dient zusätzlich der Abgleich mit dem verketteten Audit-Log (Ablage.pruefen) bzw. im
    Portal mit der Datenbank.
    """
    fehler = []
    try:
        if stand.get("eingabe_hash") != eingabe_hash(stand["eingabe"]):
            fehler.append("Eingabedaten passen nicht zur Eingabe-Prüfsumme")
        if "ergebnis_hash" not in stand or stand["ergebnis_hash"] != ergebnis_hash(stand["ergebnis"]):
            fehler.append("Ergebnisdaten passen nicht zur Ergebnis-Prüfsumme")
        stufen = [w.get("stufe") for w in stand["ergebnis"].get("warnungen", [])]
        if stand.get("rote_warnungen") != stufen.count("rot") or stand.get("gelbe_warnungen") != stufen.count("gelb"):
            fehler.append("Warnungszähler passen nicht zu den gespeicherten Warnungen")
    except (KeyError, TypeError, AttributeError):
        fehler.append("Stand ist unvollständig")
    return fehler


def statusfolge(ereignisse: list[dict], erlaubt: tuple[str, ...]) -> tuple[str, list[str]]:
    """Prüft die vollständige Statusfolge eines Stands oder Reports.

    Zulässig ist nur genau die Folge erlaubt[0], erlaubt[1], … ohne ausgelassene, doppelte,
    rückwärts gerichtete oder unbekannte Stufen, jede mit verantwortlicher Person. Rückgabe:
    (aktueller Status, Fehlerliste). Bei Fehlern ist der Status leer, er wird also nie
    ungeprüft aus dem letzten Eintrag übernommen.
    """
    folge = [e.get("status") for e in ereignisse]
    fehler = []
    if not folge:
        fehler.append("Statusverlauf fehlt")
    elif folge != list(erlaubt[:len(folge)]) or len(folge) > len(erlaubt):
        fehler.append("Statusverlauf ist unzulässig (" + " → ".join(str(f) for f in folge) + ")")
    if any(not e.get("person") for e in ereignisse):
        fehler.append("Statuswechsel ohne verantwortliche Person")
    return ("" if fehler else folge[-1]), fehler


class Ablage:
    def __init__(self, verzeichnis: str | Path):
        self.pfad = Path(verzeichnis)
        self.pfad.mkdir(parents=True, exist_ok=True)
        self.audit_pfad = self.pfad / "audit.jsonl"
        # Kettenende: Anzahl und Prüfsumme des letzten Eintrags. Ohne diesen Anker hätte der letzte
        # Eintrag keinen Nachfolger, der seine Veränderung verrät.
        self.kopf_pfad = self.pfad / "audit.kopf.json"

    # --- Versionen --------------------------------------------------------------

    def versionen(self) -> list[int]:
        return sorted(int(p.stem[1:]) for p in self.pfad.glob("v[0-9][0-9][0-9].json"))

    def lesen(self, version: int) -> dict:
        return json.loads((self.pfad / f"v{version:03d}.json").read_text(encoding="utf-8"))

    def speichern(self, erg, *, zeit: datetime, person: str) -> tuple[int, bool]:
        """Legt einen neuen Berechnungsstand an; identische Eingaben erzeugen keine neue Version."""
        h = eingabe_hash(erg.daten)
        vorhanden = self.versionen()
        if vorhanden:
            letzte = self.lesen(vorhanden[-1])
            if letzte.get("eingabe_hash") == h and letzte.get("ergebnis", {}).get("regeln_version") == erg.regeln_version:
                # Einen beschädigten Altstand nie als vermeintlich identisches Ergebnis zurückgeben.
                self.pruefen(vorhanden[-1])
                return vorhanden[-1], False
        version = (vorhanden[-1] + 1) if vorhanden else 1
        ergebnis = als_dict(erg)
        stand = {
            "version": version,
            "erstellt": zeit.isoformat(),
            "erstellt_von": person,
            "eingabe_hash": h,
            "eingabe": json.loads(kanonisch(erg.daten)),
            "ergebnis": ergebnis,
            "ergebnis_hash": ergebnis_hash(ergebnis),
            "rote_warnungen": len(erg.rot),
            "gelbe_warnungen": len(erg.gelb),
        }
        with open(self.pfad / f"v{version:03d}.json", "x", encoding="utf-8") as f:
            f.write(json.dumps(stand, ensure_ascii=False, indent=1, sort_keys=True))
        self._audit({"ereignis": "berechnet", "version": version, "eingabe_hash": h, "ergebnis_hash": stand["ergebnis_hash"],
                     "regeln": erg.regeln_version,
                     "rot": len(erg.rot), "gelb": len(erg.gelb), "person": person, "zeit": zeit.isoformat()})
        self._audit({"ereignis": "status", "version": version, "status": "entwurf", "person": person, "zeit": zeit.isoformat()})
        return version, True

    # --- Status und Audit-Log ---------------------------------------------------

    def audit(self) -> list[dict]:
        if not self.audit_pfad.exists():
            return []
        return [json.loads(z) for z in self.audit_pfad.read_text(encoding="utf-8").splitlines() if z.strip()]

    def audit_unverfaelscht(self) -> bool:
        """Kette vollständig: jeder Eintrag verweist auf seinen Vorgänger, das Kettenende auf den letzten.

        Schutzgrenze: Wer Audit-Log und Kettenende gemeinsam konsistent neu schreibt, wird hiermit allein
        nicht erkannt. Einzelne Änderungen, auch am letzten Eintrag, dagegen schon."""
        try:
            eintraege = self.audit()
        except ValueError:
            return False
        vorher = ""
        for e in eintraege:
            if e.get("vorher") != vorher:
                return False
            vorher = hashlib.sha256(kanonisch(e).encode()).hexdigest()
        if not eintraege:
            return not self.kopf_pfad.exists()
        try:
            kopf = json.loads(self.kopf_pfad.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return kopf == {"eintraege": len(eintraege), "letzter": vorher}

    def _statusereignisse(self, version: int) -> list[dict]:
        return [e for e in self.audit() if e.get("ereignis") == "status" and e.get("version") == version]

    def pruefen(self, version: int) -> dict:
        """Liest einen Stand und prüft Inhalt, Prüfsummen, Warnungszähler und Audit-Kette.
        Wirft Freigabefehler, sonst Rückgabe des geprüften Stands."""
        if version not in self.versionen():
            raise Freigabefehler(f"Version {version} gibt es nicht")
        try:
            stand = self.lesen(version)
        except (OSError, ValueError):
            raise Freigabefehler(f"Version {version} ist nicht lesbar. Weitere Schritte gesperrt.") from None
        fehler = stand_fehler(stand)
        if stand.get("version") != version:
            fehler.append("Versionsnummer passt nicht zur Datei")
        if not self.audit_unverfaelscht():
            fehler.append("Audit-Log ist beschädigt")
        berechnet = [e for e in self.audit() if e.get("ereignis") == "berechnet" and e.get("version") == version]
        _, folgefehler = statusfolge(self._statusereignisse(version), STATUS)
        fehler.extend(folgefehler)
        if len(berechnet) != 1:
            fehler.append("Berechnung fehlt im Audit-Log")
        else:
            e = berechnet[0]
            if (e.get("eingabe_hash") != stand.get("eingabe_hash") or e.get("ergebnis_hash") != stand.get("ergebnis_hash")
                    or e.get("rot") != stand.get("rote_warnungen") or e.get("gelb") != stand.get("gelbe_warnungen")
                    or e.get("regeln") != stand.get("ergebnis", {}).get("regeln_version")):
                fehler.append("Stand weicht vom Audit-Log ab")
        if fehler:
            raise Freigabefehler(f"Version {version} ist nicht unverfälscht ({'; '.join(fehler)}). Weitere Schritte gesperrt.")
        return stand

    def _audit(self, eintrag: dict) -> None:
        eintraege = self.audit()
        eintrag = dict(eintrag, vorher=hashlib.sha256(kanonisch(eintraege[-1]).encode()).hexdigest() if eintraege else "")
        with open(self.audit_pfad, "a", encoding="utf-8") as f:
            f.write(kanonisch(eintrag) + "\n")
        kopf = {"eintraege": len(eintraege) + 1, "letzter": hashlib.sha256(kanonisch(eintrag).encode()).hexdigest()}
        tmp = self.kopf_pfad.with_suffix(".tmp")
        tmp.write_text(kanonisch(kopf), encoding="utf-8")
        os.replace(tmp, self.kopf_pfad)

    def status(self, version: int) -> str:
        """Aktueller Status aus der vollständig geprüften Statusfolge. Unzulässige Folge → Freigabefehler."""
        ereignisse = self._statusereignisse(version)
        if not ereignisse:
            return ""
        status, fehler = statusfolge(ereignisse, STATUS)
        if fehler:
            raise Freigabefehler(f"Version {version}: {'; '.join(fehler)}. Weitere Schritte gesperrt.")
        return status

    def status_setzen(self, version: int, neu: str, *, person: str, zeit: datetime, notiz: str = "") -> None:
        if neu not in STATUS:
            raise Freigabefehler(f"Unbekannter Status {neu}")
        if version not in self.versionen():
            raise Freigabefehler(f"Version {version} gibt es nicht")
        stand = self.pruefen(version)  # beschädigter Stand oder Audit-Log sperrt jeden weiteren Schritt
        alt = self.status(version)
        if STATUS.index(neu) != STATUS.index(alt) + 1:
            raise Freigabefehler(f"Von {alt} ist nur {STATUS[STATUS.index(alt) + 1] if alt != STATUS[-1] else 'kein weiterer Schritt'} möglich")
        if version != self.versionen()[-1]:
            raise Freigabefehler("Nur die neueste Version kann freigegeben werden")
        if stand["rote_warnungen"]:
            raise Freigabefehler("Rote Warnungen bestehen. Freigabe gesperrt.")
        if not person:
            raise Freigabefehler("Freigabe braucht eine verantwortliche Person")
        self._audit({"ereignis": "status", "version": version, "status": neu, "person": person,
                     "zeit": zeit.isoformat(), "notiz": notiz})
