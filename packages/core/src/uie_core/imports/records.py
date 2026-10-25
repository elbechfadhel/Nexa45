"""Tables des imports de fichiers catalogue (V2c.1).

`catalog_imports`         un fichier déposé par un fournisseur : organisation (du CONTEXTE, jamais du
                          client), système cible choisi, forme du fichier, empreinte et référence de stockage.
                          Le contenu du fichier n'est PAS en base : il est dans le FileStore (`storage_ref`).
`catalog_import_columns`  une ligne par colonne : type détecté, sens reconnu, cible, statut, décision.
`learned_column_mappings` (V2c.2) mémoire d'une organisation, apprise des choix explicites du fournisseur,
                          jamais partagée : en-tête normalisé → semantic_key (MAPPED, toute l'organisation ;
                          jamais un champ d'un système) ou NOT_RELEVANT (V2c.2b, pour UN système cible du
                          Registry : une colonne inutile pour un système peut servir à un autre).
Système cible supprimé → `instance_id` SET NULL : l'import revient à « choisir un système », son fichier reste
géré par l'import (supprimé avec lui).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from uie_core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utcnow

_UUID = UUID(as_uuid=True)


class CatalogImportRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "catalog_imports"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    instance_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("instances.id", ondelete="SET NULL"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_format: Mapped[str] = mapped_column(String(10), nullable=False)  # csv | xlsx
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # UPLOADED | ANALYZED | READY
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    column_count: Mapped[int] = mapped_column(Integer, nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_ref: Mapped[str] = mapped_column(String(500), nullable=False)  # jamais exposé
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # KPI figés au moment de l'analyse (V2c.2) : décisions humaines requises avant toute action
    resolved_auto: Mapped[int | None] = mapped_column(Integer)
    resolved_learned: Mapped[int | None] = mapped_column(Integer)
    resolved_ignored: Mapped[int | None] = mapped_column(Integer)  # ignorées par la mémoire (V2c.2b)
    # fallback LLM (V2c.3) : KPI figés, empreinte de la requête (cache par organisation) et résultat validé
    # (décisions par colonne + provenance, jamais les valeurs d'exemple envoyées)
    resolved_ai: Mapped[int | None] = mapped_column(Integer)
    suggested_ai: Mapped[int | None] = mapped_column(Integer)
    not_relevant_ai: Mapped[int | None] = mapped_column(Integer)
    ai_request_checksum: Mapped[str | None] = mapped_column(String(64), index=True)
    ai_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))  # None = NULL SQL
    needs_attention: Mapped[int | None] = mapped_column(Integer)

    columns: Mapped[list[CatalogImportColumnRecord]] = relationship(
        back_populates="catalog_import",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="CatalogImportColumnRecord.position",
    )


class CatalogImportColumnRecord(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "catalog_import_columns"
    __table_args__ = (UniqueConstraint("import_id", "position"),)

    import_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("catalog_imports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    detected_type: Mapped[str] = mapped_column(String(20), nullable=False)
    filled_values: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # valeurs minoritaires qui ne sont pas du type détecté (ex. « 22.0016.50 » dans une colonne de prix) :
    # conservées pour l'import réel (V2c.2)
    invalid_values: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    semantic_key: Mapped[str | None] = mapped_column(String(100))
    target_id: Mapped[str | None] = mapped_column(String(120))  # « groupe:clé » (opaque)
    target_field: Mapped[str | None] = mapped_column(String(300))  # « entité.champ » : jamais exposé
    confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # MATCHED | REVIEW | UNMATCHED | EMPTY
    reason: Mapped[str | None] = mapped_column(String(30))
    ignored: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # AUTO (règles) | LEARNED (mémoire de l'organisation) | AI (fallback LLM, V2c.3) | SUPPLIER (décision)
    decided_by: Mapped[str] = mapped_column(String(20), nullable=False, default="AUTO")

    catalog_import: Mapped[CatalogImportRecord] = relationship(back_populates="columns")


class LearnedColumnMapping(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "learned_column_mappings"
    __table_args__ = (
        # un mapping par en-tête pour l'organisation, un NOT_RELEVANT par en-tête et par système
        Index(
            "uq_learned_column_mappings_mapped",
            "organization_id",
            "header_key",
            unique=True,
            postgresql_where=text("system_id IS NULL"),
        ),
        Index(
            "uq_learned_column_mappings_not_relevant",
            "organization_id",
            "header_key",
            "system_id",
            unique=True,
            postgresql_where=text("system_id IS NOT NULL"),
        ),
        CheckConstraint(
            "(decision = 'MAPPED' AND semantic_key IS NOT NULL AND system_id IS NULL)"
            " OR (decision = 'NOT_RELEVANT' AND semantic_key IS NULL AND system_id IS NOT NULL)",
            name="decision_shape",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    header_key: Mapped[str] = mapped_column(String(255), nullable=False)  # en-tête normalisé
    decision: Mapped[str] = mapped_column(String(20), nullable=False, default="MAPPED")  # | NOT_RELEVANT
    semantic_key: Mapped[str | None] = mapped_column(String(100))  # MAPPED seulement
    # NOT_RELEVANT seulement : système du Registry (pas une instance), jamais une version
    system_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("systems.id", ondelete="CASCADE"), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CatalogImportRunRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Une exécution d'import vers le système cible (V2d.2). `request_key` (donnée par le portail à la
    confirmation) rend la demande idempotente : même clé = même exécution (retry, double clic). Survit à la
    suppression de l'import (historique des écritures) ; supprimée avec l'instance."""

    __tablename__ = "catalog_import_runs"
    __table_args__ = (
        UniqueConstraint("import_id", "request_key"),
        # une seule exécution en cours par import
        Index(
            "uq_catalog_import_runs_running",
            "import_id",
            unique=True,
            postgresql_where=text("status = 'RUNNING'"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    instance_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("instances.id", ondelete="CASCADE"), nullable=False, index=True
    )
    import_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("catalog_imports.id", ondelete="SET NULL"), index=True
    )
    request_key: Mapped[str] = mapped_column(String(64), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    plan_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # RUNNING | COMPLETED | COMPLETED_WITH_WARNINGS | PARTIALLY_COMPLETED | FAILED
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pending: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    with_warnings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # relations résolues : colonne → libellé → {status, id} (ids jamais exposés) ; résumé pour l'écran
    relations: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    not_imported_yet: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    error_kind: Mapped[str | None] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(String(500))  # assaini, jamais de secret
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items: Mapped[list[CatalogImportRunItemRecord]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="CatalogImportRunItemRecord.source_row",
    )


class CatalogImportRunItemRecord(UUIDPrimaryKeyMixin, Base):
    """Une ligne du fichier dans une exécution. Créée PENDING et commitée AVANT tout appel au système, avec
    sa `external_key` : rejouée, une création reprend la même clé (aucun doublon)."""

    __tablename__ = "catalog_import_run_items"
    __table_args__ = (UniqueConstraint("run_id", "source_row"),)

    run_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("catalog_import_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_row: Mapped[int] = mapped_column(Integer, nullable=False)
    identity_value: Mapped[str | None] = mapped_column(String(255))  # valeur de l'identité (SKU)
    external_key: Mapped[str] = mapped_column(String(64), nullable=False)
    entity: Mapped[str] = mapped_column(String(120), nullable=False)  # technique : jamais exposé
    action: Mapped[str] = mapped_column(String(10), nullable=False)  # CREATE | UPDATE | SKIP
    status: Mapped[str] = mapped_column(String(10), nullable=False)  # PENDING | DONE | FAILED | SKIPPED
    target_record_id: Mapped[str | None] = mapped_column(String(64))  # jamais exposé
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    error_code: Mapped[str | None] = mapped_column(String(30))
    error: Mapped[str | None] = mapped_column(String(500))  # assaini
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    run: Mapped[CatalogImportRunRecord] = relationship(back_populates="items")
