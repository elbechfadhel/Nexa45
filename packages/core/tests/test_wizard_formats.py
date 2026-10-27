"""Source FICHIER vers un format (DATEV) : tableau de source, constructeurs du CDM (partenaires, factures),
période d'un fichier, numérotation « décalage + numéro du client ». Données INVENTÉES."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import uie_domain_packs  # noqa: F401 - enregistre les tableaux de source et les bindings
from uie_cdm import DocumentType, PartnerKind, PartnerRole
from uie_core.accounting.target import PeriodSpan
from uie_core.domains.model import DomainKey
from uie_core.exports.service import plan_accounts
from uie_core.mapping import TargetBindingRegistry
from uie_core.sources.table import FileTableRegistry
from uie_core.wizard.cdm_accounting import documents_from_rows
from uie_core.wizard.cdm_partners import (
    DUPLICATES_KEY,
    KINDS_KEY,
    NUMBER_KEY,
    POSTAL_KEY,
    partners_from_rows,
)
from uie_core.wizard.formats import matched_keys
from uie_core.wizard.quick import infer_data_type, target_for_data_type

PARTNERS = FileTableRegistry.get(DomainKey.PARTNER)
INVOICES = FileTableRegistry.get(DomainKey.ACCOUNTING)


def test_german_headers_are_known_columns_of_the_file_tables():
    assert PARTNERS and INVOICES
    headers = [
        "Kd-Nr.",
        "Firma",
        "Ansprechpartner",
        "Straße",
        "PLZ",
        "Ort",
        "Land",
        "USt-IdNr.",
        "E-Mail",
        "Telefon",
        "IBAN",
        "Zahlungsziel",
    ]
    assert matched_keys(PARTNERS, headers) == {
        "customer_number",
        "name",
        "contact_person",
        "street",
        "postal_code",
        "city",
        "country",
        "vat_id",
        "email",
        "phone",
        "iban",
    }
    headers = ["Re-Nr.", "Re-Datum", "Kd-Nr.", "Kunde", "Netto €", "USt %", "USt €", "Brutto €", "Bemerkung"]
    assert matched_keys(INVOICES, headers) == {
        "document_number",
        "document_date",
        "partner_number",
        "partner_name",
        "net_amount",
        "tax_rate",
        "tax_amount",
        "gross_amount",
        "remark",  # reconnue (jamais envoyée à l'IA), jamais exportée
    }


def _p(number: str, name: str, **kw: str) -> dict[str, str]:
    return {"customer_number": number, "name": name, **kw}


def test_customers_are_built_without_guessing_and_every_trap_is_a_decision_or_a_signal():
    rows = [
        _p(
            "1001",
            "Muster GmbH & Co. KG",
            postal_code="01067",
            country="Deutschland",
            vat_id="DE123456789",
            iban="DE89 3704 0044 0532 0130 00",
            email="a@b.de",
            contact_person="Herr Muster",
        ),
        _p("1002", "Becker Medien UG (haftungsbeschränkt)", postal_code="1307", country="D"),
        _p("1003", "Bäckerei Groß e.K.", country="DE", vat_id="DE 135 792 468"),
        _p("1004", "Lefèvre SARL", country="Frankreich", vat_id="FR123", email="compta@lefevre"),
        _p("1005", "Krüger IT-Service", postal_code="1060", country="Österreich"),
        {"name": "Neumann Consulting"},
        _p("1007", "Mueller GmbH", vat_id="DE123456789"),
        {"customer_number": "1008"},
    ]
    t = partners_from_rows(PARTNERS, rows, {"name": "Firma"}, PartnerRole.CUSTOMER, {})
    by = {p.source_ref: p for p in t.partners}
    assert by["file:1001"].kind == PartnerKind.ORGANIZATION  # « GmbH & Co. KG »
    assert by["file:1002"].kind == PartnerKind.ORGANIZATION  # « UG (haftungsbeschränkt) »
    assert by["file:1003"].kind == PartnerKind.ORGANIZATION  # « e.K. »
    assert by["file:1004"].kind == PartnerKind.ORGANIZATION  # « SARL »
    # aucune forme juridique : question posée, « Companies » présélectionné (toujours modifiable)
    assert by["file:1005"].kind == PartnerKind.ORGANIZATION
    q = {x.key: x for x in t.questions}
    assert q[KINDS_KEY].count == 2 and q[KINDS_KEY].answer == "ORGANIZATION"  # présélection ; Krüger, Neumann
    assert (
        q[POSTAL_KEY].answer == "add"
        and q[DUPLICATES_KEY].answer == "first"
        and q[NUMBER_KEY].answer == "assign"
    )
    assert by["file:1002"].addresses[0].postal_code == "01307"  # zéro initial ajouté (présélection)
    assert by["file:1005"].addresses[0].postal_code == "1060"  # code autrichien : 4 chiffres
    assert by["file:1005"].addresses[0].country_code == "AT"
    assert by["file:1003"].tax.vat_id == "DE135792468"  # espaces retirés (transformation listée)
    assert (
        by["file:1004"].tax.vat_id is None and by["file:1004"].contact.email is None
    )  # signalés, non écrits
    assert by["file:1001"].contact.contact_person == "Herr Muster"
    assert by["file:1001"].bank_accounts[0].iban == "DE89370400440532013000"
    assert "file:row-6" in by  # sans numéro : numéro de compte libre (présélection), jamais en silence
    assert "file:1007" not in by  # même USt-IdNr que 1001 : doublon probable, non exporté
    # même USt-IdNr : doublon PROBABLE, distinct des lignes identiques
    assert [(e.row, e.kind) for e in t.skipped] == [(7, "probable_duplicate"), (8, "no_name")]
    assert t.skipped[0].reason == "Probable duplicate (same VAT ID as row 1): not exported."
    assert {i.kind for i in t.issues} == {"vat_invalid", "email_invalid"}

    answered = partners_from_rows(
        PARTNERS,
        rows,
        {},
        PartnerRole.CUSTOMER,
        {KINDS_KEY: "PERSON", POSTAL_KEY: "empty", DUPLICATES_KEY: "both", NUMBER_KEY: "skip"},
    )
    by = {p.source_ref: p for p in answered.partners}
    assert (
        by["file:1005"].kind == PartnerKind.PERSON and by["file:1005"].person.full_name == "Krüger IT-Service"
    )
    assert by["file:1002"].addresses[0].postal_code is None
    assert "file:1007" in by and "file:row-6" not in by


def _i(
    number: str, day: str, net: str, rate: str, tax: str, gross: str, partner: str = "1001"
) -> dict[str, str]:
    return {
        "document_number": number,
        "document_date": day,
        "partner_number": partner,
        "partner_name": "Muster",
        "net_amount": net,
        "tax_rate": rate,
        "tax_amount": tax,
        "gross_amount": gross,
    }


def test_invoices_are_built_without_correction_and_every_trap_is_listed():
    rows = [
        _i("RE-1", "2026-09-28", "1299", "19", "246.81", "1545.81"),
        _i("RE-2", "2026-09-24", "-250", "19", "-47.5", "-297.5"),
        _i("RE-3", "2026-09-12", "2500", "16", "400", "2900"),
        _i("RE-4", "2026-09-20", "1000", "19", "190", "1180"),
        _i("RE-5", "2026-07-14", "100", "19", "19", "119"),
        _i("RE-5", "2026-07-14", "100", "19", "19", "119"),
        _i("RE-6", "2026-07-15", "100", "19", "19", "119"),
        _i("RE-6", "2026-07-16", "200", "19", "38", "238"),
        _i("", "2026-09-25", "640", "19", "121.6", "761.6"),
        _i("RE-7", "2026-09-15", "3200", "0", "0", "3200"),
        _i("RE-8", "2026-09-21", "333.33", "19", "63.33", "396.67"),
    ]
    t = documents_from_rows(INVOICES, rows, {}, "EUR", {})
    docs = {d.number: d for d in t.documents}
    assert docs["RE-1"].document_type == DocumentType.CUSTOMER_INVOICE
    assert docs["RE-1"].lines[0].amount == Decimal("-1299")  # vente au crédit
    assert docs["RE-2"].document_type == DocumentType.CUSTOMER_CREDIT_NOTE and docs[
        "RE-2"
    ].total_amount == Decimal("297.5")
    assert docs["RE-1"].partner.source_ref == "file:1001" and docs["RE-1"].source_ref == "file:RE-1"
    assert "RE-8" in docs  # écart d'un centime : toléré (arrondi)
    reasons = {e.row: (e.kind, e.reason) for e in t.skipped}
    assert reasons[3][0] == "rate" and "16 %" in reasons[3][1]
    assert reasons[4][0] == "inconsistent"
    assert reasons[6][0] == "duplicate" and "entered twice" in reasons[6][1]  # identique : écrite une fois
    assert reasons[7][0] == reasons[8][0] == "probable_duplicate"  # même numéro, contenu différent : aucune
    assert reasons[9][0] == "missing_number"
    # 0 % pour un client pas encore exporté : son pays n'est jamais deviné, aucune question
    assert reasons[10][0] == "zero_rate" and "export your customers first" in reasons[10][1]
    assert t.questions == [] and set(t.categories) == {"sale:19"}


def test_the_period_of_a_file_covers_whole_months():
    span = PeriodSpan.covering([date(2026, 7, 5), date(2026, 9, 28), date(2026, 8, 1)])
    assert (span.first_day, span.last_day) == (date(2026, 7, 1), date(2026, 9, 30))
    assert span.label == "07/2026 bis 09/2026" and str(span) == "2026-07" and span.slug == "2026-07_2026-09"
    assert PeriodSpan.covering([date(2026, 7, 5)]).label == "07/2026"


def test_account_numbers_follow_offset_plus_customer_number_when_it_fits(session):  # noqa: ANN001
    binding = TargetBindingRegistry.find("datev", "debitoren-kreditoren")
    profile = SimpleNamespace(
        id=None,
        values={
            "account_length": 4,
            "debtor_range": {"start": 10000, "end": 69999},
            "creditor_range": {"start": 70000, "end": 99999},
            "account_offset": 10000,
        },
    )
    rows = [
        _p("1001", "Muster GmbH"),
        _p("1002", "Beispiel GmbH"),
        _p("99999", "Hors plage GmbH"),
        {"name": "Neu GmbH"},
    ]
    built = partners_from_rows(PARTNERS, rows, {}, PartnerRole.CUSTOMER, {})
    needs = [(p, PartnerRole.CUSTOMER) for p in built.partners]
    plan = plan_accounts(session, profile, binding, None, needs)  # type: ignore[arg-type]
    numbers = {ref: (n, plan.origin[(ref, role)]) for (ref, role), n in plan.numbers.items()}
    assert numbers["file:1001"] == ("11001", "OFFSET")
    assert numbers["file:1002"] == ("11002", "OFFSET")
    assert numbers["file:99999"] == ("10000", "ASSIGNED")  # 109999 hors de la plage : premier libre
    assert numbers["file:row-4"] == ("10001", "ASSIGNED")
    # jamais un repli silencieux : la règle non appliquée est dite
    assert plan.notes[("file:99999", "CUSTOMER")] == (
        "10000 + 99999 = 109999 is outside the range 10000-69999: account 10000 given instead "
        "(check the rule in your settings)"
    )
    assert ("file:1001", "CUSTOMER") not in plan.notes


def test_supplier_accounts_follow_their_own_offset(session):  # noqa: ANN001
    """Un décalage par rôle : 70000 + numéro du fournisseur (2001 → 72001), jamais celui des clients."""
    binding = TargetBindingRegistry.find("datev", "debitoren-kreditoren")
    profile = SimpleNamespace(
        id=None,
        values={
            "account_length": 4,
            "debtor_range": {"start": 10000, "end": 69999},
            "creditor_range": {"start": 70000, "end": 99999},
            "account_offset": 10000,
            "creditor_offset": 70000,
        },
    )
    rows = [
        {"supplier_number": "2001", "name": "Papier GmbH"},
        {"supplier_number": "45000", "name": "Groß AG"},
    ]
    built = partners_from_rows(PARTNERS, rows, {}, PartnerRole.SUPPLIER, {})
    needs = [(p, PartnerRole.SUPPLIER) for p in built.partners]
    plan = plan_accounts(session, profile, binding, None, needs)  # type: ignore[arg-type]
    assert plan.numbers[("file:2001", "SUPPLIER")] == "72001"
    assert plan.origin[("file:2001", "SUPPLIER")] == "OFFSET"
    assert plan.numbers[("file:45000", "SUPPLIER")] == "70000"
    assert plan.notes[("file:45000", "SUPPLIER")].startswith("70000 + 45000 = 115000 is outside the range")


def test_invoice_headers_name_the_data_type_only_when_the_target_takes_invoices():
    from uie_core.wizard.quick import infer_data_type

    invoices = ["Re-Nr.", "Re-Datum", "Kd-Nr.", "Kunde", "Netto €", "USt %", "USt €", "Brutto €", "Bemerkung"]
    assert infer_data_type(invoices) == "Invoices"
    assert infer_data_type(
        ["Rechnungsnummer", "Rechnungsdatum", "Kunde", "Netto", "Brutto", "USt"], ["Invoices"]
    ) == ("Invoices")
    assert infer_data_type(invoices, ["Products", "Customers"]) is None  # cible sans factures : jamais imposé
    # une liste d'articles avec « VK netto », « MwSt » reste une liste d'articles
    articles = [
        "Art.-Nr.",
        "Artikelbezeichnung",
        "EAN",
        "Einheit",
        "VK netto",
        "VK brutto",
        "MwSt",
        "Bestand",
    ]
    assert infer_data_type(articles) == "Products"


def test_a_zero_rate_invoice_follows_the_vat_id_kept_from_the_customer_export():
    rows = [
        {**_i("RE-1", "2026-09-15", "3200", "0", "0", "3200", partner="1008"), "supply_kind": "Ware"},  # AT
        _i("RE-2", "2026-09-18", "1850", "0", "0", "1850", partner="1009"),  # France, FR123 invalide
        _i("RE-3", "2026-09-19", "100", "0", "0", "100", partner="1003"),  # Allemagne, sans USt-IdNr
        _i("RE-4", "2026-09-20", "100", "0", "0", "100", partner="1099"),  # client inconnu
    ]
    vat = {"1008": ("valid", "AT"), "1009": ("invalid", "FR"), "1003": ("missing", "DE")}
    t = documents_from_rows(INVOICES, rows, {}, "EUR", {}, partner_vat=vat)
    assert [d.number for d in t.documents] == ["RE-1"]  # justifiée : écrite sans question
    assert t.documents[0].lines[0].category.source_ref == "vat:sale:eu:goods:0"
    reasons = {e.row: (e.kind, e.reason) for e in t.skipped}
    assert reasons[2][0] == "zero_rate_vat" and "is not valid in your customer list (FR)" in reasons[2][1]
    # client allemand à 0 % : refusé sans question ; client inconnu : exporter les clients d'abord
    assert reasons[3][0] == "zero_rate_domestic" and "a customer in your own country (DE)" in reasons[3][1]
    assert reasons[4][0] == "zero_rate" and "export your customers first" in reasons[4][1]
    assert t.questions == []


def test_the_customer_export_keeps_the_state_of_each_vat_id():
    rows = [
        _p("1001", "A GmbH", vat_id="DE123456789", country="Deutschland"),
        _p("1002", "B SARL", vat_id="FR123", country="Frankreich"),
        _p("1003", "C GmbH", country="Österreich"),
    ]
    t = partners_from_rows(PARTNERS, rows, {}, PartnerRole.CUSTOMER, {})
    assert t.vat == {
        "file:1001": ("valid", "DE"),
        "file:1002": ("invalid", "FR"),
        "file:1003": ("missing", "AT"),
    }


def test_the_target_follows_the_kind_of_file_only_when_exactly_one_takes_it():
    receives = {"journal": ["Invoices"], "people": ["Customers & suppliers", "Customers", "Suppliers"]}
    assert target_for_data_type("Invoices", receives) == "journal"
    # les types de partenaires entre eux : une liste de clients va au fichier des partenaires
    assert target_for_data_type("Customers", receives) == "people"
    assert target_for_data_type("Customers & suppliers", receives) == "people"
    # type pas sûr, aucune cible ou plusieurs : rien n'est présélectionné
    assert target_for_data_type(None, receives) is None
    assert target_for_data_type("Products", receives) is None
    assert target_for_data_type("Invoices", receives | {"ledger": ["Invoices"]}) is None


def test_a_purchase_journal_is_named_purchase_invoices_by_its_supplier_headers():
    from uie_core.wizard.quick import infer_data_type

    purchases = [
        "Beleg-Nr. Lieferant",
        "Rechnungsdatum",
        "Lief-Nr.",
        "Lieferant",
        "Kostenart",
        "Netto €",
        "USt %",
        "USt €",
        "Brutto €",
        "Bemerkung",
    ]
    assert infer_data_type(purchases) == "Purchase invoices"
    assert infer_data_type(purchases, ["Invoices", "Purchase invoices"]) == "Purchase invoices"
    assert infer_data_type(purchases, ["Invoices"]) is None  # cible sans factures d'achat : jamais imposé
    # un journal de ventes reste un journal de ventes (le client est nommé)
    sales = ["Re-Nr.", "Re-Datum", "Kd-Nr.", "Kunde", "Netto €", "USt %", "USt €", "Brutto €"]
    assert infer_data_type(sales, ["Invoices", "Purchase invoices"]) == "Invoices"


def _buy(
    number: str, day: str, net: str, rate: str, tax: str, gross: str, supplier: str = "3001"
) -> dict[str, str]:
    return {
        "document_number": number,
        "document_date": day,
        "supplier_number": supplier,
        "partner_name": "Holz",
        "net_amount": net,
        "tax_rate": rate,
        "tax_amount": tax,
        "gross_amount": gross,
    }


def test_purchase_invoices_are_bills_of_the_supplier_booked_on_the_other_side():
    from uie_core.accounting.bookings import Side, gross_bookings

    rows = [
        _buy("H-100", "2026-09-28", "1000", "19", "190", "1190"),
        _buy("GS-1", "2026-09-21", "-150", "19", "-28.5", "-178.5", supplier="3002"),
        _buy("H-200", "2026-09-22", "35", "19", "6.65", "41.65", supplier=""),
    ]
    t = documents_from_rows(INVOICES, rows, {}, "EUR", purchase=True)
    bill, credit = t.documents
    assert (bill.document_type, bill.partner.role) == (DocumentType.VENDOR_BILL, PartnerRole.SUPPLIER)
    assert credit.document_type == DocumentType.VENDOR_CREDIT_NOTE
    # référence = fournisseur + numéro : un numéro n'est unique que chez son fournisseur
    assert (bill.source_ref, bill.partner.source_ref) == ("file:purchase:3001:H-100", "file:3001")
    # charge au débit : le fournisseur au crédit (H) ; l'avoir en sens inverse
    assert [b.partner_side for b in gross_bookings(bill).bookings] == [Side.CREDIT]
    assert [b.partner_side for b in gross_bookings(credit).bookings] == [Side.DEBIT]
    assert gross_bookings(bill).bookings[0].amount == Decimal("1190")
    assert t.scope == "PURCHASE" and t.categories == {"purchase:19": "purchases at 19 % VAT"}
    assert [(e.row, e.reason) for e in t.skipped] == [(3, "Invoice H-200: no supplier number: not exported.")]


def test_a_purchase_invoice_number_is_unique_only_at_its_supplier():
    rows = [
        _buy("500", "2026-08-10", "500", "19", "95", "595"),
        _buy("500", "2026-08-12", "800", "19", "152", "952", supplier="3002"),  # autre fournisseur : exportée
        _buy("500", "2026-08-10", "500", "19", "95", "595"),  # saisie double : exportée une fois
    ]
    t = documents_from_rows(INVOICES, rows, {}, "EUR", purchase=True)
    assert [d.source_ref for d in t.documents] == ["file:purchase:3001:500", "file:purchase:3002:500"]
    assert [(e.row, e.kind, e.reason) for e in t.skipped] == [
        (3, "duplicate", "Invoice 500 of supplier 3001 entered twice (same content as row 1): exported once.")
    ]
    # ventes : inchangé, le numéro de facture est unique dans tout le journal
    sales = documents_from_rows(
        INVOICES, [_i("RE-1", "2026-09-01", "1", "19", "0.19", "1.19")] * 2, {}, "EUR"
    )
    assert [e.reason for e in sales.skipped] == [
        "Invoice RE-1 entered twice (same content as row 1): exported once."
    ]


def test_the_usual_expense_accounts_are_data_and_an_unknown_category_has_none():
    from uie_core.mapping.suggestions import CategoryAccountRegistry

    table = CategoryAccountRegistry.find("datev", "buchungsstapel", "03", "PURCHASE")
    assert table is not None and "to be confirmed" in table.status
    assert table.account_for("Telefon/Internet", Decimal("19")) == "4920"
    assert table.account_for("wareneinkauf", Decimal("7")) == "3300"
    assert table.account_for("Druckkosten", Decimal("0")) is None  # « à définir » : jamais devinée
    assert CategoryAccountRegistry.find("datev", "buchungsstapel", "04", "PURCHASE") is None
    rows = [
        {**_buy("H-1", "2026-09-01", "100", "19", "19", "119"), "expense_category": "Telefon/Internet"},
        {**_buy("H-2", "2026-09-02", "100", "19", "19", "119"), "expense_category": ""},
    ]
    t = documents_from_rows(INVOICES, rows, {"expense_category": "Kostenart"}, "EUR", purchase=True)
    assert t.categories == {"purchase:telefon-internet:19": "Telefon/Internet at 19 % VAT"}
    assert [(e.row, e.kind) for e in t.skipped] == [(2, "missing_category")]


def test_a_long_category_name_keeps_a_short_stable_key():
    from uie_core.mapping.suggestions import category_key

    long_name = "Wartung und Instandhaltung der technischen Anlagen im Lager Nord, Halle 3 und 4"
    key = category_key(long_name)
    assert len(key) == 60 and key == category_key(long_name.upper())
    assert key != category_key(long_name + " und 5")  # jamais deux catégories confondues
    assert category_key("Telefon/Internet") == "telefon-internet"


def test_a_space_in_an_invoice_number_becomes_a_hyphen_only_by_decision():
    from uie_core.wizard.cdm_accounting import NUMBER_SPACES_KEY

    rows = [_buy("Miete 07/2026", "2026-07-01", "1500", "19", "285", "1785")]
    asked = documents_from_rows(INVOICES, rows, {}, "EUR", purchase=True)
    q = next(q for q in asked.questions if q.key == NUMBER_SPACES_KEY)
    assert (q.suggested, q.answer, asked.documents[0].number) == (
        "hyphen",
        None,
        "Miete 07/2026",
    )  # rien seul
    done = documents_from_rows(INVOICES, rows, {}, "EUR", {NUMBER_SPACES_KEY: "hyphen"}, purchase=True)
    assert done.documents[0].number == "Miete-07/2026"
    assert done.transforms[0].examples == [["Miete 07/2026", "Miete-07/2026"]]
    kept = documents_from_rows(INVOICES, rows, {}, "EUR", {NUMBER_SPACES_KEY: "keep"}, purchase=True)
    assert kept.documents[0].number == "Miete 07/2026" and kept.transforms == []


def test_the_kind_of_supply_is_read_from_its_column_and_never_guessed():
    from uie_cdm import SupplyKind

    headers = ["Re-Nr.", "Re-Datum", "Kd-Nr.", "Kunde", "Leistungsart", "Netto €", "USt %", "Brutto €"]
    assert "supply_kind" in matched_keys(INVOICES, headers)
    rows = [
        {**_i("RE-1", "2026-10-05", "100", "19", "19", "119"), "supply_kind": "Ware"},
        {**_i("RE-2", "2026-10-06", "100", "19", "19", "119"), "supply_kind": " dienstleistung "},
        {**_i("RE-3", "2026-10-07", "100", "19", "19", "119"), "supply_kind": ""},
        {**_i("RE-4", "2026-10-08", "100", "19", "19", "119"), "supply_kind": "Montage"},
    ]
    t = documents_from_rows(INVOICES, rows, {"supply_kind": "Leistungsart"}, "EUR", {})
    kinds = {d.number: d.lines[0].supply_kind for d in t.documents}
    assert kinds == {"RE-1": SupplyKind.GOODS, "RE-2": SupplyKind.SERVICES, "RE-3": None, "RE-4": None}
    (issue,) = [i for i in t.issues if i.kind == "supply_kind_unknown"]
    assert (issue.rows, issue.examples, issue.severity) == ([4], ["Montage"], "to_check")


def test_the_usual_accounts_of_cross_border_operations_are_data_to_be_validated():
    from uie_core.mapping.suggestions import (
        VatAccountRegistry,
        automatic_accounts,
        parse_vat_ref,
        vat_category_ref,
    )
    from uie_core.wizard.formats import format_target, mapping_questions

    table = VatAccountRegistry.find("datev", "buchungsstapel", "03")
    assert table is not None and "to be validated by your tax advisor" in table.status
    usual = {(a.scope, a.zone, a.supply_kind): a.account for a in table.accounts}
    assert usual == {
        ("SALE", "EU", "GOODS"): "8125",
        ("SALE", "EU", "SERVICES"): "8336",
        ("SALE", "NON_EU", "GOODS"): "8120",
        ("SALE", "NON_EU", "SERVICES"): "8338",
        ("PURCHASE", "EU", "GOODS"): "3425",
        ("PURCHASE", "EU", "SERVICES"): "3123",
        ("PURCHASE", "NON_EU", "SERVICES"): "3125",
    }
    assert table.account_for("PURCHASE", "NON_EU", "GOODS") is None  # import : jamais deviné
    assert VatAccountRegistry.find("datev", "buchungsstapel", "04") is None
    # comptes automatiques : ceux des catégories d'achat ET ceux des opérations avec l'étranger
    assert {"3400", "3425", "3123", "3125"} <= set(
        automatic_accounts("datev", "buchungsstapel", "03", "PURCHASE")
    )
    ref = vat_category_ref("SALE", "EU", "SERVICES", Decimal("0"))
    assert ref == "vat:sale:eu:services:0" and parse_vat_ref(ref) == ("SALE", "EU", "SERVICES", Decimal("0"))
    assert parse_vat_ref("purchase:wareneinkauf:19") is None
    fmt = format_target("datev", "buchungsstapel", "13", "DATEV Buchungsstapel 13")
    assert fmt is not None
    (q, _tax) = mapping_questions(fmt, {ref: "EU services at 0 % VAT"}, ["0"], "03", {}, scope="SALE")
    assert q.default == "8336" and "to be validated by your tax advisor" in q.question.help
    assert q.mapping is not None and q.mapping.category == "EU services"


def test_a_cross_border_sale_at_zero_rate_follows_the_zone_and_the_kind_of_supply():
    def sale(number: str, partner: str, kind: str) -> dict[str, str]:
        return {**_i(number, "2026-10-05", "100", "0", "0", "100", partner=partner), "supply_kind": kind}

    rows = [
        sale("RE-101", "1008", "Ware"),  # AT, USt-IdNr valide
        sale("RE-103", "1021", "Dienstleistung"),  # NL, prestation : jamais une livraison
        sale("RE-104", "1020", "Ware"),  # CH, hors UE : exportation
        sale("RE-105", "1020", "Dienstleistung"),  # CH : prestation hors UE
        sale("RE-106", "1022", ""),  # DK, nature inconnue : jamais devinée
    ]
    vat = {
        "1008": ("valid", "AT"),
        "1021": ("valid", "NL"),
        "1022": ("valid", "DK"),
        "1020": ("non_eu", "CH"),
    }
    t = documents_from_rows(INVOICES, rows, {}, "EUR", {}, partner_vat=vat)
    cats = {d.number: d.lines[0].category.source_ref for d in t.documents}
    assert cats == {
        "RE-101": "vat:sale:eu:goods:0",
        "RE-103": "vat:sale:eu:services:0",
        "RE-104": "vat:sale:non_eu:goods:0",
        "RE-105": "vat:sale:non_eu:services:0",
    }
    assert t.categories["vat:sale:eu:services:0"] == "Services to EU businesses at 0 % VAT"
    (skipped,) = t.skipped
    assert (skipped.row, skipped.kind) == (5, "supply_kind")
    assert skipped.reason.endswith(
        "goods or services is not known (Leistungsart), waiting for your answer above."
    )
    assert all(q.key != "accounting:zero_rate" for q in t.questions)


def test_an_unknown_kind_of_supply_is_one_question_never_preselected():
    from uie_core.wizard.cdm_accounting import SUPPLY_KIND_KEY

    rows = [_i("RE-106", "2026-10-12", "780", "0", "0", "780", partner="1022")]  # sans colonne Leistungsart
    vat = {"1022": ("valid", "DK")}
    asked = documents_from_rows(INVOICES, rows, {}, "EUR", {}, partner_vat=vat)
    (q,) = asked.questions
    assert (q.key, q.suggested, q.answer, q.count) == (SUPPLY_KIND_KEY, None, None, 1)
    assert [o.value for o in q.options] == ["GOODS", "SERVICES", "skip"]
    assert "RE-106" in q.help and asked.documents == []
    # comptes des deux natures possibles demandés d'avance (présélectionnés), utilisés après la réponse
    assert set(asked.categories) == {"vat:sale:eu:goods:0", "vat:sale:eu:services:0"} and asked.rates == ["0"]
    goods = documents_from_rows(INVOICES, rows, {}, "EUR", {SUPPLY_KIND_KEY: "SERVICES"}, partner_vat=vat)
    assert goods.documents[0].lines[0].category.source_ref == "vat:sale:eu:services:0"
    skipped = documents_from_rows(INVOICES, rows, {}, "EUR", {SUPPLY_KIND_KEY: "skip"}, partner_vat=vat)
    assert skipped.documents == [] and skipped.skipped[0].reason.endswith("not exported (your choice).")


def test_the_eu_members_are_an_explicit_list_with_their_vat_id_patterns():
    assert len(PARTNERS.eu_members) == 27 and "IE" in PARTNERS.eu_members
    assert not {"CH", "GB", "NO"} & set(PARTNERS.eu_members)
    # chaque pays membre (hors pays du dossier) a le motif de son USt-IdNr (Grèce : préfixe EL)
    prefixes = {"GR": "EL"}
    assert {prefixes.get(c, c) for c in PARTNERS.eu_members} <= set(PARTNERS.vat_ids)
    rows = [
        {
            "supplier_number": "2022",
            "name": "CloudDesk Ltd.",
            "postal_code": "D02 P820",
            "country": "Irland",
            "vat_id": "IE1234567T",
        },
        {"supplier_number": "2023", "name": "Nordic AB", "country": "Schweden"},  # UE sans USt-IdNr
    ]
    t = partners_from_rows(PARTNERS, rows, {}, PartnerRole.SUPPLIER, {})
    assert t.vat == {"file:2022": ("valid", "IE"), "file:2023": ("missing", "SE")}
    ie = t.partners[0]
    assert (ie.addresses[0].country_code, ie.addresses[0].postal_code, ie.tax.vat_id) == (
        "IE",
        "D02 P820",
        "IE1234567T",
    )
    assert [i.kind for i in t.issues] == ["vat_missing_eu"]  # le pays d'Irlande est reconnu


def test_a_tax_number_outside_the_eu_is_never_an_invalid_eu_vat_id():
    rows = [
        {
            "customer_number": "1020",
            "name": "Helvetia Office AG",
            "country": "Schweiz",
            "vat_id": "CHE-123.456.789 MWST",
        },
        {"customer_number": "1023", "name": "Oslo Paper AS", "country": "Norwegen"},
    ]
    t = partners_from_rows(PARTNERS, rows, {}, PartnerRole.CUSTOMER, {})
    assert t.vat == {"file:1020": ("non_eu", "CH"), "file:1023": ("non_eu", "NO")}
    assert t.partners[0].tax.vat_id is None  # jamais écrit dans le champ de l'USt-IdNr
    (issue,) = t.issues
    assert issue.kind == "vat_non_eu" and issue.kind != "vat_invalid" and issue.rows == [1]
    assert issue.message.startswith("1 tax number of a country outside the EU is not an EU VAT ID")


def test_a_cross_border_purchase_at_zero_rate_follows_the_zone_and_the_kind_of_supply():
    from uie_core.wizard.cdm_accounting import SUPPLY_KIND_KEY

    def buy(number: str, supplier: str, kind: str) -> dict[str, str]:
        return {**_buy(number, "2026-10-02", "100", "0", "0", "100", supplier=supplier), "supply_kind": kind}

    rows = [
        buy("PL/0412", "2021", "Ware"),  # PL, USt-IdNr valide : acquisition intracommunautaire
        buy("CD-77812", "2022", "Dienstleistung"),  # IE : prestation UE, autoliquidation
        buy("SS-31", "2020", "Dienstleistung"),  # CH : prestation hors UE, autoliquidation
        buy("CH-GOODS", "2020", "Ware"),  # CH, marchandise : TVA à l'importation
        buy("ES-81", "2007", "Dienstleistung"),  # ES sans USt-IdNr
        buy("PL/0430", "2021", ""),  # nature inconnue
        buy("M-1", "2001", ""),  # fournisseur allemand sans TVA (loyer) : inchangé
    ]
    vat = {
        "2021": ("valid", "PL"),
        "2022": ("valid", "IE"),
        "2020": ("non_eu", "CH"),
        "2007": ("missing", "ES"),
        "2001": ("valid", "DE"),
    }
    t = documents_from_rows(INVOICES, rows, {}, "EUR", {}, partner_vat=vat, purchase=True)
    cats = {d.number: d.lines[0].category.source_ref for d in t.documents}
    assert cats == {
        "PL/0412": "vat:purchase:eu:goods:0",
        "CD-77812": "vat:purchase:eu:services:0",
        "SS-31": "vat:purchase:non_eu:services:0",
        "M-1": "purchase:0",
    }
    reasons = {e.row: (e.kind, e.reason) for e in t.skipped}
    assert (
        reasons[4][0] == "import_vat" and "import VAT (Einfuhrumsatzsteuer) is not supported" in reasons[4][1]
    )
    assert reasons[5][0] == "vat_abroad" and "its VAT ID is missing in your supplier list" in reasons[5][1]
    assert reasons[6][0] == "supply_kind"
    (q,) = t.questions
    assert q.key == SUPPLY_KIND_KEY and q.count == 1
    # réponse « Goods » pour un fournisseur hors UE : importation, jamais exportée
    ch = documents_from_rows(
        INVOICES,
        [buy("CH-2", "2020", "")],
        {},
        "EUR",
        {SUPPLY_KIND_KEY: "GOODS"},
        partner_vat=vat,
        purchase=True,
    )
    assert ch.documents == [] and ch.skipped[0].kind == "import_vat" and "(CH)" in ch.skipped[0].reason


def test_foreign_vat_invoiced_by_a_supplier_abroad_is_never_input_tax():
    rows = [
        _buy("AT-951", "2026-10-06", "500", "20", "100", "600", supplier="2006"),  # USt autrichienne
        _buy(
            "AT-952", "2026-10-07", "500", "19", "95", "595", supplier="2006"
        ),  # même taux qu'ici : étranger
        _buy("R-99310", "2026-10-13", "1000", "19", "190", "1190", supplier="2001"),  # national : inchangé
    ]
    vat = {"2006": ("valid", "AT"), "2001": ("valid", "DE")}
    t = documents_from_rows(INVOICES, rows, {}, "EUR", {}, partner_vat=vat, purchase=True)
    assert [d.number for d in t.documents] == ["R-99310"]
    reasons = {e.row: (e.kind, e.reason) for e in t.skipped}
    assert reasons[1] == (
        "foreign_vat",
        "Invoice AT-951: 20 % VAT invoiced by Holz (AT): foreign VAT is not deductible in your country (DE), "
        "never booked as input tax: not exported.",
    )
    assert reasons[2][0] == "foreign_vat"


CASH_HEADERS = [
    "Datum",
    "Beleg-Nr.",
    "Buchungstext",
    "Kategorie",
    "Einnahme €",
    "Ausgabe €",
    "USt %",
    "Kassenbestand €",
    "Bemerkung",
]


def test_a_cash_book_is_its_own_kind_of_file_recognized_by_its_columns():
    from uie_core.wizard.formats import data_type_of_file, format_target, structure
    from uie_core.wizard.quick import CASH_BOOK

    assert "Cash book" in INVOICES.data_types and CASH_BOOK == "Cash book"
    assert infer_data_type(CASH_HEADERS) == CASH_BOOK  # règles, sans IA
    fmt = format_target("datev", "buchungsstapel", "13", "DATEV Buchungsstapel 13")
    assert fmt is not None and data_type_of_file(fmt, CASH_HEADERS) == CASH_BOOK
    fields = {f.name for f in structure(fmt, CASH_BOOK).fields}
    assert {
        "cash_in",
        "cash_out",
        "cash_balance",
        "booking_text",
        "cash_category",
        "document_number",
    } <= fields
    # ni client, ni fournisseur, ni montant de facture dans un livre de caisse
    assert not {"partner_number", "supplier_number", "gross_amount", "expense_category"} & fields
    # « Kategorie » : la catégorie de caisse ici, la Kostenart dans un journal d'achat (vocabulaire du type)
    keys = {c.key for c in INVOICES.columns_for(CASH_BOOK)}
    assert (
        matched_keys(INVOICES.model_copy(update={"columns": INVOICES.columns_for(CASH_BOOK)}), CASH_HEADERS)
        == {
            "document_date",
            "document_number",
            "booking_text",
            "cash_category",
            "cash_in",
            "cash_out",
            "tax_rate",
            "cash_balance",
            "remark",
        }
        and "cash_category" in keys
    )
    # les journaux de factures ne changent pas
    sales = ["Re-Nr.", "Re-Datum", "Kd-Nr.", "Kunde", "Netto €", "USt %", "USt €", "Brutto €"]
    assert data_type_of_file(fmt, sales) == "Invoices" and "gross_amount" in {
        f.name for f in structure(fmt, "Invoices").fields
    }


def test_the_usual_accounts_of_a_cash_book_are_data_to_be_validated():
    from uie_core.mapping.suggestions import (
        CashAccountRegistry,
        automatic_accounts,
        cash_category_ref,
        parse_cash_ref,
    )
    from uie_core.wizard.formats import format_target, mapping_questions

    table = CashAccountRegistry.find("datev", "buchungsstapel", "03")
    assert table is not None and "to be validated by your tax advisor" in table.status
    assert (table.cash_account, table.transit_account) == ("1000", "1360")
    assert table.account_for("Barverkauf", Decimal("19")) == "8400"
    assert table.account_for("barverkauf", Decimal("7")) == "8300"
    assert table.account_for("Bürobedarf", Decimal("19")) == "4930"
    assert table.account_for("Porto", Decimal("0")) == "4910"
    assert table.account_for("Bankeinzahlung", None) == table.account_for("Bankabhebung", None) == "1360"
    # jamais deviné : catégorie absente, ou sans compte (dépend de la forme juridique)
    assert table.account_for("Bewirtung", Decimal("19")) is None
    assert table.account_for("Privatentnahme", None) is None and table.without_vat("Privatentnahme")
    assert not table.without_vat("Barverkauf") and table.tax_key_for(Decimal("19")) == "0009"
    assert {"8400", "8300"} <= set(automatic_accounts("datev", "buchungsstapel", "03", "SALE", cash=True))
    assert "8400" not in automatic_accounts("datev", "buchungsstapel", "03", "SALE")  # factures : inchangé
    assert CashAccountRegistry.find("datev", "buchungsstapel", "04") is None
    assert cash_category_ref("Bürobedarf", Decimal("19")) == "cash:bürobedarf:19"
    assert parse_cash_ref("cash:bankeinzahlung") == ("bankeinzahlung", None)
    assert parse_cash_ref("purchase:bürobedarf:19") is None
    fmt = format_target("datev", "buchungsstapel", "13", "DATEV Buchungsstapel 13")
    assert fmt is not None
    cats = {
        "cash:barverkauf:19": "Barverkauf at 19 % VAT",
        "cash:bankeinzahlung": "Bankeinzahlung (no VAT)",
        "cash:bewirtung:19": "Bewirtung at 19 % VAT",
    }
    qs = {q.decision: q for q in mapping_questions(fmt, cats, ["19"], "03", {}, scope="SALE")}
    sales = qs["map:CATEGORY:cash:barverkauf:19"]
    assert sales.default == "8400" and "to be validated by your tax advisor" in sales.question.help
    transit = qs["map:CATEGORY:cash:bankeinzahlung"]
    assert transit.default == "1360" and transit.mapping is not None
    assert (transit.mapping.category, transit.mapping.rate, transit.mapping.tax_decision) == (
        "Bankeinzahlung",
        "",
        None,
    )
    assert qs["map:CATEGORY:cash:bewirtung:19"].default is None  # question, jamais devinée


# livre de caisse INVENTÉ qui reproduit les pièges de la vérité terrain du domaine 3 (valeurs mises en forme)
CASH_ROWS = [
    ("2026-09-01", "", "Anfangsbestand", "", "", "", "", "850"),
    ("2026-09-01", "K-001", "Tageslosung Laden", "Barverkauf", "329.5", "", "19", "1179.5"),
    ("2026-09-02", "K-002", "Tageslosung Laden", "Barverkauf", "358.03", "", "19", "1537.53"),
    ("2026-09-02", "K-003", "Tageslosung Laden Bücher", "Barverkauf", "77.71", "", "7", "1615.24"),
    ("2026-09-03", "K-004", "Tageslosung Laden", "Barverkauf", "406.19", "", "19", "2021.43"),
    ("2026-09-03", "K-005", "Briefmarken", "Porto", "", "17", "0", "2004.43"),
    ("2026-09-04", "K-006", "Tageslosung Laden", "Barverkauf", "357.58", "", "19", "2362.01"),
    ("2026-09-04", "K-007", "Druckerpapier Baumarkt", "Bürobedarf", "", "23.8", "19", "2338.21"),
    ("2026-09-07", "K-008", "Tageslosung Laden", "Barverkauf", "401.36", "", "19", "2739.57"),
    ("2026-09-07", "K-009", "Tageslosung Laden Bücher", "Barverkauf", "31.74", "", "7", "2771.31"),
    ("2026-09-08", "K-010", "Tageslosung Laden", "Barverkauf", "291.75", "", "19", "3063.06"),
    ("2026-09-08", "K-011", "Einzahlung auf Bankkonto", "Bankeinzahlung", "", "1200", "", "1863.06"),
    ("2026-09-09", "K-012", "Tageslosung Laden", "Barverkauf", "406.41", "", "19", "2269.47"),
    ("2026-09-10", "K-013", "Tageslosung Laden", "Barverkauf", "335.75", "", "19", "2605.22"),
    ("2026-09-10", "K-014", "Tageslosung EC-Karte", "Barverkauf", "640", "", "19", "3245.22"),
    ("2026-09-11", "K-015", "Tageslosung Laden", "Barverkauf", "396.22", "", "19", "3641.44"),
    ("2026-09-11", "K-016", "Geschäftsessen Kunde", "Bewirtung", "", "95.2", "19", "3546.24"),
    ("2026-09-14", "K-017", "Barentnahme Inhaber", "Privatentnahme", "", "300", "", "3246.24"),
    ("2026-09-14", "K-018", "Tageslosung Laden", "Barverkauf", "207.17", "", "19", "3453.41"),
    ("2026-09-15", "K-019", "Tageslosung Laden", "Barverkauf", "292.58", "", "19", "3745.99"),
    ("2026-09-15", "K-020", "Einzahlung auf Bankkonto", "Bankeinzahlung", "", "4246", "", "-500.01"),
    ("2026-09-16", "K-021", "Abhebung für Wechselgeld", "Bankabhebung", "250", "", "", "-250.01"),
    ("2026-09-16", "K-022", "Tageslosung Laden", "Barverkauf", "239.18", "", "19", "-10.83"),
    ("2026-09-17", "K-023", "Tageslosung Laden", "Barverkauf", "310.5", "", "19", "299.67"),
    ("2026-09-17", "", "Kaffee und Milch Büro", "Bürobedarf", "", "12.4", "19", "287.27"),
    ("2026-09-18", "K-024", "Tageslosung Laden", "Barverkauf", "317.75", "", "19", "605.02"),
    ("2026-09-18", "K-025", "Tageslosung Laden", "Barverkauf", "210", "35", "19", "780.02"),
    ("2026-09-21", "K-026", "Tageslosung Laden", "Barverkauf", "183.15", "", "19", "963.17"),
    ("2026-09-21", "K-027", "Toner Drucker", "Bürobedarf", "", "59.5", "19", "903.67"),
    ("2026-09-22", "K-028", "Tageslosung Laden", "Barverkauf", "357", "", "19", "1260.67"),
    ("2026-09-22", "K-028", "Tageslosung Laden", "Barverkauf", "357", "", "19", "1617.67"),
    ("2026-09-19", "K-029", "Tageslosung Laden", "Barverkauf", "288", "", "19", "1905.67"),
    ("2026-09-23", "K-030", "Tageslosung Laden", "Barverkauf", "305", "", "16", "2210.67"),
    ("2026-09-24", "K-031", "Tageslosung Laden", "Barverkauf", "199", "", "19", "2419.67"),
]
CASH_KEYS = (
    "document_date",
    "document_number",
    "booking_text",
    "cash_category",
    "cash_in",
    "cash_out",
    "tax_rate",
    "cash_balance",
)


def _cash_rows() -> list[dict[str, str]]:
    return [{k: v for k, v in zip(CASH_KEYS, r, strict=True) if v} for r in CASH_ROWS]


def _no_vat(name: str) -> bool:
    from uie_core.mapping.suggestions import CashAccountRegistry

    table = CashAccountRegistry.find("datev", "buchungsstapel", "03")
    return table is not None and table.without_vat(name)


def test_a_cash_book_becomes_entries_without_partner_and_every_trap_is_listed():
    from uie_cdm import DocumentType
    from uie_core.wizard.cdm_cash import documents_from_cash_rows

    t = documents_from_cash_rows(INVOICES, _cash_rows(), {}, "EUR", {}, no_vat=_no_vat)
    assert t.opening_balance == Decimal("850")
    docs = {d.number: d for d in t.documents}
    reasons = {e.row: (e.kind, e.reason) for e in t.skipped}
    # Anfangsbestand : ni une écriture, ni une ligne refusée : dit en information
    assert 1 not in reasons
    (opening,) = [i for i in t.issues if i.kind == "cash_opening"]
    assert (opening.severity, opening.message) == ("info", "Opening balance read: 850.00 € (row 1).")
    assert reasons[25][0] == "missing_number"  # pièce vide
    assert reasons[27][0] == "cash_both"  # K-025 : recette ET dépense
    assert reasons[31][0] == "duplicate"  # second K-028 : exporté une fois
    assert reasons[33][0] == "rate" and "16 %" in reasons[33][1]  # K-030
    # K-020 : décision (solde négatif) ; K-014 : paiement par carte
    assert len(t.documents) == 27 and all(d.partner is None for d in t.documents)
    # recette : caisse au débit (ligne au crédit) ; dépense : ligne au débit ; montant brut, TVA déduite
    k027, k028 = docs["K-027"], docs["K-028"]
    assert k027.document_type == DocumentType.CASH_PAYMENT and k027.lines[0].gross_amount == Decimal("59.5")
    assert (k027.lines[0].amount, k027.tax_amount) == (Decimal("50.00"), Decimal("9.50"))
    assert k028.document_type == DocumentType.CASH_RECEIPT and k028.lines[0].gross_amount == Decimal("-357")
    assert k027.lines[0].description == "Toner Drucker" and k027.source_ref == "file:cash:K-027"
    # sans TVA : seulement pour une catégorie déclarée sans TVA (Geldtransit, Privatentnahme)
    assert docs["K-011"].lines[0].tax_rate is None and docs["K-011"].lines[0].category.source_ref == (
        "cash:bankeinzahlung"
    )
    assert docs["K-003"].lines[0].category.source_ref == "cash:barverkauf:7"
    assert t.category_scope["cash:barverkauf:19"] == "SALE" and t.category_scope["cash:porto:0"] == "PURCHASE"
    assert t.rates == {"SALE": ["19", "7"], "PURCHASE": ["19", "0"]}
    strict = documents_from_cash_rows(INVOICES, _cash_rows(), {}, "EUR", {}, no_vat=None)
    assert {e.row for e in strict.skipped if e.kind == "rate"} >= {12, 18, 22}  # jamais admis sans table


def test_the_cash_balance_is_recalculated_and_a_negative_balance_is_a_serious_anomaly():
    from uie_core.wizard.cdm_cash import NEGATIVE_KEY, documents_from_cash_rows

    t = documents_from_cash_rows(
        INVOICES, _cash_rows(), {"cash_balance": "Kassenbestand €"}, "EUR", {}, _no_vat
    )
    issues = {i.kind: i for i in t.issues}
    negative = issues["cash_negative"]
    assert negative.severity == "serious" and negative.rows == [21, 22, 23]  # K-020, puis K-021, K-022
    assert negative.message.startswith("The cash balance becomes negative with K-020 (lowest -500.01 €)")
    assert "stays negative with K-021, K-022" in negative.message
    differs = issues["cash_balance_differs"]  # K-031 : 2419,67 dans le fichier, 2409,67 recalculé
    assert (differs.rows, differs.severity) == ([34], "to_check")
    assert "2419.67 € in your file, 2409.67 € recalculated" in differs.message
    (q,) = [q for q in t.questions if q.key == NEGATIVE_KEY]
    assert (q.suggested, q.answer, q.count) == (None, None, 1)  # jamais présélectionnée
    assert "K-020" not in {d.number for d in t.documents}
    assert {"K-021", "K-022"} <= {d.number for d in t.documents}  # encore négatives : exportées, nommées
    done = documents_from_cash_rows(INVOICES, _cash_rows(), {}, "EUR", {NEGATIVE_KEY: "export"}, _no_vat)
    assert "K-020" in {d.number for d in done.documents}
    # sans ligne d'ouverture : solde non contrôlé, et c'est dit
    rows = _cash_rows()[1:]
    blind = documents_from_cash_rows(INVOICES, rows, {}, "EUR", {}, _no_vat)
    assert "cash_no_opening" in [i.kind for i in blind.issues] and blind.opening_balance is None
    assert "cash_negative" not in [i.kind for i in blind.issues]


def test_an_entry_out_of_date_order_is_information_and_still_exported():
    from uie_core.wizard.cdm_cash import documents_from_cash_rows

    t = documents_from_cash_rows(INVOICES, _cash_rows(), {}, "EUR", {}, _no_vat)
    (order,) = [i for i in t.issues if i.kind == "cash_order"]
    assert (order.severity, order.rows) == ("info", [32])  # K-029 du 19.09 après le 22.09
    assert order.message == (
        "1 entry is not in date order (K-029 of 19.09.2026 after an entry of 22.09.2026): "
        "exported as they are."
    )
    assert "K-029" in {d.number for d in t.documents}


def test_a_card_payment_written_in_the_cash_book_is_never_exported_as_cash():
    from uie_core.wizard.cdm_cash import card_word, documents_from_cash_rows

    t = documents_from_cash_rows(INVOICES, _cash_rows(), {}, "EUR", {}, _no_vat)
    reasons = {e.row: (e.kind, e.reason) for e in t.skipped}
    assert reasons[15][0] == "card_payment" and "is a card payment (EC-Karte), not cash" in reasons[15][1]
    assert "K-014" not in {d.number for d in t.documents}
    # mots entiers du texte de l'écriture ; la remarque libre n'est jamais lue
    assert card_word(INVOICES, "Tageslosung Laden") is None
    assert card_word(INVOICES, "Umsatz Girocard Terminal") == "Girocard"
    assert card_word(INVOICES, "Visabeantragung") is None
    rows = _cash_rows()
    rows[2] = {**rows[2], "remark": "Kartenzahlung"}  # K-002 : remarque seulement
    assert "K-002" in {
        d.number for d in documents_from_cash_rows(INVOICES, rows, {}, "EUR", {}, _no_vat).documents
    }


def test_an_account_never_preselected_says_why_in_its_question():
    from uie_core.wizard.formats import format_target, mapping_questions

    fmt = format_target("datev", "buchungsstapel", "13", "DATEV Buchungsstapel 13")
    assert fmt is not None
    cats = {"cash:privatentnahme": "Privatentnahme (no VAT)", "cash:bewirtung:19": "Bewirtung at 19 % VAT"}
    qs = {q.decision: q for q in mapping_questions(fmt, cats, ["19"], "03", {}, scope="PURCHASE")}
    private = qs["map:CATEGORY:cash:privatentnahme"]
    assert private.default is None and "depends on the legal form of the client" in private.question.help
    meal = qs["map:CATEGORY:cash:bewirtung:19"]
    assert meal.default is None and "never guessed" in meal.question.help


BANK_CSV_HEADERS = [
    "Auftragskonto",
    "Buchungstag",
    "Valutadatum",
    "Buchungstext",
    "Verwendungszweck",
    "Glaeubiger ID",
    "Mandatsreferenz",
    "Beguenstigter/Zahlungspflichtiger",
    "Kontonummer/IBAN",
    "BIC (SWIFT-Code)",
    "Betrag",
    "Waehrung",
    "Info",
]


def test_a_bank_statement_is_its_own_kind_of_file_recognized_by_its_columns():
    from uie_core.sources.camt import COLUMNS
    from uie_core.wizard.formats import data_type_of_file, format_target, structure
    from uie_core.wizard.quick import BANK_STATEMENT

    fmt = format_target("datev", "buchungsstapel", "13", "DATEV Buchungsstapel 13")
    assert fmt is not None and BANK_STATEMENT in fmt.table.data_types
    # export « CSV-CAMT » d'une banque et relevé CAMT.053 (lu sous la même forme) : règles, sans IA
    assert infer_data_type(BANK_CSV_HEADERS) == BANK_STATEMENT == infer_data_type(list(COLUMNS))
    assert data_type_of_file(fmt, BANK_CSV_HEADERS) == BANK_STATEMENT == data_type_of_file(fmt, list(COLUMNS))
    bank = INVOICES.model_copy(update={"columns": INVOICES.columns_for(BANK_STATEMENT)})
    assert matched_keys(bank, list(COLUMNS)) == {
        "account_iban",
        "document_date",
        "value_date",
        "bank_text",
        "purpose",
        "counterparty_name",
        "counterparty_iban",
        "amount",
        "currency",
        "bank_reference",
    }
    fields = {f.name for f in structure(fmt, BANK_STATEMENT).fields}
    assert not {"partner_number", "gross_amount", "cash_in", "cash_category"} & fields
    # les livres de caisse et les journaux de factures ne changent pas
    assert data_type_of_file(fmt, CASH_HEADERS) == "Cash book"
    assert (
        infer_data_type(["Re-Nr.", "Re-Datum", "Kd-Nr.", "Kunde", "Netto €", "USt %", "Brutto €"])
        == "Invoices"
    )


def test_the_usual_accounts_of_a_bank_statement_are_data_to_be_validated():
    from uie_core.mapping.suggestions import BankAccountRegistry, bank_category_ref, parse_bank_ref
    from uie_core.wizard.formats import format_target, mapping_questions, settings_questions

    table = BankAccountRegistry.find("datev", "buchungsstapel", "03")
    assert table is not None and "to be validated by your tax advisor" in table.status
    assert (table.bank_account, table.accounts) == (
        "1200",
        {"bank_fees": "4970", "cash_deposit": "1360", "vat_prepayment": "1780"},
    )
    keys = {c.key: c for c in INVOICES.bank_categories}
    assert keys["rent"].per_counterparty and keys["card_settlement"].never_booked
    assert "Skonto" in INVOICES.discount_words
    rent = bank_category_ref("rent", "DE27100777770209299700")
    assert parse_bank_ref(rent) == ("rent", "DE27100777770209299700") and parse_bank_ref("cash:x") is None
    fmt = format_target("datev", "buchungsstapel", "13", "DATEV Buchungsstapel 13")
    assert fmt is not None
    cats = {"bank:bank_fees": "Bank fees", rent: "Rent (Immobilien Vogel GbR)"}
    qs = {q.decision: q for q in mapping_questions(fmt, cats, [], "03", {}, scope="PURCHASE")}
    fees = qs["map:CATEGORY:bank:bank_fees"]
    assert fees.default == "4970" and fees.mapping is not None and fees.mapping.tax_decision is None
    assert qs[f"map:CATEGORY:{rent}"].default is None  # loyer : jamais deviné
    # compte bancaire : posé pour un relevé seulement, présélectionné depuis la table (à valider)
    bank = {q.decision: q for q in settings_questions(fmt, {}, {}, 2026, kind="bank")}
    assert bank["setting:bank_account"].default == "1200" and "setting:cash_account" not in bank
    assert "to be validated" in (bank["setting:bank_account"].question.help or "")
    plain = {q.decision for q in settings_questions(fmt, {}, {}, 2026)}
    assert not {"setting:bank_account", "setting:cash_account"} & plain
