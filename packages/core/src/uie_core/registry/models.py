"""Modèle PostgreSQL du System Knowledge Registry (document SKR §5).

Règle : aucune notion Odoo/DATEV dans ce modèle. Les spécificités d'un système
vivent dans les colonnes `metadata_` (JSONB) et dans son Schema Loader.

Hiérarchie de définition standard :
    System → Product → Version → Interface → SchemaSnapshot (immutable)
        → Module → Entity → Field / Relation / Constraint / Capability
        → PhysicalTable → PhysicalColumn (optionnel)
Côté client (V1d : toute instance appartient à une Organization, uie_core.identity) :
    Organization → Instance → InstanceConnection, InstanceCapability
             → InstanceSchemaSnapshot (immutable) → InstanceSchemaDiff
Connaissance réutilisable :
    DomainBinding, MappingTemplate, CompatibilityRule
Domaines métier (Product Domain V1a) :
    ProductDomainDefinitionRecord (standard, par snapshot standard) → ProductDomainSnapshotRecord
    (effectif, par snapshot d'instance, IMMUTABLE)
Semantic Enrichment (Product Domain V1b) :
    FieldSemanticBinding (état de décision par champ d'instance, NON immutable) → FieldSemanticBindingEvent
    (journal des changements de statut)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from uie_core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from uie_core.identity.models import Organization

_UUID = UUID(as_uuid=True)


def _meta() -> Mapped[dict[str, Any]]:
    return mapped_column("metadata", JSONB, nullable=False, default=dict)


# --------------------------------------------------------------------------- #
# Définition standard
# --------------------------------------------------------------------------- #
class System(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Éditeur / système logique : odoo, datev, dynamics, ariba, ..."""

    __tablename__ = "systems"

    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    vendor: Mapped[str | None] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(50), nullable=False, default="erp")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="supported")
    metadata_: Mapped[dict[str, Any]] = _meta()

    products: Mapped[list[SystemProduct]] = relationship(
        back_populates="system", cascade="all, delete-orphan"
    )


