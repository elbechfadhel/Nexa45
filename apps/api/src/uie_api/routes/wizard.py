"""Routes fournisseur de l'assistant de migration (AI Migration Wizard) : API → MigrationWizardService → Core.

Sans état : le client renvoie ses réponses à chaque étape. Aucun envoi vers un système ; l'IA n'est
appelée que par le Core, côté serveur (clé jamais exposée)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import ValidationError
from uie_core.imports import ImportFileError
from uie_core.imports.profile import MAX_FILE_BYTES
from uie_core.wizard import (
    FillError,
    MappingError,
    ValueChoiceError,
    WizardDataTypeUnknown,
    WizardInputError,
    excel_filename,
    render_excel,
)

from uie_api.deps import TenantMigrationWizard, TenantWizardTemplates
from uie_api.supplier_wizard import (
    FileMappingOut,
    MissingInfoOut,
    PreparedFileOut,
    QuickStartOut,
    QuickTargetChoiceOut,
    TargetProposalOut,
    WizardAnswersIn,
    WizardEarlyIn,
    WizardExcelIn,
    WizardFileIn,
    WizardQuickIn,
    WizardStepOut,
    WizardTemplateIn,
    WizardTemplateOut,
    WizardTemplateRenameIn,
    mapping_out,
    missing_out,
    prepared_out,
    proposal_out,
    quick_out,
    step_out,
    template_out,
    template_steps,
)

router = APIRouter(prefix="/api/wizard", tags=["supplier-wizard"])

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.post("/next", summary="Question suivante d'après les réponses données (ou fin)")
def next_step(svc: TenantMigrationWizard, body: WizardAnswersIn) -> WizardStepOut:
    return step_out(svc.next(body.core(), body.mode))


@router.post("/proposal", summary="Structure cible proposée, confrontée au Registry")
def proposal(svc: TenantMigrationWizard, body: WizardAnswersIn) -> TargetProposalOut:
    return proposal_out(svc.propose(body.core()))


@router.post(
    "/excel",
    summary="Fichier Excel d'exemple (statuts vérifiés recalculés par le Registry)",
    response_class=Response,
    responses={200: {"content": {XLSX: {}}}},
)
def excel(svc: TenantMigrationWizard, body: WizardExcelIn) -> Response:
    p = svc.verify(body.core(), body.core_proposal())
    return Response(
        content=render_excel(p),
        media_type=XLSX,
        headers={"Content-Disposition": f'attachment; filename="{excel_filename(p)}"'},
    )


# --------------------------------------------------------------------------- #
# Fichier du client : jamais stocké ; envoyé avec la structure et les réponses (JSON « payload »)
# --------------------------------------------------------------------------- #
Upload = Annotated[UploadFile, File(description="Your data file (.xlsx or .csv)")]
Payload = Annotated[str, Form(description="JSON : answers, proposal, decisions, fills, previous")]


def _payload(raw: str) -> WizardFileIn:
    try:
        return WizardFileIn.model_validate_json(raw)
    except ValidationError:
        raise HTTPException(422, "Invalid wizard data") from None


def _read(file: UploadFile) -> tuple[bytes, str]:
    return file.file.read(MAX_FILE_BYTES + 1), file.filename or ""


@router.get(
    "/targets",
    summary="Cibles du démarrage par le fichier : vos systèmes, puis les versions du Registry, puis les "
    "systèmes connus",
)
def quick_targets(svc: TenantMigrationWizard) -> list[QuickTargetChoiceOut]:
    return [QuickTargetChoiceOut.model_validate(t.model_dump()) for t in svc.quick_targets()]


@router.post(
    "/quick-start",
    summary="Démarrage par le fichier : cible, type de données, structure du Registry, correspondances, "
    "au plus 3 questions bloquantes",
)
def quick_start(svc: TenantMigrationWizard, file: Upload, payload: Payload) -> QuickStartOut:
    try:
        body = WizardQuickIn.model_validate_json(payload)
    except ValidationError:
        raise HTTPException(422, "Invalid wizard data") from None
    data, name = _read(file)
    try:
        result = svc.quick_start(body.target, data, name, body.mode, body.data_type)
    except ImportFileError as exc:
        raise HTTPException(400, str(exc)) from None
    except WizardDataTypeUnknown as exc:
        # ne pas s'arrêter : l'utilisateur choisit le type de données parmi ceux que la cible reçoit
        raise HTTPException(
            422, {"code": "data_type_unknown", "message": str(exc), "data_types": exc.options}
        ) from None
    except (MappingError, ValueChoiceError) as exc:
        raise HTTPException(422, str(exc)) from None
    return quick_out(result)


@router.post("/file/mapping", summary="Colonnes du fichier → champs de la structure (nom exact, puis IA)")
def file_mapping(svc: TenantMigrationWizard, file: Upload, payload: Payload) -> FileMappingOut:
    body = _payload(payload)
    data, name = _read(file)
    try:
        p, mapping = svc.map_file(body.core(), body.core_proposal(), data, name, body.core_previous())
    except ImportFileError as exc:
        raise HTTPException(400, str(exc)) from None
    return mapping_out(mapping, p.verification.value)


def _prepared(svc: TenantMigrationWizard, file: UploadFile, payload: str, generate: bool = False):  # noqa: ANN202
    body = _payload(payload)
    data, name = _read(file)
    try:
        proposal = svc.verify(body.core(), body.core_proposal())
        summary, content, filename = svc.prepare_file(
            body.core(),
            proposal,
            data,
            name,
            body.core_decisions(),
            body.core_fills(),
            body.core_choices(),
            confirmed=body.confirmed,
            generate=generate,
        )
    except ImportFileError as exc:
        raise HTTPException(400, str(exc)) from None
    except (MappingError, FillError, ValueChoiceError, WizardInputError) as exc:
        raise HTTPException(422, str(exc)) from None
    return proposal, summary, content, filename


@router.post(
    "/file/preview", summary="Aperçu du fichier préparé (valeurs du client copiées, aucune conversion)"
)
def file_preview(svc: TenantMigrationWizard, file: Upload, payload: Payload) -> PreparedFileOut:
    proposal, summary, _, _ = _prepared(svc, file, payload)
    return prepared_out(summary, proposal)


@router.post(
    "/file/prepared",
    summary="Fichier préparé pour la cible (jamais envoyé à un système)",
    response_class=Response,
    responses={200: {"content": {XLSX: {}}}},
)
def file_prepared(svc: TenantMigrationWizard, file: Upload, payload: Payload) -> Response:
    # téléchargement = génération : pour un format de fichier (DATEV…), numéros de compte réservés et pièces
    # journalisées (un second téléchargement n'écrit jamais deux fois la même pièce)
    _, _, content, filename = _prepared(svc, file, payload, generate=True)
    return Response(
        content=content,
        media_type=XLSX if filename.endswith(".xlsx") else "text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/early",
    summary="Questions prévisibles posées avant le fichier (champs obligatoires indépendants des lignes)",
)
def early(svc: TenantMigrationWizard, body: WizardEarlyIn) -> MissingInfoOut:
    try:
        info = svc.early_info(body.core(), body.core_proposal(), body.mode, body.core_fills())
    except FillError as exc:
        raise HTTPException(422, str(exc)) from None
    return missing_out(info)


@router.post(
    "/file/missing",
    summary="Questions pour les champs obligatoires sans valeur (règles et Registry d'abord, puis IA)",
)
def file_missing(svc: TenantMigrationWizard, file: Upload, payload: Payload) -> MissingInfoOut:
    body = _payload(payload)
    data, name = _read(file)
    try:
        info = svc.missing_info(
            body.core(),
            body.core_proposal(),
            data,
            name,
            body.core_decisions(),
            body.mode,
            body.core_fills(),
            body.core_choices(),
        )
    except ImportFileError as exc:
        raise HTTPException(400, str(exc)) from None
    except (MappingError, ValueChoiceError) as exc:
        raise HTTPException(422, str(exc)) from None
    return missing_out(info)


# --------------------------------------------------------------------------- #
# Modèles de questionnaire (« templates ») de l'utilisateur : questions et réponses seulement
# --------------------------------------------------------------------------- #
@router.get("/templates", summary="Modèles de questionnaire de l'utilisateur (les plus récents d'abord)")
def list_templates(svc: TenantWizardTemplates) -> list[WizardTemplateOut]:
    return [template_out(t) for t in svc.list()]


@router.post("/templates", status_code=201, summary="Sauvegarder les réponses du questionnaire comme modèle")
def create_template(svc: TenantWizardTemplates, body: WizardTemplateIn) -> WizardTemplateOut:
    return template_out(svc.create(body.name, template_steps(body)))


@router.patch("/templates/{template_id}", summary="Renommer un modèle")
def rename_template(
    svc: TenantWizardTemplates, template_id: UUID, body: WizardTemplateRenameIn
) -> WizardTemplateOut:
    return template_out(svc.rename(template_id, body.name))


@router.delete("/templates/{template_id}", status_code=204, summary="Supprimer un modèle")
def delete_template(svc: TenantWizardTemplates, template_id: UUID) -> Response:
    svc.delete(template_id)
    return Response(status_code=204)
