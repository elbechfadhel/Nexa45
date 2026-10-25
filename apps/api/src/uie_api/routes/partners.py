"""Supplier API — analyse PARTNER d'un système (clients / fournisseurs) : même moteur et mêmes réponses que
l'analyse du catalogue (D8), domaine PARTNER. TenantContext obligatoire ; DTO de `uie_api.supplier`."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from uie_core.domains import CatalogAnalysisSummary, SemanticEnrichmentService
from uie_core.domains.catalog_analysis import CatalogAnalysisFailed, CatalogAnalysisService
from uie_core.registry import NotFound
from uie_core.registry import models as m

from uie_api.deps import TenantInstances, TenantPartnerEnrichment, TenantPartnerTarget
from uie_api.supplier import (
    PARTNER_STRUCTURE_LABEL,
    AnswerOut,
    CatalogOut,
    ChangeAnswerIn,
    ConfirmAnswerIn,
    NoteAnswerIn,
    QuestionOut,
    SupplierVocabularyOut,
    answer,
    question,
)
from uie_api.supplier_partner_target import (
    LinkIn,
    MatchOut,
    PartnerTargetOut,
    PartnerTargetPreviewOut,
    ResolveIn,
    SendIn,
    SendRunOut,
    SendRunSummaryOut,
)

router = APIRouter(prefix="/api", tags=["supplier-partners"])

SYSTEM = "/systems/{system_id}"
FIELD = SYSTEM + "/partners/fields/{entity}/{field}"
ANALYSIS_FAILED = "We couldn't analyze your system. Please check the connection and try again."


def _summary(svc: SemanticEnrichmentService, inst: m.Instance) -> CatalogAnalysisSummary | None:
    try:
        return svc.summary(inst.id)
    except NotFound:
        return None


def _out(svc: SemanticEnrichmentService, inst: m.Instance) -> CatalogOut:
    return CatalogOut.of(inst, _summary(svc, inst), PARTNER_STRUCTURE_LABEL)


@router.post(SYSTEM + "/partners/analyze", summary="Analyze your customers and suppliers (partner data)")
def analyze_partners(
    instances: TenantInstances, svc: TenantPartnerEnrichment, system_id: uuid.UUID
) -> CatalogOut:
    try:
        CatalogAnalysisService(svc.ctx, instances, svc).analyze(system_id)
    except CatalogAnalysisFailed:
        raise HTTPException(502, ANALYSIS_FAILED) from None
    return _out(svc, instances.get(system_id))


@router.get(SYSTEM + "/partners/analysis", summary="Partner analysis: identified, excluded, still open")
def get_partner_analysis(svc: TenantPartnerEnrichment, system_id: uuid.UUID) -> CatalogOut:
    return _out(svc, svc.domains.instance(system_id))


@router.get(SYSTEM + "/partners/questions", summary="Partner fields we need you to confirm")
def list_partner_questions(svc: TenantPartnerEnrichment, system_id: uuid.UUID) -> list[QuestionOut]:
    vocab = svc.vocabulary()
    return [question(v, vocab) for v in svc.get_pending_questions(system_id)]


@router.get(SYSTEM + "/partners/answers", summary="Your answers about partner fields, most recent first")
def list_partner_answers(svc: TenantPartnerEnrichment, system_id: uuid.UUID) -> list[AnswerOut]:
    vocab = svc.vocabulary()
    return [answer(v, vocab) for v in svc.list_decisions(system_id)]


@router.post(FIELD + "/confirm", summary="Confirm the suggested meaning of a partner field")
def confirm_partner_field(
    svc: TenantPartnerEnrichment, system_id: uuid.UUID, entity: str, field: str, body: ConfirmAnswerIn
) -> AnswerOut:
    return answer(svc.confirm(system_id, entity, field, body.meaning, body.note), svc.vocabulary())


@router.post(FIELD + "/change", summary="Give another meaning to a partner field, or 'other'")
def change_partner_field(
    svc: TenantPartnerEnrichment, system_id: uuid.UUID, entity: str, field: str, body: ChangeAnswerIn
) -> AnswerOut:
    return answer(svc.change(system_id, entity, field, body.meaning, body.note), svc.vocabulary())


@router.post(FIELD + "/ignore", summary="This field is not part of your partner data")
def ignore_partner_field(
    svc: TenantPartnerEnrichment,
    system_id: uuid.UUID,
    entity: str,
    field: str,
    body: NoteAnswerIn | None = None,
) -> AnswerOut:
    return answer(svc.ignore(system_id, entity, field, body.note if body else None), svc.vocabulary())


@router.post(FIELD + "/reopen", summary="Reopen one of your answers about a partner field")
def reopen_partner_field(
    svc: TenantPartnerEnrichment,
    system_id: uuid.UUID,
    entity: str,
    field: str,
    body: NoteAnswerIn | None = None,
) -> QuestionOut:
    return question(svc.reopen(system_id, entity, field, body.note if body else None), svc.vocabulary())


@router.get("/partners/vocabulary", summary="Meanings you can choose from for partner fields")
def partner_vocabulary(svc: TenantPartnerEnrichment) -> SupplierVocabularyOut:
    return SupplierVocabularyOut.of(svc.vocabulary())


# --------------------------------------------------------------------------- #
# Préparer les partenaires pour un système cible relié par son API (aperçu, aucune écriture)
# --------------------------------------------------------------------------- #
@router.get(
    SYSTEM + "/partners/targets",
    summary="Your connected systems that can receive the customers and suppliers of this system",
)
def partner_targets(svc: TenantPartnerTarget, system_id: uuid.UUID) -> list[PartnerTargetOut]:
    return [PartnerTargetOut.of(t) for t in svc.targets(system_id)]


@router.get(
    SYSTEM + "/partners/targets/{target_id}/preview",
    summary="Preview what would be sent to the target system for each customer and supplier "
    "(read-only: nothing is sent, nothing is saved)",
)
def partner_target_preview(
    svc: TenantPartnerTarget, system_id: uuid.UUID, target_id: uuid.UUID
) -> PartnerTargetPreviewOut:
    return PartnerTargetPreviewOut.of(svc.preview(system_id, target_id))


# --------------------------------------------------------------------------- #
# Envoyer les partenaires choisis au système cible (phase 1b) : sélection + confirmation explicites
# --------------------------------------------------------------------------- #
TARGET = SYSTEM + "/partners/targets/{target_id}"


@router.post(
    TARGET + "/sends",
    summary="Send the selected customers and suppliers to the target system (explicit confirmation required; "
    "the same request key never sends twice)",
)
def partner_target_send(
    svc: TenantPartnerTarget, system_id: uuid.UUID, target_id: uuid.UUID, body: SendIn
) -> SendRunOut:
    view = svc.send(
        system_id,
        target_id,
        refs=body.references,
        request_key=body.request_key,
        confirm=body.confirm,
        create_new=body.create_new,
    )
    return SendRunOut.of(view)


@router.get(TARGET + "/sends", summary="Previous sends to this target system")
def partner_target_sends(
    svc: TenantPartnerTarget, system_id: uuid.UUID, target_id: uuid.UUID
) -> list[SendRunSummaryOut]:
    return [SendRunSummaryOut.of(r) for r in svc.runs(system_id, target_id)]


@router.get(TARGET + "/sends/{run_id}", summary="Result of a send, record by record")
def partner_target_send_result(
    svc: TenantPartnerTarget, system_id: uuid.UUID, target_id: uuid.UUID, run_id: uuid.UUID
) -> SendRunOut:
    return SendRunOut.of(svc.run(system_id, target_id, run_id))


@router.post(
    TARGET + "/sends/{run_id}/resume",
    summary="Resume an interrupted send (records that were being written become 'unknown', never resent)",
)
def partner_target_send_resume(
    svc: TenantPartnerTarget, system_id: uuid.UUID, target_id: uuid.UUID, run_id: uuid.UUID
) -> SendRunOut:
    return SendRunOut.of(svc.resume(system_id, target_id, run_id))


@router.post(
    TARGET + "/sends/{run_id}/items/{item_id}/resolve",
    summary="Tell us what you found in the target system for a record with an unknown outcome",
)
def partner_target_send_resolve(
    svc: TenantPartnerTarget,
    system_id: uuid.UUID,
    target_id: uuid.UUID,
    run_id: uuid.UUID,  # noqa: ARG001 — lisibilité de l'adresse ; l'élément porte son envoi
    item_id: uuid.UUID,
    body: ResolveIn,
) -> SendRunOut:
    item = svc.resolve_unknown(
        system_id, target_id, item_id, created=body.created, remote_id=body.target_record_id
    )
    return SendRunOut.of(svc.run(system_id, target_id, item.run_id))


@router.get(
    TARGET + "/matches",
    summary="Existing records of the target system that may be this customer or supplier (read-only)",
)
def partner_target_matches(
    svc: TenantPartnerTarget, system_id: uuid.UUID, target_id: uuid.UUID, reference: str
) -> list[MatchOut]:
    return [MatchOut.of(r) for r in svc.matches(system_id, target_id, reference)]


@router.post(
    TARGET + "/links",
    status_code=204,
    summary="Link a customer or supplier to an existing record of the target system (it will be updated, "
    "never duplicated)",
)
def partner_target_link(
    svc: TenantPartnerTarget, system_id: uuid.UUID, target_id: uuid.UUID, body: LinkIn
) -> None:
    svc.link(system_id, target_id, body.reference, body.target_record_id)


@router.delete(
    TARGET + "/links", status_code=204, summary="Remove a link you made that was never used by a send"
)
def partner_target_unlink(
    svc: TenantPartnerTarget, system_id: uuid.UUID, target_id: uuid.UUID, reference: str
) -> None:
    svc.unlink(system_id, target_id, reference)
