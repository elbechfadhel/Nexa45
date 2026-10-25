"""Articles Lexware Office, phase 1a — Product CDM, lecture générique des produits, aperçu des articles.

Trois niveaux :
- PUR : unités vendables du Product CDM → requêtes d'articles (décisions D1-D6), sans base ni réseau ;
- FRONTIÈRES : ni nom de système dans le Core / le CDM, ni source dans le binding cible ; la traduction de la
  sorte d'un produit est une donnée du pack source, hors de la définition et des règles PRODUCT ;
- BOUT EN BOUT : domaine réel de customer17 + mini catalogue inventé servi par un faux Odoo, cible Lexware
  Office servie par un faux serveur. L'aperçu n'appelle jamais la cible, ne lit pas son secret, n'écrit rien.
"""

from __future__ import annotations

import ast
import copy
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import uie_domain_packs  # noqa: F401  (enregistre les packs et les bindings cibles)
from pydantic import SecretStr
from sqlalchemy import func, select
from test_catalog_customer17_v1b1 import DUMPS, PASSWORD, ROOT
from test_catalog_odoo_instance import FakeOdooResolver
from test_catalog_sync import RECORDS, CountingStore, Readers
from uie_cdm import Price, Product, ProductKind, ProductTaxRef, ProductVariant, VariantAttribute
from uie_connectors.lexware import LexwareClient, LexwareDiscoveryProvider, LexwareSchemaLoader
from uie_connectors.lexware.ratelimit import TokenBucket
from uie_connectors.lexware.testing import FAKE_KEY, FakeLexwareServer
from uie_connectors.odoo import OdooSchemaLoader
from uie_connectors.odoo.testing import FakeOdooServer
from uie_core.catalog.service import CatalogNotReady, CatalogSyncService
from uie_core.db.base import Base
from uie_core.domains import ProductDomainService, SemanticEnrichmentService
from uie_core.domains.model import DomainKey
from uie_core.domains.pack import DomainPackRegistry
from uie_core.exports.records import ExportValueMappingRecord
from uie_core.identity import MembershipRole, Organization, SystemContext, TenantContext
from uie_core.instances import ConnectionContext, InstanceCreate, InstanceService, ProviderResolver
from uie_core.mapping import (
    RecordRequestBuilderRegistry,
    RecordStatus,
    ResolvedBinding,
    TargetBindingRegistry,
)
from uie_core.mapping.engine import _check, _ordered
from uie_core.products import (
    ProductReadService,
    ProductTargetError,
    ProductTargetService,
    prepare_products,
)
from uie_core.registry import NotFound, RegistryService

SYSTEM = SystemContext("test")
BINDING_FILE = ROOT / "domain-packs" / "catalog" / "targets" / "lexware_articles.yaml"
CORE_PRODUCTS = ROOT / "packages" / "core" / "src" / "uie_core" / "products"
CDM_PRODUCT = ROOT / "packages" / "cdm" / "src" / "uie_cdm" / "product.py"
VAT19 = ProductTaxRef(ref="T1", label="19% VAT")
VAT7 = ProductTaxRef(ref="T2", label="7% VAT")


# --------------------------------------------------------------------------- #
# Outils PURS : binding résolu contre le contrat réel, sans base
# --------------------------------------------------------------------------- #
def resolved() -> ResolvedBinding:
    binding = TargetBindingRegistry.get("product", "lexware", "public-api")
    entity = LexwareSchemaLoader().build_snapshot().entity("articles")
    assert entity is not None
    r = ResolvedBinding(
        binding=binding,
        snapshot_id=uuid.uuid4(),
        snapshot_label="lexware public-api v1 @test",
        format_version="v1",
        metadata={},
        fields=_ordered(list(entity.fields)),
        header_fields=[],
    )
    _check(r)  # toutes les positions du binding existent dans le contrat
    return r


def product(**kw: Any) -> Product:
    base: dict[str, Any] = {
        "source_ref": "P-1",
        "name": "Office Chair",
        "kind": ProductKind.GOODS,
        "sku": "CH-1",
        "gtin": "4006381333931",
        "unit": "Units",
        "price": Price(amount=Decimal("120.0"), currency="EUR"),
        "sales_taxes": [VAT19],
        "active": True,
    }
    return Product(**(base | kw))


def prepare(*products: Product, basis: str | None = "NET", rates: dict[str, Decimal] | None = None):  # noqa: ANN201
    items = [i for p in products for i in p.items()]
    return prepare_products(
        items,
        resolved(),
        RecordRequestBuilderRegistry.get("lexware", "rest"),
        price_basis=basis,
        tax_rates={"T1": Decimal("19"), "T2": Decimal("7")} if rates is None else rates,
    )


