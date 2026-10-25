"""Phase 2a Lexware Office — pièces comptables (Accounting CDM) → vouchers : APERÇU, lecture seule.

La SOURCE est un système IMAGINAIRE qui fournit un Accounting CDM construit à la main (aucun connecteur de
source). La cible est une instance Lexware Office servie par le faux serveur : seules des lectures (GET) sont
permises ; aucune écriture n'est tentée."""

from __future__ import annotations

import ast
import json
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import uie_domain_packs  # noqa: F401  (enregistre les bindings cibles)
from pydantic import SecretStr
from sqlalchemy import func, select
from test_partner_lexware_preview import CountingStore, Resolver, _counts
from uie_cdm import (
    AccountingDocument,
    DocumentLine,
    DocumentStatus,
    DocumentType,
    LedgerAccountRef,
    LineKind,
    PartnerRef,
    PartnerRole,
    TaxComputation,
    TaxRef,
)
from uie_connectors.lexware import LexwareClient, LexwareRecordSender, LexwareSchemaLoader
from uie_connectors.lexware.ratelimit import TokenBucket
from uie_connectors.lexware.testing import FAKE_KEY, POSTING_CATEGORIES, FakeLexwareServer
from uie_core.accounting import AccountingPeriod
from uie_core.accounting.api_target import AccountingApiTargetError, AccountingApiTargetService
from uie_core.accounting.service import AccountingReadItem, AccountingReadResult, CompanyRef
from uie_core.delivery import RecordSenderResolver, TargetResourceLink
from uie_core.exports import DocumentExportState, ExportDocumentRecord
from uie_core.exports.documents import DocumentExportLedger
from uie_core.identity import MembershipRole, Organization, SystemContext, TenantContext
from uie_core.instances import ConnectionContext, InstanceCreate, InstanceService
from uie_core.mapping import RecordStatus
from uie_core.registry import DataType, EntityDef, FieldDef, NotFound, RegistryService, SchemaSnapshotModel

ROOT = Path(__file__).resolve().parents[2]
BINDING = ROOT / "domain-packs" / "accounting" / "targets" / "lexware_vouchers.yaml"
INCOME, SERVICES, OUTGO = (c["id"] for c in POSTING_CATEGORIES)
PERIOD = AccountingPeriod(year=2026, month=1)

TAX19 = TaxRef(source_ref="tax-19", name="19 %", computation=TaxComputation.PERCENT, rate=Decimal("19"))
TAX7 = TaxRef(source_ref="tax-7", name="7 %", computation=TaxComputation.PERCENT, rate=Decimal("7"))
SALES = LedgerAccountRef(source_ref="acc-sales", code="8400", name="Revenue 19 %")
BOOKS = LedgerAccountRef(source_ref="acc-books", code="8300", name="Revenue 7 %")
GOODS = LedgerAccountRef(source_ref="acc-goods", code="3400", name="Goods")
RECEIVABLE = LedgerAccountRef(source_ref="acc-rec", code="1400", name="Receivables")
PAYABLE = LedgerAccountRef(source_ref="acc-pay", code="1600", name="Payables")


def doc(
    ref: str,
    *,
    kind: DocumentType = DocumentType.CUSTOMER_INVOICE,
    status: DocumentStatus = DocumentStatus.POSTED,
    currency: str = "EUR",
    partner: str = "P-1",
    lines: list[tuple[LedgerAccountRef, str, TaxRef | None]] | None = None,
) -> AccountingDocument:
    """Pièce équilibrée : (compte, montant HT, taxe) par ligne ; signe d'après le type (vente = crédit)."""
    sale = kind in (DocumentType.CUSTOMER_INVOICE, DocumentType.VENDOR_CREDIT_NOTE)
    sign = Decimal(-1) if sale else Decimal(1)
    out, total, taxes = [], Decimal(0), Decimal(0)
    for account, net, tax in lines or [(SALES, "100.00", TAX19)]:
        n = Decimal(net)
        t = (n * tax.rate / 100).quantize(Decimal("0.01")) if tax and tax.rate else Decimal(0)
        out.append(
            DocumentLine(
                kind=LineKind.ITEM,
                account=account,
                amount=sign * n,
                gross_amount=sign * (n + t),
                taxes=[tax] if tax else [],
            )
        )
        if t:
            out.append(DocumentLine(kind=LineKind.TAX, account=account, amount=sign * t, taxes=[tax]))
        total, taxes = total + n + t, taxes + t
    party = RECEIVABLE if kind.partner_role == PartnerRole.CUSTOMER else PAYABLE
    out.append(DocumentLine(kind=LineKind.OPEN_ITEM, account=party, amount=-sign * total))
    return AccountingDocument(
        source_ref=ref,
        document_type=kind,
        status=status,
        number=f"NR-{ref}",
        reference="EXT-1" if kind.partner_role == PartnerRole.SUPPLIER else None,
        accounting_date=date(2026, 1, 15),
        document_date=date(2026, 1, 14),
        due_date=date(2026, 2, 13),
        currency=currency,
        partner=PartnerRef(source_ref=partner, role=kind.partner_role, name=f"Partner {partner}"),
        total_amount=total,
        tax_amount=taxes,
        lines=out,
        description="Invented test document",
    )


def item(d: AccountingDocument, issues: list | None = None) -> AccountingReadItem:
    return AccountingReadItem(
        number=d.number, status=d.status, document_type=d.document_type, document=d, issues=issues or []
    )


class AnyAccountingSource:
    """Tient lieu de lecteur comptable : rend un Accounting CDM, d'où qu'il vienne."""

    def __init__(self, instances: InstanceService, items: list[AccountingReadItem]) -> None:
        self.ctx, self.instances, self.items = instances.ctx, instances, items

    def read(self, instance_id: uuid.UUID, company: str, period: AccountingPeriod) -> AccountingReadResult:
        self.instances.get(instance_id)
        return AccountingReadResult(
            period=f"{period.year}-{period.month:02d}",
            company=CompanyRef(source_ref=company, name="Invented GmbH"),
            plan_checksum="0" * 64,
            items=self.items,
        )


