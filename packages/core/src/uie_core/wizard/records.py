"""Cache PERSISTANT des correspondances de colonnes proposées par le LLM (assistant de migration).

Clé = empreinte SHA-256 de la requête complète (en-têtes, types, remplissage, exemples MASQUÉS, cibles
offertes, contexte) et de l'identité du fournisseur (modèle, effort, version du prompt). Valeur = réponse
VALIDÉE : positions, décisions, clés, confiances et provenance ; jamais une valeur du fichier, jamais la
réponse brute. Un échec n'est jamais gardé. Partagé par tous les processus de l'API (le cache du processus
reste devant)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from uie_core.db.base import Base, utcnow
from uie_core.imports import ColumnProposalBatch


class WizardColumnCacheRecord(Base):
    __tablename__ = "wizard_column_cache"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    batch: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


def cached_batch(session: Session, key: str) -> ColumnProposalBatch | None:
    row = session.scalar(
        select(WizardColumnCacheRecord.batch).where(WizardColumnCacheRecord.cache_key == key)
    )
    return ColumnProposalBatch.model_validate(row) if row is not None else None


def store_batch(session: Session, key: str, batch: ColumnProposalBatch) -> None:
    """Réponse validée gardée (sans la réponse brute) ; une clé déjà présente n'est pas réécrite."""
    data = batch.model_dump(mode="json", exclude={"raw"})
    session.execute(
        insert(WizardColumnCacheRecord)
        .values(cache_key=key, batch=data, created_at=utcnow())
        .on_conflict_do_nothing(index_elements=["cache_key"])
    )
