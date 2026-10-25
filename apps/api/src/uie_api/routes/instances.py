"""Routes Instances (SKR §16) : API → InstanceService → Core. Aucune logique système ici."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status
from uie_core.instances import InstanceDiffSummary

from uie_api.deps import Instances
from uie_api.schemas_instances import (
    DiscoverOut,
    InstanceCreateIn,
    InstanceDeletedOut,
    InstanceDetail,
    InstanceOut,
    InstanceSnapshotOut,
    SchemaDiffOut,
    TestConnectionOut,
)

router = APIRouter(prefix="/admin/instances", tags=["instances"])


@router.get("", summary="Instances client")
def list_instances(svc: Instances) -> list[InstanceOut]:
    return [InstanceOut.of(i) for i in svc.list()]


@router.post(
    "", status_code=status.HTTP_201_CREATED, summary="Créer une instance (secret chiffré, jamais renvoyé)"
)
def create_instance(svc: Instances, body: InstanceCreateIn) -> InstanceDetail:
    inst = svc.create(body.to_core())
    return InstanceDetail.build(svc.get(inst.id))


@router.get("/{instance_id}", summary="Instance : connexion, capabilities, historique des snapshots")
def get_instance(svc: Instances, instance_id: uuid.UUID) -> InstanceDetail:
    return InstanceDetail.build(svc.get(instance_id))


@router.delete(
    "/{instance_id}",
    summary="Supprime l'instance et tout ce qui lui appartient ; le snapshot standard est conservé",
)
def delete_instance(svc: Instances, instance_id: uuid.UUID) -> InstanceDeletedOut:
    d = svc.delete(instance_id)
    return InstanceDeletedOut(
        id=d.instance_id,
        name=d.name,
        deleted={
            "connections": d.connections,
            "capabilities": d.capabilities,
            "snapshots": d.snapshots,
            "diffs": d.diffs,
            "secrets": d.secrets,
        },
        kept_base_snapshot_ids=list(d.base_snapshot_ids),
    )


@router.post(
    "/{instance_id}/test-connection", summary="Test réel de connexion + vérification des capabilities"
)
def test_connection(svc: Instances, instance_id: uuid.UUID) -> TestConnectionOut:
    res = svc.test_connection(instance_id)
    return TestConnectionOut.build(res, svc.get(instance_id))


@router.post(
    "/{instance_id}/discover-schema",
    status_code=status.HTTP_201_CREATED,
    summary="Découvre le schéma réel → NOUVEAU snapshot d'instance immutable + diff",
)
def discover_schema(svc: Instances, instance_id: uuid.UUID) -> DiscoverOut:
    snap = svc.discover_schema(instance_id)
    return DiscoverOut(
        instance_snapshot=InstanceSnapshotOut.of(snap),
        summary=InstanceDiffSummary.model_validate(snap.diff_summary),
    )


@router.get("/{instance_id}/schema-diff", summary="Instance Diff : standard (baseline exacte) → instance")
def schema_diff(
    svc: Instances,
    instance_id: uuid.UUID,
    sequence: Annotated[
        int | None, Query(ge=1, description="N° du snapshot d'instance (dernier par défaut)")
    ] = None,
) -> SchemaDiffOut:
    return SchemaDiffOut.build(svc.get_schema_diff(instance_id, sequence))