class FakeSenders(RecordSenderResolver):
    def __init__(self, server: FakeLexwareServer) -> None:
        self.server = server

    def supports(self, system: str, interface: str) -> bool:
        return system == "lexware"

    def create(self, system: str, interface: str, ctx: ConnectionContext) -> LexwareRecordSender:
        assert ctx.secret is not None
        noop = TokenBucket(clock=lambda: 0.0, sleep=lambda _s: None)
        client = LexwareClient(
            ctx.secret, transport=self.server.transport, bucket=noop, sleep=lambda _s: None
        )
        return LexwareRecordSender(ctx, client=client)  # client LECTURE SEULE : toute écriture serait refusée


@pytest.fixture
def acc(session) -> dict[str, Any]:
    registry = RegistryService(session, SystemContext("test"))
    registry.publish_snapshot(LexwareSchemaLoader().build_snapshot())
    registry.publish_snapshot(
        SchemaSnapshotModel(
            system="acme-erp",
            product="acme-erp",
            version="9",
            interface="api",
            interface_type="api",
            metadata={"system_name": "Acme ERP"},
            entities=[
                EntityDef(
                    technical_name="journal", fields=[FieldDef(technical_name="x", data_type=DataType.STRING)]
                )
            ],
        )
    )
    orgs = {s: Organization(name=s.title(), slug=s) for s in ("acme", "globex")}
    session.add_all(orgs.values())
    session.flush()
    server, store = FakeLexwareServer(), CountingStore()

    def make(slug: str, items: list[AccountingReadItem]) -> dict[str, Any]:
        ctx = TenantContext(uuid.uuid4(), orgs[slug].id, MembershipRole.OWNER)
        inst = InstanceService(session, ctx, store, Resolver(server))
        source = inst.create(
            InstanceCreate(name="erp", system="acme-erp", version="9", endpoint="https://erp.test")
        )
        target, _ = inst.connect_values("books", "lexware", {"api_key": SecretStr(FAKE_KEY)})
        src = AnyAccountingSource(inst, items)
        svc = AccountingApiTargetService(session, ctx, src, senders=FakeSenders(server))
        return {"inst": inst, "source": source, "target": target, "src": src, "svc": svc, "ctx": ctx}

    a = make("acme", [item(doc("D-1"))])
    return {"session": session, "server": server, "store": store, "orgs": orgs, "a": a, "make": make}


def link_partner(session, a: dict[str, Any], partner: str = "P-1", contact: str = "contact-1") -> None:  # noqa: ANN001
    """Lien PARTNER tel que l'envoi des contacts (phase 1b) le crée."""
    session.add(
        TargetResourceLink(
            organization_id=a["source"].organization_id,
            source_instance_id=a["source"].id,
            target_instance_id=a["target"].id,
            domain="partner",
            resource="contacts",
            source_ref=partner,
            target_id=contact,
            target_version=1,
            origin="SENT",
        )
    )
    session.flush()


def ready_world(acc: dict[str, Any], items: list[AccountingReadItem]) -> dict[str, Any]:
    a = acc["a"]
    a["src"].items = items
    link_partner(acc["session"], a)
    for account, cat in ((SALES, INCOME), (BOOKS, SERVICES), (GOODS, OUTGO)):
        a["svc"].decide(
            a["source"].id, a["target"].id, account.source_ref, cat, code=account.code, name=account.name
        )
    return a


def preview(a: dict[str, Any]):  # noqa: ANN201
    return a["svc"].preview(a["source"].id, a["target"].id, "company-1", PERIOD)


def by_ref(p) -> dict[str, Any]:  # noqa: ANN001
    return {r.prepared.source_ref: r for r in p.records}


# --------------------------------------------------------------------------- #
# Préparation
# --------------------------------------------------------------------------- #
def test_ready_sales_invoice_becomes_a_voucher_with_one_item_per_account_and_tax(acc):
    d = doc("D-1", lines=[(SALES, "100.00", TAX19), (SALES, "50.00", TAX19), (BOOKS, "20.00", TAX7)])
    a = ready_world(acc, [item(d)])
    r = by_ref(preview(a))["D-1"]
    assert r.prepared.status == RecordStatus.READY and r.export_state == DocumentExportState.NEW
    req = r.prepared.request
    assert {k: req[k] for k in ("type", "voucherNumber", "taxType", "contactId")} == {
        "type": "salesinvoice",
        "voucherNumber": "NR-D-1",
        "taxType": "gross",
        "contactId": "contact-1",
    }
    assert req["voucherDate"] == "2026-01-14T00:00:00.000+00:00" and req["dueDate"].startswith("2026-02-13")
    assert (req["totalGrossAmount"], req["totalTaxAmount"]) == (199.9, 29.9)
    assert req["voucherItems"] == [
        {"amount": 178.5, "taxAmount": 28.5, "taxRatePercent": 19.0, "categoryId": INCOME},
        {"amount": 21.4, "taxAmount": 1.4, "taxRatePercent": 7.0, "categoryId": SERVICES},
    ]
    assert "version" not in req and "id" not in req
    # faits génériques pour l'affichage (aucun nom de champ de la cible)
    p = r.prepared
    assert (p.contact_linked, p.currency, p.total_amount, p.tax_amount) == (
        True,
        "EUR",
        Decimal("199.90"),
        Decimal("29.90"),
    )
    assert [(i.gross_amount, i.tax_rate, i.choice_label) for i in p.items] == [
        (Decimal("178.50"), Decimal("19"), "Einnahmen"),
        (Decimal("21.40"), Decimal("7"), "Dienstleistungen"),
    ]
    json.dumps(req)  # sérialisable tel quel


