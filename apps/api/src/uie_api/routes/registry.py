"""Routes Registry (lecture seule) — document SKR §16.

Chaque route appelle RegistryService puis convertit en schéma de réponse : aucune logique métier ici.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from uie_core.registry import SnapshotRef

from uie_api.deps import Registry
from uie_api.schemas import (
    CompareOut,
    EntityDetail,
    EntityPage,
    EntitySummary,
    FieldHit,
    FieldOut,
    SnapshotDetail,
    SystemNode,
)

router = APIRouter(prefix="/admin", tags=["registry"])


def _snapshot_detail(svc: Registry, snapshot_id: uuid.UUID) -> SnapshotDetail:
    snap = svc.get_snapshot(snapshot_id)
    return SnapshotDetail.build(snap, label=svc.snapshot_label(snap), stats=svc.stats(snap.id))


@router.get("/systems", summary="Arbre System → Product → Version → Interface → Snapshots")
def list_systems(svc: Registry) -> list[SystemNode]:
    return [SystemNode.of(s) for s in svc.list_systems()]


@router.get("/snapshots/resolve", summary="Résout system/version (dernier snapshot par défaut)")
def resolve_snapshot(
    svc: Registry,
    system: str,
    version: str,
    product: str | None = None,
    interface: str | None = None,
    snapshot_version: str | None = None,
) -> SnapshotDetail:
    ref = SnapshotRef(
        system=system,
        version=version,
        product=product,
        interface=interface,
        snapshot_version=snapshot_version,
    )
    return _snapshot_detail(svc, svc.resolve_snapshot(ref).id)


@router.get("/schema-snapshots/{snapshot_id}", summary="Snapshot : identité, provenance, statistiques")
def get_snapshot(svc: Registry, snapshot_id: uuid.UUID) -> SnapshotDetail:
    return _snapshot_detail(svc, snapshot_id)


@router.get("/schema-snapshots/{snapshot_id}/entities", summary="Entités d'un snapshot (paginées)")
def list_entities(
    svc: Registry,
    snapshot_id: uuid.UUID,
    search: Annotated[str | None, Query(description="Filtre sur le nom technique (contient)")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> EntityPage:
    svc.get_snapshot(snapshot_id)  # 404 si le snapshot n'existe pas
    items, total = svc.page_entities(snapshot_id, search, limit=limit, offset=offset)
    counts = svc.field_counts(snapshot_id)
    return EntityPage(
        total=total,
        limit=limit,
        offset=offset,
        items=[EntitySummary.of(e, counts.get(e.id, 0)) for e in items],
    )


@router.get("/schema-snapshots/{snapshot_id}/fields", summary="Recherche de champs dans un snapshot")
def search_fields(
    svc: Registry,
    snapshot_id: uuid.UUID,
    search: Annotated[str, Query(min_length=1, description="Nom technique du champ (contient)")],
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> list[FieldHit]:
    svc.get_snapshot(snapshot_id)
    return [FieldHit.build(entity, f) for entity, f in svc.search_fields(snapshot_id, search, limit=limit)]


@router.get("/entities/{entity_id}", summary="Entité : champs et relations")
def get_entity(svc: Registry, entity_id: uuid.UUID) -> EntityDetail:
    ent = svc.get_entity_by_id(entity_id)
    return EntityDetail.build(ent, svc.list_relations(ent.id))


@router.get("/entities/{entity_id}/fields", summary="Champs d'une entité")
def list_entity_fields(svc: Registry, entity_id: uuid.UUID) -> list[FieldOut]:
    return [FieldOut.of(f) for f in svc.get_entity_by_id(entity_id).fields]


@router.get("/schema-compare", summary="Version Compare / Instance Diff entre deux snapshots")
def schema_compare(
    svc: Registry,
    from_: Annotated[uuid.UUID, Query(alias="from", description="Snapshot source (ancien / standard)")],
    to: Annotated[uuid.UUID, Query(description="Snapshot cible (nouveau / instance)")],
    entity: Annotated[str | None, Query(description="Limiter à une entité (nom technique)")] = None,
    include_unchanged: bool = False,
) -> CompareOut:
    res = svc.compare_by_id(from_, to, entity=entity, include_unchanged=include_unchanged)
    return CompareOut.build(res, from_id=from_, to_id=to, entity=entity)
