"""Mode Simple vers DATEV par la Supplier API : factures d'ACHAT (Eingangsrechnungen), liste des fournisseurs.

Fichiers INVENTÉS (aucune donnée réelle, distincts de la vérité terrain du domaine 1) qui reproduisent ses
pièges : fournisseur témoin, même numéro de facture chez deux fournisseurs, saisie double, 7 %, 0 % d'un
bailleur et d'un fournisseur d'un autre pays de l'UE, brut incohérent, 16 %, avoir, fournisseur inconnu,
numéro manquant, catégorie de charge vide, facture de l'exercice précédent. Specs DATEV LOCALES (module sauté
si elles sont absentes). Aucun LLM, rien n'est envoyé à DATEV."""

from __future__ import annotations

import json
from typing import Any

from test_wizard_datev_api import (  # noqa: F401 — fixture et marqueur partagés
    _by,
    _download,
    _flow,
    _rows,
    _target,
    pytestmark,
    world,
)

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
    "M-0926;01.09.2026;3003;Hausverwaltung Ost GbR;Miete;1500,00;0;0,00;1500,00;\n"
    "Miete 08/2026;01.08.2026;3003;Hausverwaltung Ost GbR;Miete;1500,00;0;0,00;1500,00;\n"
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


def _suppliers_exported(c: Any) -> None:
    flow = _flow(c, "Debitoren/Kreditoren 5", SUPPLIERS, "Lieferanten.csv")
    assert _download(c, flow).status_code == 200


def test_a_purchase_invoice_is_booked_on_the_creditor_account_of_its_supplier(world):  # noqa: F811
    """Fournisseurs exportés d'abord (Kreditor = 70000 + Lief-Nr.), puis la facture témoin : Konto = le
    Kreditor du fournisseur, au crédit (H)."""
    c = world["client"]
    _suppliers_exported(c)
    accounts = {
        "map:CATEGORY:purchase:strom:19": "4240",  # catégorie inconnue de la table : saisie par l'utilisateur
        "map:TAX:rate:PURCHASE:19": "none",
        "map:TAX:rate:PURCHASE:7": "none",
        "setting:lock_bookings": "0",
    }
    flow = _flow(c, "Buchungsstapel 13", PURCHASES, "Rechnungseingang.csv", accounts)
    res = _download(c, flow)
    assert res.status_code == 200, res.text
    _, rows = _rows(res.content)
    witness = _by(rows, 11, "H-100")
    # Umsatz, Soll/Haben, Konto (Kreditor), Gegenkonto, Belegdatum, Belegfeld 1
    assert [witness[0], witness[1], witness[6], witness[7], witness[9], witness[10]] == [
        "1190,00",
        '"H"',
        "73001",
        "3400",
        "2809",
        '"H-100"',
    ]


def test_the_expense_account_is_preselected_by_category_never_guessed_and_remembered(world):  # noqa: F811
    """Kostenart → compte : présélection d'usage (table SKR03, à valider) ; catégorie inconnue sans
    présélection ; catégorie vide : facture non exportée ; la réponse est mémorisée pour le dossier."""
    c = world["client"]
    _suppliers_exported(c)
    first = _flow(c, "Buchungsstapel 13", PURCHASES, "Rechnungseingang.csv", {"setting:lock_bookings": "0"})
    start = first["start"]
    every = {q["decision"]: q for q in start["questions"] + start["applied"]}
    wares = every["map:CATEGORY:purchase:wareneinkauf:19"]
    assert (wares["default"], wares["default_from"]) == ("3400", "usual_default")
    assert "to be confirmed by your tax advisor" in wares["question"]["help"]
    assert every["map:CATEGORY:purchase:wareneinkauf:7"]["default"] == "3300"
    assert every["map:CATEGORY:purchase:strom:19"]["default"] is None  # inconnue : jamais devinée
    reasons = {e["row"]: e["reason"] for e in first["preview"]["excluded_rows"]}
    assert (
        reasons[15]
        == "Invoice H-103: no expense category (Kostenart): its account is never guessed, not exported."
    )
    # téléchargement avec la réponse de l'utilisateur, puis un nouveau fichier : réponse reprise
    extra = {
        "map:CATEGORY:purchase:strom:19": "4240",
        "map:TAX:rate:PURCHASE:19": "none",
        "map:TAX:rate:PURCHASE:7": "none",
        "setting:lock_bookings": "0",
    }
    flow = _flow(c, "Buchungsstapel 13", PURCHASES, "Rechnungseingang.csv", extra)
    assert _download(c, flow).status_code == 200
    again = _flow(c, "Buchungsstapel 13", PURCHASES, "Rechnungseingang-2.csv")["start"]
    strom = next(
        q for q in again["questions"] + again["applied"] if q["decision"] == "map:CATEGORY:purchase:strom:19"
    )
    assert (strom["default"], strom["default_from"]) == ("4240", "your_settings")