@pytest.mark.parametrize(
    ("kind", "account", "expected"),
    [
        (DocumentType.CUSTOMER_CREDIT_NOTE, SALES, "salescreditnote"),
        (DocumentType.VENDOR_BILL, GOODS, "purchaseinvoice"),
        (DocumentType.VENDOR_CREDIT_NOTE, GOODS, "purchasecreditnote"),
    ],
)
def test_document_types_and_positive_amounts(acc, kind, account, expected):
    a = ready_world(acc, [item(doc("D-1", kind=kind, lines=[(account, "100.00", TAX19)]))])
    r = by_ref(preview(a))["D-1"]
    assert r.prepared.request["type"] == expected
    assert r.prepared.request["voucherItems"][0]["amount"] == 119.0  # toujours positif
    assert r.prepared.status == RecordStatus.READY
    sale = kind.partner_role == PartnerRole.CUSTOMER
    # vente : remarque = description ; achat : numéro interne conservé en remarque (numéro = référence)
    assert r.prepared.request["remark"] == (
        "Invented test document" if sale else "NR-D-1 · Invented test document"
    )
    assert r.prepared.request["voucherNumber"] == ("NR-D-1" if sale else "EXT-1")


def test_blocked_without_partner_link_or_category_choice(acc):
    a = acc["a"]
    a["src"].items = [item(doc("D-1"))]
    p = preview(a)
    r = by_ref(p)["D-1"]
    messages = [i.message for i in r.prepared.issues] + [
        i.message for it in r.prepared.items for i in it.issues
    ]
    assert r.prepared.status == RecordStatus.BLOCKED and r.prepared.request is None
    assert any("Send this customer or supplier to Lexware Office first" in m for m in messages)
    assert any(m.startswith("Choose the Lexware Office category for account 8400") for m in messages)
    assert [(x.code, x.choice_id, x.role) for x in p.accounts] == [("8400", None, "CUSTOMER")]


def test_category_must_exist_in_the_target_and_be_of_the_expected_kind(acc):
    a = ready_world(
        acc, [item(doc("D-1")), item(doc("D-2", kind=DocumentType.VENDOR_BILL, lines=[(SALES, "10", TAX19)]))]
    )
    rows = by_ref(preview(a))
    kind_msgs = [i.message for it in rows["D-2"].prepared.items for i in it.issues]
    assert rows["D-1"].prepared.status == RecordStatus.READY
    assert rows["D-2"].prepared.status == RecordStatus.BLOCKED and "needs « outgo »" in kind_msgs[0]
    acc["server"].queue = []
    POSTING = list(POSTING_CATEGORIES)
    acc["server"].fail[("GET", "/v1/posting-categories")] = 403  # options illisibles : non vérifiées, signalé
    p = preview(a)
    assert p.options_checked is False and by_ref(p)["D-1"].prepared.status == RecordStatus.READY
    del acc["server"].fail[("GET", "/v1/posting-categories")]
    acc["server"].fail = {}
    assert POSTING == POSTING_CATEGORIES
    with pytest.raises(AccountingApiTargetError):  # une option inconnue de la cible n'est jamais enregistrée
        a["svc"].decide(a["source"].id, a["target"].id, SALES.source_ref, str(uuid.uuid4()))


@pytest.mark.parametrize(
    ("make_item", "message"),
    [
        (lambda: item(doc("D-1", currency="USD")), "accepts only EUR"),
        (lambda: item(doc("D-1", status=DocumentStatus.DRAFT)), "Draft documents are not sent"),
        (lambda: item(doc("D-1", status=DocumentStatus.CANCELLED)), "Cancelled documents are not sent"),
        (
            lambda: item(
                doc("D-1"),
                issues=[{"code": "MULTIPLE_TAXES", "message": "A line has several taxes"}],
            ),
            "A line has several taxes",
        ),
        (
            lambda: item(
                doc(
                    "D-1",
                    lines=[
                        (SALES, "100", TaxRef(source_ref="t", name="Fixed", computation=TaxComputation.FIXED))
                    ],
                )
            ),
            "is not a percentage",
        ),
    ],
)
def test_blocking_validations(acc, make_item, message):
    a = ready_world(acc, [make_item()])
    r = by_ref(preview(a)).popitem()[1]
    texts = [i.message for i in r.prepared.issues] + [i.message for it in r.prepared.items for i in it.issues]
    assert r.prepared.status == RecordStatus.BLOCKED and any(message in t for t in texts), texts


def test_line_without_tax_is_a_warning_sent_with_zero_percent(acc):
    a = ready_world(acc, [item(doc("D-1", lines=[(SALES, "100.00", None)]))])
    r = by_ref(preview(a))["D-1"]
    assert r.prepared.status == RecordStatus.WARNING
    assert r.prepared.request["voucherItems"] == [
        {"amount": 100.0, "taxAmount": 0.0, "taxRatePercent": 0.0, "categoryId": INCOME}
    ]


