"""V2a commit 3 : synchronisation du catalogue réel et lecture, sur le domaine RÉEL de customer17
(dump complet) et un mini catalogue inventé servi par le même faux serveur Odoo (odoo17_catalog_records.json).

Chaîne complète : discovery → analyze → réponses du fournisseur → plan de lecture → OdooCatalogReader →
CatalogProduct (variantes regroupées) → catalog_products → lecture filtrée par tenant.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import SecretStr
from test_catalog_customer17_v1b1 import DUMPS, PASSWORD, ROOT
from test_catalog_odoo_instance import FakeOdooResolver
from uie_connectors.odoo import OdooCatalogReader, OdooSchemaLoader
from uie_connectors.odoo.discovery import READ_ONLY_METHODS
from uie_connectors.odoo.testing import FakeOdooServer
from uie_core.catalog import CatalogReaderResolver
from uie_core.catalog.records import CatalogProductRecord
from uie_core.catalog.service import CatalogNotReady, CatalogReadService, CatalogSyncService
from uie_core.domains import ProductDomainService, SemanticEnrichmentService
from uie_core.identity import MembershipRole, Organization, SystemContext, TenantContext
from uie_core.instances import FernetSecretStore, InstanceCreate, InstanceService
from uie_core.registry import NotFound, RegistryService

RECORDS = ROOT / "packages" / "connectors" / "tests" / "fixtures" / "odoo17_catalog_records.json"
SYSTEM = SystemContext("test")


class Readers(CatalogReaderResolver):
    def __init__(self, fake: FakeOdooServer) -> None:
        self.fake = fake
        self.opened = 0

    def resolve(self, system, version, interface):  # noqa: ANN001, ANN201
        self.opened += 1
        return OdooCatalogReader(proxy_factory=self.fake.proxy)


class CountingStore(FernetSecretStore):
    def __init__(self) -> None:
        super().__init__(FernetSecretStore.generate_key())
        self.reads = 0

    def get(self, secret_ref):  # noqa: ANN001, ANN201
        self.reads += 1
        return super().get(secret_ref)


@pytest.fixture
def world(session):  # noqa: ANN201
    RegistryService(session, SYSTEM).publish_snapshot(
        OdooSchemaLoader({"json": DUMPS / "odoo17_ref.json"}).build_snapshot()
    )
    fake = FakeOdooServer.from_dump(DUMPS / "odoo17_customer.json", password=PASSWORD).with_records(RECORDS)
    store, readers = CountingStore(), Readers(fake)
    orgs = {}
    for slug in ("acme", "globex"):
        orgs[slug] = Organization(name=slug.title(), slug=slug)
        session.add(orgs[slug])
    session.flush()
    instances = InstanceService(session, SYSTEM, store, FakeOdooResolver(fake))
    inst = {}
    for slug, org in orgs.items():
        i = instances.create(
            InstanceCreate(
                organization_id=org.id,
                name="customer17",
                system="odoo",
                version="17",
                endpoint="http://odoo:8069",
                config={"db": fake.db, "username": "admin"},
                password=SecretStr(PASSWORD),
            )
        )
        instances.discover_schema(i.id)
        inst[slug] = i
    domains = ProductDomainService(session, SYSTEM)
    enrichment = SemanticEnrichmentService(session, SYSTEM, domains=domains)
    domains.analyze(inst["acme"].id)
    store.reads = 0  # la mise en place (discovery) a lu les secrets : on ne compte que la suite

    def tenant(slug: str):  # noqa: ANN202
        ctx = TenantContext(uuid.uuid4(), orgs[slug].id, MembershipRole.OWNER)
        i = InstanceService(session, ctx, store, FakeOdooResolver(fake))
        e = SemanticEnrichmentService(session, ctx)
        return CatalogSyncService(session, ctx, i, e, readers), CatalogReadService(session, ctx, i, readers)

    return {
        "inst": inst,
        "enrichment": enrichment,
        "sync": CatalogSyncService(session, SYSTEM, instances, enrichment, readers),
        "read": CatalogReadService(session, SYSTEM, instances, readers),
        "tenant": tenant,
        "fake": fake,
        "store": store,
        "readers": readers,
    }


def _answer(world) -> None:  # noqa: ANN001
    e, iid = world["enrichment"], world["inst"]["acme"].id
    e.confirm(iid, "product.template", "x_supplier_ref")
    e.change(iid, "product.template", "x_pack_code", "packaging_code")
    e.ignore(iid, "product.template", "x_notes_internal")


def test_sync_waits_for_catalog_ready(world):
    sync, iid = world["sync"], world["inst"]["acme"].id
    with pytest.raises(CatalogNotReady, match="3 question"):
        sync.sync(iid)  # 3 questions ouvertes
    with pytest.raises(CatalogNotReady, match="not analyzed"):
        sync.sync(world["inst"]["globex"].id)  # jamais analysé
    assert world["readers"].opened == 0 and world["store"].reads == 0


def test_sync_builds_canonical_products_with_grouped_variants(world):
    _answer(world)
    iid = world["inst"]["acme"].id
    run = world["sync"].sync(iid)
    assert run.status == "SUCCEEDED", run.error
    assert (run.products, run.variants, run.categories, run.images, run.created) == (3, 4, 2, 1, 3)
    assert {m for model, m in world["fake"].calls if model != "common"} <= READ_ONLY_METHODS

    page = world["read"].list_products(instance_id=iid)
    assert [p.name for p in page.items] == ["Desk Lamp", "Office Chair", "Old Stool"]
    chair_row = page.items[1]
    _, chair = world["read"].get_product(chair_row.id)
    assert (chair.name, chair.category, chair.price.amount, chair.price.currency) == (
        "Office Chair",
        "All / Furniture / Chairs",
        120.0,
        "EUR",
    )
    assert (chair.description, chair.weight, chair.unit, chair.has_image) == (
        "Ergonomic office chair",
        7.5,
        "Units",
        True,
    )
    assert [(v.sku, v.price.amount, [(a.name, a.value) for a in v.attributes]) for v in chair.variants] == [
        ("CHAIR-01-BLK", 120.0, [("Color", "Black"), ("Size", "Standard")]),
        ("CHAIR-01-RED", 130.0, [("Color", "Red"), ("Size", "Standard")]),
    ]
    assert [v.has_image for v in chair.variants] == [False, True]
    assert [(a.name, a.values) for a in chair.attributes] == [
        ("Color", ["Black", "Red"]),
        ("Size", ["Standard"]),
    ]
    offer = chair.supplier_offers[0]
    assert (
        offer.supplier,
        offer.supplier_reference,
        offer.price.amount,
        offer.min_quantity,
        offer.lead_time_days,
    ) == (
        "Wood & Co",
        "WC-778",
        55.0,
        10.0,
        14.0,
    )
    # extensions confirmées : visibles ; extension ignorée (x_notes_internal) : jamais lue
    assert {(x.label, x.value) for x in chair.extra} == {
        ("Packaging code", "PK-10"),
        ("Supplier product code", "SUP-CH-1"),
    }
    assert "do not publish" not in str(chair_row.payload)

    _, lamp = world["read"].get_product(page.items[0].id)
    assert (lamp.sku, lamp.barcode, lamp.variants) == (
        "LAMP-01",
        "4006381333931",
        [],
    )  # variante unique repliée
    _, stool = world["read"].get_product(page.items[2].id)
    assert stool.active is False  # archivé : lu et signalé


def test_sync_is_idempotent_and_detects_removed_products(world):
    _answer(world)
    iid = world["inst"]["acme"].id
    world["sync"].sync(iid)
    again = world["sync"].sync(iid)
    assert (again.created, again.updated, again.unchanged, again.removed) == (0, 0, 3, 0)
    world["fake"].records["product.template"] = [
        r for r in world["fake"].records["product.template"] if r["id"] != 3
    ]
    third = world["sync"].sync(iid)
    assert (third.unchanged, third.removed) == (2, 1)
    assert [p.name for p in world["read"].list_products(instance_id=iid).items] == [
        "Desk Lamp",
        "Office Chair",
    ]


def test_search_pagination_and_images(world):
    _answer(world)
    iid = world["inst"]["acme"].id
    world["sync"].sync(iid)
    read = world["read"]
    assert [p.name for p in read.list_products(search="chair-01-red").items] == [
        "Office Chair"
    ]  # SKU de variante
    assert [p.name for p in read.list_products(search="furniture / chairs").items] == ["Office Chair"]
    first = read.list_products(instance_id=iid, page=1, page_size=2)
    second = read.list_products(instance_id=iid, page=2, page_size=2)
    assert (first.total, len(first.items), len(second.items)) == (3, 2, 1)

    chair = read.list_products(search="office").items[0]
    thumb = read.product_image(chair.id)
    assert thumb is not None and thumb.startswith(b"\x89PNG")
    _, product = read.get_product(chair.id)
    red = product.variants[1]
    assert read.product_image(chair.id, red.id, size="large").startswith(b"\x89PNG")
    assert read.product_image(chair.id, product.variants[0].id) is None  # pas d'image propre
    lamp = read.list_products(search="lamp").items[0]
    assert read.product_image(lamp.id) is None


def test_failed_read_is_recorded_not_raised(world):
    _answer(world)
    world["fake"].password = "rotated-password-1"  # le mot de passe a changé côté Odoo
    run = world["sync"].sync(world["inst"]["acme"].id)
    assert run.status == "FAILED" and run.error and PASSWORD not in run.error


def test_products_of_another_tenant_do_not_exist(world, session):
    _answer(world)
    world["sync"].sync(world["inst"]["acme"].id)
    chair = world["read"].list_products(search="office").items[0]
    b_sync, b_read = world["tenant"]("globex")
    world["store"].reads, world["readers"].opened = 0, 0
    for call in (
        lambda: b_read.get_product(chair.id),
        lambda: b_read.product_image(chair.id),
        lambda: b_read.list_products(instance_id=world["inst"]["acme"].id),
        lambda: b_sync.sync(world["inst"]["acme"].id),
        lambda: b_sync.last_sync(world["inst"]["acme"].id),
    ):
        with pytest.raises(NotFound):
            call()
    assert b_read.list_products().total == 0
    assert (world["store"].reads, world["readers"].opened) == (0, 0)  # ni secret ni connexion
    a_sync, a_read = world["tenant"]("acme")
    assert a_read.list_products().total == 3
    assert a_sync.last_sync(world["inst"]["acme"].id).status == "SUCCEEDED"
    assert session.get(CatalogProductRecord, chair.id) is not None
