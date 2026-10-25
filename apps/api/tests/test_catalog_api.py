"""V2a commit 4 : API du catalogue réel (synchronisation, produits, fiche, images), authentification réelle.

Domaine RÉEL de customer17 (dump complet) + mini catalogue inventé servi par le faux serveur Odoo. Deux
organisations possèdent chacune une instance « customer17 » ; seule celle d'acme est analysée.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from test_product_domain_api import Resolver
from test_supplier_api import TECHNICAL_KEYS, Supplier, _reset_limiter  # noqa: F401 — fixture partagée
from uie_ai import NullProposer
from uie_api import deps
from uie_api.app import create_app
from uie_connectors.odoo import OdooCatalogReader, OdooSchemaLoader
from uie_connectors.odoo.testing import FakeOdooServer
from uie_core.catalog import CatalogReaderResolver
from uie_core.domains import ProductDomainService, SemanticEnrichmentService
from uie_core.identity import (
    MembershipRole,
    Organization,
    OrganizationMembership,
    PlatformRole,
    SystemContext,
    User,
    hash_password,
)
from uie_core.instances import FernetSecretStore, InstanceCreate, InstanceService
from uie_core.registry import RegistryService

ROOT = Path(__file__).resolve().parents[3]
DUMPS = ROOT / "docs" / "dumps"
RECORDS = ROOT / "packages" / "connectors" / "tests" / "fixtures" / "odoo17_catalog_records.json"
ODOO_PW = "Catalog-Api-S3cret"
PW = "Catalog-Portal-Pass-1"
SYSTEM = SystemContext("test")
# noms et clés du système source qui ne doivent jamais atteindre le fournisseur
SOURCE_KEYS = {"source_ref", "entity", "field", "key", "semantic_key", "read_plan", "external_id"}
SOURCE_VALUES = (
    "product.template",
    "product.product",
    "default_code",
    "image_128",
    "lst_price",
    "x_pack_code",
)
SEEN: list[Any] = []


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
def world(session) -> dict[str, Any]:
    RegistryService(session, SYSTEM).publish_snapshot(
        OdooSchemaLoader({"json": DUMPS / "odoo17_ref.json"}).build_snapshot()
    )
    fake = FakeOdooServer.from_dump(DUMPS / "odoo17_customer.json", password=ODOO_PW).with_records(RECORDS)
    store, readers = CountingStore(), Readers(fake)
    orgs = {slug: Organization(name=slug.title(), slug=slug) for slug in ("acme", "globex")}
    session.add_all(orgs.values())
    session.flush()
    for email, slug, role in (
        ("admin@go45.local", None, PlatformRole.ADMIN),
        ("alice@acme.io", "acme", PlatformRole.NONE),
        ("bob@globex.io", "globex", PlatformRole.NONE),
    ):
        u = User(
            email=email,
            display_name=email,
            password_hash=hash_password(SecretStr(PW)),
            platform_role=role.value,
        )
        session.add(u)
        session.flush()
        if slug:
            session.add(
                OrganizationMembership(
                    organization_id=orgs[slug].id, user_id=u.id, role=MembershipRole.OWNER.value
                )
            )
    instances = InstanceService(session, SYSTEM, store, Resolver(fake))
    ids = {}
    for slug, org in orgs.items():
        inst = instances.create(
            InstanceCreate(
                organization_id=org.id,
                name="customer17",
                system="odoo",
                version="17",
                endpoint="http://odoo:8069",
                config={"db": fake.db, "username": "admin"},
                password=SecretStr(ODOO_PW),
            )
        )
        instances.discover_schema(inst.id)
        ids[slug] = str(inst.id)
    domains = ProductDomainService(session, SYSTEM)
    domains.analyze(uuid.UUID(ids["acme"]))
    e = SemanticEnrichmentService(session, SYSTEM, domains=domains)
    acme = uuid.UUID(ids["acme"])
    e.confirm(acme, "product.template", "x_supplier_ref")
    e.change(acme, "product.template", "x_pack_code", "packaging_code")
    e.ignore(acme, "product.template", "x_notes_internal")
    session.flush()
    store.reads = 0

    app = create_app()
    app.dependency_overrides[deps.get_session] = lambda: session
    app.dependency_overrides[deps.get_secret_store] = lambda: store
    app.dependency_overrides[deps.get_provider_resolver] = lambda: Resolver(fake)
    app.dependency_overrides[deps.get_catalog_readers] = lambda: readers
    app.dependency_overrides[deps.get_semantic_proposer] = NullProposer
    return {"app": app, "ids": ids, "store": store, "readers": readers}


def _as(world, email: str) -> Supplier:  # noqa: ANN001
    s = Supplier(world["app"], email, PW)
    original = s.call

    def call(method: str, url: str, **kw: Any):  # noqa: ANN202
        r = original(method, url, **kw)
        if url.startswith("/api/") and r.headers.get("content-type", "").startswith("application/json"):
            SEEN.append(r.json())
        return r

    s.call = call  # type: ignore[method-assign]
    return s


def test_supplier_syncs_then_browses_real_products(world):
    alice, sid = _as(world, "alice@acme.io"), world["ids"]["acme"]
    assert alice.get(f"/api/systems/{sid}").json()["catalog"]["status"] == "complete"
    assert alice.get(f"/api/systems/{sid}/catalog/sync").json() is None

    r = alice.post(f"/api/systems/{sid}/catalog/sync")
    assert r.status_code == 200, r.text
    run = r.json()
    assert (run["status"], run["products"], run["variants"], run["categories"], run["images"]) == (
        "succeeded",
        3,
        4,
        2,
        1,
    )
    assert alice.get(f"/api/systems/{sid}/catalog/sync").json()["status"] == "succeeded"

    page = alice.get("/api/products", params={"system_id": sid}).json()
    assert page["total"] == 3
    rows = {p["name"]: p for p in page["items"]}
    assert (rows["Office Chair"]["variant_count"], rows["Office Chair"]["has_image"]) == (2, True)
    assert rows["Old Stool"]["status"] == "archived"
    assert rows["Desk Lamp"]["price"] == {"amount": 35.5, "currency": "EUR"}
    assert [
        p["name"] for p in alice.get("/api/products", params={"search": "CHAIR-01-RED"}).json()["items"]
    ] == ["Office Chair"]
    two = alice.get("/api/products", params={"page": 2, "page_size": 2}).json()
    assert (two["total"], len(two["items"])) == (3, 1)

    chair = alice.get(f"/api/products/{rows['Office Chair']['id']}").json()
    assert (chair["category"], chair["description"], chair["unit"]) == (
        "All / Furniture / Chairs",
        "Ergonomic office chair",
        "Units",
    )
    assert [(v["label"], v["sku"], v["price"]["amount"]) for v in chair["variants"]] == [
        ("Black · Standard", "CHAIR-01-BLK", 120.0),
        ("Red · Standard", "CHAIR-01-RED", 130.0),
    ]
    assert chair["attributes"] == [
        {"name": "Color", "values": ["Black", "Red"]},
        {"name": "Size", "values": ["Standard"]},
    ]
    assert chair["supplier_offers"][0]["supplier"] == "Wood & Co"
    assert {(x["label"], x["value"]) for x in chair["additional_information"]} == {
        ("Packaging code", "PK-10"),
        ("Supplier product code", "SUP-CH-1"),
    }

    img = alice.get(f"/api/products/{chair['id']}/image")
    assert (img.status_code, img.headers["content-type"]) == (200, "image/png")
    assert img.content.startswith(b"\x89PNG") and "private" in img.headers["cache-control"]
    red = chair["variants"][1]["id"]
    assert (
        alice.get(f"/api/products/{chair['id']}/variants/{red}/image", params={"size": "large"}).status_code
        == 200
    )
    black = chair["variants"][0]["id"]
    assert alice.get(f"/api/products/{chair['id']}/variants/{black}/image").status_code == 404
    assert alice.get(f"/api/products/{rows['Desk Lamp']['id']}/image").status_code == 404


def test_sync_waits_for_catalog_ready(world):
    bob, sid = _as(world, "bob@globex.io"), world["ids"]["globex"]
    r = bob.post(f"/api/systems/{sid}/catalog/sync")
    assert r.status_code == 409 and "Analyze" in r.json()["detail"]
    assert (world["readers"].opened, world["store"].reads) == (0, 0)


def test_other_tenant_products_do_not_exist(world):
    alice = _as(world, "alice@acme.io")
    assert alice.post(f"/api/systems/{world['ids']['acme']}/catalog/sync").status_code == 200
    chair = next(p for p in alice.get("/api/products").json()["items"] if p["name"] == "Office Chair")
    world["store"].reads, world["readers"].opened = 0, 0

    bob, missing = _as(world, "bob@globex.io"), str(uuid.uuid4())
    for url in (
        "/api/products/{}",
        "/api/products/{}/image",
        "/api/products/{}/variants/" + missing + "/image",
    ):
        foreign, unknown = bob.get(url.format(chair["id"])), bob.get(url.format(missing))
        assert foreign.status_code == unknown.status_code == 404, url
        assert foreign.json()["detail"].replace(chair["id"], "<id>") == unknown.json()["detail"].replace(
            missing, "<id>"
        )
    assert bob.get("/api/products", params={"system_id": world["ids"]["acme"]}).status_code == 404
    assert bob.get("/api/products").json()["total"] == 0
    assert bob.post(f"/api/systems/{world['ids']['acme']}/catalog/sync").status_code == 404
    assert (world["store"].reads, world["readers"].opened) == (0, 0)  # ni secret ni connexion


def test_admin_sees_the_read_plan_suppliers_do_not(world):
    admin, iid = _as(world, "admin@go45.local"), world["ids"]["acme"]
    plan = admin.get(f"/admin/instances/{iid}/catalog/read-plan").json()
    assert plan["primary"]["entity"] == "product.template"
    assert plan["variant"]["fields"]["list_price"]["field"] == "lst_price"
    assert len(plan["checksum"]) == 64
    assert _as(world, "alice@acme.io").get(f"/admin/instances/{iid}/catalog/read-plan").status_code == 403


def test_zz_catalog_responses_contain_no_source_or_technical_data():
    assert SEEN, "aucune réponse collectée"

    def walk(node: Any, path: str = "") -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                assert k not in TECHNICAL_KEYS | SOURCE_KEYS, f"clé {k!r} en {path}"
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        elif isinstance(node, str):
            assert not any(s in node for s in SOURCE_VALUES), f"valeur {node!r} en {path}"
            assert node not in {"STANDARD", "CUSTOM", "THIRD_PARTY"} and ODOO_PW not in node, path

    for body in SEEN:
        walk(body)