# --------------------------------------------------------------------------- #
# D11 par instance cible, aucun effet, isolation
# --------------------------------------------------------------------------- #
def test_d11_history_is_per_target_instance_and_preview_writes_nothing(acc):
    a = ready_world(acc, [item(doc("D-1")), item(doc("D-2"))])
    session = acc["session"]
    before, calls = _counts(session), len(acc["server"].calls)
    first = preview(a)
    assert _counts(session) == before and acc["server"].writes == []
    assert {c.method + " " + c.path for c in acc["server"].calls[calls:]} == {"GET /v1/posting-categories"}
    from uie_core.delivery import DeliveryService
    from uie_core.delivery.records import TargetSendItem, TargetSendRun

    scope = a["svc"]._scope(a["source"], a["target"], a["svc"]._binding(a["target"]), "accounting")
    run = TargetSendRun(
        organization_id=scope.organization_id,
        source_instance_id=scope.source_instance_id,
        target_instance_id=scope.target_instance_id,
        domain="accounting",
        resource="vouchers",
        target_label="t",
        request_key="k-1",
        status="COMPLETED",
    )
    session.add(run)
    session.flush()
    sent = by_ref(first)["D-1"].prepared.content
    it = TargetSendItem(run_id=run.id, position=1, source_ref="D-1", operation="CREATE", status="SENT")
    session.add(it)
    session.flush()
    DocumentExportLedger(session).record_sent(
        scope.documents,
        send_item_id=it.id,
        source_ref="D-1",
        fingerprint=sent.fingerprint,
        field_digests=sent.field_digests,
        units=len(sent.units),
        origin=__import__("uie_core.exports.documents", fromlist=["RecordOrigin"]).RecordOrigin.EXPORTED,
        previous=None,
    )
    again = by_ref(preview(a))
    assert again["D-1"].export_state == DocumentExportState.ALREADY_EXPORTED
    assert again["D-2"].export_state == DocumentExportState.NEW
    other, _ = a["inst"].connect_values("books-2", "lexware", {"api_key": SecretStr(FAKE_KEY)})
    link = DeliveryService(session)  # autre compte cible : son propre historique (et ses propres liens)
    assert link is not None
    states = {r.export_state for r in a["svc"].preview(a["source"].id, other.id, "company-1", PERIOD).records}
    assert states == {DocumentExportState.NEW}
    datev_like = session.scalar(
        select(func.count())
        .select_from(ExportDocumentRecord)
        .where(ExportDocumentRecord.target_instance_id.is_(None))
    )
    assert datev_like == 0


def test_another_organization_cannot_preview_or_decide(acc):
    a = acc["a"]
    g = acc["make"]("globex", [])
    with pytest.raises(NotFound):
        g["svc"].preview(a["source"].id, a["target"].id, "company-1", PERIOD)
    with pytest.raises(NotFound):
        g["svc"].decide(a["source"].id, a["target"].id, SALES.source_ref, INCOME)
    with pytest.raises(NotFound):
        g["svc"].preview(g["source"].id, a["target"].id, "company-1", PERIOD)


def test_options_read_the_key_only_when_asked_and_never_store_it(acc):
    a = acc["a"]
    store = acc["store"]
    reads = store.reads
    options = a["svc"].options(a["source"].id, a["target"].id)
    assert [(o.label, o.kind) for o in options][:1] == [("Einnahmen", "income")] and store.reads == reads + 1
    assert acc["server"].writes == []


# --------------------------------------------------------------------------- #
# Frontières
# --------------------------------------------------------------------------- #
SOURCE_WORDS = ("odoo", "account.move", "res.partner", "xmlrpc", "l10n_", "skr03")


def test_binding_and_core_are_source_free_and_core_names_no_target():
    low = BINDING.read_text(encoding="utf-8").lower()
    assert not any(w in low for w in SOURCE_WORDS)
    core = ROOT / "packages" / "core" / "src" / "uie_core" / "accounting" / "api_target.py"
    tree = ast.parse(core.read_text(encoding="utf-8"))
    constants = [
        n.value.lower() for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]
    names = [
        getattr(n, "id", getattr(n, "attr", "")).lower()
        for n in ast.walk(tree)
        if isinstance(n, ast.Name | ast.Attribute)
    ]
    for word in ("lexware", "odoo", "datev", "voucher", "posting"):
        assert not any(word in t for t in constants if len(t) < 60), word
        assert not any(word in t for t in names), word


def test_registry_contract_has_vouchers_and_categories_for_the_binding(acc):
    model = LexwareSchemaLoader().build_snapshot()
    names = {e.technical_name: {f.technical_name for f in e.fields} for e in model.entities}
    assert {"posting-categories", "vouchers", "voucherlist"} <= set(names)
    assert {"voucherItems.amount", "voucherItems.categoryId", "contactId", "taxType"} <= names["vouchers"]
    vouchers = next(e for e in model.entities if e.technical_name == "vouchers")
    enum = next(f for f in vouchers.fields if f.technical_name == "type").metadata["enum"]
    assert enum == [
        "salesinvoice",
        "salescreditnote",
        "purchaseinvoice",
        "purchasecreditnote",
    ]  # jamais une facture


# --------------------------------------------------------------------------- #
# Arrondis de TVA, précision, totaux, dates
# --------------------------------------------------------------------------- #
def manual(ref: str, rows: list[tuple[str, str]], *, total: str | None = None, tax: str | None = None):  # noqa: ANN201
    """Pièce de vente aux montants imposés : (HT, TVA) par ligne, taxe 19 % déclarée."""
    lines, g, t = [], Decimal(0), Decimal(0)
    for net, vat in rows:
        n, v = Decimal(net), Decimal(vat)
        lines.append(
            DocumentLine(kind=LineKind.ITEM, account=SALES, amount=-n, gross_amount=-(n + v), taxes=[TAX19])
        )
        lines.append(DocumentLine(kind=LineKind.TAX, account=SALES, amount=-v, taxes=[TAX19]))
        g, t = g + n + v, t + v
    lines.append(DocumentLine(kind=LineKind.OPEN_ITEM, account=RECEIVABLE, amount=g))
    base = doc(ref)
    return base.model_copy(
        update={
            "lines": lines,
            "total_amount": Decimal(total) if total else g,
            "tax_amount": Decimal(tax) if tax else t,
        }
    )


def messages(r) -> list[str]:  # noqa: ANN001
    return [i.message for i in r.prepared.issues] + [i.message for it in r.prepared.items for i in it.issues]


