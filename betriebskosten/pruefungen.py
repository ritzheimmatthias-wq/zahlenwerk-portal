"""Abstimmungsprüfungen nach der Rechnung (Summen, Pools, Messdienst, Vorjahr)."""
from __future__ import annotations

from decimal import Decimal

from .modell import NULL


def pruefe(erg) -> None:
    from .rechnen import Warnung

    w, d = erg.warnungen, erg.daten
    gesamt_pools = NULL
    for k in erg.kontrolle:
        gesamt_pools += k.gesamt_brutto
        if k.verteilt_brutto != k.gesamt_brutto:
            w.append(Warnung("rot", "summe_pool", f"{k.bezeichnung}: verteilt {k.verteilt_brutto} statt {k.gesamt_brutto} (brutto).", k.kostenart))
        if k.gesamt_netto is not None and k.verteilt_netto != k.gesamt_netto:
            w.append(Warnung("rot", "summe_pool_netto", f"{k.bezeichnung}: verteilt {k.verteilt_netto} statt {k.gesamt_netto} (netto).", k.kostenart))
        if k.vorjahr:
            abw = (k.gesamt_brutto - k.vorjahr) / k.vorjahr
            if abs(abw) > d.vorjahr_schwelle:
                w.append(Warnung("gelb", "vorjahr", f"{k.bezeichnung}: {abw * 100:+.1f} % gegenüber Vorjahr. Bitte plausibilisieren.", k.kostenart))

    verteilt = sum((z.anteil_brutto for m in erg.mietverhaeltnisse for z in m.zeilen), NULL) + sum(erg.leerstand.values(), NULL)
    if verteilt != gesamt_pools:
        w.append(Warnung("rot", "summe_gesamt", f"Summe aller Anteile {verteilt} weicht von den Gesamtkosten {gesamt_pools} ab."))

    for m in erg.mietverhaeltnisse:
        if sum((z.betrag for z in m.zeilen), NULL) != m.kosten or m.saldo != m.kosten - m.vz:
            w.append(Warnung("rot", "summe_mv", f"{m.id}: Summen oder Saldo nicht schlüssig.", m.id))
        if m.saldo_brutto != m.kosten_brutto - m.vz_brutto:
            w.append(Warnung("rot", "summe_mv_brutto", f"{m.id}: Bruttosaldo nicht schlüssig.", m.id))
        if abs(m.rundung) >= Decimal("0.01") * max(1, len(m.zeilen)):
            w.append(Warnung("gelb", "rundung", f"{m.id}: Rundungsdifferenz {m.rundung} ungewöhnlich hoch.", m.id))
        for z in m.zeilen:
            ka = d.kostenart(z.kostenart)
            if ka.pool != "alle" and ka.pool != m.nutzung:
                w.append(Warnung("rot", "pool_vermischt", f"{m.id}: {ka.bezeichnung} gehört zum Pool {ka.pool}, das Mietverhältnis ist {m.nutzung}.", m.id))
        if m.nutzung == "wohnen" and (m.kosten_ust != NULL or m.vz_ust != NULL):
            w.append(Warnung("rot", "wohnen_ust", f"{m.id}: Wohnraumabrechnung enthält Umsatzsteuer.", m.id))

    for md in d.messdienst:
        summe = sum((x.brutto for x in md.werte), NULL)
        if summe != md.gesamt_brutto:
            w.append(Warnung("rot", "messdienst_summe", f"Messdienst {md.id}: Einzelwerte {summe} ≠ Gesamtbetrag {md.gesamt_brutto}.", md.id))
    for ka in d.kostenarten:
        if ka.schluessel != "messdienst":
            continue
        belege = sum((p.brutto for p in d.positionen if p.kostenart == ka.id), NULL)
        werte = sum((x.brutto for md in d.messdienst for x in md.werte if x.kostenart == ka.id), NULL)
        if belege and belege != werte:
            w.append(Warnung("gelb", "messdienst_belege", f"{ka.bezeichnung}: Belege {belege} und Messdienstwerte {werte} unterscheiden sich. Abgleich mit der Messdienstabrechnung prüfen.", ka.id))
        if not werte:
            w.append(Warnung("rot", "messdienst_fehlt", f"{ka.bezeichnung}: keine Messdienstwerte übernommen.", ka.id))
