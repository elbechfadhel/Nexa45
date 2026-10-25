"""Schémas HTTP des instances client. Aucun secret en sortie : ni mot de passe, ni secret_ref."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from uie_core.identity import GO45_DEV_ORG_ID
from uie_core.instances import (
    ConnectionTestResult,
    DiffItem,
    InstanceCreate,
    InstanceDiffResult,
    InstanceDiffSummary,
)
from uie_core.registry import models as m
from uie_core.registry.service import RegistryService

from uie_api.schemas import SnapshotSummary


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------- #
# Entrée
# --------------------------------------------------------------------------- #
class InstanceCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    system: str
    version: str
    product: str | None = None
    interface: str | None = None
    environment: str = "test"
    organization_id: uuid.UUID | None = Field(
        default=None, description="Organisation propriétaire (absente : GO45 Development)"
    )
    endpoint: str = Field(description="URL de l'instance, ex. http://localhost:8027")
    config: dict[str, str | int | bool] = Field(
        default_factory=dict, description="Paramètres non secrets, ex. {db, username}"
    )
    password: SecretStr | None = Field(default=None, description="Écrit seulement : jamais renvoyé")

    def to_core(self) -> InstanceCreate:
        data = self.model_dump(exclude={"password", "organization_id"})
        org = self.organization_id or GO45_DEV_ORG_ID
        return InstanceCreate(**data, organization_id=org, password=self.password)


# --------------------------------------------------------------------------- #
# Sortie
# --------------------------------------------------------------------------- #
class CapabilityOut(_Out):
    capability: str
    status: str
    detail: str | None
    checked_at: datetime


class ConnectionOut(_Out):
    connector_type: str
    endpoint: str
    config: dict[str, Any]
    has_secret: bool
    last_test_at: datetime | None
    last_test_status: str | None
    last_test_error: str | None
    last_test_info: dict[str, Any]


class InstanceSnapshotOut(_Out):
    id: uuid.UUID
    sequence: int
    base_snapshot_id: uuid.UUID
    base_snapshot_label: str | None
    checksum: str
    discovered_at: datetime
    entity_count: int
    field_count: int
    relation_count: int
    provenance: dict[str, Any]

    @classmethod
    def of(cls, s: m.InstanceSchemaSnapshot) -> InstanceSnapshotOut:
        return cls(
            id=s.id,
            sequence=s.sequence,
            base_snapshot_id=s.base_snapshot_id,
            base_snapshot_label=s.metadata_.get("base_snapshot_label"),
            checksum=s.checksum,
            discovered_at=s.discovered_at,
            entity_count=s.entity_count,
            field_count=s.field_count,
            relation_count=s.relation_count,
            provenance=s.provenance,
        )


class InstanceOut(_Out):
    id: uuid.UUID
    name: str
    system: str
    product: str
    version: str
    edition: str
    interface: str
    environment: str
    organization_id: uuid.UUID
    organization_name: str
    status: str
    created_at: datetime
    connection: ConnectionOut | None
    capabilities: list[CapabilityOut]
    snapshot_count: int
    last_discovery: InstanceSnapshotOut | None

    @classmethod
    def of(cls, i: m.Instance) -> InstanceOut:
        itf = i.interface
        ver = itf.version
        conn = i.connections[0] if i.connections else None
        snaps = sorted(i.snapshots, key=lambda s: s.sequence)
        return cls(
            id=i.id,
            name=i.name,
            system=ver.product.system.code,
            product=ver.product.code,
            version=ver.version,
            edition=ver.edition,
            interface=itf.code,
            environment=i.environment,
            organization_id=i.organization_id,
            organization_name=i.organization.name,
            status=i.status,
            created_at=i.created_at,
            connection=ConnectionOut(
                connector_type=conn.connector_type,
                endpoint=conn.endpoint,
                config=conn.config,
                has_secret=bool(conn.secret_ref),
                last_test_at=conn.last_test_at,
                last_test_status=conn.last_test_status,
                last_test_error=conn.last_test_error,
                last_test_info=conn.last_test_info,
            )
            if conn
            else None,
            capabilities=[
                CapabilityOut(
                    capability=c.capability, status=c.status, detail=c.detail, checked_at=c.checked_at
                )
                for c in i.capabilities
            ],
            snapshot_count=len(snaps),
            last_discovery=InstanceSnapshotOut.of(snaps[-1]) if snaps else None,
        )


class InstanceDetail(InstanceOut):
    snapshots: list[InstanceSnapshotOut]

    @classmethod
    def build(cls, i: m.Instance) -> InstanceDetail:
        snaps = sorted(i.snapshots, key=lambda s: s.sequence)
        return cls(**InstanceOut.of(i).model_dump(), snapshots=[InstanceSnapshotOut.of(s) for s in snaps])


class TestConnectionOut(_Out):
    ok: bool
    error: str | None
    server_info: dict[str, Any]
    instance: InstanceOut

    @classmethod
    def build(cls, res: ConnectionTestResult, inst: m.Instance) -> TestConnectionOut:
        return cls(ok=res.ok, error=res.error, server_info=res.server_info, instance=InstanceOut.of(inst))


class InstanceChangeOut(_Out):
    object_type: str
    entity: str
    path: str
    change_type: str
    origin: str
    modules: list[str]
    reason: str | None
    details: dict[str, Any]

    @classmethod
    def of(cls, d: DiffItem) -> InstanceChangeOut:
        return cls(**d.model_dump(mode="json"))


class BaseSnapshotOut(SnapshotSummary):
    label: str


class SchemaDiffOut(_Out):
    instance: InstanceOut
    base_snapshot: BaseSnapshotOut
    instance_snapshot: InstanceSnapshotOut
    summary: InstanceDiffSummary
    changes: list[InstanceChangeOut]

    @classmethod
    def build(cls, r: InstanceDiffResult) -> SchemaDiffOut:
        return cls(
            instance=InstanceOut.of(r.instance),
            base_snapshot=BaseSnapshotOut(
                **SnapshotSummary.of(r.base_snapshot).model_dump(),
                label=RegistryService.snapshot_label(r.base_snapshot),
            ),
            instance_snapshot=InstanceSnapshotOut.of(r.instance_snapshot),
            summary=r.summary,
            changes=[InstanceChangeOut.of(c) for c in r.changes],
        )


class InstanceDeletedOut(_Out):
    id: uuid.UUID
    name: str
    deleted: dict[str, int]  # connections, capabilities, snapshots, diffs, secrets
    kept_base_snapshot_ids: list[uuid.UUID]  # snapshots standard référencés : conservés tels quels


class DiscoverOut(_Out):
    instance_snapshot: InstanceSnapshotOut
    summary: InstanceDiffSummary