def messages(p) -> list[str]:  # noqa: ANN001
    return [i.message for i in p.issues]


# --------------------------------------------------------------------------- #
# A. Product CDM : unités vendables (D1)
# --------------------------------------------------------------------------- #
def test_a_product_without_variant_is_one_sellable_item():
    (item,) = product().items()
    assert (item.source_ref, item.title, item.sku, item.price.amount) == ("P-1", "Office Chair", "CH-1", 120)


def test_each_variant_is_a_sellable_item_with_its_own_number_price_and_title():
    p = product(
        variants=[
            ProductVariant(
                source_ref="V1",
                sku="CH-1-BLK",
                attributes=[
                    VariantAttribute(name="Color", value="Black"),
                    VariantAttribute(name="Size", value="L"),
                ],
            ),
            ProductVariant(
                source_ref="V2",
                sku="CH-1-RED",
                price=Price(amount=Decimal("130"), currency="EUR"),
                attributes=[VariantAttribute(name="Color", value="Red")],
                active=False,
            ),
        ]
    )
    black, red = p.items()
    assert (black.source_ref, black.title, black.sku, black.price.amount) == (
        "P-1/V1",
        "Office Chair (Color: Black, Size: L)",
        "CH-1-BLK",
        120,  # prix du produit quand la variante n'en a pas
    )
    assert (red.title, red.price.amount, red.active) == ("Office Chair (Color: Red)", 130, False)
    assert black.sales_taxes == red.sales_taxes == [VAT19]  # taxes du produit


# --------------------------------------------------------------------------- #
# B. Préparation pure : D2-D6
# --------------------------------------------------------------------------- #
def test_ready_article_net_and_gross_follow_the_explicit_price_basis():
    (net,) = prepare(product(), basis="NET")
    assert net.status == RecordStatus.READY, messages(net)
    assert net.request == {
        "articleNumber": "CH-1",
        "gtin": "4006381333931",
        "price": {"leadingPrice": "NET", "netPrice": 120.0, "taxRate": 19.0},
        "title": "Office Chair",
        "type": "PRODUCT",
        "unitName": "Units",
    }
    (gross,) = prepare(product(), basis="GROSS")
    assert gross.request["price"] == {"leadingPrice": "GROSS", "grossPrice": 120.0, "taxRate": 19.0}


def test_without_a_price_basis_nothing_is_guessed():
    (p,) = prepare(product(), basis=None)
    assert p.status == RecordStatus.BLOCKED and p.request is None
    assert messages(p) == ["Choose whether the prices of the source are net or gross (Settings)"]


def test_tax_rate_is_never_guessed_from_the_source():
    no_tax, several, undecided = prepare(
        product(source_ref="A", sales_taxes=[]),
        product(source_ref="B", sales_taxes=[VAT19, VAT7]),
        product(source_ref="C", sales_taxes=[VAT19]),
        rates={},
    )
    assert messages(no_tax) == [
        "The source gives no sales tax for this product: its tax rate cannot be chosen"
    ]
    assert "several sales taxes" in messages(several)[0]
    assert messages(undecided) == ["Choose the tax rate of « 19% VAT » (Tax rates)"]
    assert {p.status for p in (no_tax, several, undecided)} == {RecordStatus.BLOCKED}


def test_sku_is_required_as_article_number_and_unknown_kinds_are_blocked():
    no_sku, kit, service = prepare(
        product(source_ref="A", sku=None),
        product(source_ref="B", kind=ProductKind.UNKNOWN),
        product(source_ref="C", kind=ProductKind.SERVICE),
    )
    assert messages(no_sku) == [
        "Lexware Office articles need an article number, and this product has no SKU in the source"
    ]
    assert messages(kit) == [
        "Only goods and services can be sent: the type of this product in the source is not supported"
    ]  # un seul message : la règle du binding remplace le « required » générique
    assert service.request["type"] == "SERVICE"


