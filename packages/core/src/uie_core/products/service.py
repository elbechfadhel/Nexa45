"""Lecture d'un système source vers le Product CDM (domaine PRODUCT, à la demande, rien n'est stocké).

    système source ──► plan PRODUCT (semantic bindings, clés `PRODUCT_KEYS`) + value_maps du pack ──► Product

Même moteur que le catalogue (V2a) : plan de lecture par RÔLE et par SEMANTIC KEY (`CatalogSyncService.
read_plan`), même lecteur du connecteur, mêmes règles de regroupement des variantes. Ce module ne remplit que
le modèle canonique : la sorte du produit vient des `value_maps` du pack (jamais devinée), les taxes de vente
sont des références de la source avec leur libellé (jamais un taux), le prix n'a pas de base net / brut.

Aucun nom de système ; aucune donnée de produit persistée (contrairement à la projection du catalogue)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session
from uie_cdm import Price, Product, ProductKind, ProductTaxRef, ProductVariant, VariantAttribute

from uie_core.catalog.plan import CatalogReadPlan, EntityPlan, KeySpec
from uie_core.catalog.reader import CatalogReadError, CatalogSource, SourceRecord
from uie_core.catalog.service import (
    BATCH,
    DEFAULT_MAX_PRODUCTS,
    CatalogError,
    CatalogNotReady,
    CatalogSyncService,
    _children,
    _get,
    _ids,
    _label,
    _number,
    _order,
    _requests,
    _text,
)
from uie_core.domains.model import EntityRole
from uie_core.identity.context import ensure_context
from uie_core.registry.service import NotFound

# Clés lues par rôle pour le Product CDM (même choix déterministe du champ que le catalogue)
PRODUCT_KEYS: dict[EntityRole, dict[str, KeySpec]] = {
    EntityRole.PRIMARY: {
        "name": KeySpec("value"),
        "sku": KeySpec("value"),
        "ean_gtin": KeySpec("value"),
        "list_price": KeySpec("value"),
        "currency": KeySpec("label"),
        "description_sale": KeySpec("value"),
        "uom": KeySpec("label"),
        "product_type": KeySpec("value"),
        "active": KeySpec("value"),
        "sales_taxes": KeySpec("labels"),  # collection : références des taxes de vente
    },
    EntityRole.VARIANT: {
        "product_template": KeySpec("link", EntityRole.PRIMARY),
        "sku": KeySpec("value"),
        "ean_gtin": KeySpec("value"),
        "list_price": KeySpec("value"),
        "active": KeySpec("value"),
        "variant_identity": KeySpec("collection", EntityRole.VARIANT_VALUE),
    },
    EntityRole.VARIANT_VALUE: {
        "attribute": KeySpec("label"),
        "attribute_value_name": KeySpec("value"),
    },
}
TYPE_KEY = "product_type"


class ProductError(CatalogError):
    """Lecture des produits impossible (→ 400)."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProductIssue(_Model):
    source_ref: str
    name: str | None
    message: str  # phrase simple (anglais), sans nom technique


class ProductReadResult(_Model):
    plan_checksum: str
    products: list[Product]
    issues: list[ProductIssue] = []
    truncated: bool = False  # plafond atteint : la liste n'est pas complète


class ProductReadService:
    """Produits d'une instance source, en Product CDM. Exige une analyse du catalogue COMPLÈTE (comme la
    synchronisation) : un champ exclu, ignoré ou encore en question n'est jamais lu."""

    def __init__(
        self, session: Session, catalog: CatalogSyncService, max_products: int = DEFAULT_MAX_PRODUCTS
    ) -> None:
        self.s = session
        self.catalog = catalog
        self.ctx = ensure_context(catalog.ctx)
        self.instances = catalog.instances
        self.max_products = max_products

    def read(self, instance_id: uuid.UUID) -> ProductReadResult:
        inst = self.instances.get(instance_id)  # contrôle d'appartenance AVANT tout secret
        try:
            summary = self.catalog.enrichment.summary(inst.id)
        except NotFound:
            raise CatalogNotReady("Catalog not analyzed yet: run Analyze first") from None
        if not summary.complete:
            raise CatalogNotReady(f"Catalog not ready: {summary.pending} question(s) to answer first")
        plan = self.catalog.read_plan(inst.id, PRODUCT_KEYS)
        kinds = _kind_map(self.catalog.domains.pack_for_instance(inst).value_maps(), plan)
        _, ctx = self.instances.connection_context(inst.id)
        ver = inst.interface.version
        try:
            reader = self.catalog.readers.resolve(ver.product.system.code, ver.version, inst.interface.code)
            products, issues, truncated = read_products(reader.open(ctx), plan, kinds, self.max_products)
        except CatalogReadError as exc:
            raise ProductError(f"The products could not be read: {exc}") from None
        return ProductReadResult(
            plan_checksum=plan.checksum, products=products, issues=issues, truncated=truncated
        )


def _kind_map(maps: list[Any], plan: CatalogReadPlan) -> dict[str, ProductKind]:
    """Valeur source → sorte canonique, d'après le `value_map` du pack pour LE champ retenu par le plan."""
    f = plan.primary.get(TYPE_KEY)
    if f is None:
        return {}
    for vm in maps:
        if (vm.key, vm.entity, vm.field) == (TYPE_KEY, plan.primary.entity, f.field):
            return {str(k): ProductKind(v) for k, v in vm.values.items()}
    return {}


