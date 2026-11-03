"""Einlesen der Abrechnungsdaten aus TOML (Testobjekte, später Export aus dem Portal).

Beträge stehen als Text ("1234.56"), damit keine Fließkommazahlen entstehen.
"""
from __future__ import annotations

import tomllib
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .modell import (
    KATEGORIEN, NUTZUNGEN, POOLS, SCHLUESSEL, STEUERBEHANDLUNG, UMLAGEARTEN, VZ_ARTEN, Abrechnungsdaten, Abschnitt, Einheit, Kostenart,
    Messdienstabrechnung, Messwert, Mietverhaeltnis, Objekt, Personen, Position, Steuerfall, Vorauszahlung, Zeitraum,
)


class Eingabefehler(ValueError):
    pass


def _dez(wert, feld: str, *, pflicht: bool = True) -> Decimal | None:
    if wert is None or wert == "":
        if pflicht:
            raise Eingabefehler(f"{feld}: Angabe fehlt")
        return None
    if isinstance(wert, float):
        raise Eingabefehler(f"{feld}: Beträge und Werte bitte als Text angeben, z. B. \"12.50\"")
    try:
        return Decimal(str(wert).replace(",", "."))
    except InvalidOperation:
        raise Eingabefehler(f"{feld}: kein Zahlenwert ({wert!r})") from None


def _geld(wert, feld: str, *, pflicht: bool = True) -> Decimal | None:
    d = _dez(wert, feld, pflicht=pflicht)
    if d is not None and d != d.quantize(Decimal("0.01")):
        raise Eingabefehler(f"{feld}: mehr als zwei Nachkommastellen ({wert})")
    return d.quantize(Decimal("0.01")) if d is not None else None


def _wahl(wert, erlaubt, feld):
    if wert not in erlaubt:
        raise Eingabefehler(f"{feld}: {wert!r} ist nicht erlaubt ({', '.join(erlaubt)})")
    return wert


def _datum(wert, feld) -> date:
    if not isinstance(wert, date):
        raise Eingabefehler(f"{feld}: Datum im Format JJJJ-MM-TT erwartet")
    return wert


def lade(pfad: str | Path) -> Abrechnungsdaten:
    with open(pfad, "rb") as f:
        return aus_dict(tomllib.load(f))