class SystemProduct(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Produit d'un système (Odoo ERP ; DATEV Rechnungswesen, DATEVconnect, ...)."""

    __tablename__ = "system_products"
    __table_args__ = (UniqueConstraint("system_id", "code"),)

    system_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("systems.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = _meta()

    system: Mapped[System] = relationship(back_populates="products")
    versions: Mapped[list[SystemVersion]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class SystemVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Version / édition d'un produit (Odoo 17, 18, 19 ; DATEV format 7.x ...)."""

    __tablename__ = "system_versions"
    __table_args__ = (UniqueConstraint("product_id", "version", "edition"),)

    product_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("system_products.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    edition: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    release_status: Mapped[str] = mapped_column(String(30), nullable=False, default="stable")
    metadata_: Mapped[dict[str, Any]] = _meta()

    product: Mapped[SystemProduct] = relationship(back_populates="versions")
    interfaces: Mapped[list[SystemInterface]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )


class SystemInterface(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Interface exploitable d'une version : ORM/XML-RPC, OData, fichier CSV, API REST..."""

    __tablename__ = "system_interfaces"
    __table_args__ = (UniqueConstraint("version_id", "code"),)

    version_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("system_versions.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)  # orm, odata, rest, file, db
    direction: Mapped[str] = mapped_column(String(20), nullable=False, default="bidirectional")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="supported")
    metadata_: Mapped[dict[str, Any]] = _meta()

    version: Mapped[SystemVersion] = relationship(back_populates="interfaces")
    snapshots: Mapped[list[SchemaSnapshot]] = relationship(
        back_populates="interface", cascade="all, delete-orphan"
    )


class SchemaSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Snapshot immutable d'un schéma standard (checksum, source, date)."""

    __tablename__ = "schema_snapshots"
    __table_args__ = (UniqueConstraint("interface_id", "snapshot_version"),)

    interface_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("system_interfaces.id", ondelete="CASCADE"), nullable=False
    )
    snapshot_version: Mapped[str] = mapped_column(String(50), nullable=False)
    source: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="published")
    entity_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    field_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_: Mapped[dict[str, Any]] = _meta()

    interface: Mapped[SystemInterface] = relationship(back_populates="snapshots")
    modules: Mapped[list[SchemaModule]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )
    entities: Mapped[list[SchemaEntity]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )


class SchemaModule(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "schema_modules"
    __table_args__ = (UniqueConstraint("snapshot_id", "technical_name"),)

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("schema_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    technical_name: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200))
    metadata_: Mapped[dict[str, Any]] = _meta()

    snapshot: Mapped[SchemaSnapshot] = relationship(back_populates="modules")


class SchemaEntity(UUIDPrimaryKeyMixin, Base):
    """Objet logique : model Odoo, entité OData, record DATEV, table..."""

    __tablename__ = "schema_entities"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "technical_name"),
        Index("ix_schema_entities_snapshot_kind", "snapshot_id", "kind"),
    )

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("schema_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    module_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("schema_modules.id", ondelete="SET NULL")
    )
    technical_name: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(30), nullable=False, default="entity")
    physical_name: Mapped[str | None] = mapped_column(String(200))
    metadata_: Mapped[dict[str, Any]] = _meta()

    snapshot: Mapped[SchemaSnapshot] = relationship(back_populates="entities")
    module: Mapped[SchemaModule | None] = relationship()
    fields: Mapped[list[SchemaField]] = relationship(
        back_populates="entity",
        cascade="all, delete-orphan",
        order_by="SchemaField.technical_name",
    )


class SchemaField(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "schema_fields"
    __table_args__ = (UniqueConstraint("entity_id", "technical_name"),)

    entity_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("schema_entities.id", ondelete="CASCADE"), nullable=False
    )
    technical_name: Mapped[str] = mapped_column(String(200), nullable=False)
    label: Mapped[str | None] = mapped_column(String(300))
    data_type: Mapped[str] = mapped_column(String(50), nullable=False)
    native_type: Mapped[str | None] = mapped_column(String(80))
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    readonly: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stored: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    computed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    physical_column: Mapped[str | None] = mapped_column(String(200))
    help: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = _meta()

    entity: Mapped[SchemaEntity] = relationship(back_populates="fields")


class SchemaRelation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "schema_relations"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("schema_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    source_entity_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("schema_entities.id", ondelete="CASCADE"), nullable=False
    )
    source_field_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("schema_fields.id", ondelete="CASCADE")
    )
    target_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("schema_entities.id", ondelete="SET NULL")
    )
    target_entity_name: Mapped[str] = mapped_column(String(200), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(30), nullable=False)
    inverse_field: Mapped[str | None] = mapped_column(String(200))
    metadata_: Mapped[dict[str, Any]] = _meta()

    source_field: Mapped[SchemaField | None] = relationship()


class SchemaConstraint(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "schema_constraints"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("schema_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("schema_entities.id", ondelete="CASCADE")
    )
    field_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("schema_fields.id", ondelete="CASCADE")
    )
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class SchemaCapability(UUIDPrimaryKeyMixin, Base):
    """Capability déclarée au niveau snapshot (scope=interface) ou entité."""

    __tablename__ = "schema_capabilities"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("schema_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("schema_entities.id", ondelete="CASCADE")
    )
    scope: Mapped[str] = mapped_column(String(30), nullable=False)  # interface | entity
    capability: Mapped[str] = mapped_column(String(50), nullable=False)
    supported: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_: Mapped[dict[str, Any]] = _meta()