def test_per_line_tax_rounding_is_a_warning_and_never_recomputed(acc):
    a = ready_world(
        acc, [item(manual("D-1", [("1.03", "0.20"), ("1.03", "0.20")]))]
    )  # 2,46 TTC → 0,39 attendu
    r = by_ref(preview(a))["D-1"]
    assert r.prepared.status == RecordStatus.WARNING
    assert any("differs by 0.01" in m and "rounding per line" in m for m in messages(r))
    assert (
        r.prepared.request["voucherItems"][0]["taxAmount"] == 0.4
    )  # la taxe de la source, jamais recalculée


def test_tax_not_matching_its_rate_blocks(acc):
    a = ready_world(acc, [item(manual("D-1", [("100.00", "25.00")]))])
    r = by_ref(preview(a))["D-1"]
    assert r.prepared.status == RecordStatus.BLOCKED
    assert any("does not match its rate 19 %" in m for m in messages(r))


def test_more_than_two_decimals_blocks_without_rounding(acc):
    a = ready_world(acc, [item(manual("D-1", [("100.005", "19.001")]))])
    r = by_ref(preview(a))["D-1"]
    assert r.prepared.status == RecordStatus.BLOCKED
    assert any("more than 2 decimals" in m for m in messages(r))


def test_document_totals_must_equal_the_items(acc):
    a = ready_world(acc, [item(manual("D-1", [("100.00", "19.00")], total="120.00"))])
    r = by_ref(preview(a))["D-1"]
    assert r.prepared.status == RecordStatus.BLOCKED
    assert any("the document total is 120.00" in m for m in messages(r))


