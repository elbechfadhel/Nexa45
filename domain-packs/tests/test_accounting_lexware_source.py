"""Lexware Office comme SOURCE ACCOUNTING (phase source 1b.1) : pièces de comptabilité → Accounting CDM par le
binding de source à schéma fixe, LECTURE SEULE. Faux serveur Lexware (pièces INVENTÉES), aucune écriture,
aucune analyse, aucune migration ; CDM 1.0, DATEV et D11 compatibles.

La source comptabilise par catégorie et taux, sans comptes (Accounting CDM 1.1, phase 1b.2) : lignes ITEM
avec catégorie, montants et TVA par taux, sans compte ; aucune ligne TAX ni OPEN_ITEM inventée ; pièces
jamais exportables ni préparées pour DATEV sans correspondance confirmée."""

from __future__ import annotations

import ast
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import uie_domain_packs  # noqa: F401  (enregistre les bindings de source)
from pydantic import SecretStr
from sqlalchemy import func, select
from test_partner_lexware_preview import CountingStore, Resolver
from uie_cdm import DocumentStatus, DocumentType, LineKind, PartnerRole
from uie_connectors.lexware import LexwareClient, LexwareRecordSource, LexwareSchemaLoader
from uie_connectors.lexware.ratelimit import TokenBucket
from uie_connectors.lexware.testing import FAKE_KEY, FakeLexwareServer
from uie_core.accounting import AccountingReadService
from uie_core.accounting.service import AccountingError, AccountingPeriod
from uie_core.domains import AccountingDomainService, DomainPackResolver, SemanticEnrichmentService
from uie_core.exports import ExportDocumentRecord, ExportRunRecord
from uie_core.identity import MembershipRole, Organization, SystemContext, TenantContext
from uie_core.instances import InstanceService
from uie_core.registry import RegistryService
from uie_core.registry import models as m
from uie_core.sources import RecordSourceResolver

ROOT = Path(__file__).resolve().parents[2]
FIXED_PY = ROOT / "packages" / "core" / "src" / "uie_core" / "accounting" / "fixed.py"
JANUARY = AccountingPeriod(year=2026, month=1)
# catégories du faux serveur (posting-categories) : « Einnahmen », « Wareneinkauf »
SALES, PURCHASE = "00000000-0000-4000-8000-00000000c001", "00000000-0000-4000-8000-00000000c003"


def voucher(vid: str, vtype: str, status: str, day: str, items: list[tuple[str, str, str, str]], **kw: Any):  # noqa: ANN201
    """Pièce de la forme rendue par `vouchers/{id}` ; `items` = (montant, TVA, taux, catégorie)."""
    gross = sum(Decimal(a) for a, _, _, _ in items)
    tax = sum(Decimal(t) for _, t, _, _ in items)
    return {
        "id": vid,
        "type": vtype,
        "voucherStatus": status,
        "voucherNumber": f"NR-{vid}",
        "voucherDate": f"{day}T00:00:00.000+01:00",
        "dueDate": "2026-02-15T00:00:00.000+01:00",
        "totalGrossAmount": float(gross),
        "totalTaxAmount": float(tax),
        "taxType": "gross",
        "useCollectiveContact": False,
        "contactId": "contact-1",
        "remark": f"Remark {vid}",
        "voucherItems": [
            {"amount": float(a), "taxAmount": float(t), "taxRatePercent": float(r), "categoryId": c}
            for a, t, r, c in items
        ],
        "version": 1,
        "_list": {"contactName": "Werkstatt Beispiel GmbH"},
        **kw,
    }


ONE = [("119.00", "19.00", "19", SALES)]
VOUCHERS = [
    # ouverte et échue : la LISTE dit « overdue », le détail « open »
    voucher(
        "v-1",
        "salesinvoice",
        "open",
        "2026-01-12",
        [("119.00", "19.00", "19", SALES), ("107.00", "7.00", "7", SALES)],
        _list={"voucherStatus": "overdue", "contactName": "Werkstatt Beispiel GmbH"},
    ),
    voucher("v-2", "purchasecreditnote", "overdue", "2026-01-20", [("59.50", "9.50", "19", PURCHASE)]),
    voucher("v-3", "salesinvoice", "draft", "2026-01-21", ONE),
    voucher("v-4", "purchaseinvoice", "voided", "2026-01-22", ONE),
    voucher("v-5", "purchaseinvoice", "unchecked", "2026-01-23", ONE),
    voucher("v-6", "salescreditnote", "blank", "2026-01-24", ONE),  # statut hors traduction
    voucher("v-7", "salesinvoice", "open", "2026-02-03", ONE),  # hors période : jamais lue
    voucher(
        "v-8",
        "salesinvoice",
        "paid",
        "2026-01-25",
        ONE,
        contactId=None,
        useCollectiveContact=True,
        _list={"contactName": "Sammelkunde"},
    ),
    voucher("v-9", "salesinvoice", "paidoff", "2026-01-26", ONE, totalGrossAmount=120.0),  # lignes ≠ total
    voucher(
        "v-10",
        "purchaseinvoice",
        "open",
        "2026-01-31",
        [("100.00", "19.00", "19", PURCHASE)],
        taxType="net",
        totalGrossAmount=119.0,
    ),  # lignes NETTES + TVA = total
]


