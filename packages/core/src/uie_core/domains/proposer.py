"""Contrat de la proposition sémantique par LLM (Product Domain V1c) : le Core ne connaît que ceci.

    Rules → LLM semantic proposal → Human confirmation when needed.

Le LLM aide à comprendre un champ ; il ne décide JAMAIS de retirer un champ du catalogue. Une réponse ne
peut être que MATCH (une semantic_key du vocabulaire fourni, une confiance, une justification) ou
NO_CONFIDENT_MATCH (une raison). Tout le reste d'une réponse brute (pertinence, exclusion, statut...) est
ignoré et signalé : la validation se fait ICI, pour tous les fournisseurs.

Les implémentations (Null, Replay, Anthropic, OpenAI) vivent dans `uie_ai` et dépendent de ce module,
jamais l'inverse (même principe que SchemaDiscoveryProvider / uie_connectors).
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from functools import cached_property
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from uie_core.domains.model import DomainKey, EntityRole, FieldOrigin

MAX_JUSTIFICATION = 200
# seules clés acceptées dans une réponse brute ; toute autre (relevance, exclude...) est ignorée et signalée
ANSWER_KEYS = frozenset({"outcome", "semantic_key", "confidence", "justification", "reason"})


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProposalOutcome(StrEnum):
    MATCH = "MATCH"
    NO_CONFIDENT_MATCH = "NO_CONFIDENT_MATCH"


class PromptSpec(_Model):
    """Prompt système + exemples, fournis par le domain pack (fichiers versionnés, jamais dans le code)."""

    version: str
    system: str
    few_shots: list[dict[str, Any]] = []

    @cached_property
    def checksum(self) -> str:
        raw = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class VocabularyChoice(_Model):
    """Une clé proposable pour CE champ (type et rôle déjà compatibles)."""

    key: str
    label: str
    description: str
    group: str
    data_type: str
    expected_types: list[str] = []


class FieldMetadata(_Model):
    """Métadonnées techniques du champ : JAMAIS de valeur métier, de secret ni d'URL d'instance."""

    entity: str
    technical_name: str
    label: str | None = None
    help: str | None = None
    native_type: str | None = None
    data_type: str | None = None
    required: bool = False
    stored: bool = True
    computed: bool = False
    related: str | None = None
    relation_target: str | None = None
    relation_target_role: EntityRole | None = None
    selection_values: list[str] | None = None
    modules: list[str] = []
    origin: FieldOrigin = FieldOrigin.STANDARD


class ProposalRequest(_Model):
    request_ref: str  # identifiant opaque (corrélation), jamais le nom de l'instance ni du client
    domain_key: DomainKey
    system: str
    version: str
    entity_role: EntityRole | None
    field: FieldMetadata
    vocabulary: list[VocabularyChoice]
    # clés single déjà tenues sur l'entité → champ qui la tient (le LLM doit le nommer, pas la reprendre)
    keys_held_on_entity: dict[str, str] = {}
    prompt: PromptSpec

    @property
    def path(self) -> str:
        return f"{self.field.entity}.{self.field.technical_name}"

    @property
    def allowed_keys(self) -> frozenset[str]:
        return frozenset(v.key for v in self.vocabulary)


class ProposalMeta(_Model):
    """Provenance d'un appel (journalisée) ; aucun secret."""

    provider: str
    model: str | None = None
    request_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    cost_usd: float | None = None


class ProposalResult(_Model):
    outcome: ProposalOutcome
    semantic_key: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    justification: str | None = None
    reason: str | None = None
    raw: dict[str, Any] | None = None  # réponse structurée brute, telle que reçue (journal)
    ignored_fields: list[str] = []  # clés de la réponse brute hors contrat (pertinence, exclusion...)
    error: str | None = None  # échec réseau, timeout, réponse illisible, clé hors vocabulaire
    meta: ProposalMeta

    @classmethod
    def failure(cls, error: str, meta: ProposalMeta, raw: dict[str, Any] | None = None) -> ProposalResult:
        """Tout échec devient NO_CONFIDENT_MATCH (jamais une exception jusqu'au supplier)."""
        return cls(
            outcome=ProposalOutcome.NO_CONFIDENT_MATCH,
            reason="AI could not answer",
            raw=raw,
            error=error[:500],
            meta=meta,
        )

    @classmethod
    def from_raw(cls, raw: Any, meta: ProposalMeta, request: ProposalRequest) -> ProposalResult:
        """Validation commune de la réponse structurée d'un fournisseur (ou d'un replay)."""
        if not isinstance(raw, dict):
            return cls.failure("réponse structurée attendue (objet JSON)", meta)
        ignored = sorted(k for k in raw if k not in ANSWER_KEYS)
        outcome = raw.get("outcome")
        if outcome == ProposalOutcome.NO_CONFIDENT_MATCH:
            return cls(
                outcome=ProposalOutcome.NO_CONFIDENT_MATCH,
                reason=_text(raw.get("reason")) or "No confident match",
                raw=raw,
                ignored_fields=ignored,
                meta=meta,
            )
        if outcome != ProposalOutcome.MATCH:
            return cls.failure(f"outcome inattendu : {outcome!r}", meta, raw).model_copy(
                update={"ignored_fields": ignored}
            )
        key = raw.get("semantic_key")
        if key not in request.allowed_keys:
            return cls.failure(f"semantic_key hors vocabulaire : {key!r}", meta, raw).model_copy(
                update={"ignored_fields": ignored}
            )
        confidence = raw.get("confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, int | float)
            or not 0 <= confidence <= 1
        ):
            return cls.failure(f"confidence invalide : {confidence!r}", meta, raw).model_copy(
                update={"ignored_fields": ignored}
            )
        return cls(
            outcome=ProposalOutcome.MATCH,
            semantic_key=key,
            confidence=round(float(confidence), 2),
            justification=_text(raw.get("justification")),
            raw=raw,
            ignored_fields=ignored,
            meta=meta,
        )


def _text(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:MAX_JUSTIFICATION]


@runtime_checkable
class SemanticProposer(Protocol):
    """Propose une semantic_key pour UN champ. Ne lève jamais : un échec rend NO_CONFIDENT_MATCH."""

    provider: str  # "none" = aucun fournisseur configuré (le produit fonctionne sans LLM)
    model: str | None

    def propose(self, request: ProposalRequest) -> ProposalResult: ...


class NoProposer:
    """Défaut du Core quand aucun proposer n'est injecté : aucune proposition (équivalent NullProposer)."""

    provider = "none"
    model: str | None = None

    def propose(self, request: ProposalRequest) -> ProposalResult:
        return ProposalResult(
            outcome=ProposalOutcome.NO_CONFIDENT_MATCH,
            reason="No AI provider configured",
            meta=ProposalMeta(provider=self.provider),
        )
