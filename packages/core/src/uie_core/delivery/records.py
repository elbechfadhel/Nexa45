"""Tables de l'envoi vers un système cible par API (générique : tout domaine, toute source, toute cible).

Aucune donnée métier (nom, adresse…) : seulement des références, des états et des empreintes.

`target_resource_links`  lien DURABLE enregistrement source ↔ enregistrement cible (+ version connue de la
                         cible) : créé par un envoi réussi ou par un rapprochement HUMAIN. Un lien existant =
                         l'envoi suivant met à jour, jamais une nouvelle création.
`target_send_runs`       un envoi demandé par un humain, identifié par (cible, request_key) : même clé = même
                         envoi (jamais rejoué). Un seul envoi RUNNING par (source, cible, domaine).
`target_send_items`      un enregistrement source dans un envoi : commité PENDING AVANT l'appel, puis
                         `attempted_at` commité juste avant l'écriture ; SENT / FAILED / UNKNOWN / SKIPPED /
                         NEEDS_MATCH. UNKNOWN n'est résolu que par un humain.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from uie_core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utcnow

_UUID = UUID(as_uuid=True)


class TargetResourceLink(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "target_resource_links"
    __table_args__ = (
        UniqueConstraint(
            "source_instance_id", "target_instance_id", "domain", "source_ref", name="uq_target_links_source"
        ),
        # une ressource cible n'est liée qu'à UN enregistrement d'une même source (jamais de fusion)
        UniqueConstraint(
            "source_instance_id", "target_instance_id", "domain", "target_id", name="uq_target_links_target"
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_instance_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("instances.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_instance_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("instances.id", ondelete="CASCADE"), nullable=False, index=True
    )
    domain: Mapped[str] = mapped_column(String(30), nullable=False)
    resource: Mapped[str] = mapped_column(String(80), nullable=False)  # ressource du contrat cible
    source_ref: Mapped[str] = mapped_column(String(120), nullable=False)  # référence opaque de la source
    target_id: Mapped[str] = mapped_column(String(120), nullable=False)  # identifiant attribué par la cible
    target_version: Mapped[int | None] = mapped_column(Integer)  # dernière version connue de la cible
    origin: Mapped[str] = mapped_column(String(20), nullable=False)  # SENT | MATCHED (rapprochement humain)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(_UUID)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class TargetSendRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "target_send_runs"
    __table_args__ = (
        UniqueConstraint("target_instance_id", "request_key", name="uq_target_send_runs_key"),
        Index(
            "uq_target_send_runs_running",
            "source_instance_id",
            "target_instance_id",
            "domain",
            unique=True,
            postgresql_where=text("status = 'RUNNING'"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_instance_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("instances.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_instance_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("instances.id", ondelete="CASCADE"), nullable=False, index=True
    )
    domain: Mapped[str] = mapped_column(String(30), nullable=False)
    resource: Mapped[str] = mapped_column(String(80), nullable=False)
    target_label: Mapped[str] = mapped_column(String(200), nullable=False)  # binding + contrat (libellé)
    request_key: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # RUNNING | COMPLETED | PARTIAL | NOT_SENT
    created_by: Mapped[uuid.UUID | None] = mapped_column(_UUID)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TargetSendItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "target_send_items"
    __table_args__ = (UniqueConstraint("run_id", "source_ref", name="uq_target_send_items_ref"),)

    run_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("target_send_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    source_ref: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(10), nullable=False)  # CREATE | UPDATE | NONE
    create_new: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # décision humaine
    # PENDING | SENT | FAILED | UNKNOWN | SKIPPED | NEEDS_MATCH
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(40))  # code (BLOCKED, UNCHANGED, REJECTED…)
    fingerprint: Mapped[str | None] = mapped_column(String(64))  # contenu préparé (D11)
    field_digests: Mapped[list[Any] | None] = mapped_column(JSONB)  # empreintes par champ, jamais de valeur
    target_id: Mapped[str | None] = mapped_column(String(120))
    target_version: Mapped[int | None] = mapped_column(Integer)
    candidates: Mapped[list[Any] | None] = mapped_column(JSONB)  # identifiants cible seulement
    error_status: Mapped[int | None] = mapped_column(Integer)
    error_trace_id: Mapped[str | None] = mapped_column(String(120))
    error_fields: Mapped[list[Any] | None] = mapped_column(JSONB)  # NOMS de champs, jamais de valeur
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[str | None] = mapped_column(String(20))  # UNKNOWN résolu : CREATED | NOT_CREATED
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(_UUID)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
