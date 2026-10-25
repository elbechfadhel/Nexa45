"""Lecture ACCOUNTING : Odoo (schéma RÉEL d'accounting17, l10n_de / SKR03, + pièces INVENTÉES extraites de
l'instance de test) → Accounting CDM, pour UNE société et UN mois, en lecture seule.

Chaîne : baseline Odoo 17 → instance → discovery (faux serveur) → analyse ACCOUNTING (les 4 extensions
de la localisation sont des questions : réponses ici) → plan (semantic bindings) → conditions génériques
(société =, date comptable entre, type parmi) → OdooCatalogReader → AccountingDocument.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import SecretStr
from test_catalog_customer17_v1b1 import DUMPS, PASSWORD, ROOT
from test_catalog_odoo_instance import FakeOdooResolver
from test_catalog_sync import Readers
from uie_cdm import DocumentStatus, DocumentType, LineKind, PartnerRole, TaxComputation, TaxScope
from uie_connectors.odoo import OdooSchemaLoader
from uie_connectors.odoo.discovery import READ_ONLY_METHODS
from uie_connectors.odoo.testing import FakeOdooServer
from uie_core.accounting import (
    AccountingError,
    AccountingNotReady,
    AccountingPeriod,
    AccountingReadService,
)
from uie_core.domains import AccountingDomainService, PartnerDomainService, SemanticEnrichmentService
from uie_core.identity import GO45_DEV_ORG_ID, SystemContext
from uie_core.instances import FernetSecretStore, InstanceCreate, InstanceService
from uie_core.partners import PartnerReadService
from uie_core.registry import RegistryService

RECORDS = ROOT / "packages" / "connectors" / "tests" / "fixtures" / "odoo17_accounting17_records.json"
CTX = SystemContext("test")
JANUARY = AccountingPeriod.parse("2026-01")
COMPANY = "1"  # GO45 Demo GmbH (identité source, choisie explicitement)


@pytest.fixture
def world(session):  # noqa: ANN001, ANN201
    RegistryService(session, CTX).publish_snapshot(
        OdooSchemaLoader({"json": DUMPS / "odoo17_ref.json"}).build_snapshot()
    )
    fake = FakeOdooServer.from_dump(DUMPS / "odoo17_accounting17.json", password=PASSWORD).with_records(
        RECORDS
    )
    instances = InstanceService(
        session, CTX, FernetSecretStore(FernetSecretStore.generate_key()), FakeOdooResolver(fake)
    )
    inst = instances.create(
        InstanceCreate(
            organization_id=GO45_DEV_ORG_ID,
            name="accounting17",
            system="odoo",
            version="17",
            endpoint="http://odoo:8069",
            config={"db": fake.db, "username": "admin"},
            password=SecretStr(PASSWORD),
        )
    )
    instances.discover_schema(inst.id)
    domains = AccountingDomainService(session, CTX)
    domains.analyze(inst.id)
    enrichment = SemanticEnrichmentService(session, CTX, domains=domains)
    svc = AccountingReadService(session, CTX, instances, enrichment, Readers(fake))
    return {
        "inst": inst,
        "instances": instances,
        "enrichment": enrichment,
        "svc": svc,
        "fake": fake,
        "session": session,
    }


def _answer(world) -> None:  # noqa: ANN001
    """Les extensions de la localisation (mise en page DIN 5008, code DATEV des taxes) : hors V1, ignorées."""
    e, iid = world["enrichment"], world["inst"].id
    for q in e.get_pending_questions(iid):
        e.ignore(iid, q.entity_technical_name, q.field_technical_name)


def _read(world, period=JANUARY, company=COMPANY):  # noqa: ANN001, ANN202
    _answer(world)
    return world["svc"].read(world["inst"].id, company, period)


def _doc(result, number):  # noqa: ANN001, ANN202
    return next(i for i in result.items if i.number == number)


# --------------------------------------------------------------------------- #
def test_localization_extensions_are_questions_before_reading(world):
    questions = sorted(q.path for q in world["enrichment"].get_pending_questions(world["inst"].id))
    assert questions == [
        "account.move.l10n_din5008_addresses",
        "account.move.l10n_din5008_document_title",
        "account.move.l10n_din5008_template_data",
        "account.tax.l10n_de_datev_code",
    ]
    with pytest.raises(AccountingNotReady):
        world["svc"].read(world["inst"].id, COMPANY, JANUARY)


def test_period_parsing():
    assert (str(JANUARY), JANUARY.first_day.isoformat(), JANUARY.last_day.isoformat()) == (
        "2026-01",
        "2026-01-01",
        "2026-01-31",
    )
    assert AccountingPeriod.parse("2024-02").last_day.day == 29  # année bissextile
    for bad in ("2026-13", "2026-1", "26-01", "2026/01", ""):
        with pytest.raises(AccountingError):
            AccountingPeriod.parse(bad)


def test_companies_are_listed_for_an_explicit_choice(world):
    _answer(world)
    assert [(c.source_ref, c.name) for c in world["svc"].companies(world["inst"].id)] == [
        ("1", "GO45 Demo GmbH")
    ]


def test_period_filter_reads_only_the_requested_month(world):
    result = _read(world)
    numbers = {i.number for i in result.items}
    assert len(result.items) == 13 and "RE/2026/00009" not in numbers  # UIE-ACC-M : février
    february = world["svc"].read(world["inst"].id, COMPANY, AccountingPeriod.parse("2026-02"))
    assert [i.document.reference for i in february.items] == ["UIE-ACC-M"]
    # la période est transmise à la source : aucune pièce d'un autre mois n'est lue
    assert all(JANUARY.first_day <= i.document.accounting_date <= JANUARY.last_day for i in result.items)


def test_company_filter(world):
    assert _read(world, company="2").items == []  # aucune pièce d'une autre société, jamais mélangées


def test_counts_by_status_and_type(world):
    result = _read(world)
    assert result.by_status() == {"POSTED": 11, "DRAFT": 1, "CANCELLED": 1}
    assert result.by_type() == {
        "CUSTOMER_INVOICE": 9,
        "CUSTOMER_CREDIT_NOTE": 1,
        "VENDOR_BILL": 2,
        "VENDOR_CREDIT_NOTE": 1,
    }
    assert (result.period, result.company.name) == ("2026-01", "GO45 Demo GmbH")


def test_customer_invoice_with_several_lines(world):
    doc = _doc(_read(world), "RE/2026/00002").document  # UIE-ACC-B
    assert (doc.document_type, doc.status, doc.currency, doc.reference) == (
        DocumentType.CUSTOMER_INVOICE,
        DocumentStatus.POSTED,
        "EUR",
        "UIE-ACC-B",
    )
    assert (doc.total_amount, doc.tax_amount) == (Decimal("739.89"), Decimal("110.09"))
    assert doc.balance() == 0
    items = doc.lines_of(LineKind.ITEM)
    assert [(ln.account.code, ln.amount, ln.gross_amount) for ln in items] == [
        ("8400", Decimal("-360.0"), Decimal("-428.4")),
        ("8400", Decimal("-190.0"), Decimal("-226.1")),
        ("8300", Decimal("-79.8"), Decimal("-85.39")),
    ]
    assert [[t.rate for t in ln.taxes] for ln in items] == [
        [Decimal("19.0")],
        [Decimal("19.0")],
        [Decimal("7.0")],
    ]
    tax = items[0].taxes[0]
    assert (tax.computation, tax.scope, tax.price_included, tax.name) == (
        TaxComputation.PERCENT,
        TaxScope.SALE,
        False,
        "19%",
    )
    assert sorted(ln.account.code for ln in doc.lines_of(LineKind.TAX)) == ["1771", "1776"]
    [open_item] = doc.lines_of(LineKind.OPEN_ITEM)
    assert (open_item.account.code, open_item.amount) == ("1410", Decimal("739.89"))  # débit client


def test_credit_notes_and_vendor_documents_are_signed(world):
    result = _read(world)
    credit = next(i.document for i in result.items if i.document.reference == "UIE-ACC-C")
    assert credit.document_type == DocumentType.CUSTOMER_CREDIT_NOTE
    assert credit.lines_of(LineKind.ITEM)[0].amount > 0 and credit.lines_of(LineKind.OPEN_ITEM)[0].amount < 0
    bill = next(i.document for i in result.items if i.document.reference == "UIE-ACC-D")
    assert (bill.document_type, bill.partner.role) == (DocumentType.VENDOR_BILL, PartnerRole.SUPPLIER)
    assert bill.lines_of(LineKind.ITEM)[0].amount > 0 and bill.lines_of(LineKind.OPEN_ITEM)[0].amount < 0
    assert bill.lines_of(LineKind.ITEM)[0].taxes[0].scope == TaxScope.PURCHASE
    vendor_credit = next(i.document for i in result.items if i.document.reference == "UIE-ACC-E")
    assert vendor_credit.document_type == DocumentType.VENDOR_CREDIT_NOTE
    for item in result.items:
        if item.document:
            assert item.document.balance() == 0, item.number  # débit = crédit, aucune ligne perdue


def test_draft_and_cancelled_are_never_exportable(world):
    result = _read(world)
    by_ref = {i.document.reference: i for i in result.items}
    assert by_ref["UIE-ACC-J"].status == DocumentStatus.DRAFT and by_ref["UIE-ACC-J"].document.number is None
    assert by_ref["UIE-ACC-K"].status == DocumentStatus.CANCELLED
    assert not by_ref["UIE-ACC-J"].exportable and not by_ref["UIE-ACC-K"].exportable
    assert sum(1 for i in result.items if i.exportable) == 10  # 11 comptabilisées − 1 non prise en charge


def test_unsupported_constructs_are_detected_and_kept(world):
    item = next(i for i in _read(world).items if i.document.reference == "UIE-ACC-L")
    assert [i.code for i in item.issues] == ["MULTIPLE_TAXES"]
    assert not item.exportable
    [line] = item.document.lines_of(LineKind.ITEM)
    assert sorted(t.rate for t in line.taxes) == [Decimal("7.0"), Decimal("19.0")]  # rien d'écarté


def test_ledger_accounts_keep_the_source_code(world):
    doc = next(i.document for i in _read(world).items if i.document.reference == "UIE-ACC-I")
    account = doc.lines_of(LineKind.ITEM)[0].account
    assert account.code == "8200" and account.name  # code SOURCE et son nom, jamais un numéro cible
    assert account.source_ref.isdigit()


def test_partner_references_match_the_partner_domain(world):
    """La référence du partenaire d'une pièce = l'identité source du Partner CDM de la MÊME instance : c'est
    la clé qui permettra de réutiliser les numéros DATEV attribués par l'export PARTNER (phases suivantes)."""
    result = _read(world)
    s = world["session"]
    partner_domains = PartnerDomainService(s, CTX)
    partner_domains.analyze(world["inst"].id)
    partner_enrichment = SemanticEnrichmentService(s, CTX, domains=partner_domains)
    for q in partner_enrichment.get_pending_questions(world["inst"].id):
        partner_enrichment.ignore(world["inst"].id, q.entity_technical_name, q.field_technical_name)
    partners = PartnerReadService(s, CTX, world["instances"], partner_enrichment, Readers(world["fake"]))
    cdm = {p.source_ref: p for p in partners.read(world["inst"].id).partners}
    for item in result.items:
        ref = item.document.partner
        assert ref.source_ref in cdm, ref
        assert ref.role in cdm[ref.source_ref].roles  # le rôle de la pièce est un rôle du partenaire
    rhein = {i.document.partner.role for i in result.items if i.document.partner.name == "Rhein Logistik AG"}
    assert rhein == {PartnerRole.CUSTOMER, PartnerRole.SUPPLIER}


