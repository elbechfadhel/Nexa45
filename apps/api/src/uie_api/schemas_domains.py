"""Schémas HTTP du Product Domain (V1a / V1b). Le contenu est celui du Core (modèles immutables) ;
les enveloppes ajoutent l'identité persistée et le résumé calculé par le Core."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from uie_core.domains import (
    OTHER_KEY,
    AnalysisOutcome,
    DomainSummary,
    EnrichmentResult,
    FieldGroup,
    ProductDomainDefinition,
    ProductDomainSnapshot,
    StoredDefinition,
    StoredDomainSnapshot,
    Vocabulary,
    summarize_domain,
)
from uie_core.registry import DataType


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StandardDefinitionOut(_Out):
    id: uuid.UUID
    created_at: datetime
    summary: DomainSummary
    definition: ProductDomainDefinition

    @classmethod
    def of(cls, d: StoredDefinition) -> StandardDefinitionOut:
        return cls(
            id=d.id,
            created_at=d.created_at,
            summary=summarize_domain(d.definition.entities, d.definition.fields),
            definition=d.definition,
        )


class DomainSnapshotOut(_Out):
    id: uuid.UUID
    definition_id: uuid.UUID
    instance_schema_snapshot_sequence: int
    created: bool  # False : snapshot existant retourné (même snapshot d'instance, même définition)
    summary: DomainSummary
    snapshot: ProductDomainSnapshot
    enrichment: EnrichmentResult | None = None  # seulement en réponse à analyze (V1b)

    @classmethod
    def of(cls, s: StoredDomainSnapshot, enrichment: EnrichmentResult | None = None) -> DomainSnapshotOut:
        snap = s.snapshot
        return cls(
            id=s.id,
            definition_id=s.definition_id,
            instance_schema_snapshot_sequence=s.instance_schema_snapshot_sequence,
            created=s.created,
            summary=summarize_domain(snap.entities, snap.fields, snap.extensions, snap.missing_standard),
            snapshot=snap,
            enrichment=enrichment,
        )

    @classmethod
    def analyzed(cls, outcome: AnalysisOutcome) -> DomainSnapshotOut:
        return cls.of(outcome.stored, outcome.enrichment)


class DomainSnapshotHistoryOut(_Out):
    """Une ligne d'historique (sans le contenu complet)."""

    id: uuid.UUID
    definition_id: uuid.UUID
    definition_version: str
    instance_schema_snapshot_id: uuid.UUID
    instance_schema_snapshot_sequence: int
    base_schema_snapshot_id: uuid.UUID
    analyzed_at: datetime
    checksum: str
    summary: DomainSummary

    @classmethod
    def of(cls, s: StoredDomainSnapshot) -> DomainSnapshotHistoryOut:
        snap = s.snapshot
        return cls(
            id=s.id,
            definition_id=s.definition_id,
            definition_version=snap.definition_version,
            instance_schema_snapshot_id=snap.instance_schema_snapshot_id,
            instance_schema_snapshot_sequence=s.instance_schema_snapshot_sequence,
            base_schema_snapshot_id=snap.base_schema_snapshot_id,
            analyzed_at=snap.analyzed_at,
            checksum=snap.checksum,
            summary=summarize_domain(snap.entities, snap.fields, snap.extensions, snap.missing_standard),
        )


# --------------------------------------------------------------------------- #
# Semantic Enrichment (V1b)
# --------------------------------------------------------------------------- #
class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfirmIn(_In):
    semantic_key: str | None = Field(
        None, description="Clé du vocabulaire ; absente = accepter la proposition"
    )
    note: str | None = None


class ChangeIn(_In):
    semantic_key: str = Field(description=f"Clé du vocabulaire, ou '{OTHER_KEY}' (autre / je ne sais pas)")
    note: str | None = None


class NoteIn(_In):
    note: str | None = None


class FieldRefIn(_In):
    entity: str
    field: str


class EnrichAiIn(_In):
    """Ask AI (V1c) : champs précis (sinon toutes les cibles éligibles), force = ignorer cache et règle."""

    fields: list[FieldRefIn] | None = None
    force: bool = False
    max_fields: int = Field(50, ge=1, le=200)


class VocabularyKeyOut(_Out):
    key: str
    label: str
    description: str
    data_type: DataType
    cardinality: str


class VocabularyGroupOut(_Out):
    group: FieldGroup
    keys: list[VocabularyKeyOut]


class VocabularyOut(_Out):
    domain_key: str
    version: str
    groups: list[VocabularyGroupOut]

    @classmethod
    def of(cls, v: Vocabulary) -> VocabularyOut:
        groups: dict[FieldGroup, list[VocabularyKeyOut]] = {}
        for k in v.keys:
            groups.setdefault(k.group, []).append(
                VocabularyKeyOut(
                    key=k.key,
                    label=k.label,
                    description=k.description,
                    data_type=k.data_type,
                    cardinality=k.cardinality,
                )
            )
        return cls(
            domain_key=v.domain_key.value,
            version=v.version,
            groups=[VocabularyGroupOut(group=g, keys=keys) for g, keys in groups.items()],
        )
