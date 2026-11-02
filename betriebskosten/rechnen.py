"""Rechenkern: verteilt Kosten je Kostenart auf Mietverhältnisse und Leerstand.

Ablauf
1. Zeitsegmente bilden: je Einheit und Abschnitt (Fläche, Nutzung) die Tage je
   Mietverhältnis; nicht vermietete Tage sind Leerstand des Eigentümers.
2. Je Kostenart den Pool bilden (Positionen, gegebenenfalls zeitanteilig
   abgegrenzt) und nach Schlüssel verteilen. Gewichte sind z. B. Fläche × Tage.
   Die Rundung auf Cent erfolgt nach dem Verfahren der größten Reste, damit die
   Summe der Anteile exakt dem Pool entspricht.
3. Messdienstwerte werden je Einheit bzw. Mietverhältnis übernommen.
4. Je Mietverhältnis die Darstellung wählen: Wohnraum (brutto),
   Gewerbe steuerfrei (brutto), Gewerbe steuerpflichtig (netto + USt + brutto)
   oder gesperrt, solange die Steuerbehandlung nicht bestätigt ist.

Der Kern entscheidet nichts Steuerliches oder Rechtliches. Fehlende oder
widersprüchliche Angaben werden als Warnung ausgegeben (rot = sperrt Freigabe).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from .modell import CENT, NULL, Abrechnungsdaten, Kostenart, Mietverhaeltnis, Position, Zeitraum, cent

REGELN_VERSION = "2026.11-2"
GENAU = Decimal("0.000001")
RUNDUNGSREGELN = (
    "Belegbeträge und Messdienstwerte werden unverändert in Cent übernommen.",
    "Zeitanteilige Abgrenzung eines Belegs: kaufmännisch auf Cent gerundet.",
    "Verteilung je Kostenart: exakter Anteil = Betrag × Gewicht / Summe der Gewichte; abgerundet auf Cent, "
    "die verbleibenden Cent erhalten die Empfänger mit den größten Resten (bei Gleichstand die Reihenfolge der Erfassung).",
    "Umsatzsteuer je Kostenart und Mietverhältnis: Basis × Steuersatz, kaufmännisch auf Cent gerundet.",
)


@dataclass(frozen=True)
class Warnung:
    stufe: str  # rot sperrt die Freigabe, gelb verlangt eine Prüfung
    code: str
    text: str
    bezug: str = ""


@dataclass(frozen=True)
class Segment:
    einheit: str
    empfaenger: tuple[str, str]  # ("mv", id) oder ("leer", einheit)
    zeitraum: Zeitraum
    nutzung: str
    flaeche: Decimal


@dataclass
class Anteil:
    brutto: Decimal
    netto: Decimal | None
    exakt: Decimal
    bezug: Decimal
    gesamt_brutto: Decimal
    gesamt_netto: Decimal | None
    bezug_gesamt: Decimal
    herkunft: list

    def plus(self, o: "Anteil") -> "Anteil":
        return Anteil(self.brutto + o.brutto,
                      (self.netto + o.netto) if self.netto is not None and o.netto is not None else None,
                      self.exakt + o.exakt, self.bezug + o.bezug, self.gesamt_brutto + o.gesamt_brutto,
                      (self.gesamt_netto + o.gesamt_netto) if self.gesamt_netto is not None and o.gesamt_netto is not None else None,
                      self.bezug_gesamt + o.bezug_gesamt, self.herkunft + o.herkunft)


@dataclass
class Zeile:
    kostenart: str
    bezeichnung: str
    schluessel: str
    heizkosten: bool
    gesamt_brutto: Decimal
    gesamt_netto: Decimal | None
    bezug_gesamt: Decimal  # Summe der Gewichte, umgerechnet auf ein Jahr (z. B. m² im Jahresmittel)
    bezug_anteil: Decimal
    anteil_brutto: Decimal
    anteil_netto: Decimal | None
    exakt_brutto: Decimal
    betrag: Decimal = NULL  # in der Darstellung des Mietverhältnisses (brutto oder netto)
    ust: Decimal = NULL
    herkunft: list = field(default_factory=list)


@dataclass
class Vorauszahlungen:
    art: str
    betrag: Decimal
    ust: Decimal
    brutto: Decimal


@dataclass
class MvErgebnis:
    id: str
    mieter: str
    anschrift: str
    einheit: str
    einheit_bezeichnung: str
    nutzung: str
    nutzungszeitraum: Zeitraum
    darstellung: str  # wohnen, gewerbe_brutto, gewerbe_netto, gesperrt
    ust_satz: Decimal | None
    zeilen: list[Zeile]
    vorauszahlungen: list[Vorauszahlungen]
    kosten: Decimal = NULL
    kosten_ust: Decimal = NULL
    kosten_brutto: Decimal = NULL
    vz: Decimal = NULL
    vz_ust: Decimal = NULL
    vz_brutto: Decimal = NULL
    saldo: Decimal = NULL  # positiv = Nachzahlung, negativ = Guthaben
    saldo_ust: Decimal = NULL
    saldo_brutto: Decimal = NULL
    rundung: Decimal = NULL  # Summe gerundet minus Summe exakt (brutto)
    vorsteueranteil_info: Decimal = NULL  # nur Information für den Eigentümer, keine steuerliche Feststellung
    umlageart: str = ""

    @property
    def gesperrt(self) -> bool:
        return self.darstellung == "gesperrt"


@dataclass
class KostenartKontrolle:
    kostenart: str
    bezeichnung: str
    pool: str
    schluessel: str
    gesamt_brutto: Decimal
    gesamt_netto: Decimal | None
    verteilt_brutto: Decimal
    verteilt_netto: Decimal | None
    leerstand_brutto: Decimal
    belege: list[str]
    vorjahr: Decimal | None = None
    kategorie: str = "sonstige"
    schritte: list = field(default_factory=list)  # Rechenschritte: Positionen, Gewichte, exakte und gerundete Anteile


@dataclass
class Ergebnis:
    daten: Abrechnungsdaten
    mietverhaeltnisse: list[MvErgebnis]
    kontrolle: list[KostenartKontrolle]
    leerstand: dict[str, Decimal]  # Einheit -> Anteil Eigentümer brutto
    warnungen: list[Warnung]
    regeln_version: str = REGELN_VERSION
    rundungsregeln: tuple = RUNDUNGSREGELN

    @property
    def rot(self) -> list[Warnung]:
        return [w for w in self.warnungen if w.stufe == "rot"]

    @property
    def gelb(self) -> list[Warnung]:
        return [w for w in self.warnungen if w.stufe == "gelb"]

    def mv(self, mid: str) -> MvErgebnis:
        return next(m for m in self.mietverhaeltnisse if m.id == mid)


# --- Hilfen -------------------------------------------------------------------------

def verteile_nachweis(betrag: Decimal, gewichte: dict, reihenfolge: list) -> tuple[dict, dict, set]:
    """Verteilt einen Centbetrag nach Gewichten (größte Reste) und liefert den Rechenweg mit.

    Rückgabe: gerundete Anteile, exakte Anteile in Euro (6 Nachkommastellen) und die Empfänger,
    die einen Restcent erhalten haben.
    """
    summe = sum(gewichte.values(), Decimal(0))
    if summe <= 0:
        raise ValueError("Keine Verteilungsgrundlage")
    vorzeichen = -1 if betrag < 0 else 1
    cents = int((abs(betrag) / CENT).to_integral_value())
    exakt = {k: Decimal(cents) * gewichte[k] / summe for k in reihenfolge}
    basis = {k: int(exakt[k]) for k in reihenfolge}
    rest = cents - sum(basis.values())
    zuschlag = set(sorted(reihenfolge, key=lambda k: (-(exakt[k] - basis[k]), reihenfolge.index(k)))[:rest])
    for k in zuschlag:
        basis[k] += 1
    return ({k: vorzeichen * Decimal(basis[k]) * CENT for k in reihenfolge},
            {k: (vorzeichen * exakt[k] * CENT).quantize(GENAU) for k in reihenfolge}, zuschlag)


def verteile(betrag: Decimal, gewichte: dict, reihenfolge: list) -> dict:
    """Verteilt einen Centbetrag nach Gewichten; Summe der Anteile = Betrag (größte Reste)."""
    return verteile_nachweis(betrag, gewichte, reihenfolge)[0]


def _empf(e: tuple) -> str:
    return f"{e[0]}:{e[1]}"


def _schritt_anteile(g: dict, reihe: list, vb: dict, vn: dict, exakt: dict, zuschlag: set) -> list:
    return [{"empfaenger": _empf(e), "gewicht": g[e], "exakt_brutto": exakt[e], "brutto": vb[e], "netto": vn.get(e),
             "restcent": e in zuschlag} for e in reihe]


def _luecken(z: Zeitraum, belegt: list[Zeitraum]) -> list[Zeitraum]:
    frei, tag = [], z.von
    for b in sorted(belegt, key=lambda x: x.von):
        if b.von > tag:
            frei.append(Zeitraum(tag, b.von - timedelta(days=1)))
        tag = max(tag, b.bis + timedelta(days=1))
    if tag <= z.bis:
        frei.append(Zeitraum(tag, z.bis))
    return frei


def segmente(d: Abrechnungsdaten, w: list[Warnung]) -> list[Segment]:
    erg = []
    mvs_je_einheit = defaultdict(list)
    for m in d.mietverhaeltnisse:
        if d.einheit(m.einheit) is None:
            w.append(Warnung("rot", "einheit_unbekannt", f"Mietverhältnis {m.id} verweist auf unbekannte Einheit {m.einheit}.", m.id))
            continue
        mvs_je_einheit[m.einheit].append(m)
    for e in d.einheiten:
        mvs = sorted(mvs_je_einheit[e.id], key=lambda m: m.zeitraum.von)
        for a, b in zip(mvs, mvs[1:]):
            if a.zeitraum.schnitt(b.zeitraum):
                w.append(Warnung("rot", "ueberschneidung", f"Mietverhältnisse {a.id} und {b.id} überschneiden sich in {e.id}.", e.id))
        abgedeckt = defaultdict(int)
        for ab in e.abschnitte:
            z = ab.zeitraum.schnitt(d.zeitraum)
            if z is None:
                continue
            belegt = []
            for m in mvs:
                s = m.zeitraum.schnitt(z)
                if s:
                    erg.append(Segment(e.id, ("mv", m.id), s, ab.nutzung, ab.flaeche))
                    belegt.append(s)
                    abgedeckt[m.id] += s.tage
            for frei in _luecken(z, belegt):
                erg.append(Segment(e.id, ("leer", e.id), frei, ab.nutzung, ab.flaeche))
        for m in mvs:
            im_jahr = m.zeitraum.schnitt(d.zeitraum)
            if im_jahr and abgedeckt[m.id] != im_jahr.tage:
                w.append(Warnung("rot", "mv_ohne_einheit",
                                 f"Mietverhältnis {m.id} liegt teilweise außerhalb der Zeitabschnitte von {e.id}.", m.id))
    return erg


def _personentage(m: Mietverhaeltnis, z: Zeitraum) -> Decimal:
    if not m.personen:
        return Decimal(0)
    stufen = sorted(m.personen, key=lambda p: p.ab)
    summe = 0
    for i, p in enumerate(stufen):
        bis = stufen[i + 1].ab - timedelta(days=1) if i + 1 < len(stufen) else z.bis
        s = Zeitraum(p.ab, max(bis, p.ab)).schnitt(z) if bis >= p.ab else None
        if s:
            summe += p.anzahl * s.tage
    return Decimal(summe)


def _im_pool(ka: Kostenart, nutzung: str) -> bool:
    return ka.pool == "alle" or ka.pool == nutzung


def gewichte(ka: Kostenart, segs: list[Segment], d: Abrechnungsdaten, w: list[Warnung], einheit: str = "") -> dict:
    g: dict = defaultdict(Decimal)
    mvs = {m.id: m for m in d.mietverhaeltnisse}
    gezaehlt = set()
    for s in segs:
        if not _im_pool(ka, s.nutzung) or (ka.schluessel == "direkt" and s.einheit != einheit):
            continue
        art, eid = s.empfaenger
        if ka.schluessel == "flaeche":
            g[s.empfaenger] += s.flaeche * s.zeitraum.tage
        elif ka.schluessel in ("einheiten", "direkt"):
            g[s.empfaenger] += Decimal(s.zeitraum.tage)
        elif ka.schluessel == "personen":
            if art == "mv":
                g[s.empfaenger] += _personentage(mvs[eid], s.zeitraum)
            else:
                g[s.empfaenger] += Decimal(0)
                w.append(Warnung("gelb", "leerstand_personen",
                                 f"Leerstand in {eid} bei Personenschlüssel ({ka.bezeichnung}): Umgang mit Leerstand fachlich prüfen.", eid))
        elif ka.schluessel == "verbrauch":
            if s.empfaenger in gezaehlt:
                continue
            gezaehlt.add(s.empfaenger)
            if art == "mv":
                wert = mvs[eid].verbrauch.get(ka.id)
                if wert is None:
                    w.append(Warnung("rot", "verbrauch_fehlt", f"Verbrauch {ka.bezeichnung} fehlt für {eid}.", eid))
                    wert = Decimal(0)
            else:
                wert = d.einheit(eid).leerstand_verbrauch.get(ka.id, Decimal(0))
            g[s.empfaenger] += wert
    return dict(g)


def _abgrenzen(p: Position, jahr: Zeitraum, w: list[Warnung]) -> Position | None:
    lz = p.leistungszeitraum
    if lz is None or jahr.enthaelt(lz):
        return p
    s = lz.schnitt(jahr)
    if s is None:
        w.append(Warnung("rot", "zeitraum_ausserhalb", f"Beleg {p.beleg_id}: Leistungszeitraum liegt außerhalb des Abrechnungsjahres, nicht berücksichtigt.", p.beleg_id))
        return None
    if not p.abgrenzen:
        w.append(Warnung("gelb", "zeitraum_teilweise", f"Beleg {p.beleg_id}: Leistungszeitraum {lz.von:%d.%m.%Y}–{lz.bis:%d.%m.%Y} reicht über das Abrechnungsjahr hinaus; voll berücksichtigt, Abgrenzung prüfen.", p.beleg_id))
        return p
    f = Decimal(s.tage) / Decimal(lz.tage)
    return Position(p.beleg_id, p.kostenart, cent(p.brutto * f), cent(p.netto * f) if p.netto is not None else None,
                    None, p.ust_satz, lz, True, p.einheit, p.lieferant, p.rechnungsnummer, p.beleg_sha256)


def _belegpruefung(d: Abrechnungsdaten, w: list[Warnung]) -> None:
    gesehen_hash, gesehen_nr = {}, {}
    for p in d.positionen:
        if p.netto is not None and p.ust is not None and p.netto + p.ust != p.brutto:
            w.append(Warnung("rot", "summe_beleg", f"Beleg {p.beleg_id}: netto + USt ergibt nicht brutto.", p.beleg_id))
        if p.netto is not None and p.ust is not None and p.ust_satz is not None and abs(cent(p.netto * p.ust_satz) - p.ust) > CENT:
            w.append(Warnung("gelb", "steuersatz", f"Beleg {p.beleg_id}: USt passt nicht zum angegebenen Satz.", p.beleg_id))
        if p.beleg_sha256:
            andere = gesehen_hash.setdefault(p.beleg_sha256, p.beleg_id)
            if andere != p.beleg_id:
                w.append(Warnung("rot", "dublette_datei", f"Belege {andere} und {p.beleg_id} sind dieselbe Datei.", p.beleg_id))
        if p.lieferant and p.rechnungsnummer:
            schl = (p.lieferant.lower(), p.rechnungsnummer)
            andere = gesehen_nr.setdefault(schl, p.beleg_id)
            if andere != p.beleg_id:
                w.append(Warnung("rot", "dublette_rechnung", f"Belege {andere} und {p.beleg_id}: gleicher Lieferant und gleiche Rechnungsnummer.", p.beleg_id))
        if d.kostenart(p.kostenart) is None:
            w.append(Warnung("rot", "kostenart_unbekannt", f"Beleg {p.beleg_id}: unbekannte Kostenart {p.kostenart}.", p.beleg_id))


# --- Hauptfunktion ------------------------------------------------------------------

def berechne(d: Abrechnungsdaten) -> Ergebnis:
    w: list[Warnung] = []
    segs = segmente(d, w)
    _belegpruefung(d, w)
    empf_reihe = []
    for s in segs:
        if s.empfaenger not in empf_reihe:
            empf_reihe.append(s.empfaenger)

    # Anteile je Empfänger und Kostenart
    anteile: dict = defaultdict(dict)  # Empfänger -> Kostenart -> Anteil
    kontrolle: list[KostenartKontrolle] = []
    tage_jahr = Decimal(d.zeitraum.tage)

    for ka in d.kostenarten:
        if not ka.umlagefaehig_bestaetigt:
            w.append(Warnung("rot", "umlage_unbestaetigt", f"Umlagefähigkeit von {ka.bezeichnung} ist nicht bestätigt.", ka.id))
        if ka.schluessel == "messdienst":
            kontrolle.append(_messdienst(ka, d, segs, anteile, w))
            continue
        original = {}
        positionen = []
        for p in d.positionen:
            if p.kostenart != ka.id:
                continue
            q = _abgrenzen(p, d.zeitraum, w)
            if q:
                original[id(q)] = p
                positionen.append(q)
        if not positionen:
            continue
        gruppen = defaultdict(list)
        for p in positionen:
            gruppen[p.einheit if ka.schluessel == "direkt" else ""].append(p)
        k = KostenartKontrolle(ka.id, ka.bezeichnung, ka.pool, ka.schluessel, NULL, NULL, NULL, NULL, NULL, [],
                               d.vorjahr.get(ka.id), ka.kategorie)
        netto_vollstaendig = True
        for einheit, ps in gruppen.items():
            if ka.schluessel == "direkt" and not einheit:
                w.append(Warnung("rot", "direkt_ohne_einheit", f"{ka.bezeichnung}: Direktzuordnung ohne Einheit.", ka.id))
                continue
            g = gewichte(ka, segs, d, w, einheit)
            reihe = [e for e in empf_reihe if e in g]
            brutto = sum((p.brutto for p in ps), NULL)
            hat_netto = all(p.netto is not None for p in ps)
            netto = sum((p.netto for p in ps), NULL) if hat_netto else None
            netto_vollstaendig &= hat_netto
            k.gesamt_brutto += brutto
            k.belege += [p.beleg_id for p in ps]
            if sum(g.values(), Decimal(0)) <= 0:
                w.append(Warnung("rot", "keine_grundlage", f"{ka.bezeichnung}: keine Verteilungsgrundlage im Pool {ka.pool}.", ka.id))
                continue
            vb, exakt, zuschlag = verteile_nachweis(brutto, g, reihe)
            vn = verteile(netto, g, reihe) if netto is not None else {}
            summe_g = sum(g.values(), Decimal(0))
            zeitlich = ka.schluessel in ("flaeche", "einheiten", "personen", "direkt")
            teiler = tage_jahr if zeitlich else Decimal(1)
            k.schritte.append({
                "gruppe": einheit,
                "positionen": [{"beleg": p.beleg_id, "lieferant": p.lieferant, "rechnungsnummer": p.rechnungsnummer,
                                "sha256": p.beleg_sha256, "brutto_beleg": original[id(p)].brutto, "brutto": p.brutto,
                                "netto": p.netto, "abgegrenzt": p is not original[id(p)]} for p in ps],
                "betrag_brutto": brutto, "betrag_netto": netto,
                "gewicht": {"flaeche": "m² × Tage", "einheiten": "Einheit × Tage", "direkt": "Tage",
                            "personen": "Personen × Tage", "verbrauch": "Verbrauch"}[ka.schluessel],
                "summe_gewichte": summe_g, "bezugsgroesse_jahr": (summe_g / teiler).quantize(GENAU),
                "anteile": _schritt_anteile(g, reihe, vb, vn, exakt, zuschlag),
            })
            herkunft = [{"beleg": p.beleg_id, "sha256": p.beleg_sha256, "lieferant": p.lieferant,
                         "rechnungsnummer": p.rechnungsnummer, "brutto": str(p.brutto)} for p in ps]
            for e in reihe:
                neu = Anteil(vb[e], vn.get(e), brutto * g[e] / summe_g, g[e] / teiler, brutto, netto,
                             summe_g / teiler, herkunft)
                alt = anteile[e].get(ka.id)
                anteile[e][ka.id] = alt.plus(neu) if alt else neu
            k.verteilt_brutto += sum(vb.values(), NULL)
            k.leerstand_brutto += sum((v for e, v in vb.items() if e[0] == "leer"), NULL)
            if netto is not None:
                k.gesamt_netto += netto
                k.verteilt_netto += sum(vn.values(), NULL)
        if not netto_vollstaendig:
            k.gesamt_netto = k.verteilt_netto = None
        kontrolle.append(k)

    mv_ergebnisse = [_mv_ergebnis(m, d, segs, anteile, w) for m in d.mietverhaeltnisse
                     if m.zeitraum.schnitt(d.zeitraum) and d.einheit(m.einheit)]
    leerstand = defaultdict(lambda: NULL)
    for e, ka_anteile in anteile.items():
        if e[0] == "leer":
            leerstand[e[1]] += sum((v.brutto for v in ka_anteile.values()), NULL)
    erg = Ergebnis(d, mv_ergebnisse, kontrolle, dict(leerstand), w)
    from .pruefungen import pruefe  # Gesamtprüfungen nach der Rechnung

    pruefe(erg)
    return erg


def _messdienst(ka: Kostenart, d: Abrechnungsdaten, segs, anteile, w) -> KostenartKontrolle:
    k = KostenartKontrolle(ka.id, ka.bezeichnung, ka.pool, ka.schluessel, NULL, NULL, NULL, NULL, NULL, [],
                           d.vorjahr.get(ka.id), ka.kategorie)
    netto_vollstaendig = True
    mvs = {m.id: m for m in d.mietverhaeltnisse}
    for md in d.messdienst:
        for mw in md.werte:
            if mw.kostenart != ka.id:
                continue
            k.gesamt_brutto += mw.brutto
            if mw.netto is None:
                netto_vollstaendig = False
            else:
                k.gesamt_netto += mw.netto
            herkunft = [{"messdienst": md.id, "anbieter": md.anbieter, "sha256": md.dokument_sha256,
                         "einheit": mw.einheit, "brutto": str(mw.brutto)}]
            if mw.mietverhaeltnis == "leerstand":
                ziele = {("leer", mw.einheit): Decimal(1)}
            elif mw.mietverhaeltnis:
                if mw.mietverhaeltnis not in mvs:
                    w.append(Warnung("rot", "messwert_mv_unbekannt", f"Messdienstwert für unbekanntes Mietverhältnis {mw.mietverhaeltnis}.", md.id))
                    continue
                ziele = {("mv", mw.mietverhaeltnis): Decimal(1)}
            else:
                ziele = defaultdict(Decimal)
                for s in segs:
                    if s.einheit == mw.einheit:
                        ziele[s.empfaenger] += Decimal(s.zeitraum.tage)
                if not ziele:
                    w.append(Warnung("rot", "messwert_einheit_unbekannt", f"Messdienstwert für Einheit {mw.einheit} ohne Zeitabschnitt.", md.id))
                    continue
                if len(ziele) > 1:
                    w.append(Warnung("gelb", "zwischenablesung_fehlt",
                                     f"{ka.bezeichnung} {mw.einheit}: Nutzerwechsel ohne Zwischenablesung, zeitanteilig nach Tagen aufgeteilt. Nach Heizkostenverordnung prüfen.", mw.einheit))
            reihe = list(ziele)
            vb, exakt, zuschlag = verteile_nachweis(mw.brutto, ziele, reihe)
            vn = verteile(mw.netto, ziele, reihe) if mw.netto is not None else {}
            k.schritte.append({
                "gruppe": mw.einheit, "messdienst": md.id, "anbieter": md.anbieter, "sha256": md.dokument_sha256,
                "betrag_brutto": mw.brutto, "betrag_netto": mw.netto,
                "gewicht": "Messwert" if len(ziele) == 1 else "Tage (ohne Zwischenablesung)",
                "summe_gewichte": sum(ziele.values(), Decimal(0)), "bezugsgroesse_jahr": None,
                "anteile": _schritt_anteile(ziele, reihe, vb, vn, exakt, zuschlag),
            })
            for e in reihe:
                neu = Anteil(vb[e], vn.get(e), vb[e], NULL, mw.brutto, mw.netto, NULL, herkunft)
                alt = anteile[e].get(ka.id)
                anteile[e][ka.id] = alt.plus(neu) if alt else neu
            k.verteilt_brutto += sum(vb.values(), NULL)
            k.leerstand_brutto += sum((v for e, v in vb.items() if e[0] == "leer"), NULL)
            if mw.netto is not None:
                k.verteilt_netto += sum(vn.values(), NULL)
            k.belege.append(md.id)
    if not netto_vollstaendig:
        k.gesamt_netto = k.verteilt_netto = None
    return k


def _darstellung(m: Mietverhaeltnis, nutzung: str, w: list[Warnung]) -> str:
    s = m.steuer
    if nutzung == "wohnen":
        if s and s.behandlung == "steuerpflichtig":
            w.append(Warnung("rot", "wohnen_mit_ust", f"{m.id}: Wohnraummietverhältnis ist als umsatzsteuerpflichtig eingerichtet. Bitte prüfen.", m.id))
        return "wohnen"
    if s is None or s.behandlung == "ungeklaert":
        w.append(Warnung("rot", "steuer_ungeklaert", f"{m.id}: Umsatzsteuerliche Behandlung des Gewerbemietverhältnisses ist ungeklärt. Abrechnung gesperrt.", m.id))
        return "gesperrt"
    if not s.bestaetigt:
        w.append(Warnung("rot", "steuer_unbestaetigt", f"{m.id}: Steuerbehandlung ohne Bestätigung (wer, wann). Abrechnung gesperrt.", m.id))
        return "gesperrt"
    if s.behandlung == "steuerfrei":
        return "gewerbe_brutto"
    if s.satz is None or s.satz <= 0:
        w.append(Warnung("rot", "steuersatz_fehlt", f"{m.id}: Steuerpflicht ohne Steuersatz. Abrechnung gesperrt.", m.id))
        return "gesperrt"
    return "gewerbe_netto"


def _mv_ergebnis(m: Mietverhaeltnis, d: Abrechnungsdaten, segs, anteile, w) -> MvErgebnis:
    eigene = [s for s in segs if s.empfaenger == ("mv", m.id)]
    nutzungen = {s.nutzung for s in eigene}
    nutzung = "gewerbe" if "gewerbe" in nutzungen else "wohnen"
    if len(nutzungen) > 1:
        w.append(Warnung("rot", "nutzung_wechselt", f"{m.id}: Nutzung wechselt im Mietverhältnis. Bitte als getrennte Abschnitte prüfen.", m.id))
    darstellung = _darstellung(m, nutzung, w)
    s = m.steuer
    nz = m.zeitraum.schnitt(d.zeitraum)
    zeilen = []
    for ka in d.kostenarten:
        a = anteile[("mv", m.id)].get(ka.id)
        if a is None:
            continue
        z = Zeile(ka.id, ka.bezeichnung, ka.schluessel, ka.heizkosten, a.gesamt_brutto, a.gesamt_netto, a.bezug_gesamt,
                  a.bezug, a.brutto, a.netto, a.exakt, herkunft=a.herkunft)
        if darstellung == "gewerbe_netto":
            basis = a.netto if s.kostenbasis == "netto" else a.brutto
            if basis is None:
                w.append(Warnung("rot", "netto_fehlt", f"{m.id}: Nettobeträge für {ka.bezeichnung} fehlen. Abrechnung gesperrt.", m.id))
                darstellung = "gesperrt"
                basis = a.brutto
            z.betrag = basis
            z.ust = NULL if ka.id in s.ohne_ust else cent(basis * s.satz)
        else:
            z.betrag = a.brutto
        zeilen.append(z)
    if darstellung == "gesperrt":
        for z in zeilen:
            z.betrag, z.ust = z.anteil_brutto, NULL
    e = MvErgebnis(m.id, m.mieter, m.anschrift, m.einheit, d.einheit(m.einheit).bezeichnung, nutzung, nz, darstellung,
                   s.satz if (s and darstellung == "gewerbe_netto") else None, zeilen, [], umlageart=m.umlageart)
    if m.umlageart == "pauschale":
        w.append(Warnung("gelb", "pauschale", f"{m.id}: Betriebskostenpauschale laut Stammdaten. Kein Abrechnungsschreiben an den Mieter; "
                                              "Kosten nur zur internen Übersicht.", m.id))
    e.kosten = sum((z.betrag for z in zeilen), NULL)
    e.kosten_ust = sum((z.ust for z in zeilen), NULL)
    e.kosten_brutto = e.kosten + e.kosten_ust
    e.rundung = sum((z.anteil_brutto for z in zeilen), NULL) - sum((z.exakt_brutto for z in zeilen), Decimal(0)).quantize(GENAU)
    if darstellung == "gewerbe_netto":
        e.vorsteueranteil_info = sum((z.anteil_brutto - z.anteil_netto for z in zeilen if z.anteil_netto is not None), NULL)
    for art in ("betriebskosten", "heizkosten"):
        vz = [v for v in m.vorauszahlungen if v.art == art]
        if not vz:
            continue
        v = vz[0]
        if darstellung == "gewerbe_netto":
            if v.netto is None or v.ust is None:
                w.append(Warnung("rot", "vz_netto_fehlt", f"{m.id}: Vorauszahlungen {art} ohne Netto/USt-Aufteilung.", m.id))
                betrag, ust = v.brutto, NULL
            else:
                if v.netto + v.ust != v.brutto:
                    w.append(Warnung("rot", "vz_summe", f"{m.id}: Vorauszahlungen {art}: netto + USt ergibt nicht brutto.", m.id))
                betrag, ust = v.netto, v.ust
        else:
            betrag, ust = v.brutto, NULL
        e.vorauszahlungen.append(Vorauszahlungen(art, betrag, ust, betrag + ust))
    if not m.vorauszahlungen:
        w.append(Warnung("gelb", "vz_fehlen", f"{m.id}: keine Vorauszahlungen erfasst.", m.id))
    e.vz = sum((v.betrag for v in e.vorauszahlungen), NULL)
    e.vz_ust = sum((v.ust for v in e.vorauszahlungen), NULL)
    e.vz_brutto = e.vz + e.vz_ust
    e.saldo = e.kosten - e.vz
    e.saldo_ust = e.kosten_ust - e.vz_ust
    e.saldo_brutto = e.saldo + e.saldo_ust
    return e
