"""Synchronisation et lecture du catalogue réel (V2a).

    CatalogSyncService   plan de lecture (Product Domain + bindings) → lecteur du connecteur → CatalogProduct
                         canonique (produit + variantes regroupées) → catalog_products (idempotent)
    CatalogReadService   liste / recherche / détail / image, TOUJOURS filtrés par l'instance du contexte

Contexte obligatoire (V1d) : une instance ou un produit d'un autre tenant est introuvable, exactement comme un
id inexistant, AVANT tout accès au SecretStore ou au système source. Lecture seule vers le système source.
Aucun nom de système ici : entités et champs viennent du plan de lecture.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from uie_core.catalog.model import (
    CATALOG_MODEL_VERSION,
    AttributeValue,
    CatalogProduct,
    CatalogVariant,
    ExtraAttribute,
    Money,
    ProductAttribute,
    SupplierOffer,
)
from uie_core.catalog.plan import BoundField, CatalogReadPlan, EntityPlan, KeySpec, build_read_plan
from uie_core.catalog.reader import (
    CatalogReaderResolver,
    CatalogReadError,
    CatalogSource,
    Condition,
    FieldRequest,
    Ref,
    SourceRecord,
)
from uie_core.catalog.records import CatalogProductRecord, CatalogSyncRecord
from uie_core.db.base import utcnow
from uie_core.domains.enrichment import SemanticEnrichmentService
from uie_core.domains.model import EntityRole
from uie_core.domains.semantics import DecisionSource, MeaningStatus
from uie_core.identity.context import Context, ensure_context, tenant_organization
from uie_core.instances.service import InstanceService
from uie_core.registry import models as m
from uie_core.registry.service import NotFound, RegistryError

BATCH = 200
DEFAULT_MAX_PRODUCTS = 10_000
USABLE = {MeaningStatus.KNOWN.value, MeaningStatus.CONFIRMED.value}
_VARIANT_NS = uuid.UUID("5b0f3c1e-7a51-4c8e-9d0e-2a1f6c7b8d90")


class CatalogError(RegistryError):
    """Opération de catalogue impossible (→ HTTP 400)."""


class CatalogNotReady(CatalogError):
    """Analyse du catalogue absente ou incomplète : la synchronisation attend « Catalog ready » (→ 409)."""


def _same_context(*ctxs: Context) -> Context:
    first = ensure_context(ctxs[0])
    if any(c is not first and c != first for c in ctxs[1:]):
        raise ValueError("Les services du catalogue doivent partager le même contexte")
    return first


# --------------------------------------------------------------------------- #
# Synchronisation
# --------------------------------------------------------------------------- #
class CatalogSyncService:
    def __init__(
        self,
        session: Session,
        ctx: Context,
        instances: InstanceService,
        enrichment: SemanticEnrichmentService,
        readers: CatalogReaderResolver | None = None,
        max_products: int = DEFAULT_MAX_PRODUCTS,
    ) -> None:
        self.s = session
        self.ctx = _same_context(ctx, instances.ctx, enrichment.ctx)
        self.instances = instances
        self.enrichment = enrichment
        self.domains = enrichment.domains
        self.readers = readers or CatalogReaderResolver()
        self.max_products = max_products

    def read_plan(
        self, instance_id: uuid.UUID, keys: dict[EntityRole, dict[str, KeySpec]] | None = None
    ) -> CatalogReadPlan:
        """Plan de lecture courant : dernier domaine analysé + bindings KNOWN / CONFIRMED. `keys` : clés par
        rôle (défaut : affichage V2a ; l'import de fichiers passe les siennes)."""
        inst = self.domains.instance(instance_id)  # contrôle d'appartenance
        stored = self.domains.get_instance_domain(inst.id)
        rows = self.s.scalars(
            select(m.FieldSemanticBinding).where(
                m.FieldSemanticBinding.instance_id == inst.id,
                m.FieldSemanticBinding.domain_key == self.domains.domain_key.value,
                m.FieldSemanticBinding.meaning_status.in_(USABLE),
                m.FieldSemanticBinding.semantic_key.is_not(None),
                m.FieldSemanticBinding.pending.is_(False),
            )
        )
        bound = [
            BoundField(
                entity=r.entity_technical_name,
                field=r.field_technical_name,
                key=r.semantic_key or "",
                human=r.source == DecisionSource.HUMAN.value,
                pack=r.source == DecisionSource.STANDARD.value,
            )
            for r in rows
        ]
        snap = stored.snapshot
        return build_read_plan(
            snap.entities,
            [*snap.fields, *snap.extensions],
            bound,
            media_size=self.domains.pack_for_instance(inst).media_size,
            labels={k.key: k.label for k in self.enrichment.vocabulary().keys},
            domain_snapshot_id=stored.id,
            keys=keys,
        )

    def sync(self, instance_id: uuid.UUID) -> CatalogSyncRecord:
        """Lit le catalogue du système source et met à jour catalog_products. Refusée tant que l'analyse n'est
        pas complète. Un échec de lecture est consigné (status FAILED), jamais une exception."""
        inst = self.instances.get(instance_id)  # contrôle d'appartenance AVANT tout secret
        try:
            summary = self.enrichment.summary(inst.id)
        except NotFound:
            raise CatalogNotReady("Catalog not analyzed yet: run Analyze first") from None
        if not summary.complete:
            raise CatalogNotReady(f"Catalog not ready: {summary.pending} question(s) to answer first")
        plan = self.read_plan(inst.id)
        run = CatalogSyncRecord(
            instance_id=inst.id,
            product_domain_snapshot_id=plan.domain_snapshot_id,
            read_plan=plan.model_dump(mode="json"),
            read_plan_checksum=plan.checksum,
            catalog_model_version=CATALOG_MODEL_VERSION,
            status="RUNNING",
            started_at=utcnow(),
        )
        self.s.add(run)
        self.s.flush()
        try:
            _, ctx = self.instances.connection_context(inst.id)
            ver = inst.interface.version
            reader = self.readers.resolve(ver.product.system.code, ver.version, inst.interface.code)
            products, truncated = _read_catalog(reader.open(ctx), plan, inst.id, self.max_products)
        except CatalogReadError as exc:
            run.status, run.error, run.finished_at = "FAILED", str(exc), utcnow()
            self.s.flush()
            return run
        self._store(inst.id, run, products, truncated)
        run.status, run.finished_at, run.truncated = "SUCCEEDED", utcnow(), truncated
        self.s.flush()
        return run

    def last_sync(self, instance_id: uuid.UUID) -> CatalogSyncRecord | None:
        inst = self.domains.instance(instance_id)
        return self.s.scalar(
            select(CatalogSyncRecord)
            .where(CatalogSyncRecord.instance_id == inst.id)
            .order_by(CatalogSyncRecord.started_at.desc())
            .limit(1)
        )

    # ------------------------------------------------------------------ #
    def _store(
        self, instance_id: uuid.UUID, run: CatalogSyncRecord, products: list[CatalogProduct], truncated: bool
    ) -> None:
        existing = {
            r.source_ref: r
            for r in self.s.scalars(
                select(CatalogProductRecord).where(CatalogProductRecord.instance_id == instance_id)
            )
        }
        now, seen = utcnow(), set()
        for p in products:
            seen.add(p.source_ref)
            payload = p.model_dump(mode="json")
            digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            row = existing.get(p.source_ref)
            if row is not None and row.content_hash == digest and row.removed_at is None:
                run.unchanged += 1
                row.last_sync_id = run.id
                continue
            if row is None:
                row = CatalogProductRecord(instance_id=instance_id, source_ref=p.source_ref)
                self.s.add(row)
                run.created += 1
            else:
                run.updated += 1
            row.name, row.sku, row.category = p.name, p.sku, p.category
            row.list_price = p.price.amount if p.price else None
            row.currency = p.price.currency if p.price else None
            row.active, row.has_image, row.variant_count = p.active, p.has_image, len(p.variants)
            row.search_text, row.payload, row.content_hash = p.search_text, payload, digest
            row.last_sync_id, row.removed_at, row.updated_at = run.id, None, now
        if not truncated:  # lecture partielle (plafond) : on ne peut rien déclarer disparu
            for ref, row in existing.items():
                if ref not in seen and row.removed_at is None:
                    row.removed_at = now
                    run.removed += 1
        run.products = len(products)
        run.variants = sum(max(len(p.variants), 1) for p in products)
        run.categories = len({p.category for p in products if p.category})
        run.images = sum(1 for p in products if p.has_image)


def _read_catalog(
    src: CatalogSource, plan: CatalogReadPlan, instance_id: uuid.UUID, max_products: int
) -> tuple[list[CatalogProduct], bool]:
    p = plan.primary
    total = src.count(p.entity)
    wanted = min(total, max_products)
    categories: dict[str, SourceRecord] = {}
    values: dict[str, SourceRecord] = {}
    out: list[CatalogProduct] = []
    for offset in range(0, wanted, BATCH):
        rows = src.read_page(p.entity, _requests(p), offset=offset, limit=min(BATCH, wanted - offset))
        ids = [r.id for r in rows]
        variants = _children(src, plan.variant, ids)
        offers = _children(src, plan.supplier_info, ids)
        if plan.category:
            refs = {_ref(r, p, "category") for r in rows} - {None, *categories}
            categories |= {
                c.id: c for c in src.read(plan.category.entity, sorted(refs), _requests(plan.category))
            }
        if plan.variant_value and plan.variant:
            wanted_values = {
                v
                for recs in variants.values()
                for rec in recs
                for v in _ids(rec, plan.variant, "variant_identity")
            } - set(values)
            values |= {
                v.id: v
                for v in src.read(
                    plan.variant_value.entity, sorted(wanted_values), _requests(plan.variant_value)
                )
            }
        for r in rows:
            out.append(
                _product(
                    r, plan, instance_id, variants.get(r.id, []), offers.get(r.id, []), categories, values
                )
            )
    return out, total > max_products


def _requests(ep: EntityPlan) -> list[FieldRequest]:
    return [FieldRequest(name=f.field, data_type=f.data_type) for f in ep.requested()]


def _children(
    src: CatalogSource, ep: EntityPlan | None, parent_ids: list[str]
) -> dict[str, list[SourceRecord]]:
    """Enregistrements liés au produit par leur clé de lien (variantes, offres), groupés par produit."""
    if ep is None or not parent_ids:
        return {}
    link = ep.fields["product_template"].field
    grouped: dict[str, list[SourceRecord]] = {}
    offset = 0
    while True:
        page = src.read_page(
            ep.entity,
            _requests(ep),
            where=[Condition(field=link, values=parent_ids)],
            offset=offset,
            limit=BATCH,
        )
        for rec in page:
            ref = rec.values.get(link)
            if isinstance(ref, Ref):
                grouped.setdefault(ref.id, []).append(rec)
        if len(page) < BATCH:
            return grouped
        offset += BATCH


def _get(rec: SourceRecord, ep: EntityPlan, key: str) -> Any:
    f = ep.get(key)
    return rec.values.get(f.field) if f else None


def _ref(rec: SourceRecord, ep: EntityPlan, key: str) -> str | None:
    v = _get(rec, ep, key)
    return v.id if isinstance(v, Ref) else None


def _label(rec: SourceRecord, ep: EntityPlan, key: str) -> str | None:
    v = _get(rec, ep, key)
    return v.label if isinstance(v, Ref) else None


def _ids(rec: SourceRecord, ep: EntityPlan, key: str) -> list[str]:
    v = _get(rec, ep, key)
    return list(v) if isinstance(v, list) else []


def _text(v: Any) -> str | None:
    return str(v) if v not in (None, "") else None


def _number(v: Any) -> float | None:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def _money(amount: float | None, currency: str | None) -> Money | None:
    """Montant arrondi (les prix calculés en virgule flottante donnent 19.900000000000002)."""
    return Money(amount=round(amount, 6), currency=currency) if amount is not None else None


def _selection_label(rec: SourceRecord, ep: EntityPlan, key: str) -> str | None:
    """Valeur de sélection → libellé lisible ; sans libellé connu : None (jamais un code technique)."""
    f, v = ep.get(key), _get(rec, ep, key)
    if f is None or v is None:
        return None
    if f.selection:
        return next((label for code, label in f.selection if code == str(v)), None)
    return None if f.data_type == "enum" else _text(v)


def _has_image(rec: SourceRecord, ep: EntityPlan) -> bool:
    return bool(ep.media and rec.values.get(ep.media.thumb))


def _product(
    rec: SourceRecord,
    plan: CatalogReadPlan,
    instance_id: uuid.UUID,
    variant_rows: list[SourceRecord],
    offer_rows: list[SourceRecord],
    categories: dict[str, SourceRecord],
    values: dict[str, SourceRecord],
) -> CatalogProduct:
    p = plan.primary
    currency = _label(rec, p, "currency")
    price = _number(_get(rec, p, "list_price"))
    category = None
    if cat_id := _ref(rec, p, "category"):
        cat = categories.get(cat_id)
        if cat and plan.category:
            category = _text(_get(cat, plan.category, "category_path")) or _text(
                _get(cat, plan.category, "category_name")
            )
        category = category or _label(rec, p, "category")
    variants = [_variant(v, plan, instance_id, currency, values) for v in sorted(variant_rows, key=_order)]
    sku, barcode = _text(_get(rec, p, "sku")), _text(_get(rec, p, "ean_gtin"))
    if len(variants) == 1 and not variants[0].attributes:
        # produit sans déclinaison : sa variante unique EST le produit
        sku, barcode = sku or variants[0].sku, barcode or variants[0].barcode
        variants = []
    return CatalogProduct(
        source_ref=rec.id,
        name=_text(_get(rec, p, "name")) or rec.label,
        sku=sku,
        barcode=barcode,
        category=category,
        price=_money(price, currency),
        description=_text(_get(rec, p, "description_sale")),
        weight=_number(_get(rec, p, "weight")),
        volume=_number(_get(rec, p, "volume")),
        unit=_label(rec, p, "uom"),
        product_type=_selection_label(rec, p, "product_type"),
        active=_get(rec, p, "active") if p.get("active") else None,
        has_image=_has_image(rec, p),
        variants=variants,
        attributes=_attributes(variants),
        supplier_offers=[_offer(o, plan) for o in sorted(offer_rows, key=_order)]
        if plan.supplier_info
        else [],
        extra=[
            ExtraAttribute(key=f.key, label=f.label or f.key, value=rec.values.get(f.field))
            for f in p.extras
            if rec.values.get(f.field) not in (None, "")
        ],
    )


def _order(rec: SourceRecord) -> tuple[int, str]:
    return (int(rec.id), rec.id) if rec.id.isdigit() else (0, rec.id)


def _variant(
    rec: SourceRecord,
    plan: CatalogReadPlan,
    instance_id: uuid.UUID,
    currency: str | None,
    values: dict[str, SourceRecord],
) -> CatalogVariant:
    v = plan.variant
    assert v is not None
    attrs = []
    for vid in _ids(rec, v, "variant_identity"):
        val = values.get(vid)
        if val is None or plan.variant_value is None:
            continue
        name = _label(val, plan.variant_value, "attribute")
        value = _text(_get(val, plan.variant_value, "attribute_value_name")) or val.label
        if value:
            attrs.append(AttributeValue(name=name, value=value))
    price = _number(_get(rec, v, "list_price"))
    return CatalogVariant(
        id=uuid.uuid5(_VARIANT_NS, f"{instance_id}/{v.entity}/{rec.id}"),
        source_ref=rec.id,
        name=_text(_get(rec, v, "name")),
        sku=_text(_get(rec, v, "sku")),
        barcode=_text(_get(rec, v, "ean_gtin")),
        price=_money(price, currency),
        active=_get(rec, v, "active") if v.get("active") else None,
        weight=_number(_get(rec, v, "weight")),
        attributes=attrs,
        has_image=_has_image(rec, v),
    )


def _attributes(variants: Iterable[CatalogVariant]) -> list[ProductAttribute]:
    grouped: dict[str, list[str]] = {}
    for v in variants:
        for a in v.attributes:
            vals = grouped.setdefault(a.name or "Option", [])
            if a.value not in vals:
                vals.append(a.value)
    return [ProductAttribute(name=k, values=vals) for k, vals in grouped.items()]


def _offer(rec: SourceRecord, plan: CatalogReadPlan) -> SupplierOffer:
    s = plan.supplier_info
    assert s is not None
    price = _number(_get(rec, s, "supplier_price"))
    return SupplierOffer(
        supplier=_label(rec, s, "supplier_name") or rec.label,
        supplier_reference=_text(_get(rec, s, "supplier_reference")),
        price=_money(price, _label(rec, s, "currency")),
        min_quantity=_number(_get(rec, s, "supplier_min_qty")),
        lead_time_days=_number(_get(rec, s, "supplier_lead_time")),
    )


# --------------------------------------------------------------------------- #
# Lecture
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ProductPage:
    total: int
    page: int
    page_size: int
    items: list[CatalogProductRecord]


class CatalogReadService:
    def __init__(
        self,
        session: Session,
        ctx: Context,
        instances: InstanceService,
        readers: CatalogReaderResolver | None = None,
    ) -> None:
        self.s = session
        self.ctx = _same_context(ctx, instances.ctx)
        self.instances = instances
        self.readers = readers or CatalogReaderResolver()

    def _scoped(self):  # noqa: ANN202
        """Point de filtrage des produits : ceux des instances visibles dans le contexte (V1d)."""
        q = (
            select(CatalogProductRecord)
            .join(m.Instance, m.Instance.id == CatalogProductRecord.instance_id)
            .where(CatalogProductRecord.removed_at.is_(None))
        )
        org = tenant_organization(self.ctx)
        return q.where(m.Instance.organization_id == org) if org is not None else q

    def list_products(
        self,
        *,
        instance_id: uuid.UUID | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> ProductPage:
        page, page_size = max(page, 1), min(max(page_size, 1), 100)
        q = self._scoped()
        if instance_id is not None:
            self.instances.get(instance_id)  # autre tenant → NotFound, comme un id inexistant
            q = q.where(CatalogProductRecord.instance_id == instance_id)
        if search and search.strip():
            term = f"%{search.strip().lower()}%"
            q = q.where(
                or_(
                    CatalogProductRecord.search_text.like(term),
                    func.lower(CatalogProductRecord.category).like(term),
                )
            )
        total = self.s.scalar(select(func.count()).select_from(q.subquery())) or 0
        rows = list(
            self.s.scalars(
                q.order_by(func.lower(CatalogProductRecord.name), CatalogProductRecord.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return ProductPage(total=total, page=page, page_size=page_size, items=rows)

    def get_product(self, product_id: uuid.UUID) -> tuple[CatalogProductRecord, CatalogProduct]:
        row = self.s.scalar(self._scoped().where(CatalogProductRecord.id == product_id))
        if row is None:
            raise NotFound(f"Produit {product_id} introuvable")
        return row, CatalogProduct.model_validate(row.payload)

    def product_image(
        self, product_id: uuid.UUID, variant_id: uuid.UUID | None = None, size: str = "thumb"
    ) -> bytes | None:
        """Octets de l'image lus À LA DEMANDE dans le système source (jamais stockés). Produit contrôlé
        D'ABORD ; puis entité et champ d'après le plan de la synchronisation qui a produit la fiche."""
        row, product = self.get_product(product_id)
        sync = self.s.get(CatalogSyncRecord, row.last_sync_id) if row.last_sync_id else None
        if sync is None:
            return None
        plan = CatalogReadPlan.model_validate(sync.read_plan)
        ep, ref, present = plan.primary, product.source_ref, product.has_image
        if variant_id is not None:
            variant = next((v for v in product.variants if v.id == variant_id), None)
            if variant is None:
                raise NotFound(f"Variante {variant_id} introuvable")
            if plan.variant is None:
                return None
            ep, ref, present = plan.variant, variant.source_ref, variant.has_image
        if ep.media is None or not present:
            return None
        field = ep.media.large if size == "large" else ep.media.thumb
        inst, ctx = self.instances.connection_context(row.instance_id)
        ver = inst.interface.version
        reader = self.readers.resolve(ver.product.system.code, ver.version, inst.interface.code)
        return reader.open(ctx).read_binary(ep.entity, ref, field)
