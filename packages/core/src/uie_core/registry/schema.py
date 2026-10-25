"""Modèle Pydantic d'un snapshot de schéma : le format pivot produit par tout SchemaLoader.

Un loader (Odoo, DATEV, Dynamics, ...) produit un `SchemaSnapshotModel` ; le
RegistryService le persiste tel quel, de façon immutable, avec un checksum.
Le même modèle décrit aussi le schéma réel d'une instance client.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DataType(StrEnum):
    """Types logiques communs. Le type natif du système reste dans `native_type`."""

    STRING = "string"
    TEXT = "text"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    ENUM = "enum"
    BINARY = "binary"
    JSON = "json"
    REFERENCE = "reference"  # lien vers une autre entité (many2one, FK, lookup)
    COLLECTION = "collection"  # collection de références (one2many, many2many)
    UNKNOWN = "unknown"


class RelationType(StrEnum):
    MANY_TO_ONE = "many_to_one"
    ONE_TO_MANY = "one_to_many"
    MANY_TO_MANY = "many_to_many"
    ONE_TO_ONE = "one_to_one"


class Capability(StrEnum):
    READ = "READ"
    SEARCH = "SEARCH"
    SCHEMA_DISCOVERY = "SCHEMA_DISCOVERY"
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    BULK_READ = "BULK_READ"
    BULK_WRITE = "BULK_WRITE"
    EVENTS = "EVENTS"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FieldDef(_Model):
    technical_name: str
    label: str | None = None
    data_type: DataType = DataType.UNKNOWN
    native_type: str | None = None
    required: bool = False
    readonly: bool = False
    stored: bool = True
    computed: bool = False
    physical_column: str | None = None
    help: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RelationDef(_Model):
    source_field: str | None
    target_entity: str
    relation_type: RelationType
    inverse_field: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConstraintDef(_Model):
    type: str  # unique | check | not_null | fk | business
    field: str | None = None
    definition: dict[str, Any] = Field(default_factory=dict)


class EntityDef(_Model):
    technical_name: str
    display_name: str | None = None
    module: str | None = None
    kind: str = "entity"  # entity | view | abstract | record | file
    physical_name: str | None = None
    fields: list[FieldDef] = Field(default_factory=list)
    relations: list[RelationDef] = Field(default_factory=list)
    constraints: list[ConstraintDef] = Field(default_factory=list)
    capabilities: dict[Capability, bool] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def field(self, name: str) -> FieldDef | None:
        return next((f for f in self.fields if f.technical_name == name), None)


class ModuleDef(_Model):
    technical_name: str
    display_name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PhysicalColumnDef(_Model):
    column_name: str
    db_type: str
    nullable: bool = True


class PhysicalTableDef(_Model):
    table_name: str
    schema_name: str = "public"
    entity: str | None = None
    columns: list[PhysicalColumnDef] = Field(default_factory=list)


class SchemaSnapshotModel(_Model):
    """Snapshot complet, indépendant du système."""

    system: str
    product: str
    version: str
    edition: str = ""
    interface: str
    interface_type: str
    source: dict[str, Any] = Field(default_factory=dict)
    modules: list[ModuleDef] = Field(default_factory=list)
    entities: list[EntityDef] = Field(default_factory=list)
    physical_tables: list[PhysicalTableDef] = Field(default_factory=list)
    capabilities: dict[Capability, bool] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    # ---- helpers ---------------------------------------------------------
    def entity(self, name: str) -> EntityDef | None:
        return next((e for e in self.entities if e.technical_name == name), None)

    @property
    def field_count(self) -> int:
        return sum(len(e.fields) for e in self.entities)

    def checksum(self) -> str:
        """SHA-256 du contenu structurel (hors `source`/`metadata` de provenance).

        Indépendant de l'ordre des listes : deux snapshots au même contenu ont
        le même checksum quel que soit l'ordre d'extraction.
        """
        payload = self.model_dump(mode="json", exclude={"source", "metadata"})
        raw = json.dumps(_canonical(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _canonical(value: Any) -> Any:
    """Trie récursivement les listes de dicts (par clé de nom) pour un hash stable."""
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in value.items()}
    if isinstance(value, list):
        items = [_canonical(v) for v in value]
        return sorted(items, key=lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False))
    return value