def aus_dict(d: dict) -> Abrechnungsdaten:
    o = d["objekt"]
    objekt = Objekt(o["name"], o["anschrift"], o["vermieter"], o.get("vermieter_anschrift", ""))
    a = d["abrechnung"]
    jahr = Zeitraum(_datum(a["von"], "abrechnung.von"), _datum(a["bis"], "abrechnung.bis"))

    einheiten = []
    for e in d.get("einheiten", []):
        abschnitte = tuple(
            Abschnitt(
                Zeitraum(_datum(x.get("von", jahr.von), f"{e['id']}.von"), _datum(x.get("bis", jahr.bis), f"{e['id']}.bis")),
                _wahl(x["nutzung"], NUTZUNGEN, f"{e['id']}.nutzung"),
                _dez(x["flaeche"], f"{e['id']}.flaeche"),
            )
            for x in e["abschnitte"]
        )
        lv = {k: _dez(v, f"{e['id']}.leerstand_verbrauch.{k}") for k, v in e.get("leerstand_verbrauch", {}).items()}
        einheiten.append(Einheit(e["id"], e.get("bezeichnung", e["id"]), abschnitte, lv))

    mvs = []
    for m in d.get("mietverhaeltnisse", []):
        mid = m["id"]
        zr = Zeitraum(_datum(m.get("von", jahr.von), f"{mid}.von"), _datum(m.get("bis", jahr.bis), f"{mid}.bis"))
        vz = []
        for art, wert in m.get("vorauszahlungen", {}).items():
            _wahl(art, VZ_ARTEN, f"{mid}.vorauszahlungen")
            if isinstance(wert, dict):
                vz.append(Vorauszahlung(art, _geld(wert["brutto"], f"{mid}.{art}.brutto"),
                                        _geld(wert.get("netto"), f"{mid}.{art}.netto", pflicht=False),
                                        _geld(wert.get("ust"), f"{mid}.{art}.ust", pflicht=False)))
            else:
                vz.append(Vorauszahlung(art, _geld(wert, f"{mid}.{art}")))
        p = m.get("personen", [])
        if isinstance(p, int):
            personen = (Personen(zr.von, p),)
        else:
            personen = tuple(Personen(_datum(x["ab"], f"{mid}.personen.ab"), int(x["anzahl"])) for x in p)
        steuer = None
        if "steuer" in m:
            s = m["steuer"]
            steuer = Steuerfall(
                behandlung=_wahl(s.get("behandlung", "ungeklaert"), STEUERBEHANDLUNG, f"{mid}.steuer.behandlung"),
                satz=_dez(s.get("satz"), f"{mid}.steuer.satz", pflicht=False),
                kostenbasis=_wahl(s.get("kostenbasis", "netto"), ("netto", "brutto"), f"{mid}.steuer.kostenbasis"),
                ohne_ust=tuple(s.get("ohne_ust", [])),
                bestaetigt_von=s.get("bestaetigt_von", ""),
                bestaetigt_am=s.get("bestaetigt_am"),
            )
        verbrauch = {k: _dez(v, f"{mid}.verbrauch.{k}") for k, v in m.get("verbrauch", {}).items()}
        vz_monatlich = {_wahl(k, VZ_ARTEN, f"{mid}.vz_monatlich"): _geld(v, f"{mid}.vz_monatlich.{k}")
                        for k, v in m.get("vz_monatlich", {}).items()}
        mvs.append(Mietverhaeltnis(mid, m["einheit"], m["mieter"], zr, tuple(vz), personen, verbrauch, steuer,
                                   m.get("anschrift", ""), _wahl(m.get("umlageart", ""), UMLAGEARTEN, f"{mid}.umlageart"),
                                   vz_monatlich))

    kostenarten = tuple(
        Kostenart(k["id"], k["bezeichnung"], _wahl(k["schluessel"], SCHLUESSEL, f"{k['id']}.schluessel"),
                  _wahl(k.get("pool", "alle"), POOLS, f"{k['id']}.pool"),
                  bool(k.get("umlagefaehig_bestaetigt", False)), bool(k.get("heizkosten", False)),
                  _wahl(k.get("kategorie", "sonstige"), KATEGORIEN, f"{k['id']}.kategorie"))
        for k in d.get("kostenarten", [])
    )

    positionen = []
    for b in d.get("belege", []):
        lz = None
        if "leistung_von" in b or "leistung_bis" in b:
            lz = Zeitraum(_datum(b["leistung_von"], f"{b['id']}.leistung_von"), _datum(b["leistung_bis"], f"{b['id']}.leistung_bis"))
        for i, p in enumerate(b["positionen"]):
            feld = f"{b['id']}.positionen[{i}]"
            positionen.append(Position(
                beleg_id=b["id"], kostenart=p["kostenart"], brutto=_geld(p["brutto"], f"{feld}.brutto"),
                netto=_geld(p.get("netto"), f"{feld}.netto", pflicht=False),
                ust=_geld(p.get("ust"), f"{feld}.ust", pflicht=False),
                ust_satz=_dez(p.get("satz"), f"{feld}.satz", pflicht=False),
                leistungszeitraum=lz, abgrenzen=bool(b.get("abgrenzen", False)), einheit=p.get("einheit", ""),
                lieferant=b.get("lieferant", ""), rechnungsnummer=b.get("rechnungsnummer", ""),
                beleg_sha256=b.get("sha256", ""),
            ))

    messdienst = tuple(
        Messdienstabrechnung(
            md["id"], md.get("anbieter", ""), _geld(md["gesamt_brutto"], f"{md['id']}.gesamt_brutto"),
            tuple(Messwert(w["einheit"], w["kostenart"], _geld(w["brutto"], f"{md['id']}.brutto"),
                           _geld(w.get("netto"), f"{md['id']}.netto", pflicht=False), w.get("mietverhaeltnis", ""))
                  for w in md["werte"]),
            md.get("sha256", ""),
        )
        for md in d.get("messdienst", [])
    )
    vorjahr = {k: _geld(v, f"vorjahr.{k}") for k, v in a.get("vorjahr", {}).items()}
    schwelle = _dez(a.get("vorjahr_schwelle", "0.25"), "vorjahr_schwelle")
    return Abrechnungsdaten(objekt, jahr, tuple(einheiten), tuple(mvs), kostenarten, tuple(positionen), messdienst,
                            vorjahr, schwelle)
