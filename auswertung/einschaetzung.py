"""Erste Einschätzung je Objekt und Jahr aus Kontoauszügen und Stammdaten.

Alle Ergebnisse sind vorläufig („zu prüfen“). Erst bestätigte Werte dürfen in
Abrechnung oder Prüfbericht übernommen werden.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from .modell import Auszug, Buchung, eur, iban_kurz
from .stammdaten import Einheit, Stammdaten

NULL = Decimal("0.00")
NICHT_ZUGEORDNET = "Nicht zugeordnet"


@dataclass
class HausgeldZeile:
    einheit: str
    eigentuemer: str
    soll_monat: Decimal
    ist: list[Decimal]  # 12 Monate
    status: list[str]  # ok | teil | mehr | doppelt | fehlt

    @property
    def soll_jahr(self) -> Decimal:
        return self.soll_monat * 12

    @property
    def ist_jahr(self) -> Decimal:
        return sum(self.ist, NULL)

    @property
    def abweichung(self) -> Decimal:
        return self.ist_jahr - self.soll_jahr


@dataclass
class Gruppe:
    name: str
    summe: Decimal = NULL
    anzahl: int = 0
    empfaenger: dict[str, Decimal] = field(default_factory=dict)

    def groesste(self, n: int = 3) -> list[tuple[str, Decimal]]:
        return sorted(self.empfaenger.items(), key=lambda x: x[1])[:n]


@dataclass
class KontoStand:
    iban: str
    art: str
    anfang: Decimal | None
    ende: Decimal | None
    fehlende_monate: list[int]
    kettenfehler: list[str]


@dataclass
class Ergebnis:
    objekt: str
    jahr: int
    anzahl_buchungen: int
    hausgeld: list[HausgeldZeile]
    gruppen: list[Gruppe]
    konten: list[KontoStand]
    offene_eingaenge: list[Buchung]
    offene_ausgaenge: list[Buchung]
    hinweise: list[tuple[str, str]]  # (Schwere, Text): kritisch | pruefen | info


_WE = re.compile(r"\b(?:WE|Whg\.?|Wohnung|Einheit)\s*(?:Nr\.?\s*)?0*(\d+)\b", re.IGNORECASE)


def _einheit_fuer_eingang(b: Buchung, sd: Stammdaten) -> Einheit | None:
    for e in sd.einheiten:
        eig = e.eigentuemer_am(b.datum)
        if eig and b.gegen_iban and b.gegen_iban in eig.ibans:
            return e
    treffer = _WE.search(b.zweck)
    if treffer:
        nr = treffer.group(1)
        for e in sd.einheiten:
            if re.sub(r"\D", "", e.nummer).lstrip("0") == nr:
                return e
    return None


def _gruppe_fuer_ausgang(b: Buchung, sd: Stammdaten) -> str:
    text = f"{b.gegen_name} {b.zweck}".lower()
    for g in sd.kostengruppen:
        if any(m in text for m in g.muster):
            return g.gruppe
    return NICHT_ZUGEORDNET


def _status(ist: Decimal, soll: Decimal) -> str:
    if ist == 0:
        return "fehlt"
    if soll and ist >= 2 * soll:
        return "doppelt"
    if ist > soll:
        return "mehr"
    if ist < soll:
        return "teil"
    return "ok"


MONATE = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]


def _konten(auszuege: list[Auszug], sd: Stammdaten) -> list[KontoStand]:
    nach_konto: dict[str, list[Auszug]] = defaultdict(list)
    for a in auszuege:
        nach_konto[a.konto_iban].append(a)
    staende = []
    for iban in list(sd.konten) + [i for i in nach_konto if i not in sd.konten]:
        liste = sorted(nach_konto.get(iban, []), key=lambda a: (a.von or a.bis))
        abgedeckt: set[int] = set()
        fehler: list[str] = []
        for a in liste:
            if a.von and a.bis:
                m, j = a.von.month, a.von.year
                while (j, m) <= (a.bis.year, a.bis.month):
                    if j == sd.jahr:
                        abgedeckt.add(m)
                    m, j = (1, j + 1) if m == 12 else (m + 1, j)
            if a.anfangssaldo is not None and a.endsaldo is not None:
                summe = sum((b.betrag for b in a.buchungen), NULL)
                if a.anfangssaldo + summe != a.endsaldo:
                    fehler.append(
                        f"{a.quelle}: Anfangssaldo + Buchungen ergibt {eur(a.anfangssaldo + summe)}, Endsaldo ist {eur(a.endsaldo)}"
                    )
        for vorher, nachher in zip(liste, liste[1:]):
            if vorher.endsaldo is not None and nachher.anfangssaldo is not None and vorher.endsaldo != nachher.anfangssaldo:
                fehler.append(
                    f"Saldenkette unterbrochen zwischen {vorher.quelle} (Endsaldo {eur(vorher.endsaldo)}) und {nachher.quelle} (Anfangssaldo {eur(nachher.anfangssaldo)})"
                )
        im_jahr = [a for a in liste if (a.von or a.bis) and (a.von or a.bis).year <= sd.jahr <= (a.bis or a.von).year]
        anfang = next((a.anfangssaldo for a in im_jahr if a.anfangssaldo is not None), None)
        ende = next((a.endsaldo for a in reversed(im_jahr) if a.endsaldo is not None), None)
        staende.append(
            KontoStand(iban, sd.konten.get(iban, "nicht in Stammdaten"), anfang, ende,
                       [m for m in range(1, 13) if m not in abgedeckt], fehler)
        )
    return staende


def erstelle_einschaetzung(auszuege: list[Auszug], sd: Stammdaten) -> Ergebnis:
    eigene = set(sd.konten)
    alle = [b for a in auszuege for b in a.buchungen if b.datum.year == sd.jahr]
    # Umbuchungen zwischen eigenen Konten (z. B. Zuführung Rücklage) sind weder Hausgeld noch Ausgabe.
    relevant = [b for b in alle if b.gegen_iban not in eigene]

    ist: dict[str, list[Decimal]] = {e.nummer: [NULL] * 12 for e in sd.einheiten}
    offene_ein: list[Buchung] = []
    gruppen: dict[str, Gruppe] = {}
    offene_aus: list[Buchung] = []

    for b in relevant:
        if b.ist_eingang:
            e = _einheit_fuer_eingang(b, sd)
            if e is None:
                offene_ein.append(b)
            else:
                ist[e.nummer][b.datum.month - 1] += b.betrag
        else:
            name = _gruppe_fuer_ausgang(b, sd)
            g = gruppen.setdefault(name, Gruppe(name))
            g.summe += b.betrag
            g.anzahl += 1
            empf = b.gegen_name or "(ohne Namen)"
            g.empfaenger[empf] = g.empfaenger.get(empf, NULL) + b.betrag
            if name == NICHT_ZUGEORDNET:
                offene_aus.append(b)

    hausgeld = []
    hinweise: list[tuple[str, str]] = []
    for e in sd.einheiten:
        werte = ist[e.nummer]
        status = [_status(v, e.hausgeld_soll) for v in werte]
        namen = []
        for eig in e.eigentuemer:
            if eig.name not in namen:
                namen.append(eig.name)
        zeile = HausgeldZeile(e.nummer, " / ".join(namen), e.hausgeld_soll, werte, status)
        hausgeld.append(zeile)
        for i, s in enumerate(status):
            if s == "fehlt":
                hinweise.append(("kritisch", f"{e.nummer}: Hausgeld {MONATE[i]} nicht eingegangen."))
            elif s == "doppelt":
                hinweise.append(("pruefen", f"{e.nummer}: Hausgeld {MONATE[i]} mehrfach gezahlt ({eur(werte[i])})."))
            elif s == "teil":
                hinweise.append(("pruefen", f"{e.nummer}: Teilzahlung {MONATE[i]} ({eur(werte[i])} statt {eur(e.hausgeld_soll)})."))
        if len(e.eigentuemer) > 1:
            hinweise.append(("info", f"{e.nummer}: Eigentümerwechsel im Jahr, Zuordnung nach Zahlungsdatum."))

    konten = _konten(auszuege, sd)
    for k in konten:
        if k.fehlende_monate:
            monate = ", ".join(MONATE[m - 1] for m in k.fehlende_monate)
            hinweise.append(("kritisch", f"{k.art} {iban_kurz(k.iban)}: keine Auszüge für {monate}."))
        for f in k.kettenfehler:
            hinweise.append(("kritisch", f"{k.art} {iban_kurz(k.iban)}: {f}"))
    if offene_ein:
        hinweise.append(("pruefen", f"{len(offene_ein)} Eingänge keiner Einheit zugeordnet."))
    if offene_aus:
        hinweise.append(("pruefen", f"{len(offene_aus)} Ausgaben keiner Kostengruppe zugeordnet."))

    reihenfolge = [g.gruppe for g in sd.kostengruppen] + [NICHT_ZUGEORDNET]
    gruppen_liste = sorted(gruppen.values(), key=lambda g: reihenfolge.index(g.name) if g.name in reihenfolge else 99)
    schwere = {"kritisch": 0, "pruefen": 1, "info": 2}
    hinweise.sort(key=lambda h: schwere[h[0]])
    return Ergebnis(sd.objekt, sd.jahr, len(alle), hausgeld, gruppen_liste, konten, offene_ein, offene_aus, hinweise)
