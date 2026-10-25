"""Supplier API — ACCOUNTING d'un système : analyse (même moteur et mêmes réponses que le catalogue et les
partenaires), sociétés, lecture des pièces d'UNE société pour UN mois (phase 3), puis préparation d'un format
cible (phase 4) : réglages, correspondances confirmées, aperçu validé. Lecture seule vers la source ; aucun
fichier produit, rien envoyé. TenantContext obligatoire ; DTO de `uie_api.supplier*`."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Response
from uie_core.accounting import AccountingPeriod, MappingOrigin
from uie_core.domains import CatalogAnalysisSummary, SemanticEnrichmentService
from uie_core.domains.catalog_analysis import CatalogAnalysisFailed, CatalogAnalysisService
from uie_core.mapping import LookupKind
from uie_core.registry import NotFound
from uie_core.registry import models as m

from uie_api.deps import (
    TenantAccountingApiTarget,
    TenantAccountingEnrichment,
    TenantAccountingRead,
    TenantAccountingTarget,
    TenantInstances,
)
from uie_api.supplier import (
    ACCOUNTING_STRUCTURE_LABEL,
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
from uie_api.supplier_accounting import AccountingCompanyOut, AccountingReadOut, accounting_read, company
from uie_api.supplier_accounting_api_target import (
    AccountChoiceIn,
    AccountingApiPreviewOut,
    AccountingApiTargetOut,
    ApiResolveIn,
    ApiSendIn,
    ApiSendRunOut,
    ApiSendSummaryOut,
    TargetChoiceOut,
)
from uie_api.supplier_accounting_target import (
    AccountingExportRunOut,
    AccountingFileIn,
    AccountingFilePreviewOut,
    AccountingPreparationOut,
    AccountingTargetOut,
    AccountingTargetSettingsOut,
    DocumentReleaseIn,
    MappingDecisionIn,
    TargetSettingsIn,
    accounting_run,
    file_preview,
    preparation,
    target_option,
    target_settings,
)

router = APIRouter(prefix="/api", tags=["supplier-accounting"])

SYSTEM = "/systems/{system_id}"
FIELD = SYSTEM + "/accounting/fields/{entity}/{field}"
ANALYSIS_FAILED = "We couldn't analyze your system. Please check the connection and try again."


def _summary(svc: SemanticEnrichmentService, inst: m.Instance) -> CatalogAnalysisSummary | None:
    try:
        return svc.summary(inst.id)
    except NotFound:
        return None


def _out(svc: SemanticEnrichmentService, inst: m.Instance) -> CatalogOut:
    return CatalogOut.of(inst, _summary(svc, inst), ACCOUNTING_STRUCTURE_LABEL)


@router.post(SYSTEM + "/accounting/analyze", summary="Analyze your accounting data (invoices, credit notes)")
def analyze_accounting(
    instances: TenantInstances, svc: TenantAccountingEnrichment, system_id: uuid.UUID
) -> CatalogOut:
    try:
        CatalogAnalysisService(svc.ctx, instances, svc).analyze(system_id)
    except CatalogAnalysisFailed:
        raise HTTPException(502, ANALYSIS_FAILED) from None
    return _out(svc, instances.get(system_id))


@router.get(SYSTEM + "/accounting/analysis", summary="Accounting analysis: identified, excluded, still open")
def get_accounting_analysis(svc: TenantAccountingEnrichment, system_id: uuid.UUID) -> CatalogOut:
    return _out(svc, svc.domains.instance(system_id))


@router.get(SYSTEM + "/accounting/questions", summary="Accounting fields we need you to confirm")
def list_accounting_questions(svc: TenantAccountingEnrichment, system_id: uuid.UUID) -> list[QuestionOut]:
    vocab = svc.vocabulary()
    return [question(v, vocab) for v in svc.get_pending_questions(system_id)]


@router.get(SYSTEM + "/accounting/answers", summary="Your answers about accounting fields, most recent first")
def list_accounting_answers(svc: TenantAccountingEnrichment, system_id: uuid.UUID) -> list[AnswerOut]:
    vocab = svc.vocabulary()
    return [answer(v, vocab) for v in svc.list_decisions(system_id)]


@router.post(FIELD + "/confirm", summary="Confirm the suggested meaning of an accounting field")
def confirm_accounting_field(
    svc: TenantAccountingEnrichment, system_id: uuid.UUID, entity: str, field: str, body: ConfirmAnswerIn
) -> AnswerOut:
    return answer(svc.confirm(system_id, entity, field, body.meaning, body.note), svc.vocabulary())


@router.post(FIELD + "/change", summary="Give another meaning to an accounting field, or 'other'")
def change_accounting_field(
    svc: TenantAccountingEnrichment, system_id: uuid.UUID, entity: str, field: str, body: ChangeAnswerIn
) -> AnswerOut:
    return answer(svc.change(system_id, entity, field, body.meaning, body.note), svc.vocabulary())


@router.post(FIELD + "/ignore", summary="This field is not part of your accounting data")
def ignore_accounting_field(
    svc: TenantAccountingEnrichment,
    system_id: uuid.UUID,
    entity: str,
    field: str,
    body: NoteAnswerIn | None = None,
) -> AnswerOut:
    return answer(svc.ignore(system_id, entity, field, body.note if body else None), svc.vocabulary())


@router.post(FIELD + "/reopen", summary="Reopen one of your answers about an accounting field")
def reopen_accounting_field(
    svc: TenantAccountingEnrichment,
    system_id: uuid.UUID,
    entity: str,
    field: str,
    body: NoteAnswerIn | None = None,
) -> QuestionOut:
    return question(svc.reopen(system_id, entity, field, body.note if body else None), svc.vocabulary())


@router.get("/accounting/vocabulary", summary="Meanings you can choose from for accounting fields")
def accounting_vocabulary(svc: TenantAccountingEnrichment) -> SupplierVocabularyOut:
    return SupplierVocabularyOut.of(svc.vocabulary())


@router.get(
    SYSTEM + "/accounting/companies", summary="Companies of your system (choose one to read its documents)"
)
def accounting_companies(svc: TenantAccountingRead, system_id: uuid.UUID) -> list[AccountingCompanyOut]:
    return [company(c) for c in svc.companies(system_id)]


@router.get(
    SYSTEM + "/accounting/documents",
    summary="Read the invoices and credit notes of one company for one month (read-only)",
)
def accounting_documents(
    svc: TenantAccountingRead, system_id: uuid.UUID, company_id: str, period: str
) -> AccountingReadOut:
    return accounting_read(svc.read(system_id, company_id, AccountingPeriod.parse(period)))


# --------------------------------------------------------------------------- #
# Préparation d'un format cible (phase 4) : réglages, correspondances, aperçu — aucun fichier
# --------------------------------------------------------------------------- #
TARGET = SYSTEM + "/accounting/targets/{target_system}/{target_product}"


def _kind(kind: str) -> LookupKind:
    try:
        return LookupKind(kind.upper())
    except ValueError:
        raise HTTPException(404, "Unknown mapping kind") from None


@router.get("/accounting/targets", summary="Formats your accounting documents can be prepared for")
def accounting_targets(svc: TenantAccountingTarget) -> list[AccountingTargetOut]:
    return [target_option(t) for t in svc.targets()]


@router.get(TARGET + "/settings", summary="Settings of the target client (shared ones are read-only here)")
def accounting_target_settings(
    svc: TenantAccountingTarget, system_id: uuid.UUID, target_system: str, target_product: str
) -> AccountingTargetSettingsOut:
    return target_settings(svc.settings(system_id, target_system, target_product))


@router.put(TARGET + "/settings", summary="Save the settings specific to this format")
def save_accounting_target_settings(
    svc: TenantAccountingTarget,
    system_id: uuid.UUID,
    target_system: str,
    target_product: str,
    body: TargetSettingsIn,
) -> AccountingTargetSettingsOut:
    return target_settings(svc.save_settings(system_id, target_system, target_product, body.values))


@router.get(
    TARGET + "/preparation",
    summary="Preview how the documents of one company and one month would be prepared (no file)",
)
def accounting_target_preparation(
    svc: TenantAccountingTarget,
    system_id: uuid.UUID,
    target_system: str,
    target_product: str,
    company_id: str,
    period: str,
    version: str | None = None,
) -> AccountingPreparationOut:
    return preparation(
        svc.preview(
            system_id, company_id, AccountingPeriod.parse(period), target_system, target_product, version
        )
    )


@router.put(TARGET + "/mappings/{kind}/{ref}", summary="Confirm the target value of a source account or tax")
def confirm_accounting_mapping(
    svc: TenantAccountingTarget,
    system_id: uuid.UUID,
    target_system: str,
    target_product: str,
    kind: str,
    ref: str,
    body: MappingDecisionIn,
) -> dict[str, bool]:
    svc.decide(
        system_id,
        target_system,
        target_product,
        _kind(kind),
        ref,
        value=body.value,
        no_value=body.no_value,
        accepted_suggestion=MappingOrigin.IDENTICAL_CODE if body.accept_suggestion else None,
        source_code=body.source_code,
        source_name=body.source_name,
    )
    return {"confirmed": True}


@router.delete(TARGET + "/mappings/{kind}/{ref}", summary="Remove a mapping decision")
def clear_accounting_mapping(
    svc: TenantAccountingTarget,
    system_id: uuid.UUID,
    target_system: str,
    target_product: str,
    kind: str,
    ref: str,
) -> dict[str, bool]:
    return {"removed": svc.clear(system_id, target_system, target_product, _kind(kind), ref)}


# --------------------------------------------------------------------------- #
# Fichier (phase 5) : aperçu exact, téléchargement local, fichiers générés — rien n'est envoyé
# --------------------------------------------------------------------------- #
@router.get(
    TARGET + "/file-preview",
    summary="Exact content of the file that would be downloaded (nothing is recorded)",
)
def accounting_file_preview(
    svc: TenantAccountingTarget,
    system_id: uuid.UUID,
    target_system: str,
    target_product: str,
    company_id: str,
    period: str,
    version: str | None = None,
    include_exported: bool = False,
) -> AccountingFilePreviewOut:
    return file_preview(
        svc.file_preview(
            system_id,
            company_id,
            AccountingPeriod.parse(period),
            target_system,
            target_product,
            version,
            include_exported=include_exported,
        )
    )


@router.post(
    TARGET + "/files",
    summary="Generate the file for download (not sent anywhere; your system is not modified)",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}, "description": "The export file"}},
)
def generate_accounting_file(
    svc: TenantAccountingTarget,
    system_id: uuid.UUID,
    target_system: str,
    target_product: str,
    body: AccountingFileIn,
) -> Response:
    file = svc.generate(
        system_id,
        body.company_id,
        AccountingPeriod.parse(body.period),
        target_system,
        target_product,
        body.version,
        expected_digest=body.content_token,
        include_exported=body.include_exported,
        confirm_duplicates=body.confirm_duplicates,
    )
    return Response(
        content=file.content,
        media_type=file.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{file.filename}"',
            "X-Export-Run": str(file.run.id),
        },
    )


@router.get(TARGET + "/files", summary="Files generated for this system and format (no content kept)")
def accounting_files(
    svc: TenantAccountingTarget, system_id: uuid.UUID, target_system: str, target_product: str
) -> list[AccountingExportRunOut]:
    return [accounting_run(r, tracked) for r, tracked in svc.runs(system_id, target_system, target_product)]


# --------------------------------------------------------------------------- #
# Historique d'export par pièce (idempotence) : autoriser une pièce modifiée, adopter un ancien fichier
# --------------------------------------------------------------------------- #
@router.post(
    TARGET + "/documents/{ref}/release",
    summary="Allow a new export of a document that changed since its export (explicit decision)",
)
def release_accounting_document(
    svc: TenantAccountingTarget,
    system_id: uuid.UUID,
    target_system: str,
    target_product: str,
    ref: str,
    body: DocumentReleaseIn,
) -> dict[str, bool]:
    svc.release_document(
        system_id,
        body.company_id,
        AccountingPeriod.parse(body.period),
        target_system,
        target_product,
        ref,
        version=body.version,
        note=body.note,
    )
    return {"released": True}


@router.delete(
    TARGET + "/documents/{ref}/release", summary="Withdraw the permission: the changed document stays blocked"
)
def revoke_accounting_document_release(
    svc: TenantAccountingTarget,
    system_id: uuid.UUID,
    target_system: str,
    target_product: str,
    ref: str,
    company_id: str,
    period: str,
    version: str | None = None,
) -> dict[str, bool]:
    removed = svc.release_document(
        system_id,
        company_id,
        AccountingPeriod.parse(period),
        target_system,
        target_product,
        ref,
        version=version,
        revoke=True,
    )
    return {"removed": removed}


@router.post(
    TARGET + "/files/{file_id}/adopt",
    summary="Mark the documents of a file generated before export tracking as already exported",
)
def adopt_accounting_file(
    svc: TenantAccountingTarget,
    system_id: uuid.UUID,
    target_system: str,
    target_product: str,
    file_id: uuid.UUID,
) -> dict[str, int]:
    return {"documents": svc.adopt_legacy_run(system_id, target_system, target_product, file_id)}


# --------------------------------------------------------------------------- #
# Préparer les pièces pour un système cible relié par son API (aperçu, lecture seule)
# --------------------------------------------------------------------------- #
API_TARGET = SYSTEM + "/accounting/api-targets/{target_id}"


@router.get(
    SYSTEM + "/accounting/api-targets",
    summary="Your connected systems that can receive the accounting documents of this system",
)
def accounting_api_targets(
    svc: TenantAccountingApiTarget, system_id: uuid.UUID
) -> list[AccountingApiTargetOut]:
    return [AccountingApiTargetOut.of(t) for t in svc.targets(system_id)]


@router.get(
    API_TARGET + "/options",
    summary="Values of the target system you can choose for your accounts (read from the target, read-only)",
)
def accounting_api_target_options(
    svc: TenantAccountingApiTarget, system_id: uuid.UUID, target_id: uuid.UUID
) -> list[TargetChoiceOut]:
    return [TargetChoiceOut.of(o) for o in svc.options(system_id, target_id)]


@router.put(
    API_TARGET + "/accounts/{account_reference}",
    status_code=204,
    summary="Choose the value of the target system for one of your accounts (checked against the target)",
)
def accounting_api_target_choose(
    svc: TenantAccountingApiTarget,
    system_id: uuid.UUID,
    target_id: uuid.UUID,
    account_reference: str,
    body: AccountChoiceIn,
) -> None:
    svc.decide(system_id, target_id, account_reference, body.choice_id, code=body.code, name=body.name)


@router.delete(API_TARGET + "/accounts/{account_reference}", status_code=204, summary="Remove a choice")
def accounting_api_target_clear(
    svc: TenantAccountingApiTarget, system_id: uuid.UUID, target_id: uuid.UUID, account_reference: str
) -> None:
    if not svc.clear(system_id, target_id, account_reference):
        raise HTTPException(404, "No choice for this account")


@router.get(
    API_TARGET + "/preview",
    summary="Preview the accounting documents of a month as the target system would receive them "
    "(read-only: nothing is sent, nothing is saved)",
)
def accounting_api_target_preview(
    svc: TenantAccountingApiTarget, system_id: uuid.UUID, target_id: uuid.UUID, company_id: str, period: str
) -> AccountingApiPreviewOut:
    return AccountingApiPreviewOut.of(
        svc.preview(system_id, target_id, company_id, AccountingPeriod.parse(period))
    )


# --------------------------------------------------------------------------- #
# Envoyer les pièces choisies (sélection + confirmation explicites, jamais rejoué pour une même clé)
# --------------------------------------------------------------------------- #
@router.post(
    API_TARGET + "/sends",
    summary="Send the selected accounting documents to the target system (explicit confirmation required; "
    "only ready documents never sent before; the same request key never sends twice)",
)
def accounting_api_target_send(
    svc: TenantAccountingApiTarget, system_id: uuid.UUID, target_id: uuid.UUID, body: ApiSendIn
) -> ApiSendRunOut:
    result = svc.send(
        system_id,
        target_id,
        body.company_id,
        AccountingPeriod.parse(body.period),
        refs=body.references,
        request_key=body.request_key,
        confirm=body.confirm,
    )
    return ApiSendRunOut.of(result)


@router.get(API_TARGET + "/sends", summary="Previous sends of accounting documents to this target system")
def accounting_api_target_sends(
    svc: TenantAccountingApiTarget, system_id: uuid.UUID, target_id: uuid.UUID
) -> list[ApiSendSummaryOut]:
    return [
        ApiSendSummaryOut(
            id=r.id, status=r.status.lower(), created_at=r.created_at, finished_at=r.finished_at
        )
        for r in svc.runs(system_id, target_id)
    ]


@router.get(API_TARGET + "/sends/{run_id}", summary="Result of a send, document by document")
def accounting_api_target_send_result(
    svc: TenantAccountingApiTarget, system_id: uuid.UUID, target_id: uuid.UUID, run_id: uuid.UUID
) -> ApiSendRunOut:
    return ApiSendRunOut.of(svc.run(system_id, target_id, run_id))


@router.post(
    API_TARGET + "/sends/{run_id}/items/{item_id}/resolve",
    summary="Tell us what you found in the target system for a document with an unknown outcome "
    "(read-only check; nothing is written to the target)",
)
def accounting_api_target_send_resolve(
    svc: TenantAccountingApiTarget,
    system_id: uuid.UUID,
    target_id: uuid.UUID,
    run_id: uuid.UUID,  # noqa: ARG001 — lisibilité de l'adresse ; l'élément porte son envoi
    item_id: uuid.UUID,
    body: ApiResolveIn,
) -> ApiSendRunOut:
    result = svc.resolve_unknown(
        system_id,
        target_id,
        body.company_id,
        AccountingPeriod.parse(body.period),
        item_id,
        created=body.created,
    )
    return ApiSendRunOut.of(result)
