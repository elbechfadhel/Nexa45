"""Routes Product Domain (V1a) : API → ProductDomainService → Core. Aucune logique de domaine ici.

L'analyse travaille uniquement sur des snapshots déjà persistés : elle ne contacte jamais l'instance.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from uie_core.domains import (
    DomainEntityDef,
    DomainFieldDef,
    DomainMissingElement,
    EntityRole,
    FieldGroup,
    FieldOrigin,
    Relevance,
)

from uie_api.deps import ProductDomains
from uie_api.schemas_domains import DomainSnapshotHistoryOut, DomainSnapshotOut, StandardDefinitionOut

router = APIRouter(tags=["product-domain"])

SnapshotQ = Annotated[
    uuid.UUID | None, Query(description="Domain snapshot précis (dernier analysé par défaut)")
]


@router.get(
    "/admin/product-domains/standard",
    summary="Définition standard du domaine PRODUCT pour une version (résolue sur le snapshot standard)",
)
def get_standard_definition(
    svc: ProductDomains,
    system: str,
    version: str,
    snapshot_version: Annotated[str | None, Query(description="Snapshot standard précis")] = None,
) -> StandardDefinitionOut:
    return StandardDefinitionOut.of(svc.get_standard_definition(system, version, snapshot_version))


@router.post(
    "/admin/instances/{instance_id}/product-domain/analyze",
    summary=(
        "Construit le domaine effectif (snapshot d'instance persisté, dernier par défaut ; idempotent) "
        "puis Semantic Enrichment (règles + décisions existantes)"
    ),
)
def analyze(
    svc: ProductDomains,
    instance_id: uuid.UUID,
    instance_schema_snapshot_id: Annotated[
        uuid.UUID | None, Query(description="Snapshot d'instance précis (dernier par défaut)")
    ] = None,
) -> DomainSnapshotOut:
    return DomainSnapshotOut.analyzed(svc.analyze(instance_id, instance_schema_snapshot_id))


@router.get("/admin/instances/{instance_id}/product-domain", summary="Domaine effectif (dernier analysé)")
def get_domain(
    svc: ProductDomains, instance_id: uuid.UUID, snapshot_id: SnapshotQ = None
) -> DomainSnapshotOut:
    return DomainSnapshotOut.of(svc.get_instance_domain(instance_id, snapshot_id))


@router.get(
    "/admin/instances/{instance_id}/product-domain/entities", summary="Entités du domaine et leur rôle"
)
def list_entities(
    svc: ProductDomains,
    instance_id: uuid.UUID,
    snapshot_id: SnapshotQ = None,
    role: EntityRole | None = None,
) -> list[DomainEntityDef]:
    return svc.list_domain_entities(instance_id, snapshot_id, role=role)


@router.get("/admin/instances/{instance_id}/product-domain/fields", summary="Champs du domaine effectif")
def list_fields(
    svc: ProductDomains,
    instance_id: uuid.UUID,
    snapshot_id: SnapshotQ = None,
    group: FieldGroup | None = None,
    origin: FieldOrigin | None = None,
    relevance: Relevance | None = None,
    entity: str | None = None,
) -> list[DomainFieldDef]:
    return svc.list_domain_fields(
        instance_id, snapshot_id, group=group, origin=origin, relevance=relevance, entity=entity
    )


@router.get(
    "/admin/instances/{instance_id}/product-domain/extensions",
    summary="Extensions CUSTOM / THIRD_PARTY du scope (relevance UNRESOLVED en V1a)",
)
def list_extensions(
    svc: ProductDomains, instance_id: uuid.UUID, snapshot_id: SnapshotQ = None
) -> list[DomainFieldDef]:
    return svc.list_extensions(instance_id, snapshot_id)


@router.get(
    "/admin/instances/{instance_id}/product-domain/missing-standard",
    summary="Éléments du standard (baseline) absents de l'instance",
)
def list_missing_standard(
    svc: ProductDomains, instance_id: uuid.UUID, snapshot_id: SnapshotQ = None
) -> list[DomainMissingElement]:
    return svc.list_missing_standard(instance_id, snapshot_id)


@router.get(
    "/admin/instances/{instance_id}/product-domain/snapshots",
    summary="Historique des domain snapshots (immutables) de l'instance",
)
def list_snapshots(svc: ProductDomains, instance_id: uuid.UUID) -> list[DomainSnapshotHistoryOut]:
    return [DomainSnapshotHistoryOut.of(s) for s in svc.list_instance_domains(instance_id)]
