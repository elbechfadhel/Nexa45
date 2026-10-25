"""Bout en bout POC : Lexware Office (SOURCE à schéma fixe) → Partner / Accounting CDM → DATEV, APERÇU.

Faux serveur Lexware avec une facture et un achat FICTIFS inédits (février 2026 : aucune période déjà
exportée), deux nouveaux contacts. Étapes du flux réel, sans raccourci :
1. comptes Debitoren / Kreditoren attribués par l'export PARTNER existant (base de TEST, annulée) ;
2. préparation Buchungsstapel : catégories et taux à décider, rien de suggéré ;
3. correspondances VALIDÉES explicitement (valeurs de TEST : 8400 / 3400, « No tax key ») ;
4. aperçu de la préparation et du fichier : pièces READY, cellules telles qu'écrites, rien d'enregistré.
Aucun export réel, aucune écriture dans Lexware, aucune migration. Specs DATEV locales (sinon sauté)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
import uie_domain_packs  # noqa: F401
from pydantic import SecretStr
from sqlalchemy import func, select
from test_accounting_lexware_source import PURCHASE, SALES, Sources, voucher
from test_partner_lexware_preview import CountingStore, Resolver
from test_partner_lexware_source import FakeSources as PartnerSources
from uie_connectors.datev import DatevSchemaLoader
from uie_connectors.lexware import LexwareSchemaLoader
from uie_connectors.lexware.testing import FAKE_KEY, FakeLexwareServer
from uie_core.accounting import AccountingReadService, AccountingTargetService, MappingStatus
from uie_core.accounting.service import AccountingPeriod
from uie_core.domains import (
    AccountingDomainService,
    DomainPackResolver,
    PartnerDomainService,
    SemanticEnrichmentService,
)
from uie_core.exports import ExportDocumentRecord, ExportRunRecord
from uie_core.exports.service import ExportProfileService, PartnerExportService
from uie_core.identity import MembershipRole, Organization, SystemContext, TenantContext
from uie_core.instances import InstanceService
from uie_core.mapping import LookupKind, RecordStatus
from uie_core.partners import PartnerReadService
from uie_core.registry import RegistryService

ROOT = Path(__file__).resolve().parents[2]
SPECS = ROOT / "var" / "datev" / "specs" / "extf_700"
DK, BS_SPEC = SPECS / "debitoren-kreditoren_v5.yaml", SPECS / "buchungsstapel_v13.yaml"
pytestmark = pytest.mark.skipif(
    not (DK.exists() and BS_SPEC.exists()), reason="specs DATEV locales absentes (spec_builder)"
)
DATEV, BS = "datev", "buchungsstapel"
FEBRUARY = AccountingPeriod(year=2026, month=2)
PROFILE = {  # dossier DATEV de TEST (aucune valeur par défaut dans le produit)
    "advisor_number": 1001,
    "client_number": 1,
    "fiscal_year_start": "2026-01-01",
    "account_length": 4,
    "debtor_range": {"start": 10000, "end": 69999},
    "creditor_range": {"start": 70000, "end": 99999},
    "chart_of_accounts": "03",
    "origin_code": "RE",
}
ADDRESS = {"billing": [{"street": "Teststraße 1", "zip": "20457", "city": "Hamburg", "countryCode": "DE"}]}
CONTACTS = [
    {
        "id": "k-neu-1",
        "archived": False,
        "roles": {"customer": {"number": 10501}},
        "company": {"name": "Neukunde Beispiel GmbH", "vatRegistrationId": "DE900000001"},
        "addresses": ADDRESS,
    },
    {
        "id": "k-neu-2",
        "archived": False,
        "roles": {"vendor": {"number": 70501}},
        "company": {"name": "Neulieferant Beispiel KG", "vatRegistrationId": "DE900000002"},
        "addresses": ADDRESS,
    },
]
VOUCHERS = [
    voucher(
        "neu-1",
        "salesinvoice",
        "open",
        "2026-02-10",
        [("595.00", "95.00", "19", SALES)],
        voucherNumber="RE-UIE-2026-0201",
        contactId="k-neu-1",
        _list={"contactName": "Neukunde Beispiel GmbH"},
    ),
    voucher(
        "neu-2",
        "purchaseinvoice",
        "open",
        "2026-02-12",
        [("238.00", "38.00", "19", PURCHASE)],
        voucherNumber="LIEF-4711",
        contactId="k-neu-2",
        _list={"contactName": "Neulieferant Beispiel KG"},
    ),
]


@pytest.fixture
def e2e(session) -> dict[str, Any]:  # noqa: ANN001
    registry = RegistryService(session, SystemContext("test"))
    for snap in (
        LexwareSchemaLoader().build_snapshot(),
        DatevSchemaLoader({"spec": DK}).build_snapshot(),
        DatevSchemaLoader({"spec": BS_SPEC}).build_snapshot(),
    ):
        registry.publish_snapshot(snap)
    org = Organization(name="Acme", slug="acme")
    session.add(org)
    session.flush()
    ctx = TenantContext(uuid.uuid4(), org.id, MembershipRole.OWNER)
    server = FakeLexwareServer(contacts=[dict(c) for c in CONTACTS], vouchers=[dict(v) for v in VOUCHERS])
    instances = InstanceService(session, ctx, CountingStore(), Resolver(server))
    inst, _ = instances.connect_values("books", "lexware", {"api_key": SecretStr(FAKE_KEY)})
    packs = DomainPackResolver()
    partners = PartnerReadService(
        session,
        ctx,
        instances,
        SemanticEnrichmentService(session, ctx, packs, domains=PartnerDomainService(session, ctx, packs)),
        sources=PartnerSources(server),
    )
    reader = AccountingReadService(
        session,
        ctx,
        instances,
        SemanticEnrichmentService(session, ctx, packs, domains=AccountingDomainService(session, ctx, packs)),
        sources=Sources(server),
    )
    server.calls.clear()
    return {
        "session": session,
        "ctx": ctx,
        "server": server,
        "inst": inst,
        "partners": partners,
        "target": AccountingTargetService(session, ctx, reader),
    }


def count(session, table) -> int:  # noqa: ANN001
    return session.scalar(select(func.count()).select_from(table))


def test_lexware_invoice_to_datev_preview_end_to_end(e2e):
    session, ctx, iid = e2e["session"], e2e["ctx"], e2e["inst"].id

    # 1. comptes de personne : export PARTNER existant (base de TEST, transaction annulée en fin de test)
    ExportProfileService(session, ctx).save(DATEV, "debitoren-kreditoren", PROFILE)
    PartnerExportService(session, ctx, e2e["partners"]).generate(DATEV, "debitoren-kreditoren", iid)

    # 2. préparation : catégories et taux à décider, rien de suggéré
    svc = e2e["target"]
    svc.save_settings(iid, DATEV, BS, {"currency": "EUR", "lock_bookings": "0"})
    first = svc.preview(iid, "single", FEBRUARY, DATEV, BS)
    todo = {(m.kind, m.source_ref): m for m in first.mappings}
    assert set(todo) == {
        (LookupKind.CATEGORY, SALES),
        (LookupKind.CATEGORY, PURCHASE),
        (LookupKind.TAX, "rate:SALE:19"),
        (LookupKind.TAX, "rate:PURCHASE:19"),
    }
    assert all(m.status == MappingStatus.MISSING and m.suggestion is None for m in todo.values())
    assert {
        todo[(LookupKind.CATEGORY, SALES)].source_code,
        todo[(LookupKind.CATEGORY, PURCHASE)].source_code,
    } == {
        "Einnahmen",
        "Wareneinkauf",
    }
    assert all(d.target_status == RecordStatus.BLOCKED for d in first.documents)

    # 3. correspondances VALIDÉES explicitement (valeurs de test)
    svc.decide(iid, DATEV, BS, LookupKind.CATEGORY, SALES, value="8400")
    svc.decide(iid, DATEV, BS, LookupKind.CATEGORY, PURCHASE, value="3400")
    svc.decide(iid, DATEV, BS, LookupKind.TAX, "rate:SALE:19", value=None, no_value=True)
    svc.decide(iid, DATEV, BS, LookupKind.TAX, "rate:PURCHASE:19", value=None, no_value=True)

    # 4. aperçu de la préparation : deux pièces READY, une écriture chacune, aucune ligne TAX / OPEN_ITEM
    runs, records = count(session, ExportRunRecord), count(session, ExportDocumentRecord)
    prep = svc.preview(iid, "single", FEBRUARY, DATEV, BS)
    docs = {d.number: d for d in prep.documents}
    assert {n: d.target_status for n, d in docs.items()} == {
        "RE-UIE-2026-0201": RecordStatus.READY,
        "LIEF-4711": RecordStatus.READY,
    }, {
        n: [i.message for b in d.bookings for i in b.issues] + [i.message for i in d.issues]
        for n, d in docs.items()
    }
    sale, purchase = docs["RE-UIE-2026-0201"].bookings, docs["LIEF-4711"].bookings
    assert [(b.amount, b.counter_target, b.tax_target, b.tax_no_value) for b in sale] == [
        (595, "8400", None, True)
    ]
    assert [(b.amount, b.counter_target, b.tax_target, b.tax_no_value) for b in purchase] == [
        (238, "3400", None, True)
    ]
    assert sale[0].partner_account.startswith("1") and purchase[0].partner_account.startswith("7")
    cells = {c.label: c.value for c in sale[0].cells}
    assert any("595" in v for v in cells.values())

    # aperçu du fichier : 2 écritures, rien d'enregistré (ni fichier, ni registre par pièce)
    plan = svc.file_preview(iid, "single", FEBRUARY, DATEV, BS)
    assert (plan.new_documents, plan.row_count, plan.blocked_documents) == (2, 2, 0)
    assert (count(session, ExportRunRecord), count(session, ExportDocumentRecord)) == (runs, records)
    assert e2e["server"].writes == []  # Lexware : GET seulement
