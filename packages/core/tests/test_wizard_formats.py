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
from uie_core.wizard.cdm_accounting import ZERO_RATE_KEY, documents_from_rows
from uie_core.wizard.cdm_partners import (
    DUPLICATES_KEY,
    KINDS_KEY,
    NUMBER_KEY,
    POSTAL_KEY,
    partners_from_rows,
)
from uie_core.wizard.formats import matched_keys
from uie_core.wizard.quick import target_for_data_type

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
    assert reasons[10][0] == "zero_rate"  # 0 % : en attente de la décision
    (q,) = t.questions
    assert q.key == ZERO_RATE_KEY and q.answer is None
    assert set(t.categories) == {"sale:19", "sale:0"}  # 0 % compris : son compte est demandé d'avance
    exported = documents_from_rows(INVOICES, rows, {}, "EUR", {ZERO_RATE_KEY: "export"})
    assert "RE-7" in {d.number for d in exported.documents}


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
        _i("RE-1", "2026-09-15", "3200", "0", "0", "3200", partner="1008"),  # Autriche, ATU valide
        _i("RE-2", "2026-09-18", "1850", "0", "0", "1850", partner="1009"),  # France, FR123 invalide
        _i("RE-3", "2026-09-19", "100", "0", "0", "100", partner="1003"),  # Allemagne, sans USt-IdNr
        _i("RE-4", "2026-09-20", "100", "0", "0", "100", partner="1099"),  # client inconnu
    ]
    vat = {"1008": ("valid", "AT"), "1009": ("invalid", "FR"), "1003": ("missing", "DE")}
    t = documents_from_rows(INVOICES, rows, {}, "EUR", {}, partner_vat=vat)
    assert [d.number for d in t.documents] == ["RE-1"]  # justifiée : écrite sans question
    reasons = {e.row: (e.kind, e.reason) for e in t.skipped}
    assert reasons[2][0] == "zero_rate_vat" and "is not valid in your customer list (FR)" in reasons[2][1]
    assert (
        reasons[3][0] == reasons[4][0] == "zero_rate"
    )  # client allemand sans numéro, client inconnu : décision
    (q,) = t.questions
    assert q.count == 2


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
