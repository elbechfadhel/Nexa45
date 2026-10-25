"""Catalogue réel (V2a) : synchronisation, produits, fiches, images.

Supplier (`/api/*`, TenantContext) : DTO de `uie_api.supplier`, sans identifiant ni nom du système source.
Admin (`/admin/*`) : plan de lecture (quels champs de quelle entité sont lus), pour valider la chaîne
semantic bindings → CatalogProduct. Images lues à la demande dans le système source, jamais stockées.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response
from uie_core.registry import NotFound

from uie_api.deps import CatalogSync, TenantCatalog, TenantCatalogSync, TenantProductTarget
from uie_api.supplier import CatalogSyncOut, ProductDetailOut, ProductPageOut
from uie_api.supplier_partner_target import (
    LinkIn,
    MatchOut,
    ResolveIn,
    SendIn,
    SendRunOut,
    SendRunSummaryOut,
)
from uie_api.supplier_product_target import (
    PriceBasisIn,
    ProductTargetOut,
    ProductTargetPreviewOut,
    TaxRateIn,
)

router = APIRouter(prefix="/api", tags=["supplier-catalog"])
admin_router = APIRouter(prefix="/admin", tags=["catalog"])

SYSTEM = "/systems/{system_id}/catalog"
Size = Annotated[Literal["thumb", "large"], Query(description="thumb : vignette ; large : fiche produit")]
_SIGNATURES = {
    b"\x89PNG": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"GIF8": "image/gif",
    b"RIFF": "image/webp",
    b"<svg": "image/svg+xml",
    b"<?xm": "image/svg+xml",
}


def _image(data: bytes | None) -> Response:
    if not data:
        raise NotFound("Image introuvable")
    media = next((t for sig, t in _SIGNATURES.items() if data.startswith(sig)), "application/octet-stream")
    headers = {"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"}
    if media == "image/svg+xml":
        headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
    return Response(content=data, media_type=media, headers=headers)


@router.post(SYSTEM + "/sync", summary="Read your products from your system (read-only)")
def sync_catalog(sync: TenantCatalogSync, system_id: uuid.UUID) -> CatalogSyncOut:
    return CatalogSyncOut.of(sync.sync(system_id))


@router.get(SYSTEM + "/sync", summary="Last catalog synchronization (null if never)")
def last_sync(sync: TenantCatalogSync, system_id: uuid.UUID) -> CatalogSyncOut | None:
    run = sync.last_sync(system_id)
    return CatalogSyncOut.of(run) if run else None


@router.get("/products", summary="Your products: search and pages")
def list_products(
    catalog: TenantCatalog,
    system_id: uuid.UUID | None = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> ProductPageOut:
    return ProductPageOut.of(
        catalog.list_products(instance_id=system_id, search=search, page=page, page_size=page_size)
    )


@router.get("/products/{product_id}", summary="One product with its variants")
def get_product(catalog: TenantCatalog, product_id: uuid.UUID) -> ProductDetailOut:
    row, product = catalog.get_product(product_id)
    return ProductDetailOut.of(row, product)


@router.get(
    "/products/{product_id}/image",
    summary="Product image",
    response_class=Response,
    responses={200: {"content": {"image/*": {}}}},
)
def product_image(catalog: TenantCatalog, product_id: uuid.UUID, size: Size = "thumb") -> Response:
    return _image(catalog.product_image(product_id, size=size))


@router.get(
    "/products/{product_id}/variants/{variant_id}/image",
    summary="Variant image",
    response_class=Response,
    responses={200: {"content": {"image/*": {}}}},
)
def variant_image(
    catalog: TenantCatalog, product_id: uuid.UUID, variant_id: uuid.UUID, size: Size = "thumb"
) -> Response:
    return _image(catalog.product_image(product_id, variant_id, size=size))


@admin_router.get(
    "/instances/{instance_id}/catalog/read-plan",
    summary="Plan de lecture du catalogue : semantic key → entité.champ, déduit des bindings (Admin)",
)
def read_plan(sync: CatalogSync, instance_id: uuid.UUID) -> dict[str, Any]:
    plan = sync.read_plan(instance_id)
    return {"checksum": plan.checksum, **plan.model_dump(mode="json")}


# --------------------------------------------------------------------------- #
# Produits vers un système cible relié par son API : aperçu + décisions explicites (articles 1a), envoi des
# produits choisis après confirmation explicite (articles 1b)
# --------------------------------------------------------------------------- #
PRODUCT_TARGETS = "/systems/{system_id}/products/targets"
PRODUCT_TARGET = PRODUCT_TARGETS + "/{target_id}"


@router.get(PRODUCT_TARGETS, summary="Connected systems that can receive your products")
def product_targets(svc: TenantProductTarget, system_id: uuid.UUID) -> list[ProductTargetOut]:
    return [ProductTargetOut.of(t) for t in svc.targets(system_id)]


@router.get(
    PRODUCT_TARGET + "/preview",
    summary="Preview what each product would become in the target system "
    "(read-only: nothing is sent, nothing is saved)",
)
def product_target_preview(
    svc: TenantProductTarget, system_id: uuid.UUID, target_id: uuid.UUID
) -> ProductTargetPreviewOut:
    return ProductTargetPreviewOut.of(svc.preview(system_id, target_id))


@router.put(PRODUCT_TARGET + "/price-basis", summary="Say whether the prices of your system are net or gross")
def product_target_price_basis(
    svc: TenantProductTarget, system_id: uuid.UUID, target_id: uuid.UUID, body: PriceBasisIn
) -> Response:
    svc.set_price_basis(system_id, target_id, body.price_basis)
    return Response(status_code=204)


@router.delete(PRODUCT_TARGET + "/price-basis", summary="Clear your choice of net or gross prices")
def product_target_price_basis_clear(
    svc: TenantProductTarget, system_id: uuid.UUID, target_id: uuid.UUID
) -> Response:
    svc.clear_price_basis(system_id, target_id)
    return Response(status_code=204)


@router.put(
    PRODUCT_TARGET + "/tax-rates/{tax_reference}", summary="Choose the tax rate of one of your sales taxes"
)
def product_target_tax_rate(
    svc: TenantProductTarget, system_id: uuid.UUID, target_id: uuid.UUID, tax_reference: str, body: TaxRateIn
) -> Response:
    svc.set_tax_rate(system_id, target_id, tax_reference, body.rate, label=body.label)
    return Response(status_code=204)


@router.delete(
    PRODUCT_TARGET + "/tax-rates/{tax_reference}", summary="Clear the tax rate chosen for a sales tax"
)
def product_target_tax_rate_clear(
    svc: TenantProductTarget, system_id: uuid.UUID, target_id: uuid.UUID, tax_reference: str
) -> Response:
    svc.clear_tax_rate(system_id, target_id, tax_reference)
    return Response(status_code=204)


@router.post(
    PRODUCT_TARGET + "/sends",
    summary="Send the selected products to the target system (explicit confirmation required; the same "
    "request key never sends twice)",
)
def product_target_send(
    svc: TenantProductTarget, system_id: uuid.UUID, target_id: uuid.UUID, body: SendIn
) -> SendRunOut:
    view = svc.send(
        system_id,
        target_id,
        refs=body.references,
        request_key=body.request_key,
        confirm=body.confirm,
        create_new=body.create_new,
    )
    return SendRunOut.of(view)  # type: ignore[arg-type]  # même forme que l'envoi des partenaires


@router.get(PRODUCT_TARGET + "/sends", summary="Previous sends of products to this target system")
def product_target_sends(
    svc: TenantProductTarget, system_id: uuid.UUID, target_id: uuid.UUID
) -> list[SendRunSummaryOut]:
    return [SendRunSummaryOut.of(r) for r in svc.runs(system_id, target_id)]


@router.get(PRODUCT_TARGET + "/sends/{run_id}", summary="Result of a send of products, product by product")
def product_target_send_result(
    svc: TenantProductTarget, system_id: uuid.UUID, target_id: uuid.UUID, run_id: uuid.UUID
) -> SendRunOut:
    return SendRunOut.of(svc.run(system_id, target_id, run_id))  # type: ignore[arg-type]


@router.post(
    PRODUCT_TARGET + "/sends/{run_id}/resume",
    summary="Resume an interrupted send (products that were being written become 'unknown', never resent)",
)
def product_target_send_resume(
    svc: TenantProductTarget, system_id: uuid.UUID, target_id: uuid.UUID, run_id: uuid.UUID
) -> SendRunOut:
    return SendRunOut.of(svc.resume(system_id, target_id, run_id))  # type: ignore[arg-type]


@router.post(
    PRODUCT_TARGET + "/sends/{run_id}/items/{item_id}/resolve",
    summary="Tell us what you found in the target system for a product with an unknown outcome",
)
def product_target_send_resolve(
    svc: TenantProductTarget,
    system_id: uuid.UUID,
    target_id: uuid.UUID,
    run_id: uuid.UUID,  # noqa: ARG001 — lisibilité de l'adresse ; l'élément porte son envoi
    item_id: uuid.UUID,
    body: ResolveIn,
) -> SendRunOut:
    item = svc.resolve_unknown(
        system_id, target_id, item_id, created=body.created, remote_id=body.target_record_id
    )
    return SendRunOut.of(svc.run(system_id, target_id, item.run_id))  # type: ignore[arg-type]


@router.get(
    PRODUCT_TARGET + "/matches",
    summary="Existing articles of the target system that may be this product "
    "(same article number, read-only)",
)
def product_target_matches(
    svc: TenantProductTarget, system_id: uuid.UUID, target_id: uuid.UUID, reference: str
) -> list[MatchOut]:
    return [MatchOut.of(r) for r in svc.matches(system_id, target_id, reference)]


@router.post(
    PRODUCT_TARGET + "/links",
    status_code=204,
    summary="Link a product to an existing article of the target system "
    "(it will be updated, never duplicated)",
)
def product_target_link(
    svc: TenantProductTarget, system_id: uuid.UUID, target_id: uuid.UUID, body: LinkIn
) -> None:
    svc.link(system_id, target_id, body.reference, body.target_record_id)


@router.delete(
    PRODUCT_TARGET + "/links", status_code=204, summary="Remove a link you made that was never used by a send"
)
def product_target_unlink(
    svc: TenantProductTarget, system_id: uuid.UUID, target_id: uuid.UUID, reference: str
) -> None:
    svc.unlink(system_id, target_id, reference)