def read_products(
    src: CatalogSource, plan: CatalogReadPlan, kinds: dict[str, ProductKind], max_products: int
) -> tuple[list[Product], list[ProductIssue], bool]:
    """Lecture par lots (produits, variantes, valeurs d'attribut, libellés des taxes), sans rien stocker."""
    p = plan.primary
    total = src.count(p.entity)
    wanted = min(total, max_products)
    values: dict[str, SourceRecord] = {}
    taxes: dict[str, str | None] = {}
    tax_field = p.get("sales_taxes")
    out: list[Product] = []
    issues: list[ProductIssue] = []
    for offset in range(0, wanted, BATCH):
        rows = src.read_page(p.entity, _requests(p), offset=offset, limit=min(BATCH, wanted - offset))
        variants = _children(src, plan.variant, [r.id for r in rows])
        if plan.variant_value and plan.variant:
            missing = {
                v
                for recs in variants.values()
                for rec in recs
                for v in _ids(rec, plan.variant, "variant_identity")
            } - set(values)
            values |= {
                v.id: v
                for v in src.read(plan.variant_value.entity, sorted(missing), _requests(plan.variant_value))
            }
        if tax_field is not None and tax_field.target:
            refs = {t for r in rows for t in _ids(r, p, "sales_taxes")} - set(taxes)
            # libellé seulement (aucun champ demandé) : le taux n'est jamais lu ni déduit
            taxes |= {t.id: t.label for t in src.read(tax_field.target, sorted(refs), [])}
        for r in rows:
            product, issue = _product(r, plan, kinds, variants.get(r.id, []), values, taxes)
            out.append(product)
            if issue:
                issues.append(issue)
    return out, issues, total > max_products


def _decimal(v: Any) -> Decimal | None:
    n = _number(v)
    return None if n is None else Decimal(repr(round(n, 6)))  # prix calculés : 19.900000000000002


def _price(amount: Decimal | None, currency: str | None) -> Price | None:
    return None if amount is None else Price(amount=amount, currency=currency)


def _kind(rec: SourceRecord, ep: EntityPlan, kinds: dict[str, ProductKind]) -> tuple[ProductKind, str | None]:
    """Sorte canonique et, si la valeur source n'est pas traduite, son libellé lisible (pour le signaler)."""
    f, v = ep.get(TYPE_KEY), _get(rec, ep, TYPE_KEY)
    if f is None or v in (None, ""):
        return ProductKind.UNKNOWN, None
    if str(v) in kinds:
        return kinds[str(v)], None
    label = next((lab for code, lab in (f.selection or []) if code == str(v)), None)
    return ProductKind.UNKNOWN, label or str(v)


def _product(
    rec: SourceRecord,
    plan: CatalogReadPlan,
    kinds: dict[str, ProductKind],
    variant_rows: list[SourceRecord],
    values: dict[str, SourceRecord],
    taxes: dict[str, str | None],
) -> tuple[Product, ProductIssue | None]:
    p = plan.primary
    currency = _label(rec, p, "currency")
    name = _text(_get(rec, p, "name")) or rec.label or rec.id
    variants = [_variant(v, plan, currency, values) for v in sorted(variant_rows, key=_order)]
    sku, gtin = _text(_get(rec, p, "sku")), _text(_get(rec, p, "ean_gtin"))
    if len(variants) == 1 and not variants[0].attributes:
        # produit sans déclinaison : sa variante unique EST le produit (même règle que le catalogue)
        sku, gtin = sku or variants[0].sku, gtin or variants[0].gtin
        variants = []
    kind, unsupported = _kind(rec, p, kinds)
    product = Product(
        source_ref=rec.id,
        name=name,
        kind=kind,
        sku=sku,
        gtin=gtin,
        description=_text(_get(rec, p, "description_sale")),
        unit=_label(rec, p, "uom"),
        price=_price(_decimal(_get(rec, p, "list_price")), currency),
        sales_taxes=[ProductTaxRef(ref=t, label=taxes.get(t)) for t in _ids(rec, p, "sales_taxes")],
        active=_get(rec, p, "active") if p.get("active") else None,
        variants=variants,
    )
    issue = None
    if unsupported:
        issue = ProductIssue(
            source_ref=rec.id,
            name=name,
            message=f"Product type « {unsupported} » is neither goods nor a service",
        )
    return product, issue


def _variant(
    rec: SourceRecord, plan: CatalogReadPlan, currency: str | None, values: dict[str, SourceRecord]
) -> ProductVariant:
    v = plan.variant
    assert v is not None
    attrs = []
    for vid in _ids(rec, v, "variant_identity"):
        val = values.get(vid)
        if val is None or plan.variant_value is None:
            continue
        value = _text(_get(val, plan.variant_value, "attribute_value_name")) or val.label
        if value:
            attrs.append(VariantAttribute(name=_label(val, plan.variant_value, "attribute"), value=value))
    return ProductVariant(
        source_ref=rec.id,
        sku=_text(_get(rec, v, "sku")),
        gtin=_text(_get(rec, v, "ean_gtin")),
        price=_price(_decimal(_get(rec, v, "list_price")), currency),
        active=_get(rec, v, "active") if v.get("active") else None,
        attributes=attrs,
    )
