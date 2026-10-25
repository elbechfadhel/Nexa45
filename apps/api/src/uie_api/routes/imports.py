"""Supplier API — imports de fichiers catalogue (V2c.1) : UPLOAD → ANALYZE → rapport de compatibilité.

TenantContext obligatoire : l'organisation de l'import est celle de la session, jamais une valeur du client ;
un import ou un système d'un autre fournisseur est introuvable (404 identique à un id inexistant). Aucune
écriture vers le système cible.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status
from uie_core.imports import ImportFileError
from uie_core.imports.profile import MAX_FILE_BYTES

from uie_api.deps import TenantCatalogImports, TenantImportExecution, TenantSemanticEnrichment
from uie_api.supplier_imports import (
    AnalyzeImportIn,
    ChooseTargetIn,
    ImportDetailOut,
    ImportOut,
    ImportPreviewOut,
    ImportRunOut,
    ImportTargetOut,
    RunImportIn,
    import_detail,
    import_out,
    import_preview_out,
    import_run_out,
    target_out,
)

router = APIRouter(prefix="/api", tags=["supplier"])
IMPORT = "/imports/{import_id}"


@router.get("/imports", summary="Your catalog files, most recent first")
def list_imports(imports: TenantCatalogImports, runs: TenantImportExecution) -> list[ImportOut]:
    views = imports.list_imports()
    last = runs.last_runs([v.record.id for v in views])
    return [import_out(v, last.get(v.record.id)) for v in views]


@router.post(
    "/imports",
    status_code=status.HTTP_201_CREATED,
    summary="Upload a catalog file (.xlsx or .csv). Nothing is sent to your system.",
)
def upload_import(
    imports: TenantCatalogImports, file: Annotated[UploadFile, File(description=".xlsx or .csv")]
) -> ImportOut:
    data = file.file.read(MAX_FILE_BYTES + 1)
    try:
        return import_out(imports.upload(file.filename or "", data))
    except ImportFileError as exc:
        raise HTTPException(400, str(exc)) from None


@router.get(IMPORT, summary="One of your catalog files and its compatibility report")
def get_import(
    imports: TenantCatalogImports, svc: TenantSemanticEnrichment, import_id: uuid.UUID
) -> ImportDetailOut:
    return import_detail(imports.get(import_id), svc.vocabulary())


@router.delete(
    IMPORT, status_code=status.HTTP_204_NO_CONTENT, response_class=Response, summary="Delete a catalog file"
)
def delete_import(imports: TenantCatalogImports, import_id: uuid.UUID) -> None:
    imports.delete(import_id)


@router.post(IMPORT + "/analyze", summary="Check the file against one of your systems (read-only)")
def analyze_import(
    imports: TenantCatalogImports, svc: TenantSemanticEnrichment, import_id: uuid.UUID, body: AnalyzeImportIn
) -> ImportDetailOut:
    try:
        view = imports.analyze(import_id, body.system_id)
    except ImportFileError as exc:
        raise HTTPException(400, str(exc)) from None
    return import_detail(view, svc.vocabulary())


@router.get(IMPORT + "/targets", summary="Fields of your system's catalog a column can go to")
def list_targets(imports: TenantCatalogImports, import_id: uuid.UUID) -> list[ImportTargetOut]:
    return [target_out(t) for t in imports.targets(import_id)]


@router.post(
    IMPORT + "/columns/{position}/target",
    summary="Choose where this column goes (remembered for your next files)",
)
def choose_target(
    imports: TenantCatalogImports,
    svc: TenantSemanticEnrichment,
    import_id: uuid.UUID,
    position: int,
    body: ChooseTargetIn,
) -> ImportDetailOut:
    return import_detail(imports.choose_target(import_id, position, body.target_id), svc.vocabulary())


@router.post(IMPORT + "/columns/{position}/ignore", summary="Don't import this column")
def ignore_column(
    imports: TenantCatalogImports, svc: TenantSemanticEnrichment, import_id: uuid.UUID, position: int
) -> ImportDetailOut:
    return import_detail(imports.ignore_column(import_id, position), svc.vocabulary())


@router.post(
    IMPORT + "/ignore-unmatched",
    summary="Don't import any of the unmatched columns (remembered for your next files)",
)
def ignore_unmatched(
    imports: TenantCatalogImports, svc: TenantSemanticEnrichment, import_id: uuid.UUID
) -> ImportDetailOut:
    return import_detail(imports.ignore_unmatched(import_id), svc.vocabulary())


@router.post(
    IMPORT + "/ignore-ai-not-relevant",
    summary="Don't import the columns AI marked as probably not needed (remembered for your next files)",
)
def ignore_ai_not_relevant(
    imports: TenantCatalogImports, svc: TenantSemanticEnrichment, import_id: uuid.UUID
) -> ImportDetailOut:
    return import_detail(imports.ignore_ai_not_relevant(import_id), svc.vocabulary())


@router.get(
    IMPORT + "/preview",
    summary="What would be imported from this file (read-only: nothing is changed in your system)",
)
def preview_import(imports: TenantCatalogImports, import_id: uuid.UUID) -> ImportPreviewOut:
    preview = imports.preview(import_id)  # 409 si le fichier n'est pas prêt ; 404 hors de l'organisation
    return import_preview_out(imports.get(import_id), preview)


# --------------------------------------------------------------------------- #
# V2d.2 — import réel dans le système du fournisseur
# --------------------------------------------------------------------------- #
@router.post(
    IMPORT + "/runs",
    summary="Import this file into your system (the same request key never imports twice)",
)
def run_import(
    runs: TenantImportExecution, svc: TenantSemanticEnrichment, import_id: uuid.UUID, body: RunImportIn
) -> ImportRunOut:
    # le corps ne porte que la clé : système, plan et champs viennent du serveur
    return import_run_out(runs.execute(import_id, body.request_key), svc.vocabulary())


@router.get(IMPORT + "/runs", summary="Imports of this file into your system, most recent first")
def list_import_runs(
    runs: TenantImportExecution, svc: TenantSemanticEnrichment, import_id: uuid.UUID
) -> list[ImportRunOut]:
    vocab = svc.vocabulary()
    return [import_run_out(r, vocab) for r in runs.list_runs(import_id)]


@router.get(IMPORT + "/runs/{run_id}", summary="Result of an import into your system")
def get_import_run(
    runs: TenantImportExecution, svc: TenantSemanticEnrichment, import_id: uuid.UUID, run_id: uuid.UUID
) -> ImportRunOut:
    return import_run_out(runs.get_run(import_id, run_id), svc.vocabulary())


@router.post(
    IMPORT + "/runs/{run_id}/resume",
    summary="Continue an interrupted import (rows already done are never imported again)",
)
def resume_import_run(
    runs: TenantImportExecution, svc: TenantSemanticEnrichment, import_id: uuid.UUID, run_id: uuid.UUID
) -> ImportRunOut:
    run = runs.get_run(import_id, run_id)
    return import_run_out(runs.execute(import_id, run.request_key), svc.vocabulary())
