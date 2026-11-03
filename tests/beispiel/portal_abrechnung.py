"""Erfundene Abrechnungsdaten für Portaltests (Befund H2: Beispieldaten unter tests/beispiel/)."""


def toml_jahr(jahr: int, kennung, grundsteuer: str = "1000.00", heizung: str = "900.00", von: str | None = None) -> bytes:
    """Erfundene Abrechnungsdaten für das Portalhaus; `kennung` ist die Objektkennung aus dem Portal."""
    return f'''
[objekt]
portal_id = "{kennung}"
name = "Portalhaus (fiktiv)"
anschrift = "Portalweg 1, 12345 Musterstadt"
vermieter = "Erika Eigentümerin (fiktiv)"
[abrechnung]
von = {von or f"{jahr}-01-01"}
bis = {jahr}-12-31
[[einheiten]]
id = "WE1"
abschnitte = [{{ nutzung = "wohnen", flaeche = "60" }}]
[[einheiten]]
id = "WE2"
abschnitte = [{{ nutzung = "wohnen", flaeche = "40" }}]
[[mietverhaeltnisse]]
id = "MV1"
einheit = "WE1"
mieter = "Mietpartei Eins (fiktiv)"
umlageart = "vorauszahlung"
vorauszahlungen = {{ betriebskosten = "480.00", heizkosten = "480.00" }}
vz_monatlich = {{ betriebskosten = "40.00", heizkosten = "40.00" }}
[[mietverhaeltnisse]]
id = "MV2"
einheit = "WE2"
mieter = "Mietpartei Zwei (fiktiv)"
umlageart = "vorauszahlung"
vorauszahlungen = {{ betriebskosten = "480.00", heizkosten = "360.00" }}
vz_monatlich = {{ betriebskosten = "40.00", heizkosten = "30.00" }}
[[kostenarten]]
id = "grundsteuer"
bezeichnung = "Grundsteuer"
schluessel = "flaeche"
umlagefaehig_bestaetigt = true
[[kostenarten]]
id = "heizung"
bezeichnung = "Heizkosten"
schluessel = "messdienst"
heizkosten = true
kategorie = "heizung"
umlagefaehig_bestaetigt = true
[[belege]]
id = "GS{jahr}"
lieferant = "Stadtkasse"
rechnungsnummer = "GS-{jahr}"
positionen = [{{ kostenart = "grundsteuer", brutto = "{grundsteuer}" }}]
[[messdienst]]
id = "MD{jahr}"
anbieter = "Messdienst Beispiel"
gesamt_brutto = "{heizung}"
werte = [{{ einheit = "WE1", kostenart = "heizung", brutto = "500.00" }}, {{ einheit = "WE2", kostenart = "heizung", brutto = "{int(heizung.split('.')[0]) - 500}.00" }}]
'''.encode()
