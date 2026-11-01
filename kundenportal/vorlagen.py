"""Checklisten-Vorlagen je Leistung (Entwurf, von Matthias fachlich freizugeben).

Jeder Eintrag: (Text, Kategorie). Die Vorlage wird beim Anlegen eines Auftrags
kopiert und lässt sich danach je Auftrag ändern.
"""

VORLAGEN: dict[str, list[tuple[str, str]]] = {
    "weg_jahresabrechnung": [
        ("Kontoauszüge Girokonto, Januar bis Dezember", "bank"),
        ("Kontoauszüge Rücklagenkonto, Januar bis Dezember", "bank"),
        ("Eigentümer- und Einheitenliste mit Miteigentumsanteilen", "stammdaten"),
        ("Wirtschaftsplan", "abrechnungen"),
        ("Jahresabrechnung Vorjahr", "abrechnungen"),
        ("Rechnungen", "buchhaltung"),
        ("Beschlusssammlung", "rechtliches"),
    ],
    "betriebskosten": [
        ("Kontoauszüge, Januar bis Dezember", "bank"),
        ("Mieterliste mit Wohnflächen und Vorauszahlungen", "stammdaten"),
        ("Mietverträge mit Umlagevereinbarung", "rechtliches"),
        ("Rechnungen der Versorger und Dienstleister", "buchhaltung"),
        ("Heizkostenabrechnung des Messdienstes", "abrechnungen"),
        ("Grundsteuerbescheid", "sonstiges"),
    ],
    "rechnungspruefung": [
        ("Kontoauszüge aller Konten, Januar bis Dezember", "bank"),
        ("Rechnungen", "buchhaltung"),
        ("Buchungsliste der Verwaltung", "buchhaltung"),
        ("Wirtschaftsplan", "abrechnungen"),
        ("Beschlusssammlung", "rechtliches"),
    ],
    "sonderpruefung": [
        ("Kontoauszüge aller Konten für den Prüfzeitraum", "bank"),
        ("Jahresabrechnungen und Wirtschaftspläne für den Prüfzeitraum", "abrechnungen"),
        ("Rechnungen und Belege", "buchhaltung"),
        ("Verträge (Verwaltung, Dienstleister, Versicherungen)", "rechtliches"),
        ("Beschlusssammlung und Protokolle", "rechtliches"),
        ("Beschreibung des Anlasses der Prüfung", "sonstiges"),
    ],
    "ankauf": [
        ("Exposé", "sonstiges"),
        ("Teilungserklärung", "rechtliches"),
        ("Protokolle der letzten drei Eigentümerversammlungen", "rechtliches"),
        ("Wirtschaftsplan und letzte Jahresabrechnung", "abrechnungen"),
        ("Energieausweis", "stammdaten"),
    ],
}
