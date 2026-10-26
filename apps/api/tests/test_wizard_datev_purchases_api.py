"""Mode Simple vers DATEV par la Supplier API : factures d'ACHAT (Eingangsrechnungen), liste des fournisseurs.

Fichiers INVENTÉS (aucune donnée réelle, distincts de la vérité terrain du domaine 1) qui reproduisent ses
pièges : fournisseur témoin, même numéro de facture chez deux fournisseurs, saisie double, 7 %, 0 % d'un
bailleur et d'un fournisseur d'un autre pays de l'UE, brut incohérent, 16 %, avoir, fournisseur inconnu,
numéro manquant, catégorie de charge vide, facture de l'exercice précédent. Specs DATEV LOCALES (module sauté
si elles sont absentes). Aucun LLM, rien n'est envoyé à DATEV."""

from __future__ import annotations

import json
from typing import Any

from test_wizard_datev_api import _target, pytestmark, world  # noqa: F401 — fixture et marqueur partagés

SUPPLIERS = (
    "Lieferantenstamm Beispiel GmbH;;;;;;;;\n"
    ";;;;;;;;\n"
    "Lief-Nr.;Firma;Straße;PLZ;Ort;Land;USt-IdNr.;E-Mail;IBAN\n"
    "3001;Holz Großhandel GmbH;Werkstraße 1;01159;Dresden;Deutschland;DE111111111;holz@example.de;"
    "DE89 3704 0044 0532 0130 00\n"
    "3002;Strom Nord AG;Netzweg 2;20095;Hamburg;DE;DE222222222;strom@example.de;\n"
    "3003;Hausverwaltung Ost GbR;Ringstraße 3;1069;Dresden;Deutschland;;haus@example.de;\n"
    "3004;Farben Graz GmbH;Murgasse 4;8010;Graz;Österreich;ATU76543210;farben@example.at;\n"
    "3005;Transportes Sur S.L.;Calle Sol 5;41001;Sevilla;Spanien;;sur@example.es;\n"
)
PURCHASES = (
    "Rechnungseingangsbuch Q3 2026;;;;;;;;;\n"
    "Beleg-Nr. Lieferant;Rechnungsdatum;Lief-Nr.;Lieferant;Kostenart;Netto €;USt %;USt €;Brutto €;Bemerkung\n"
    "H-100;28.09.2026;3001;Holz Großhandel GmbH;Wareneinkauf;1000,00;19;190,00;1190,00;\n"
    "H-101;2026-09-04;3001;Holz Großhandel GmbH;Wareneinkauf;100,00;7;7,00;107,00;\n"
    "500;10.08.2026;3001;Holz Großhandel GmbH;Wareneinkauf;500,00;19;95,00;595,00;\n"
    "500;12.08.2026;3002;Strom Nord AG;Strom;800,00;19;152,00;952,00;\n"
    "500;10.08.2026;3001;Holz Großhandel GmbH;Wareneinkauf;500,00;19;95,00;595,00;doppelt erfasst\n"
    "Miete 09/2026;01.09.2026;3003;Hausverwaltung Ost GbR;Miete;1500,00;0;0,00;1500,00;\n"
    "AT-1;15.09.2026;3004;Farben Graz GmbH;Wareneinkauf;1200,00;0;0,00;1200,00;innergemeinschaftlich\n"
    "ES-1;16.09.2026;3005;Transportes Sur S.L.;Fremdleistungen;900,00;0;0,00;900,00;Reverse Charge\n"
    "S-7;18.09.2026;3002;Strom Nord AG;Strom;59,90;19;11,38;71,38;\n"
    "H-102;19.09.2026;3001;Holz Großhandel GmbH;Bürobedarf;250,00;16;40,00;290,00;\n"
    "GS-1;21.09.2026;3002;Strom Nord AG;Strom;-150,00;19;-28,50;-178,50;Gutschrift\n"
    "999;22.09.2026;;Kiosk am Markt;Bürobedarf;35,00;19;6,65;41,65;\n"
    ";23.09.2026;3002;Strom Nord AG;Strom;68,00;19;12,92;80,92;\n"
    "H-103;24.09.2026;3001;Holz Großhandel GmbH;;410,00;19;77,90;487,90;\n"
    "H-90;30.12.2025;3001;Holz Großhandel GmbH;Wareneinkauf;300,00;19;57,00;357,00;\n"
    "Summe;;;;;;;;;\n"
)


def _detect(c: Any, name: str, content: str) -> dict[str, Any]:
    targets = [_target(c, "Buchungsstapel 13"), _target(c, "Debitoren/Kreditoren 5")]
    files = {"file": (name, content.encode("cp1252"), "text/csv")}
    res = c.post("/api/wizard/detect-target", files=files, data={"payload": json.dumps({"targets": targets})})
    assert res.status_code == 200, res.text
    return res.json()


def test_a_purchase_journal_goes_to_bookings_as_purchase_invoices_without_a_question(world):  # noqa: F811
    c = world["client"]
    found = _detect(c, "Rechnungseingang.csv", PURCHASES)
    assert found == {"target": _target(c, "Buchungsstapel 13"), "data_type": "Purchase invoices"}
    assert _detect(c, "Lieferanten.csv", SUPPLIERS)["target"] == _target(c, "Debitoren/Kreditoren 5")
    files = {"file": ("Rechnungseingang.csv", PURCHASES.encode("cp1252"), "text/csv")}
    start = c.post(
        "/api/wizard/quick-start",
        files=files,
        data={"payload": json.dumps({"target": found["target"]})},
    )
    assert start.status_code == 200, start.text
    body = start.json()
    # le type est reconnu par les règles, jamais demandé
    assert (body["data_type"], body["data_type_from"]) == ("Purchase invoices", "rules")
    assert "data_type" not in {q["decision"] for q in body["questions"]}


def test_every_column_of_a_purchase_journal_is_known_by_the_rules(world):  # noqa: F811
    """Aucune colonne laissée à l'IA : numéro de facture du fournisseur, numéro et nom du fournisseur,
    catégorie de charge, montants, et la remarque (reconnue, jamais exportée ni lue pour décider)."""
    c = world["client"]
    files = {"file": ("Rechnungseingang.csv", PURCHASES.encode("cp1252"), "text/csv")}
    start = c.post(
        "/api/wizard/quick-start",
        files=files,
        data={"payload": json.dumps({"target": _target(c, "Buchungsstapel 13")})},
    )
    assert start.status_code == 200, start.text
    columns = {col["header"]: (col["target"], col["status"]) for col in start.json()["mapping"]["columns"]}
    assert columns == {
        "Beleg-Nr. Lieferant": ("document_number", "matched"),
        "Rechnungsdatum": ("document_date", "matched"),
        "Lief-Nr.": ("supplier_number", "matched"),
        "Lieferant": ("partner_name", "matched"),
        "Kostenart": ("expense_category", "matched"),
        "Netto €": ("net_amount", "matched"),
        "USt %": ("tax_rate", "matched"),
        "USt €": ("tax_amount", "matched"),
        "Brutto €": ("gross_amount", "matched"),
        "Bemerkung": ("remark", "matched"),
    }
