"""Schémas de réponse de l'API Admin.

Contrat HTTP dédié : jamais les modèles SQLAlchemy directement. Les constructeurs `of()` ne font
que recopier des attributs déjà calculés par le Core (aucune logique métier ici).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from uie_core.registry import CompareResult
from uie_core.registry import models as m


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------- #
# Arbre System → Product → Version → Interface → Snapshots
# --------------------------------------------------------------------------- #
class SnapshotSummary(_Out):
    id: uuid.UUID
    snapshot_version: str
    checksum: str
    status: str
    entity_count: int
    field_count: int
    created_at: datetime

    @classmethod
    def of(cls, s: m.SchemaSnapshot) -> SnapshotSummary:
        return cls(
            id=s.id,
            snapshot_version=s.snapshot_version,
            checksum=s.checksum,
            status=s.status,
            entity_count=s.entity_count,
            field_count=s.field_count,
            created_at=s.created_at,
        )


class InterfaceNode(_Out):
    id: uuid.UUID
    code: str
    type: str
    direction: str
    status: str
    snapshots: list[SnapshotSummary]

    @classmethod
    def of(cls, i: m.SystemInterface) -> InterfaceNode:
        snaps = sorted(i.snapshots, key=lambda s: s.created_at)
        return cls(
            id=i.id,
            code=i.code,
            type=i.type,
            direction=i.direction,
            status=i.status,
            snapshots=[SnapshotSummary.of(s) for s in snaps],
        )


class VersionNode(_Out):
    id: uuid.UUID
    version: str
    edition: str
    release_status: str
    interfaces: list[InterfaceNode]

    @classmethod
    def of(cls, v: m.SystemVersion) -> VersionNode:
        return cls(
            id=v.id,
            version=v.version,
            edition=v.edition,
            release_status=v.release_status,
            interfaces=[InterfaceNode.of(i) for i in sorted(v.interfaces, key=lambda i: i.code)],
        )


class ProductNode(_Out):
    id: uuid.UUID
    code: str
    name: str
    versions: list[VersionNode]

    @classmethod
    def of(cls, p: m.SystemProduct) -> ProductNode:
        return cls(
            id=p.id,
            code=p.code,
            name=p.name,
            versions=[VersionNode.of(v) for v in sorted(p.versions, key=lambda v: v.version)],
        )


class SystemNode(_Out):
    id: uuid.UUID
    code: str
    name: str
    vendor: str | None
    category: str
    status: str
    products: list[ProductNode]

    @classmethod
    def of(cls, s: m.System) -> SystemNode:
        return cls(
            id=s.id,
            code=s.code,
            name=s.name,
            vendor=s.vendor,
            category=s.category,
            status=s.status,
            products=[ProductNode.of(p) for p in sorted(s.products, key=lambda p: p.code)],
        )


# --------------------------------------------------------------------------- #
# Snapshot
# --------------------------------------------------------------------------- #
class SnapshotStats(_Out):
    entities: int
    fields: int
    relations: int


class SnapshotDetail(SnapshotSummary):
    system: str
    product: str
    version: str
    edition: str
    interface: str
    interface_type: str
    label: str
    source: dict[str, Any]
    stats: SnapshotStats

    @classmethod
    def build(cls, s: m.SchemaSnapshot, *, label: str, stats: dict[str, int]) -> SnapshotDetail:
        itf = s.interface
        ver = itf.version
        return cls(
            **SnapshotSummary.of(s).model_dump(),
            system=ver.product.system.code,
            product=ver.product.code,
            version=ver.version,
            edition=ver.edition,
            interface=itf.code,
            interface_type=itf.type,
            label=label,
            source=s.source,
            stats=SnapshotStats(**stats),
        )


# --------------------------------------------------------------------------- #
# Entités, champs, relations
# --------------------------------------------------------------------------- #
class EntitySummary(_Out):
    id: uuid.UUID
    technical_name: str
    display_name: str | None
    module: str | None
    kind: str
    physical_name: str | None
    field_count: int

    @classmethod
    def of(cls, e: m.SchemaEntity, field_count: int) -> EntitySummary:
        return cls(
            id=e.id,
            technical_name=e.technical_name,
            display_name=e.display_name,
            module=e.module.technical_name if e.module else None,
            kind=e.kind,
            physical_name=e.physical_name,
            field_count=field_count,
        )


class FieldOut(_Out):
    id: uuid.UUID
    technical_name: str
    label: str | None
    data_type: str
    native_type: str | None
    required: bool
    readonly: bool
    stored: bool
    computed: bool
    physical_column: str | None
    help: str | None
    metadata: dict[str, Any]

    @classmethod
    def of(cls, f: m.SchemaField) -> FieldOut:
        return cls(
            id=f.id,
            technical_name=f.technical_name,
            label=f.label,
            data_type=f.data_type,
            native_type=f.native_type,
            required=f.required,
            readonly=f.readonly,
            stored=f.stored,
            computed=f.computed,
            physical_column=f.physical_column,
            help=f.help,
            metadata=f.metadata_,
        )


class FieldHit(FieldOut):
    """Résultat de recherche de champ dans tout un snapshot."""

    entity: str
    entity_id: uuid.UUID

    @classmethod
    def build(cls, entity: str, f: m.SchemaField) -> FieldHit:
        return cls(**FieldOut.of(f).model_dump(), entity=entity, entity_id=f.entity_id)


class EntityPage(_Out):
    total: int
    limit: int
    offset: int
    items: list[EntitySummary]


class RelationOut(_Out):
    id: uuid.UUID
    source_field: str | None
    target_entity: str
    target_entity_id: uuid.UUID | None
    relation_type: str
    inverse_field: str | None
    metadata: dict[str, Any]

    @classmethod
    def of(cls, r: m.SchemaRelation) -> RelationOut:
        return cls(
            id=r.id,
            source_field=r.source_field.technical_name if r.source_field else None,
            target_entity=r.target_entity_name,
            target_entity_id=r.target_entity_id,
            relation_type=r.relation_type,
            inverse_field=r.inverse_field,
            metadata=r.metadata_,
        )


class EntityDetail(EntitySummary):
    snapshot_id: uuid.UUID
    metadata: dict[str, Any]
    fields: list[FieldOut]
    relations: list[RelationOut]

    @classmethod
    def build(cls, e: m.SchemaEntity, relations: list[m.SchemaRelation]) -> EntityDetail:
        return cls(
            **EntitySummary.of(e, len(e.fields)).model_dump(),
            snapshot_id=e.snapshot_id,
            metadata=e.metadata_,
            fields=[FieldOut.of(f) for f in e.fields],
            relations=[RelationOut.of(r) for r in relations],
        )


# --------------------------------------------------------------------------- #
# Compare
# --------------------------------------------------------------------------- #
class ChangeOut(_Out):
    object_type: str
    entity: str
    path: str
    change_type: str
    details: dict[str, Any]


class CompareOut(_Out):
    source: str
    target: str
    from_snapshot_id: uuid.UUID
    to_snapshot_id: uuid.UUID
    entity: str | None
    counts: dict[str, int]
    changes: list[ChangeOut]

    @classmethod
    def build(
        cls, res: CompareResult, *, from_id: uuid.UUID, to_id: uuid.UUID, entity: str | None
    ) -> CompareOut:
        return cls(
            source=res.source,
            target=res.target,
            from_snapshot_id=from_id,
            to_snapshot_id=to_id,
            entity=entity,
            counts=res.counts(),
            changes=[ChangeOut(**c.model_dump(mode="json")) for c in res.changes],
        )