class PhysicalTable(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "physical_tables"
    __table_args__ = (UniqueConstraint("snapshot_id", "schema_name", "table_name"),)

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("schema_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("schema_entities.id", ondelete="SET NULL")
    )
    schema_name: Mapped[str] = mapped_column(String(100), nullable=False, default="public")
    table_name: Mapped[str] = mapped_column(String(200), nullable=False)

    columns: Mapped[list[PhysicalColumn]] = relationship(cascade="all, delete-orphan")


class PhysicalColumn(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "physical_columns"
    __table_args__ = (UniqueConstraint("table_id", "column_name"),)

    table_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("physical_tables.id", ondelete="CASCADE"), nullable=False
    )
    column_name: Mapped[str] = mapped_column(String(200), nullable=False)
    db_type: Mapped[str] = mapped_column(String(100), nullable=False)
    nullable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


# --------------------------------------------------------------------------- #
# Instances client
# --------------------------------------------------------------------------- #
class Instance(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Installation / endpoint réel d'un client, rattaché à une interface standard.

    Pas de rôle SOURCE/TARGET : le sens d'utilisation découle des capabilities vérifiées.
    """

    __tablename__ = "instances"
    # V1d : le nom est unique DANS une organisation (unique global = fuite d'existence entre tenants)
    __table_args__ = (UniqueConstraint("organization_id", "name"),)

    # V1d : propriétaire obligatoire ; RESTRICT : une organisation qui a des instances n'est jamais
    # supprimée en silence. Toutes les données dérivées (connexion, snapshots, domaine, bindings) sont
    # rattachées par la chaîne instance_id : aucune dénormalisation d'organization_id.
    organization_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    interface_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("system_interfaces.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    environment: Mapped[str] = mapped_column(String(30), nullable=False, default="test")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    metadata_: Mapped[dict[str, Any]] = _meta()

    interface: Mapped[SystemInterface] = relationship()
    organization: Mapped[Organization] = relationship()
    # passive_deletes : la suppression d'une instance est faite par PostgreSQL (FK ON DELETE CASCADE)
    # en une instruction, sans charger ni mettre à jour les snapshots immutables.
    connections: Mapped[list[InstanceConnection]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True
    )
    capabilities: Mapped[list[InstanceCapability]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True, order_by="InstanceCapability.capability"
    )
    snapshots: Mapped[list[InstanceSchemaSnapshot]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True, order_by="InstanceSchemaSnapshot.sequence"
    )


class InstanceConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Paramètres de connexion. Les secrets ne sont jamais ici : `secret_ref` seulement."""

    __tablename__ = "instance_connections"

    instance_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("instances.id", ondelete="CASCADE"), nullable=False
    )
    connector_type: Mapped[str] = mapped_column(String(50), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(500), nullable=False)
    secret_ref: Mapped[str | None] = mapped_column(Text)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_status: Mapped[str | None] = mapped_column(String(30))  # OK | FAILED
    last_test_error: Mapped[str | None] = mapped_column(Text)  # toujours passé par redact()
    last_test_info: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class InstanceCapability(UUIDPrimaryKeyMixin, Base):
    """État d'une capability pour une instance : DECLARED / VERIFIED / UNAVAILABLE / UNKNOWN."""

    __tablename__ = "instance_capabilities"
    __table_args__ = (UniqueConstraint("instance_id", "capability"),)

    instance_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("instances.id", ondelete="CASCADE"), nullable=False
    )
    capability: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InstanceSchemaSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Schéma réel d'une instance (IMMUTABLE), avec le snapshot standard EXACT servant de baseline."""

    __tablename__ = "instance_schema_snapshots"
    __table_args__ = (UniqueConstraint("instance_id", "sequence"),)

    instance_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("instances.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)  # n° 1, 2, ... par instance
    base_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("schema_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    entity_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    field_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    relation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # schéma complet (~2,7 Mo de JSON pour Odoo) : chargé seulement quand on le lit (diff, domaine effectif),
    # jamais pour lister ou afficher une instance
    schema_model: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, deferred=True)
    diff_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    metadata_: Mapped[dict[str, Any]] = _meta()

    base_snapshot: Mapped[SchemaSnapshot] = relationship()
    diffs: Mapped[list[InstanceSchemaDiff]] = relationship(cascade="all, delete-orphan", passive_deletes=True)


class InstanceSchemaDiff(UUIDPrimaryKeyMixin, Base):
    """Un changement standard → instance, classé (IMMUTABLE, lié à son snapshot d'instance)."""

    __tablename__ = "instance_schema_diffs"
    __table_args__ = (Index("ix_instance_schema_diffs_snapshot_origin", "instance_snapshot_id", "origin"),)

    instance_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("instance_schema_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # ordre du compare
    entity: Mapped[str] = mapped_column(String(200), nullable=False)
    object_type: Mapped[str] = mapped_column(String(30), nullable=False)  # entity|field|relation
    object_path: Mapped[str] = mapped_column(String(400), nullable=False)
    change_type: Mapped[str] = mapped_column(String(20), nullable=False)  # ADDED|REMOVED|CHANGED
    origin: Mapped[str] = mapped_column(String(20), nullable=False)  # STANDARD|CUSTOM|THIRD_PARTY|UNKNOWN
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    classification: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


# --------------------------------------------------------------------------- #
# Connaissance réutilisable
# --------------------------------------------------------------------------- #
class DomainBinding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Quelle entité d'un snapshot participe à quel domaine métier (PRODUCT, CUSTOMER...)."""

    __tablename__ = "domain_bindings"
    __table_args__ = (UniqueConstraint("domain", "entity_id", "role"),)

    domain: Mapped[str] = mapped_column(String(50), nullable=False)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("schema_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("schema_entities.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="primary")
    metadata_: Mapped[dict[str, Any]] = _meta()


# --------------------------------------------------------------------------- #
# Domaines métier (Product Domain V1a)
# --------------------------------------------------------------------------- #
class ProductDomainDefinitionRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Définition STANDARD d'un domaine résolue sur un snapshot standard exact (IMMUTABLE).

    Contenu complet dans `definition` (ProductDomainDefinition) ; colonnes = identité et recherche.
    """

    __tablename__ = "product_domain_definitions"
    # une définition par (domaine, snapshot standard, version du pack) : PRODUCT et PARTNER coexistent
    __table_args__ = (UniqueConstraint("domain_key", "base_schema_snapshot_id", "definition_version"),)

    domain_key: Mapped[str] = mapped_column(String(50), nullable=False)
    system: Mapped[str] = mapped_column(String(50), nullable=False)
    product: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    base_schema_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("schema_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    pack: Mapped[str] = mapped_column(String(200), nullable=False)
    definition_version: Mapped[str] = mapped_column(String(50), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    field_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class ProductDomainSnapshotRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Domaine EFFECTIF d'une instance (IMMUTABLE), lié au snapshot d'instance, à sa baseline et à la
    définition standard exacts. Un nouveau snapshot d'instance → un nouveau domain snapshot."""

    __tablename__ = "product_domain_snapshots"
    __table_args__ = (
        UniqueConstraint("instance_schema_snapshot_id", "definition_id"),
        Index("ix_product_domain_snapshots_instance_analyzed", "instance_id", "analyzed_at"),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("instances.id", ondelete="CASCADE"), nullable=False
    )
    instance_schema_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("instance_schema_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    base_schema_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("schema_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    definition_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("product_domain_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    domain_key: Mapped[str] = mapped_column(String(50), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entity_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    field_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    extension_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    missing_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class FieldSemanticBinding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Signification d'un champ d'instance dans un domaine (V1b) : décision de règle ou humaine.

    Clé logique = (instance, domaine, entité, champ) ; le snapshot d'instance n'est qu'une provenance.
    Ce n'est pas un snapshot : l'état évolue (analyse, réponses du supplier) et chaque changement de statut
    est journalisé dans `field_semantic_binding_events`. Une décision HUMAN n'est jamais écrasée par une
    règle.
    """

    __tablename__ = "field_semantic_bindings"
    __table_args__ = (
        # nom explicite : celui de la convention dépasserait 63 caractères (limite PostgreSQL)
        UniqueConstraint(
            "instance_id",
            "domain_key",
            "entity_technical_name",
            "field_technical_name",
            name="uq_field_semantic_bindings_field",
        ),
        Index("ix_field_semantic_bindings_instance_status", "instance_id", "meaning_status"),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("instances.id", ondelete="CASCADE"), nullable=False
    )
    domain_key: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_technical_name: Mapped[str] = mapped_column(String(200), nullable=False)
    field_technical_name: Mapped[str] = mapped_column(String(200), nullable=False)
    semantic_key: Mapped[str | None] = mapped_column(String(100))
    relevance: Mapped[str] = mapped_column(String(20), nullable=False)
    meaning_status: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # STANDARD | RULE | LLM | HUMAN
    rule_id: Mapped[str | None] = mapped_column(String(50))
    confidence: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(20), nullable=False)  # STANDARD | CUSTOM | THIRD_PARTY...
    pending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # question ouverte
    mirror_of: Mapped[str | None] = mapped_column(String(400))  # copie related : suit ce champ
    technical_signature: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    field_def: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)  # affichage
    previous: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # réponse d'avant STALE / ORPHANED
    decided_on_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("instance_schema_snapshots.id", ondelete="SET NULL")
    )
    last_seen_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("instance_schema_snapshots.id", ondelete="SET NULL")
    )
    human_label: Mapped[str | None] = mapped_column(String(200))  # libellé de la clé choisie par l'humain
    note: Mapped[str | None] = mapped_column(Text)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # V1c : dernière proposition du LLM (provenance ; le LLM ne touche jamais la pertinence)
    ai_justification: Mapped[str | None] = mapped_column(Text)  # MATCH : pourquoi cette clé
    ai_note: Mapped[str | None] = mapped_column(Text)  # NO_CONFIDENT_MATCH : raison affichée
    low_confidence: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    ai_prompt_checksum: Mapped[str | None] = mapped_column(String(64))  # cache : prompt utilisé
    ai_signature: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # cache : signature analysée

    events: Mapped[list[FieldSemanticBindingEvent]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True, order_by="FieldSemanticBindingEvent.at"
    )


class FieldSemanticBindingEvent(UUIDPrimaryKeyMixin, Base):
    """Journal des changements de statut d'un binding (analyse ou réponse humaine)."""

    __tablename__ = "field_semantic_binding_events"
    __table_args__ = (Index("ix_field_semantic_binding_events_binding_at", "binding_id", "at"),)

    binding_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("field_semantic_bindings.id", ondelete="CASCADE"), nullable=False
    )
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)  # ENRICH | CONFIRM | CHANGE | ...
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    semantic_key: Mapped[str | None] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("instance_schema_snapshots.id", ondelete="SET NULL")
    )
    note: Mapped[str | None] = mapped_column(Text)
    # V1c : détail d'un appel LLM (fournisseur, modèle, prompt, request id, tokens, latence, coût, réponse)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class MappingTemplate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "mapping_templates"
    __table_args__ = (UniqueConstraint("domain", "source_interface_id", "target_interface_id", "version"),)

    domain: Mapped[str] = mapped_column(String(50), nullable=False)
    source_interface_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("system_interfaces.id", ondelete="CASCADE"), nullable=False
    )
    target_interface_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("system_interfaces.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    direction: Mapped[str] = mapped_column(String(30), nullable=False, default="source_to_target")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class CompatibilityRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "compatibility_rules"

    source_interface_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("system_interfaces.id", ondelete="CASCADE"), nullable=False
    )
    target_interface_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("system_interfaces.id", ondelete="CASCADE"), nullable=False
    )
    domain: Mapped[str | None] = mapped_column(String(50))
    rule: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active")


@event.listens_for(InstanceSchemaSnapshot, "before_update")
@event.listens_for(InstanceSchemaDiff, "before_update")
@event.listens_for(ProductDomainDefinitionRecord, "before_update")
@event.listens_for(ProductDomainSnapshotRecord, "before_update")
def _immutable(mapper: Any, connection: Any, target: Any) -> None:
    raise ValueError(f"{type(target).__name__} est immutable : créer un nouveau snapshot")