def test_only_eur_prices_an_invalid_gtin_is_left_out_and_the_unit_is_sent_as_is():
    usd, bad_gtin, no_unit = prepare(
        product(source_ref="A", price=Price(amount=Decimal("5"), currency="USD")),
        product(source_ref="B", gtin="123"),
        product(source_ref="C", unit=None),
    )
    assert messages(usd) == ["Lexware Office only accepts prices in EUR (this price is in USD)"]
    assert bad_gtin.status == RecordStatus.WARNING and "gtin" not in bad_gtin.request
    assert messages(bad_gtin) == ["GTIN does not have the format expected by the target: left out"]
    assert no_unit.status == RecordStatus.BLOCKED and messages(no_unit) == [
        "Unit is required and has no value"
    ]
    (units,) = prepare(product(unit="Stück"))
    assert units.request["unitName"] == "Stück"  # D4 : libellé de la source, jamais traduit


def test_no_price_in_the_source_blocks_the_article():
    (p,) = prepare(product(price=None))
    assert messages(p) == ["The product has no sales price in the source"]


def test_same_data_from_two_sources_gives_the_same_request_and_fingerprint():
    a, b = prepare(product(source_ref="ERP/4711"), product(source_ref="catalog.xlsx:row-2"))
    assert a.request == b.request and a.content.fingerprint == b.content.fingerprint
    assert "ERP/4711" not in str(a.content.units)


# --------------------------------------------------------------------------- #
# C. Frontières d'architecture
# --------------------------------------------------------------------------- #
SOURCE_WORDS = ("odoo", "product.template", "product.product", "detailed_type", "taxes_id", "xmlrpc")
TARGET_WORDS = ("lexware", "datev", "articlenumber", "unitname")