def test_dates_keep_the_same_day_in_the_target_time_zone_all_year():
    """Dates envoyées à minuit UTC : même jour à Berlin (heure d'été +02:00 comme d'hiver +01:00), ce que
    la cible renvoie pour une pièce saisie à la main (constat réel GO45-TEST-001)."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    import yaml

    fmt = next(
        f["date_format"]
        for f in yaml.safe_load(BINDING.read_text("utf-8"))["fields"]
        if f["field"] == "voucherDate"
    )
    day = date(2026, 1, 1)
    while day.year == 2026:
        sent = datetime.fromisoformat(day.strftime(fmt))
        assert sent.astimezone(ZoneInfo("Europe/Berlin")).date() == day, day
        day += timedelta(days=1)


# Noms des champs d'une pièce RÉELLE relue en GET (GO45-TEST-001, saisie à la main) : noms seulement.
REAL_VOUCHER_KEYS = {
    "id",
    "organizationId",
    "type",
    "voucherStatus",
    "voucherNumber",
    "voucherDate",
    "dueDate",
    "totalGrossAmount",
    "totalTaxAmount",
    "taxType",
    "useCollectiveContact",
    "contactId",
    "remark",
    "voucherItems",
    "files",
    "createdDate",
    "updatedDate",
    "version",
}
REAL_ITEM_KEYS = {"amount", "taxAmount", "taxRatePercent", "categoryId"}


def test_prepared_request_has_the_shape_of_a_real_voucher(acc):
    a = ready_world(acc, [item(doc("D-1"))])
    req = by_ref(preview(a))["D-1"].prepared.request
    assert set(req) <= REAL_VOUCHER_KEYS and set(req["voucherItems"][0]) == REAL_ITEM_KEYS
    model = LexwareSchemaLoader().build_snapshot()
    vouchers = next(e for e in model.entities if e.technical_name == "vouchers")
    assert {k.split(".")[0] for k in (f.technical_name for f in vouchers.fields)} >= REAL_VOUCHER_KEYS


# --------------------------------------------------------------------------- #
# Envoi (phase 2b) : moteur générique, faux serveur
# --------------------------------------------------------------------------- #
class WriteSenders(FakeSenders):
    def create(self, system: str, interface: str, ctx: ConnectionContext) -> LexwareRecordSender:
        assert ctx.secret is not None
        noop = TokenBucket(clock=lambda: 0.0, sleep=lambda _s: None)
        client = LexwareClient(
            ctx.secret, transport=self.server.transport, bucket=noop, sleep=lambda _s: None, writable=True
        )
        return LexwareRecordSender(ctx, client=client)


def send_world(acc: dict[str, Any], items: list[AccountingReadItem]) -> dict[str, Any]:
    a = ready_world(acc, items)
    acc["server"].writable = True
    a["svc"].senders = WriteSenders(acc["server"])
    return a


def send(a: dict[str, Any], refs: list[str], key: str | None = None):  # noqa: ANN201
    return a["svc"].send(
        a["source"].id,
        a["target"].id,
        "company-1",
        PERIOD,
        refs=refs,
        request_key=key or uuid.uuid4().hex,
        confirm=True,
    )


def test_send_one_voucher_without_version_then_already_exported_and_no_replay(acc):
    a = send_world(acc, [item(doc("D-1")), item(doc("D-2"))])
    key = uuid.uuid4().hex
    res = send(a, ["D-1"], key)
    it = res.items[0]
    assert (res.run.status, it.status, it.operation) == ("COMPLETED", "SENT", "CREATE")
    posts = [json.loads(c.body) for c in acc["server"].calls if c.method == "POST"]
    assert len(posts) == 1 and "version" not in posts[0] and posts[0]["voucherNumber"] == "NR-D-1"
    gets = [c.path for c in acc["server"].calls if c.method == "GET" and c.path == "/v1/vouchers"]
    assert gets  # doublon cherché par numéro AVANT la création
    rows = by_ref(preview(a))
    assert rows["D-1"].export_state == DocumentExportState.ALREADY_EXPORTED
    assert rows["D-2"].export_state == DocumentExportState.NEW  # l'autre pièce n'est pas touchée
    calls = len(acc["server"].calls)
    assert send(a, ["D-1"], key).run.id == res.run.id  # rejeu de la clé : même envoi
    assert not [c for c in acc["server"].calls[calls:] if c.method != "GET"]  # aucune écriture au rejeu
    again = send(a, ["D-1"])
    assert again.items[0].reason == "UNCHANGED" and not [
        c for c in acc["server"].calls[calls:] if c.method == "POST"
    ]


def test_existing_voucher_number_is_never_created_twice(acc):
    a = send_world(acc, [item(doc("D-1"))])
    acc["server"].vouchers.append({"id": "existing-1", "voucherNumber": "NR-D-1", "version": 1})
    res = send(a, ["D-1"])
    assert (res.items[0].status, res.items[0].candidates) == ("NEEDS_MATCH", ["existing-1"])
    assert not [c for c in acc["server"].calls if c.method == "POST"]


def test_voucher_timeout_is_unknown_and_never_retried(acc):
    a = send_world(acc, [item(doc("D-1"))])
    acc["server"].fail[("POST", "/v1/vouchers")] = 504
    res = send(a, ["D-1"])
    assert (res.items[0].status, res.items[0].reason) == ("UNKNOWN", "UNKNOWN_OUTCOME")
    assert len([c for c in acc["server"].calls if c.method == "POST"]) == 1
    assert send(a, ["D-1"]).items[0].reason == "OUTCOME_UNKNOWN"


def test_blocked_voucher_is_never_sent_and_confirmation_is_required(acc):
    a = send_world(acc, [item(doc("D-1", currency="USD"))])
    with pytest.raises(AccountingApiTargetError):
        a["svc"].send(
            a["source"].id,
            a["target"].id,
            "company-1",
            PERIOD,
            refs=["D-1"],
            request_key="k" * 8,
            confirm=False,
        )
    assert send(a, ["D-1"]).items[0].reason == "BLOCKED" and acc["server"].writes == []


# --------------------------------------------------------------------------- #
# Règles d'envoi du portail (phase 2c, étape 2) : READY + jamais envoyée seulement
# --------------------------------------------------------------------------- #
def test_only_ready_and_new_documents_are_sendable(acc):
    a = send_world(
        acc,
        [
            item(doc("D-1")),
            item(doc("D-2", lines=[(SALES, "100.00", None)])),
            item(doc("D-3", currency="USD")),
        ],
    )
    rows = by_ref(preview(a))
    assert [(r, rows[r].sendable, rows[r].skip_reason) for r in ("D-1", "D-2", "D-3")] == [
        ("D-1", True, None),
        ("D-2", False, "WARNING_NOT_SENT"),
        ("D-3", False, "BLOCKED"),
    ]
    res = send(a, ["D-1", "D-2", "D-3"])
    assert {i.source_ref: (i.status, i.reason) for i in res.items} == {
        "D-1": ("SENT", None),
        "D-2": ("SKIPPED", "WARNING_NOT_SENT"),
        "D-3": ("SKIPPED", "BLOCKED"),
    }
    assert len([c for c in acc["server"].calls if c.method == "POST"]) == 1


def test_document_changed_since_sent_is_never_resent_without_a_specific_approval(acc):
    a = send_world(acc, [item(doc("D-1"))])
    send(a, ["D-1"])
    a["src"].items = [item(doc("D-1", lines=[(SALES, "250.00", TAX19)]))]  # modifiée dans la source
    row = by_ref(preview(a))["D-1"]
    assert row.export_state == DocumentExportState.CHANGED_SINCE_EXPORT
    assert (row.sendable, row.skip_reason) == (False, "CHANGED_NEEDS_APPROVAL")
    writes = len(acc["server"].writes)
    res = send(a, ["D-1"])
    assert (res.items[0].status, res.items[0].reason) == ("SKIPPED", "CHANGED_NEEDS_APPROVAL")
    assert len(acc["server"].writes) == writes  # ni POST ni PUT


def test_double_click_with_the_same_key_returns_the_same_send_without_any_call(acc):
    a = send_world(acc, [item(doc("D-1"))])
    key = uuid.uuid4().hex
    first = send(a, ["D-1"], key)
    calls = len(acc["server"].calls)
    again = send(a, ["D-1"], key)
    assert again.run.id == first.run.id and [i.status for i in again.items] == ["SENT"]
    assert len(acc["server"].calls) == calls  # ni lecture ni écriture de la cible


def test_unknown_outcome_blocks_selection_until_a_human_resolves_it(acc):
    a = send_world(acc, [item(doc("D-1"))])
    acc["server"].fail[("POST", "/v1/vouchers")] = 504
    unknown = send(a, ["D-1"]).items[0]
    row = by_ref(preview(a))["D-1"]
    assert (row.outcome_unknown, row.sendable, row.skip_reason) == (True, False, "OUTCOME_UNKNOWN")
    assert len([c for c in acc["server"].calls if c.method == "POST"]) == 1  # aucun nouvel essai

    # « créée » sans trace dans la cible : refusé (jamais deviné)
    with pytest.raises(AccountingApiTargetError):
        a["svc"].resolve_unknown(
            a["source"].id, a["target"].id, "company-1", PERIOD, unknown.id, created=True
        )
    # « pas créée » : la pièce redevient envoyable
    a["svc"].resolve_unknown(a["source"].id, a["target"].id, "company-1", PERIOD, unknown.id, created=False)
    assert by_ref(preview(a))["D-1"].sendable
    del acc["server"].fail[("POST", "/v1/vouchers")]
    assert send(a, ["D-1"]).items[0].status == "SENT"


def test_unknown_outcome_confirmed_as_created_links_without_writing(acc):
    a = send_world(acc, [item(doc("D-1"))])
    acc["server"].fail[("POST", "/v1/vouchers")] = 504
    unknown = send(a, ["D-1"]).items[0]
    acc["server"].vouchers.append(
        {"id": "created-1", "voucherNumber": "NR-D-1", "version": 1}
    )  # trouvée en GET
    writes = len(acc["server"].writes)
    res = a["svc"].resolve_unknown(
        a["source"].id, a["target"].id, "company-1", PERIOD, unknown.id, created=True
    )
    assert res.items[0].resolution == "CREATED" and len(acc["server"].writes) == writes
    row = by_ref(preview(a))["D-1"]
    assert row.export_state == DocumentExportState.ALREADY_EXPORTED and not row.sendable


# --------------------------------------------------------------------------- #
# Pièces d'achat (binding 1.1.0) : numéro = référence du fournisseur, doublon = numéro + contact
# --------------------------------------------------------------------------- #
def vendor(ref: str, partner: str = "P-1", reference: str | None = "INV-77") -> AccountingReadItem:
    d = doc(ref, kind=DocumentType.VENDOR_BILL, partner=partner, lines=[(GOODS, "100.00", TAX19)])
    return item(d.model_copy(update={"reference": reference}))


def vendor_world(acc: dict[str, Any], items: list[AccountingReadItem]) -> dict[str, Any]:
    a = send_world(acc, items)
    link_partner(acc["session"], a, partner="P-2", contact="contact-2")
    return a


def test_purchase_number_is_the_supplier_reference_and_the_internal_number_stays_in_the_remark(acc):
    a = vendor_world(acc, [vendor("D-1"), vendor("D-2", reference=None), item(doc("D-3"))])
    rows = by_ref(preview(a))
    with_ref, without_ref, sale = (rows[r].prepared.request for r in ("D-1", "D-2", "D-3"))
    assert (with_ref["voucherNumber"], with_ref["remark"]) == ("INV-77", "NR-D-1 · Invented test document")
    assert (without_ref["voucherNumber"], without_ref["remark"]) == (
        "NR-D-2",
        "NR-D-2 · Invented test document",
    )
    # pièces de vente : inchangées (numéro de la pièce, remarque = description)
    assert (sale["voucherNumber"], sale["remark"]) == ("NR-D-3", "Invented test document")
    assert all(rows[r].prepared.status == RecordStatus.READY for r in ("D-1", "D-2", "D-3"))


def test_same_number_at_two_suppliers_is_not_a_duplicate(acc):
    a = vendor_world(acc, [vendor("D-1", partner="P-1"), vendor("D-2", partner="P-2")])  # même « INV-77 »
    res = send(a, ["D-1", "D-2"])
    assert [i.status for i in res.items] == ["SENT", "SENT"]
    posts = [json.loads(c.body) for c in acc["server"].calls if c.method == "POST"]
    assert [(p["voucherNumber"], p["contactId"]) for p in posts] == [
        ("INV-77", "contact-1"),
        ("INV-77", "contact-2"),
    ]


def test_same_number_and_same_supplier_already_in_the_target_is_never_created(acc):
    a = vendor_world(acc, [vendor("D-1")])
    acc["server"].vouchers.append(
        {
            "id": "manual-1",
            "type": "purchaseinvoice",
            "voucherNumber": "INV-77",
            "contactId": "contact-1",
            "version": 2,
        }
    )
    res = send(a, ["D-1"])
    assert (res.items[0].status, res.items[0].candidates) == ("NEEDS_MATCH", ["manual-1"])
    assert not [c for c in acc["server"].calls if c.method == "POST"]


def test_ambiguous_match_without_a_comparable_contact_is_never_ignored(acc):
    a = vendor_world(acc, [vendor("D-1")])
    acc["server"].vouchers.append(  # contact collectif : impossible de dire si c'est le même fournisseur
        {
            "id": "collective-1",
            "type": "purchaseinvoice",
            "voucherNumber": "INV-77",
            "useCollectiveContact": True,
        }
    )
    acc["server"].vouchers.append({"id": "other-1", "voucherNumber": "INV-77", "contactId": "contact-9"})
    res = send(a, ["D-1"])
    assert (res.items[0].status, res.items[0].candidates) == ("NEEDS_MATCH", ["collective-1"])


def test_sales_duplicates_are_still_detected_by_number_alone(acc):
    a = send_world(acc, [item(doc("D-1"))])
    acc["server"].vouchers.append({"id": "sale-1", "voucherNumber": "NR-D-1", "contactId": "another-contact"})
    assert send(a, ["D-1"]).items[0].status == "NEEDS_MATCH"


def test_purchase_replay_and_resend_never_post_twice(acc):
    a = vendor_world(acc, [vendor("D-1")])
    key = uuid.uuid4().hex
    first = send(a, ["D-1"], key)
    posts = len([c for c in acc["server"].calls if c.method == "POST"])
    assert first.items[0].status == "SENT" and posts == 1
    assert send(a, ["D-1"], key).run.id == first.run.id
    assert send(a, ["D-1"]).items[0].reason == "UNCHANGED"
    assert len([c for c in acc["server"].calls if c.method == "POST"]) == 1
    assert by_ref(preview(a))["D-1"].export_state == DocumentExportState.ALREADY_EXPORTED


def test_binding_version_was_incremented_for_the_purchase_number_rule():
    import yaml

    data = yaml.safe_load(BINDING.read_text("utf-8"))
    assert data["binding_version"] == "1.1.0"
    numbers = [f for f in data["fields"] if f["field"] == "voucherNumber"]
    assert [(f["source"], f.get("fallback"), f["when"]["role"]) for f in numbers] == [
        ("number", None, ["CUSTOMER"]),
        ("reference", "number", ["SUPPLIER"]),
    ]


# --------------------------------------------------------------------------- #
# Envoi multiple : 2 pièces READY + NEW et 1 pièce déjà envoyée, puis l'échec d'une des deux
# --------------------------------------------------------------------------- #
def three_docs_world(acc: dict[str, Any]) -> dict[str, Any]:
    a = send_world(acc, [item(doc("D-1")), item(doc("D-2")), item(doc("D-3"))])
    assert send(a, ["D-3"]).items[0].status == "SENT"  # D-3 déjà envoyée (D11 EXPORTED)
    return a


def fail_post_of(monkeypatch, server: FakeLexwareServer, number: str, status: int) -> None:  # noqa: ANN001
    """Le faux serveur refuse le POST d'UNE pièce (par son numéro), les autres passent."""
    handle = server._handle

    def handler(request):  # noqa: ANN001, ANN202
        if request.method == "POST" and json.loads(request.content or b"{}").get("voucherNumber") == number:
            server.calls.append(
                type(server.calls[0])(request.method, request.url.path, None, request.content)
            )
            return httpx.Response(status, json={"status": status, "traceId": "trace-test"})
        return handle(request)

    monkeypatch.setattr(server, "_handle", handler)


