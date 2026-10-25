"""Routes Semantic Enrichment (Product Domain V1b) : API → SemanticEnrichmentService → Core.

Lecture = état persisté (aucun recalcul) ; seul POST .../product-domain/analyze enrichit. Aucune logique
sémantique ici : les réponses du supplier sont validées par le Core (clé du vocabulaire ou « other »).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from uie_core.domains import (
    AiEnrichmentResult,
    AiStatus,
    BindingView,
    CatalogAnalysisSummary,
    MeaningStatus,
    Relevance,
)

from uie_api.deps import SemanticEnrichment
from uie_api.schemas_domains import ChangeIn, ConfirmIn, EnrichAiIn, NoteIn, VocabularyOut

router = APIRouter(tags=["semantic-enrichment"])

FIELD = "/admin/instances/{instance_id}/product-domain/fields/{entity}/{field}"


@router.get(
    "/admin/instances/{instance_id}/product-domain/analysis",
    summary="Product Catalog Analysis : compteurs, structure, complétude (état persisté)",
)
def analysis(svc: SemanticEnrichment, instance_id: uuid.UUID) -> CatalogAnalysisSummary:
    return svc.summary(instance_id)


@router.get(
    "/admin/instances/{instance_id}/product-domain/questions",
    summary="Questions ouvertes (PROPOSED / UNRESOLVED / STALE) : extensions d'abord, puis confiance",
)
def questions(svc: SemanticEnrichment, instance_id: uuid.UUID) -> list[BindingView]:
    return svc.get_pending_questions(instance_id)


@router.get(
    "/admin/instances/{instance_id}/product-domain/exclusions",
    summary="Champs exclus automatiquement par une règle, avec la raison (contestables : include)",
)
def exclusions(
    svc: SemanticEnrichment,
    instance_id: uuid.UUID,
    extensions_only: Annotated[
        bool, Query(description="Seulement les extensions CUSTOM / THIRD_PARTY")
    ] = False,
) -> list[BindingView]:
    return svc.list_exclusions(instance_id, extensions_only=extensions_only)


@router.get(
    "/admin/instances/{instance_id}/product-domain/decisions",
    summary="Réponses données par le supplier, la plus récente d'abord (modifiables ou à rouvrir)",
)
def decisions(svc: SemanticEnrichment, instance_id: uuid.UUID) -> list[BindingView]:
    return svc.list_decisions(instance_id)


@router.get(
    "/admin/instances/{instance_id}/product-domain/bindings",
    summary="Décisions sémantiques des champs (règle ou humaine), filtrables",
)
def bindings(
    svc: SemanticEnrichment,
    instance_id: uuid.UUID,
    status: MeaningStatus | None = None,
    relevance: Relevance | None = None,
) -> list[BindingView]:
    return svc.list_bindings(instance_id, status=status, relevance=relevance)


@router.post(f"{FIELD}/confirm", summary="Confirme la proposition (ou une clé du vocabulaire) → CONFIRMED")
def confirm(
    svc: SemanticEnrichment, instance_id: uuid.UUID, entity: str, field: str, body: ConfirmIn
) -> BindingView:
    return svc.confirm(instance_id, entity, field, body.semantic_key, body.note)


@router.post(
    f"{FIELD}/change", summary="Autre clé du vocabulaire → CONFIRMED ; « other » → UNRESOLVED avec la note"
)
def change(
    svc: SemanticEnrichment, instance_id: uuid.UUID, entity: str, field: str, body: ChangeIn
) -> BindingView:
    return svc.change(instance_id, entity, field, body.semantic_key, body.note)


@router.post(f"{FIELD}/ignore", summary="Exclut le champ du catalogue → IGNORED, NOT_RELEVANT")
def ignore(
    svc: SemanticEnrichment, instance_id: uuid.UUID, entity: str, field: str, body: NoteIn | None = None
) -> BindingView:
    return svc.ignore(instance_id, entity, field, body.note if body else None)


@router.post(f"{FIELD}/include", summary="Conteste une exclusion : le champ redevient une question")
def include(
    svc: SemanticEnrichment, instance_id: uuid.UUID, entity: str, field: str, body: NoteIn | None = None
) -> BindingView:
    return svc.include(instance_id, entity, field, body.note if body else None)


@router.post(
    f"{FIELD}/reopen", summary="Revient sur une réponse du supplier : le champ redevient une question"
)
def reopen(
    svc: SemanticEnrichment, instance_id: uuid.UUID, entity: str, field: str, body: NoteIn | None = None
) -> BindingView:
    return svc.reopen(instance_id, entity, field, body.note if body else None)


@router.post(
    "/admin/instances/{instance_id}/product-domain/enrich-ai",
    summary="Ask AI : proposition sémantique par LLM des champs encore ambigus (jamais une exclusion)",
)
def enrich_ai(
    svc: SemanticEnrichment, instance_id: uuid.UUID, body: EnrichAiIn | None = None
) -> AiEnrichmentResult:
    body = body or EnrichAiIn()
    fields = [(f.entity, f.field) for f in body.fields] if body.fields is not None else None
    return svc.enrich_with_llm(instance_id, fields, force=body.force, max_fields=body.max_fields)


@router.get(
    "/admin/instances/{instance_id}/product-domain/ai-status",
    summary="Fournisseur LLM configuré (ou none), candidats, derniers appels, tokens et coût (aucun appel)",
)
def ai_status(svc: SemanticEnrichment, instance_id: uuid.UUID) -> AiStatus:
    return svc.ai_status(instance_id)


@router.get(
    "/admin/vocabulary/product",
    summary="Vocabulaire contrôlé du domaine PRODUCT, groupé (sélecteur « Change meaning »)",
)
def vocabulary(svc: SemanticEnrichment) -> VocabularyOut:
    return VocabularyOut.of(svc.vocabulary())