class Sources(RecordSourceResolver):
    def __init__(self, server: FakeLexwareServer) -> None:
        self.server = server

    def open(self, system, interface, ctx):  # noqa: ANN001, ANN201
        noop = TokenBucket(clock=lambda: 0.0, sleep=lambda _s: None)
        client = LexwareClient(
            ctx.secret, transport=self.server.transport, bucket=noop, sleep=lambda _s: None
        )
        return LexwareRecordSource(client)


@pytest.fixture
def acc(session) -> dict[str, Any]:  # noqa: ANN001
    RegistryService(session, SystemContext("test")).publish_snapshot(LexwareSchemaLoader().build_snapshot())
    org = Organization(name="Acme", slug="acme")
    session.add(org)
    session.flush()
    server = FakeLexwareServer(vouchers=[dict(v) for v in VOUCHERS])
    ctx = TenantContext(uuid.uuid4(), org.id, MembershipRole.OWNER)
    instances = InstanceService(session, ctx, CountingStore(), Resolver(server))
    inst, _ = instances.connect_values("books", "lexware", {"api_key": SecretStr(FAKE_KEY)})
    packs = DomainPackResolver()
    reader = AccountingReadService(
        session,
        ctx,
        instances,
        SemanticEnrichmentService(session, ctx, packs, domains=AccountingDomainService(session, ctx, packs)),
        sources=Sources(server),
    )
    server.calls.clear()
    return {"session": session, "server": server, "inst": inst, "reader": reader}


def by_number(result) -> dict[str, Any]:  # noqa: ANN001
    return {i.number: i for i in result.items}


def codes(item) -> set[str]:  # noqa: ANN001
    return {i.code for i in item.issues}


def test_one_company_and_vouchers_of_the_period_only(acc):
    companies = acc["reader"].companies(acc["inst"].id)
    assert [(c.source_ref, c.name) for c in companies] == [("single", "books")]
    result = acc["reader"].read(acc["inst"].id, "single", JANUARY)
    assert sorted(by_number(result)) == sorted(
        f"NR-v-{n}" for n in (1, 2, 3, 4, 5, 6, 8, 9, 10)
    )  # v-7 : février
    lists = [c for c in acc["server"].calls if c.path == "/v1/voucherlist"]
    assert lists and all(
        c.method == "GET"
        and "voucherDateFrom=2026-01-01" in c.query
        and "voucherDateTo=2026-01-31" in c.query
        for c in lists
    )
    with pytest.raises(AccountingError):
        acc["reader"].read(acc["inst"].id, "another-company", JANUARY)


def test_statuses_including_overdue_are_translated_never_guessed(acc):
    items = by_number(acc["reader"].read(acc["inst"].id, "single", JANUARY))
    status = {n: (i.status.value if i.status else None) for n, i in items.items()}
    assert status["NR-v-1"] == "POSTED"  # « overdue » dans la liste, « open » dans le détail (qui prime)
    assert status["NR-v-2"] == "POSTED"  # « overdue » lui-même : état de paiement d'une pièce comptabilisée
    assert (status["NR-v-3"], status["NR-v-4"], status["NR-v-5"]) == ("DRAFT", "CANCELLED", "DRAFT")
    assert status["NR-v-6"] is None and "UNSUPPORTED_STATUS" in codes(items["NR-v-6"])
    assert items["NR-v-6"].document is None


def test_header_and_category_lines_are_represented_accounts_never_invented(acc):
    items = by_number(acc["reader"].read(acc["inst"].id, "single", JANUARY))
    d = items["NR-v-1"].document
    assert d is not None and [ln.kind for ln in d.lines] == [
        LineKind.ITEM,
        LineKind.ITEM,
    ]  # ni TAX ni OPEN_ITEM
    assert d.missing_accounts() == [0, 1] and all(ln.account is None for ln in d.lines)
    first = d.lines[0]  # vente, montants TTC : contrepartie au CRÉDIT (signes du CDM)
    assert (
        first.category.source_ref,
        first.amount,
        first.gross_amount,
        first.tax_amount,
        first.tax_rate,
    ) == (
        SALES,
        Decimal("-100.00"),
        Decimal("-119.00"),
        Decimal("-19.00"),
        Decimal("19.0"),
    )
    assert (d.document_type, d.status, d.number, d.currency) == (
        DocumentType.CUSTOMER_INVOICE,
        DocumentStatus.POSTED,
        "NR-v-1",
        "EUR",
    )
    assert (d.accounting_date, d.document_date, d.due_date) == (
        date(2026, 1, 12),
        date(2026, 1, 12),
        date(2026, 2, 15),
    )
    assert (d.partner.source_ref, d.partner.role, d.partner.name) == (
        "contact-1",
        PartnerRole.CUSTOMER,
        "Werkstatt Beispiel GmbH",
    )
    assert (d.total_amount, d.tax_amount, d.description) == (Decimal("226.0"), Decimal("26.0"), "Remark v-1")
    assert items["NR-v-2"].document.document_type == DocumentType.VENDOR_CREDIT_NOTE
    assert items["NR-v-2"].document.lines[0].gross_amount == Decimal("-59.50")  # avoir fournisseur : crédit
    net = items["NR-v-10"].document.lines[0]  # achat, montants HT (taxType net) : débit, TTC = HT + TVA
    assert (net.amount, net.gross_amount, net.tax_amount) == (
        Decimal("100.00"),
        Decimal("119.00"),
        Decimal("19.00"),
    )
    # aucune anomalie de lecture pour l'absence de compte : c'est la cible qui exige une correspondance
    assert not any("NO_LEDGER_ACCOUNTS" in codes(i) for i in items.values())
    assert items["NR-v-1"].exportable and not items["NR-v-9"].exportable  # v-9 : lignes ≠ total