def posts_of(server: FakeLexwareServer, start: int = 0) -> list[str]:
    return [json.loads(c.body)["voucherNumber"] for c in server.calls[start:] if c.method == "POST"]


def test_multiple_send_writes_the_two_new_documents_and_skips_the_exported_one(acc):
    a = three_docs_world(acc)
    start = len(acc["server"].calls)
    res = send(a, ["D-1", "D-2", "D-3"])
    assert {i.source_ref: (i.status, i.reason) for i in res.items} == {
        "D-1": ("SENT", None),
        "D-2": ("SENT", None),
        "D-3": ("SKIPPED", "UNCHANGED"),
    }
    assert res.run.status == "COMPLETED"
    assert sorted(posts_of(acc["server"], start)) == ["NR-D-1", "NR-D-2"]  # 2 envois, D-3 jamais renvoyée
    numbers = [v["voucherNumber"] for v in acc["server"].vouchers]
    assert sorted(numbers) == ["NR-D-1", "NR-D-2", "NR-D-3"]  # aucun doublon dans la cible
    rows = by_ref(preview(a))
    assert {r: rows[r].export_state for r in rows} == dict.fromkeys(
        ("D-1", "D-2", "D-3"), DocumentExportState.ALREADY_EXPORTED
    )
    again = len(acc["server"].calls)
    assert {i.reason for i in send(a, ["D-1", "D-2", "D-3"]).items} == {"UNCHANGED"}
    assert posts_of(acc["server"], again) == []


