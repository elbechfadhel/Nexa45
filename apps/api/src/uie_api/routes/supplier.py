"""Supplier API (V1d, `/api/*`) : les systèmes du supplier et l'analyse de leur catalogue produit.

TenantContext obligatoire (require_tenant) : le Core ne voit que les instances de l'organisation
sélectionnée ; une instance d'un autre tenant est introuvable (404 identique à un id inexistant).
Réponses = DTO de `uie_api.supplier`, jamais ceux de l'Admin. Hors de cette API (Admin seulement) :
discovery, diff, snapshots, domaine technique, exclusions avec rule_id, bindings bruts, include.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from uie_core.domains import CatalogAnalysisSummary, SemanticEnrichmentService
from uie_core.domains.catalog_analysis import CatalogAnalysisFailed, CatalogAnalysisService
from uie_core.instances import (
    ConnectionCheck,
    ConnectionFailed,
    ConnectionFieldRole,
    ConnectionFieldsError,
    InstanceNameTaken,
)
from uie_core.instances.service import InstanceService
from uie_core.registry import NotFound
from uie_core.registry import models as m

from uie_api.deps import TenantInstances, TenantSemanticEnrichment
from uie_api.supplier import (
    AiAvailabilityOut,
    AnswerOut,
    AskAiOut,
    CatalogOut,
    ChangeAnswerIn,
    CheckConnectionIn,
    ConfirmAnswerIn,
    ConnectionCheckOut,
    ConnectionStatusOut,
    ConnectorFieldOut,
    ConnectorOut,
    ConnectSystemIn,
    NoteAnswerIn,
    QuestionOut,
    SupplierSystemOut,
    SupplierVocabularyOut,
    SystemRemovedOut,
    ai_availability,
    answer,
    ask_ai,
    connection_message,
    connection_status,
    question,
    supplier_system,
)

router = APIRouter(prefix="/api", tags=["supplier"])

SYSTEM = "/systems/{system_id}"
ANALYSIS_FAILED = "We couldn't analyze your system. Please check the connection and try again."
FIELD = SYSTEM + "/catalog/fields/{entity}/{field}"


def _summary(svc: SemanticEnrichmentService, inst: m.Instance) -> CatalogAnalysisSummary | None:
    """Résumé de l'analyse, None si le catalogue n'a jamais été analysé (l'instance est déjà scopée)."""
    try:
        return svc.summary(inst.id)
    except NotFound:
        return None


INCOMPLETE = connection_message("INCOMPLETE")


def _facts(instances: InstanceService, check: ConnectionCheck) -> list[tuple[str, str]]:
    """Faits sûrs d'un test réussi, choisis par le connecteur (jamais de secret)."""
    if not check.result.ok:
        return []
    return instances.providers.resolve(check.system, "", "").connection_summary(check.result)


def _system(instances: InstanceService, inst: m.Instance, summary: CatalogAnalysisSummary | None):  # noqa: ANN202
    """Système tel que le fournisseur le voit : faits sûrs du connecteur ; l'adresse n'est rappelée que si
    c'est LUI qui l'a saisie (champ d'adresse dans le formulaire du connecteur)."""
    own = any(f.role == ConnectionFieldRole.ENDPOINT for f in instances.connection_fields(inst.id))
    return supplier_system(inst, summary, instances.connection_facts(inst.id), own_address=own)


def _known(instances: InstanceService, system: str) -> None:
    """Seul un système connectable est accepté (même liste que GET /api/connectors)."""
    if system not in {c.system for c in instances.connectors()}:
        raise HTTPException(404, "This system cannot be connected yet.")


@router.get("/systems", summary="Your connected systems")
def list_systems(instances: TenantInstances, svc: TenantSemanticEnrichment) -> list[SupplierSystemOut]:
    return [_system(instances, i, _summary(svc, i)) for i in instances.list()]


@router.get(
    "/connectors",
    summary="Systems you can connect: supported versions and the connection form of each system",
)
def connectors(instances: TenantInstances) -> list[ConnectorOut]:
    return [
        ConnectorOut(
            system=c.system,
            name=c.name,
            versions=c.versions,
            fields=[ConnectorFieldOut.of(f) for f in c.fields],
        )
        for c in instances.connectors()
    ]


@router.post(
    "/systems/check-connection",
    summary="Test the connection to your system (nothing is saved; secrets are never returned)",
)
def check_connection(instances: TenantInstances, body: CheckConnectionIn) -> ConnectionCheckOut:
    _known(instances, body.system)
    try:
        check = instances.check_connection_values(body.system, body.fields)
    except ConnectionFieldsError:
        raise HTTPException(422, INCOMPLETE) from None
    return ConnectionCheckOut.of(check, _facts(instances, check))


@router.post(
    "/systems",
    status_code=status.HTTP_201_CREATED,
    summary="Connect a system to your workspace (tested again before saving)",
)
def connect_system(instances: TenantInstances, body: ConnectSystemIn) -> SupplierSystemOut:
    _known(instances, body.system)
    try:
        inst, _ = instances.connect_values(body.name, body.system, body.fields)
    except ConnectionFieldsError:
        raise HTTPException(422, INCOMPLETE) from None
    except ConnectionFailed as exc:
        raise HTTPException(422, ConnectionCheckOut.of(exc.check).message) from None
    except InstanceNameTaken:
        raise HTTPException(409, "A system with this name already exists in your workspace.") from None
    return _system(instances, inst, None)


@router.get(SYSTEM, summary="One of your systems: connection and catalog status")
def get_system(
    instances: TenantInstances, svc: TenantSemanticEnrichment, system_id: uuid.UUID
) -> SupplierSystemOut:
    inst = instances.get(system_id)
    return _system(instances, inst, _summary(svc, inst))


@router.delete(
    SYSTEM,
    summary="Remove one of your systems: connection, analysis, answers and synced products are deleted "
    "(nothing is changed in the system itself)",
)
def remove_system(instances: TenantInstances, system_id: uuid.UUID) -> SystemRemovedOut:
    removed = instances.delete(system_id)  # autre organisation → 404, comme un système inexistant
    return SystemRemovedOut(name=removed.name, removed=True)


@router.post(SYSTEM + "/test-connection", summary="Test the connection to your system")
def test_connection(instances: TenantInstances, system_id: uuid.UUID) -> ConnectionStatusOut:
    instances.test_connection(system_id)
    return connection_status(instances.get(system_id))


@router.post(
    SYSTEM + "/catalog/analyze",
    summary="Analyze your product catalog: connection, product structure, catalog fields (V1 engine)",
)
def analyze_catalog(
    instances: TenantInstances, svc: TenantSemanticEnrichment, system_id: uuid.UUID
) -> CatalogOut:
    try:
        CatalogAnalysisService(svc.ctx, instances, svc).analyze(system_id)
    except CatalogAnalysisFailed:
        raise HTTPException(502, ANALYSIS_FAILED) from None  # détail technique dans les journaux seulement
    inst = instances.get(system_id)
    return CatalogOut.of(inst, _summary(svc, inst))


@router.get(SYSTEM + "/catalog", summary="Catalog analysis: what was identified, excluded, still open")
def get_catalog(svc: TenantSemanticEnrichment, system_id: uuid.UUID) -> CatalogOut:
    inst = svc.domains.instance(system_id)
    return CatalogOut.of(inst, _summary(svc, inst))


@router.get(SYSTEM + "/catalog/questions", summary="Fields we need you to confirm")
def list_questions(svc: TenantSemanticEnrichment, system_id: uuid.UUID) -> list[QuestionOut]:
    vocab = svc.vocabulary()
    return [question(v, vocab) for v in svc.get_pending_questions(system_id)]


@router.get(SYSTEM + "/catalog/answers", summary="Your answers, most recent first")
def list_answers(svc: TenantSemanticEnrichment, system_id: uuid.UUID) -> list[AnswerOut]:
    vocab = svc.vocabulary()
    return [answer(v, vocab) for v in svc.list_decisions(system_id)]


@router.post(FIELD + "/confirm", summary="Confirm the suggested meaning (or pick one)")
def confirm(
    svc: TenantSemanticEnrichment, system_id: uuid.UUID, entity: str, field: str, body: ConfirmAnswerIn
) -> AnswerOut:
    return answer(svc.confirm(system_id, entity, field, body.meaning, body.note), svc.vocabulary())


@router.post(FIELD + "/change", summary="Give another meaning, or 'other' if none fits")
def change(
    svc: TenantSemanticEnrichment, system_id: uuid.UUID, entity: str, field: str, body: ChangeAnswerIn
) -> AnswerOut:
    return answer(svc.change(system_id, entity, field, body.meaning, body.note), svc.vocabulary())


@router.post(FIELD + "/ignore", summary="This field is not part of your catalog")
def ignore(
    svc: TenantSemanticEnrichment,
    system_id: uuid.UUID,
    entity: str,
    field: str,
    body: NoteAnswerIn | None = None,
) -> AnswerOut:
    return answer(svc.ignore(system_id, entity, field, body.note if body else None), svc.vocabulary())


@router.post(FIELD + "/reopen", summary="Reopen one of your answers: the field becomes a question again")
def reopen(
    svc: TenantSemanticEnrichment,
    system_id: uuid.UUID,
    entity: str,
    field: str,
    body: NoteAnswerIn | None = None,
) -> QuestionOut:
    return question(svc.reopen(system_id, entity, field, body.note if body else None), svc.vocabulary())


@router.get(SYSTEM + "/catalog/ai", summary="Can AI help clarify the remaining fields?")
def ai_status(svc: TenantSemanticEnrichment, system_id: uuid.UUID) -> AiAvailabilityOut:
    return ai_availability(svc.ai_status(system_id))


@router.post(SYSTEM + "/catalog/ask-ai", summary="Ask AI to suggest meanings (you still confirm each one)")
def ask_ai_route(svc: TenantSemanticEnrichment, system_id: uuid.UUID) -> AskAiOut:
    return ask_ai(svc.enrich_with_llm(system_id))


@router.get("/catalog/vocabulary", summary="Meanings you can choose from")
def vocabulary(svc: TenantSemanticEnrichment) -> SupplierVocabularyOut:
    return SupplierVocabularyOut.of(svc.vocabulary())
