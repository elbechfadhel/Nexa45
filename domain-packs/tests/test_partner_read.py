"""Lecture PARTNER : Odoo (schéma RÉEL de customer17 + partenaires INVENTÉS) → Partner CDM, en lecture seule.

Chaîne : discovery → analyse PARTNER → réponse à la question x_customer_no → plan (semantic bindings) →
OdooCatalogReader → Partner CDM. Aucune écriture dans Odoo (espion), aucune donnée de partenaire stockée.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from test_catalog_customer17_v1b1 import DUMPS, PASSWORD, ROOT
from test_catalog_odoo_instance import FakeOdooResolver
from test_catalog_sync import Readers
from uie_cdm import AddressUsage, IdentifierScheme, PartnerKind, PartnerRole
from uie_connectors.odoo import OdooSchemaLoader
from uie_connectors.odoo.discovery import READ_ONLY_METHODS
from uie_connectors.odoo.testing import FakeOdooServer
from uie_core.domains import PartnerDomainService, SemanticEnrichmentService
from uie_core.identity import GO45_DEV_ORG_ID, MembershipRole, SystemContext, TenantContext
from uie_core.instances import FernetSecretStore, InstanceCreate, InstanceService
from uie_core.partners import PartnerNotReady, PartnerReadService
from uie_core.registry import NotFound, RegistryService
from uie_core.registry import models as m

RECORDS = ROOT / "packages" / "connectors" / "tests" / "fixtures" / "odoo17_partner_records.json"
SYSTEM = SystemContext("test")


@pytest.fixture
def world(session):  # noqa: ANN001, ANN201
    RegistryService(session, SYSTEM).publish_snapshot(
        OdooSchemaLoader({"json": DUMPS / "odoo17_ref.json"}).build_snapshot()
    )
    fake = FakeOdooServer.from_dump(DUMPS / "odoo17_customer.json", password=PASSWORD).with_records(RECORDS)
    store = FernetSecretStore(FernetSecretStore.generate_key())
    instances = InstanceService(session, SYSTEM, store, FakeOdooResolver(fake))
    inst = instances.create(
        InstanceCreate(
            organization_id=GO45_DEV_ORG_ID,
            name="customer17",
            system="odoo",
            version="17",
            endpoint="http://odoo:8069",
            config={"db": fake.db, "username": "admin"},
            password=SecretStr(PASSWORD),
        )
    )
    instances.discover_schema(inst.id)
    domains = PartnerDomainService(session, SYSTEM)
    domains.analyze(inst.id)
    enrichment = SemanticEnrichmentService(session, SYSTEM, domains=domains)
    readers = Readers(fake)
    svc = PartnerReadService(session, SYSTEM, instances, enrichment, readers)
    return {"inst": inst, "svc": svc, "enrichment": enrichment, "fake": fake, "store": store}


def _ready(world) -> None:  # noqa: ANN001
    world["enrichment"].confirm(world["inst"].id, "res.partner", "x_customer_no", "customer_number")


def test_read_waits_for_complete_partner_analysis(world):
    with pytest.raises(PartnerNotReady):
        world["svc"].read(world["inst"].id)


def test_plan_reads_by_semantic_key_including_the_confirmed_extension(world):
    _ready(world)
    plan = world["svc"].read_plan(world["inst"].id)
    assert plan.primary.entity == "res.partner"
    assert plan.primary.get("customer_number").field == "x_customer_no"  # appris par la réponse, pas codé
    assert plan.primary.get("vat_id").field == "vat"
    assert (plan.bank_account.entity, plan.bank.entity, plan.country.entity) == (
        "res.partner.bank",
        "res.bank",
        "res.country",
    )


def test_ignoring_the_extension_removes_it_from_the_plan(world):
    world["enrichment"].ignore(world["inst"].id, "res.partner", "x_customer_no")
    plan = world["svc"].read_plan(world["inst"].id)
    assert plan.primary.get("customer_number") is None


def test_partners_in_cdm(world):
    _ready(world)
    result = world["svc"].read(world["inst"].id)
    by_name = {p.name: p for p in result.partners}
    # actifs sans parent : contact rattaché (15) et archivé (16) exclus
    assert sorted(p.source_ref for p in result.partners) == ["10", "11", "12", "13", "14", "17", "18"]
    assert result.skipped_children == 1 and result.read == 8 and not result.truncated

    hb = by_name["Holzbau Beispiel GmbH"]
    assert (hb.kind, hb.organization_name, hb.person) == (PartnerKind.ORGANIZATION, hb.name, None)
    assert hb.roles == [PartnerRole.CUSTOMER]
    assert hb.identifier(IdentifierScheme.CUSTOMER_NUMBER) == "10042"
    assert hb.identifier(IdentifierScheme.PARTNER_REFERENCE) == "HB-01"
    addr = hb.address(AddressUsage.MAIN)
    assert (addr.street, addr.street2, addr.postal_code, addr.city, addr.region, addr.country_code) == (
        "Hauptstraße 1",
        "2. OG",
        "80331",
        "München",
        "Bayern (DE)",
        "DE",
    )
    assert hb.tax.vat_id == "DE123456789" and hb.language == "de"
    assert [(b.iban, b.bic, b.bank_name, b.holder_name, b.is_main) for b in hb.bank_accounts] == [
        ("DE89370400440532013000", "BEISDEMMXXX", "Beispielbank", None, True),
        ("AT611904300234573201", None, None, "Holzbau Beispiel Treuhand", False),
    ]

    anna = by_name["Anna Beispiel"]
    assert anna.kind == PartnerKind.PERSON and anna.person.full_name == "Anna Beispiel"
    assert (anna.person.first_name, anna.person.last_name) == (None, None)  # jamais découpé
    assert anna.language == "en" and anna.contact.mobile == "+49 170 0000000"

    assert by_name["Alpen Handel AG"].roles == [PartnerRole.CUSTOMER, PartnerRole.SUPPLIER]
    assert by_name["Interessent Ohne Rolle GmbH"].roles == []
    assert by_name["Nordwald Holz KG"].address().country_code is None  # jamais inventé

    vd = by_name["Van Dijk Hout BV"]
    assert vd.bank_accounts[0].iban is None and vd.bank_accounts[0].account_number == "123456"
    assert {(i.source_ref, i.message) for i in result.issues} == {
        ("13", "A bank account is not a valid IBAN: only its account number is kept"),
    }


def test_read_never_writes_and_stores_nothing(world, session):
    _ready(world)
    before = session.scalar(select(func.count()).select_from(m.FieldSemanticBinding))
    world["svc"].read(world["inst"].id)
    assert world["fake"].methods_called <= READ_ONLY_METHODS | {"authenticate", "version"}
    assert session.scalar(select(func.count()).select_from(m.FieldSemanticBinding)) == before


def test_other_tenant_cannot_read(world, session):
    _ready(world)
    ctx = TenantContext(uuid.uuid4(), uuid.uuid4(), MembershipRole.OWNER)
    instances = InstanceService(session, ctx, world["store"], FakeOdooResolver(world["fake"]))
    enrichment = SemanticEnrichmentService(session, ctx, domains=PartnerDomainService(session, ctx))
    with pytest.raises(NotFound):
        PartnerReadService(session, ctx, instances, enrichment).read(world["inst"].id)