def test_multiple_send_rejection_of_one_document_does_not_stop_the_other(acc, monkeypatch):
    a = three_docs_world(acc)
    fail_post_of(monkeypatch, acc["server"], "NR-D-1", 406)  # refus définitif : rien n'est créé
    res = send(a, ["D-1", "D-2", "D-3"])
    got = {i.source_ref: (i.status, i.reason) for i in res.items}
    assert (
        got["D-1"][0] == "FAILED" and got["D-2"] == ("SENT", None) and got["D-3"] == ("SKIPPED", "UNCHANGED")
    )
    assert res.run.status == "PARTIAL"
    rows = by_ref(preview(a))
    assert (rows["D-1"].export_state, rows["D-1"].sendable) == (DocumentExportState.NEW, True)  # à corriger
    assert rows["D-2"].export_state == DocumentExportState.ALREADY_EXPORTED
    monkeypatch.undo()
    start = len(acc["server"].calls)
    retry = send(a, ["D-1", "D-2", "D-3"])
    assert {i.source_ref: i.status for i in retry.items} == {
        "D-1": "SENT",
        "D-2": "SKIPPED",
        "D-3": "SKIPPED",
    }
    assert posts_of(acc["server"], start) == ["NR-D-1"]
    assert sorted(v["voucherNumber"] for v in acc["server"].vouchers) == ["NR-D-1", "NR-D-2", "NR-D-3"]


def test_multiple_send_timeout_of_one_document_is_unknown_and_never_resent(acc, monkeypatch):
    a = three_docs_world(acc)
    fail_post_of(monkeypatch, acc["server"], "NR-D-1", 504)  # issue inconnue : la pièce existe peut-être
    res = send(a, ["D-1", "D-2", "D-3"])
    got = {i.source_ref: (i.status, i.reason) for i in res.items}
    assert got == {
        "D-1": ("UNKNOWN", "UNKNOWN_OUTCOME"),
        "D-2": ("SENT", None),
        "D-3": ("SKIPPED", "UNCHANGED"),
    }
    assert res.run.status == "PARTIAL"
    start = len(acc["server"].calls)
    again = send(a, ["D-1", "D-2", "D-3"])
    assert {i.source_ref: i.reason for i in again.items}["D-1"] == "OUTCOME_UNKNOWN"
    assert posts_of(acc["server"], start) == []  # jamais retentée d'office : pas de doublon possible


def test_multiple_send_refused_key_stops_and_the_rest_is_safe_to_resend(acc, monkeypatch):
    a = three_docs_world(acc)
    fail_post_of(monkeypatch, acc["server"], "NR-D-1", 403)  # permission : arrêt de l'envoi
    res = send(a, ["D-1", "D-2", "D-3"])
    got = {i.source_ref: (i.status, i.reason) for i in res.items}
    assert got["D-1"][0] == "FAILED" and got["D-2"] == ("FAILED", "NOT_ATTEMPTED")
    assert res.run.status == "NOT_SENT"
    rows = by_ref(preview(a))
    assert rows["D-1"].sendable and rows["D-2"].sendable  # rien n'a été écrit : renvoi sûr
    assert sorted(v["voucherNumber"] for v in acc["server"].vouchers) == ["NR-D-3"]