def test_input_tax_keys_follow_the_account_and_a_domestic_purchase_without_vat_is_exported(world):  # noqa: F811
    """Clé de Vorsteuer présélectionnée par taux (9 : 19 %, 8 : 7 %), jamais sur un compte automatique
    (3400, 3300) ; loyer à 0 % d'un bailleur allemand : charge sans TVA déductible, sans question."""
    c = world["client"]
    _suppliers_exported(c)
    flow = _flow(
        c,
        "Buchungsstapel 13",
        PURCHASES,
        "Rechnungseingang.csv",
        {"map:CATEGORY:purchase:strom:19": "4240", "setting:lock_bookings": "0"},
    )
    start = flow["start"]
    every = {q["decision"]: q["default"] for q in start["questions"] + start["applied"]}
    # clés de Vorsteuer sur 4 chiffres (format Buchungsstapel 13)
    assert every["map:TAX:rate:PURCHASE:19"] == "0009" and every["map:TAX:rate:PURCHASE:7"] == "0008"
    assert every["map:TAX:rate:PURCHASE:0"] == "none"
    assert "accounting:zero_rate" not in every  # jamais la question des livraisons intracommunautaires
    res = _download(c, flow)
    assert res.status_code == 200, res.text
    _, rows = _rows(res.content)

    def row(number: str, konto: str) -> list[str]:
        return next(r for r in rows if r[10] == f'"{number}"' and r[6] == konto)

    # Konto, Gegenkonto, BU-Schlüssel
    assert [row("H-100", "73001")[i] for i in (6, 7, 8)] == [
        "73001",
        "3400",
        '""',
    ]  # automatique : aucune clé
    assert [row("H-101", "73001")[i] for i in (6, 7, 8)] == ["73001", "3300", '""']
    assert [row("500", "73002")[i] for i in (6, 7, 8)] == ["73002", "4240", '"0009"']
    assert [row("M-0926", "73003")[i] for i in (0, 7, 8)] == ["1500,00", "4210", '""']
    # un numéro avec une espace n'est pas un Belegfeld 1 DATEV : refusé avec son motif, jamais corrigé
    reasons = {e["row"]: e["reason"] for e in flow["preview"]["excluded_rows"]}
    assert "Invoice Miete 08/2026: Belegfeld 1 does not have the format expected" in reasons[7]


def test_purchases_from_another_country_are_left_to_the_vat_domain_and_flagged(world):  # noqa: F811
    """0 % d'un fournisseur autrichien ou espagnol : relève de la TVA des achats à l'étranger (domaine 2), non
    exporté, jamais une question d'exonération ; fournisseur de l'UE sans USt-IdNr signalé dans la liste."""
    c = world["client"]
    suppliers = _flow(c, "Debitoren/Kreditoren 5", SUPPLIERS, "Lieferanten.csv")
    flagged = [i for i in suppliers["preview"]["value_issues"] if i["kind"] == "vat_missing_eu"]
    assert len(flagged) == 1 and flagged[0]["rows"] == [5]
    assert flagged[0]["message"].startswith("1 supplier from another EU country has no VAT ID")
    assert _download(c, suppliers).status_code == 200
    flow = _flow(c, "Buchungsstapel 13", PURCHASES, "Rechnungseingang.csv", {"setting:lock_bookings": "0"})
    reasons = {e["row"]: (e["kind"], e["reason"]) for e in flow["preview"]["excluded_rows"]}
    assert reasons[8] == (
        "vat_abroad",
        "Invoice AT-1: 0 % VAT from Farben Graz GmbH (AT): VAT on purchases from another country "
        "(intra-community acquisition, reverse charge) is not supported yet, not exported.",
    )
    assert reasons[9][0] == "vat_abroad" and "(ES)" in reasons[9][1]


def test_the_period_covers_only_the_months_written_and_last_year_is_refused(world):  # noqa: F811
    """Facture de l'exercice précédent : refusée avec son motif ; un mois dont les factures sont toutes
    bloquées à la préparation DATEV (juillet : catégorie sans compte) n'entre pas dans la période de l'en-tête
    (Datum von / bis)."""
    c = world["client"]
    _suppliers_exported(c)
    journal = (
        "Beleg-Nr. Lieferant;Rechnungsdatum;Lief-Nr.;Lieferant;Kostenart;Netto €;USt %;USt €;Brutto €\n"
        "J-1;15.07.2026;3001;Holz Großhandel GmbH;Reisekosten;100,00;19;19,00;119,00\n"
        "S-1;28.09.2026;3001;Holz Großhandel GmbH;Wareneinkauf;100,00;19;19,00;119,00\n"
        "H-90;30.12.2025;3001;Holz Großhandel GmbH;Wareneinkauf;300,00;19;57,00;357,00\n"
    )
    flow = _flow(c, "Buchungsstapel 13", journal, "Eingang.csv", {"setting:lock_bookings": "0"})
    reasons = {e["row"]: e["reason"] for e in flow["preview"]["excluded_rows"]}
    assert "outside the fiscal year starting 2026-01-01" in reasons[3]
    assert any("period 09/2026)" in n for n in flow["preview"]["notices"]), flow["preview"]["notices"]
    res = _download(c, flow)
    assert res.status_code == 200, res.text
    header, rows = _rows(res.content)
    assert (header[14], header[15]) == ("20260901", "20260930") and len(rows) == 1
