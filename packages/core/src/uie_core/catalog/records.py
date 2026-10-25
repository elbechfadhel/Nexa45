"""Tables du catalogue réel (V2a).

`catalog_syncs`     une ligne par synchronisation : plan de lecture (provenance), compteurs, statut.
`catalog_products`  état COURANT du catalogue d'une instance (projection mutable, pas un snapshot) : colonnes
                    de recherche / tri + `payload` = CatalogProduct canonique. Idempotence par content_hash ;
                    un produit disparu de la source reçoit `removed_at`.
Isolation : rattachement à `instances` seulement (FK ON DELETE CASCADE), comme tout le reste depuis V1d.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from uie_core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utcnow

_UUID = UUID(as_uuid=True)


class CatalogSyncRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "catalog_syncs"

    instance_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("instances.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_domain_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("product_domain_snapshots.id", ondelete="CASCADE")
    )
    read_plan: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    read_plan_checksum: Mapped[str | None] = mapped_column(String(64))
    catalog_model_version: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # RUNNING | SUCCEEDED | FAILED
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    products: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    variants: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    categories: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    images: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unchanged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    removed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # plafond atteint
    error: Mapped[str | None] = mapped_column(Text)


class CatalogProductRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "catalog_products"
    __table_args__ = (
        UniqueConstraint("instance_id", "source_ref"),
        Index("ix_catalog_products_instance_name", "instance_id", "name"),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("instances.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_ref: Mapped[str] = mapped_column(String(200), nullable=False)  # id source : jamais exposé
    name: Mapped[str | None] = mapped_column(String(500))
    sku: Mapped[str | None] = mapped_column(String(200))
    category: Mapped[str | None] = mapped_column(String(1000))
    list_price: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(20))
    active: Mapped[bool | None] = mapped_column(Boolean)
    has_image: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    variant_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    search_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    last_sync_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("catalog_syncs.id", ondelete="SET NULL")
    )
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