def _code_text(path: Path) -> str:
    """Code sans docstrings ni commentaires (les explications peuvent citer un exemple)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr):
            if isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                body.pop(0)
    return ast.unparse(tree).lower()


@pytest.mark.parametrize("path", [*sorted(CORE_PRODUCTS.glob("*.py")), CDM_PRODUCT], ids=lambda p: p.name)
def test_core_and_cdm_know_no_system(path: Path):
    text = _code_text(path)
    assert not [w for w in (*SOURCE_WORDS, *TARGET_WORDS) if w in text]


def test_the_target_binding_reads_only_the_product_cdm():
    text = "\n".join(
        line
        for line in BINDING_FILE.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ).lower()
    assert not [w for w in SOURCE_WORDS if w in text]


def test_the_lexware_connector_does_not_import_the_cdm_or_a_domain():
    for path in (ROOT / "packages" / "connectors" / "src" / "uie_connectors" / "lexware").glob("*.py"):
        imported = {
            n.module or ""
            for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(n, ast.ImportFrom)
        }
        assert not {
            m for m in imported if m.startswith(("uie_cdm", "uie_core.products", "uie_connectors.odoo"))
        }


def test_value_maps_change_neither_the_product_definition_nor_its_rules():
    """La traduction de la sorte (value_maps) est hors de la définition résolue et du checksum des règles :
    aucune analyse PRODUCT ne change (pas de nouvelle definition_version nécessaire)."""
    pack = DomainPackRegistry.resolve(DomainKey.PRODUCT, "odoo", "17")
    with_maps, without = type(pack)(), type(pack)()
    assert with_maps.value_maps()  # la table existe
    without.__dict__["spec"] = without.spec.model_copy(update={"value_maps": []})
    snapshot = OdooSchemaLoader({"json": DUMPS / "odoo17_ref.json"}).build_snapshot()
    assert with_maps.semantics().checksum == without.semantics().checksum
    assert with_maps.resolve(snapshot).model_dump() == without.resolve(snapshot).model_dump()


# --------------------------------------------------------------------------- #
# D. Bout en bout : faux Odoo (domaine réel de customer17) → Product CDM → aperçu d'articles (faux Lexware)
# --------------------------------------------------------------------------- #
TAXES = [{"id": 1, "display_name": "19% Umsatzsteuer"}, {"id": 2, "display_name": "7% Umsatzsteuer"}]


class Resolver(ProviderResolver):
    def __init__(self, odoo: FakeOdooServer, lexware: FakeLexwareServer) -> None:
        self.odoo, self.lexware = FakeOdooResolver(odoo), lexware

    def resolve(self, system: str, version: str, interface: str):  # noqa: ANN201
        if system != "lexware":
            return self.odoo.resolve(system, version, interface)

        def client(ctx: ConnectionContext) -> LexwareClient:
            noop = TokenBucket(clock=lambda: 0.0, sleep=lambda _s: None)
            assert ctx.secret is not None
            return LexwareClient(
                ctx.secret, transport=self.lexware.transport, bucket=noop, sleep=lambda _s: None
            )

        return LexwareDiscoveryProvider(client_factory=client)


@pytest.fixture
def world(session) -> dict[str, Any]:
    registry = RegistryService(session, SYSTEM)
    registry.publish_snapshot(OdooSchemaLoader({"json": DUMPS / "odoo17_ref.json"}).build_snapshot())
    registry.publish_snapshot(LexwareSchemaLoader().build_snapshot())
    odoo = FakeOdooServer.from_dump(DUMPS / "odoo17_customer.json", password=PASSWORD).with_records(RECORDS)
    odoo.records = copy.deepcopy(odoo.records)
    for row, taxes in zip(odoo.records["product.template"], ([1], [1], [2]), strict=True):
        row["taxes_id"] = taxes  # chaise et lampe : 19 % ; tabouret archivé : 7 %
    odoo.records["account.tax"] = TAXES
    lexware = FakeLexwareServer()
    store = CountingStore()
    orgs = {slug: Organization(name=slug.title(), slug=slug) for slug in ("acme", "globex")}
    session.add_all(orgs.values())
    session.flush()
    resolver = Resolver(odoo, lexware)
    admin = InstanceService(session, SYSTEM, store, resolver)
    inst: dict[str, Any] = {}
    for slug, org in orgs.items():
        src = admin.create(
            InstanceCreate(
                organization_id=org.id,
                name="customer17",
                system="odoo",
                version="17",
                endpoint="http://odoo:8069",
                config={"db": odoo.db, "username": "admin"},
                password=SecretStr(PASSWORD),
            )
        )
        admin.discover_schema(src.id)
        books = admin.create(
            InstanceCreate(
                organization_id=org.id,
                name="books",
                system="lexware",
                version="v1",
                endpoint="https://api.lexware.io",
                password=SecretStr(FAKE_KEY),
            )
        )
        inst[slug] = {"source": src, "target": books}
    domains = ProductDomainService(session, SYSTEM)
    enrichment = SemanticEnrichmentService(session, SYSTEM, domains=domains)
    domains.analyze(inst["acme"]["source"].id)
    store.reads = 0

    def service(slug: str) -> ProductTargetService:
        ctx = TenantContext(uuid.uuid4(), orgs[slug].id, MembershipRole.OWNER)
        i = InstanceService(session, ctx, store, resolver)
        catalog = CatalogSyncService(session, ctx, i, SemanticEnrichmentService(session, ctx), Readers(odoo))
        return ProductTargetService(session, ProductReadService(session, catalog))

    return {
        "session": session,
        "inst": inst,
        "enrichment": enrichment,
        "odoo": odoo,
        "lexware": lexware,
        "store": store,
        "acme": service("acme"),
        "globex": service("globex"),
    }


def _answer(world: dict[str, Any]) -> None:
    e, iid = world["enrichment"], world["inst"]["acme"]["source"].id
    e.confirm(iid, "product.template", "x_supplier_ref")
    e.change(iid, "product.template", "x_pack_code", "packaging_code")
    e.ignore(iid, "product.template", "x_notes_internal")


def _ids(world: dict[str, Any], slug: str = "acme") -> tuple[uuid.UUID, uuid.UUID]:
    return world["inst"][slug]["source"].id, world["inst"][slug]["target"].id


def _counts(session) -> dict[str, int]:  # noqa: ANN001
    return {
        t.name: session.scalar(select(func.count()).select_from(t)) or 0 for t in Base.metadata.sorted_tables
    }


def test_reading_waits_for_a_complete_catalog_analysis(world):
    with pytest.raises(CatalogNotReady, match="3 question"):
        world["acme"].preview(*_ids(world))


def test_products_are_read_generically_into_the_product_cdm(world):
    _answer(world)
    result = world["acme"].products.read(world["inst"]["acme"]["source"].id)
    by_name = {p.name: p for p in result.products}
    assert sorted(by_name) == ["Desk Lamp", "Office Chair", "Old Stool"]
    chair, lamp = by_name["Office Chair"], by_name["Desk Lamp"]
    assert chair.kind == lamp.kind == ProductKind.GOODS  # value_map du pack : consu → GOODS
    assert [(v.sku, v.price.amount) for v in chair.variants] == [
        ("CHAIR-01-BLK", Decimal("120.0")),
        ("CHAIR-01-RED", Decimal("130.0")),
    ]
    assert (lamp.sku, lamp.gtin, lamp.unit, lamp.price) == (
        "LAMP-01",
        "4006381333931",
        "Units",
        Price(amount=Decimal("35.5"), currency="EUR"),
    )
    assert lamp.sales_taxes == [ProductTaxRef(ref="1", label="19% Umsatzsteuer")]
    assert by_name["Old Stool"].active is False
    # taxes : libellé seulement, aucun champ demandé (le taux n'est jamais lu) ; jamais d'écriture
    assert "read" in {method for model, method in world["odoo"].calls if model == "account.tax"}
    assert not {method for _, method in world["odoo"].calls} & {"write", "create", "unlink"}


def test_preview_before_and_after_the_explicit_decisions(world):
    _answer(world)
    svc, (source, target) = world["acme"], _ids(world)
    before = svc.preview(source, target)
    assert (before.target, before.price_basis, before.bases, before.rates) == (
        "Lexware Office articles",
        None,
        ["NET", "GROSS"],
        ["0", "7", "19"],
    )
    assert (len(before.records), before.archived, before.count(RecordStatus.BLOCKED)) == (3, 1, 3)
    assert [(t.label, t.products, t.rate) for t in before.taxes] == [("19% Umsatzsteuer", 3, None)]

    svc.set_price_basis(source, target, "net")
    svc.set_tax_rate(source, target, "1", "19.0", label="19% Umsatzsteuer")
    after = svc.preview(source, target)
    assert after.count(RecordStatus.READY) == 3, [r.prepared.issues for r in after.records]
    titles = {r.prepared.title: r.prepared.request for r in after.records}
    assert sorted(titles) == [
        "Desk Lamp",
        "Office Chair (Color: Black, Size: Standard)",
        "Office Chair (Color: Red, Size: Standard)",
    ]
    red = titles["Office Chair (Color: Red, Size: Standard)"]
    assert (red["articleNumber"], red["price"]) == (
        "CHAIR-01-RED",
        {"leadingPrice": "NET", "netPrice": 130.0, "taxRate": 19.0},
    )
    assert {r.export_state.value for r in after.records} == {"NEW"}
    assert [t.rate for t in after.taxes] == ["19"]


def test_preview_never_calls_the_target_reads_no_target_secret_and_writes_nothing(world):
    _answer(world)
    svc, session, (source, target) = world["acme"], world["session"], _ids(world)
    svc.set_price_basis(source, target, "gross")
    session.flush()
    before, calls = _counts(session), len(world["lexware"].calls)
    first, second = svc.preview(source, target), svc.preview(source, target)
    session.flush()
    assert _counts(session) == before and not session.new and not session.dirty
    assert len(world["lexware"].calls) == calls and world["lexware"].writes == []
    assert first.model_dump() == second.model_dump()
    assert world["store"].reads == 2  # la clé de la SOURCE (lecture des produits), jamais celle de la cible
    assert FAKE_KEY not in str(first.model_dump())


def test_decisions_are_checked_against_the_registry_and_can_be_cleared(world):
    svc, session, (source, target) = world["acme"], world["session"], _ids(world)
    with pytest.raises(ProductTargetError, match="0, 7, 19"):
        svc.set_tax_rate(source, target, "1", "16")
    with pytest.raises(ProductTargetError, match="NET, GROSS"):
        svc.set_price_basis(source, target, "half")
    svc.set_tax_rate(source, target, "1", "7")
    svc.set_tax_rate(source, target, "1", "19")  # remplace la décision précédente
    rows = session.scalars(select(ExportValueMappingRecord)).all()
    assert [(r.kind, r.source_ref, r.target_value, r.origin) for r in rows] == [
        ("TAX_RATE", "1", "19", "MANUAL")
    ]
    assert svc.clear_tax_rate(source, target, "1") is True
    assert session.scalar(select(func.count()).select_from(ExportValueMappingRecord)) == 0


def test_another_organization_sees_neither_the_source_nor_the_target(world):
    a, b = _ids(world, "acme"), _ids(world, "globex")
    svc = world["globex"]
    assert [t.instance_id for t in svc.targets(b[0])] == [b[1]]
    for source, target in ((a[0], b[1]), (b[0], a[1])):
        with pytest.raises(NotFound):
            svc.preview(source, target)
        with pytest.raises(NotFound):
            svc.set_price_basis(source, target, "net")
    with pytest.raises(NotFound):
        svc.targets(a[0])
    assert world["store"].reads == 0  # aucun secret lu


def test_a_system_that_cannot_receive_products_is_not_a_target(world):
    svc, (source, target) = world["acme"], _ids(world)
    assert [t.target for t in svc.targets(source)] == ["Lexware Office articles"]
    with pytest.raises(NotFound):
        svc.preview(target, source)  # la source Odoo ne reçoit pas de produits
    with pytest.raises(ProductTargetError):
        svc.preview(target, target)