def test_reading_never_writes(world):
    _read(world)
    assert world["fake"].methods_called <= READ_ONLY_METHODS | {"authenticate", "version"}


def test_due_date_is_the_invoice_due_date_computed_by_the_payment_term(world):
    """`due_date` = échéance de la facture (Odoo : invoice_date_due, calculée par la condition de paiement
    depuis la date de FACTURE), jamais une autre date : B à 15 jours, D (achat) à 45 jours, les autres à 30
    jours ; G (facturée le 2025-12-30, comptabilisée le 2026-01-05) échoit 30 jours après sa facture."""
    by_ref = {i.document.reference: i.document for i in _read(world).items}
    due = {ref: (d.document_date.isoformat(), d.due_date.isoformat()) for ref, d in by_ref.items()}
    assert due["UIE-ACC-A"] == ("2026-01-12", "2026-02-11")
    assert due["UIE-ACC-B"] == ("2026-01-15", "2026-01-30")
    assert due["UIE-ACC-D"] == ("2026-01-08", "2026-02-22")
    assert due["UIE-ACC-G"] == ("2025-12-30", "2026-01-29")
    assert len({d for _, d in due.values()}) == len(due)  # aucune date commune (plus de date de création)
    assert all(d.due_date >= d.document_date for d in by_ref.values())