def test_source_inconsistencies_are_reported_not_corrected(acc):
    items = by_number(acc["reader"].read(acc["inst"].id, "single", JANUARY))
    assert (
        "MISSING_PARTNER" in codes(items["NR-v-8"]) and items["NR-v-8"].document is None
    )  # contact collectif
    assert "LINES_MISMATCH" in codes(items["NR-v-9"])
    assert "LINES_MISMATCH" not in codes(items["NR-v-10"])  # lignes nettes + TVA = total
    assert "LINES_MISMATCH" not in codes(items["NR-v-1"])


def test_cdm_fields_impossible_to_fill_are_listed(acc):
    result = acc["reader"].read(acc["inst"].id, "single", JANUARY)
    assert [g.code for g in result.gaps] == [
        "lines[ITEM].account",
        "lines[TAX]",
        "lines[OPEN_ITEM]",
        "lines[].taxes[]",
        "accounting_date",
    ]
    assert "category → account mapping is required" in result.gaps[0].message


def test_read_only_get_calls_and_nothing_stored(acc):
    session = acc["session"]
    tables = (m.FieldSemanticBinding, m.InstanceSchemaSnapshot, ExportRunRecord, ExportDocumentRecord)
    before = [session.scalar(select(func.count()).select_from(t)) for t in tables]
    acc["reader"].read(acc["inst"].id, "single", JANUARY)
    calls = acc["server"].calls
    assert acc["server"].writes == [] and {c.method for c in calls} == {"GET"}
    details = [c.path for c in calls if c.path.startswith("/v1/vouchers/")]
    assert len(details) == 9  # une lecture de détail par pièce de la période
    assert [c.path for c in calls].count("/v1/posting-categories") == 1  # noms lus UNE fois par lecture
    assert [session.scalar(select(func.count()).select_from(t)) for t in tables] == before


def test_fixed_accounting_conversion_names_no_system():
    low = FIXED_PY.read_text(encoding="utf-8").lower()
    assert not any(w in low for w in ("lexware", "odoo", "datev", "voucher"))
    tree = ast.parse(FIXED_PY.read_text(encoding="utf-8"))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not any((i or "").startswith("uie_connectors") for i in imported)


def test_bookings_are_grouped_by_category_and_rate_without_any_account(acc):
    """Écritures génériques (1b.3) : une par (catégorie, taux), sans compte ; contrôle sur les totaux."""
    from uie_core.accounting.bookings import gross_bookings

    items = by_number(acc["reader"].read(acc["inst"].id, "single", JANUARY))
    result = gross_bookings(items["NR-v-1"].document)
    assert result.issues == []
    assert [
        (b.counter_account, b.counter_category.source_ref, b.tax_rate, b.gross_amount)
        for b in result.bookings
    ] == [
        (None, SALES, Decimal("19.0"), Decimal("-119.00")),
        (None, SALES, Decimal("7.0"), Decimal("-107.00")),
    ]
    assert result.partner_total == Decimal("226.00")  # contrôle, jamais une ligne
    assert {i.code for i in gross_bookings(items["NR-v-9"].document).issues} == {"TOTAL_MISMATCH"}


def test_category_names_are_read_for_display_and_identifiers_stay_the_reference(acc):
    items = by_number(acc["reader"].read(acc["inst"].id, "single", JANUARY))
    cat = items["NR-v-1"].document.lines[0].category
    assert (cat.source_ref, cat.name) == (SALES, "Einnahmen")
    assert items["NR-v-2"].document.lines[0].category.name == "Wareneinkauf"


def test_unreadable_category_names_keep_identifiers_and_are_reported(acc):
    acc["server"].fail[("GET", "/v1/posting-categories")] = 403
    result = acc["reader"].read(acc["inst"].id, "single", JANUARY)
    cat = by_number(result)["NR-v-1"].document.lines[0].category
    assert (cat.source_ref, cat.name) == (SALES, None)  # jamais un nom deviné
    assert "line_category.name" in [g.code for g in result.gaps]
