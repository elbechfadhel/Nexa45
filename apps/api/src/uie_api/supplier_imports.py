"""Presenters des imports de fichiers catalogue (V2c.1, Supplier API) : DTO PROPRES au fournisseur.

Jamais exposés : référence de stockage, empreinte, entité / champ technique d'une cible, organisation.
Une cible est présentée par son libellé métier (vocabulaire) et un identifiant opaque.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from uie_core.domains import Vocabulary
from uie_core.imports import ImportTarget, ImportView
from uie_core.imports.execution import is_writable
from uie_core.imports.matching import GROUP_LABELS
from uie_core.imports.plan import ImportPreview
from uie_core.imports.records import CatalogImportColumnRecord, CatalogImportRunRecord

ImportStatus = Literal["UPLOADED", "ANALYZED", "READY"]
ColumnState = Literal["MATCHED", "REVIEW", "UNMATCHED", "IGNORED", "EMPTY"]
TYPE_LABEL = {
    "text": "Text",
    "integer": "Whole number",
    "decimal": "Number",
    "boolean": "Yes / No",
    "date": "Date",
    "empty": "Empty",
}


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ImportTargetOut(_Out):
    id: str
    label: str  # « Sales price »
    group: str  # « Product » | « Supplier offer »


class ImportSystemOut(_Out):
    id: uuid.UUID
    name: str  # nom donné par le fournisseur
    kind: str  # « Odoo »


class ImportCountsOut(_Out):
    """Chaque colonne dans UN compteur ; EMPTY (aucune valeur dans le fichier) à part, jamais « ignorée »."""

    matched: int
    review: int
    unmatched: int
    ignored: int
    empty: int


class ImportAnalysisOut(_Out):
    """KPI figés au moment de l'analyse (avant toute décision) : ce que GO45 a compris seul."""

    total: int
    automatic: int  # résolues par le vocabulaire / les règles
    learned: int  # résolues par les choix passés du fournisseur
    learned_ignored: int  # ignorées d'office : jugées inutiles par le fournisseur pour ce système (V2c.2b)
    ai_matched: int  # résolues par l'AI (confiance ≥ 90 %, V2c.3)
    ai_suggested: int  # suggestions de l'AI à confirmer
    ai_not_relevant: int  # « Probably not needed » selon l'AI, à confirmer
    needs_attention: int  # décisions humaines requises (à revoir + sans correspondance)
    empty: int


RunStatus = Literal["RUNNING", "COMPLETED", "COMPLETED_WITH_WARNINGS", "PARTIALLY_COMPLETED", "FAILED"]


class ImportRunSummaryOut(_Out):
    id: uuid.UUID
    status: RunStatus
    started_at: datetime
    finished_at: datetime | None
    rows: int
    created: int
    updated: int
    skipped: int
    failed: int
    pending: int  # lignes non traitées (coupure) : « Continue import »
    with_warnings: int


class ImportOut(_Out):
    id: uuid.UUID
    filename: str
    status: ImportStatus
    rows: int
    columns: int
    system: ImportSystemOut | None
    counts: ImportCountsOut | None  # après analyse seulement (état courant)
    analysis: ImportAnalysisOut | None  # KPI au moment de l'analyse
    created_at: datetime
    analyzed_at: datetime | None
    last_run: ImportRunSummaryOut | None = None  # dernière exécution vers le système (V2d.2)


class ImportColumnOut(_Out):
    position: int
    name: str
    content: str  # type détecté, en libellé métier
    values: int  # cellules remplies
    unexpected_values: int  # valeurs minoritaires qui ne sont pas du type détecté
    status: ColumnState
    target: ImportTargetOut | None
    recognized_as: str | None  # sens reconnu sans cible sur ce système (libellé)
    message: str | None  # pourquoi vérifier cette colonne
    decided_by_you: bool
    source: Literal["auto", "learned", "ai", "you"]  # d'où vient la correspondance
    confidence_pct: int | None  # confiance de l'AI (source « ai » seulement), en %
    probably_not_needed: bool  # l'AI juge la colonne inutile : à confirmer (Ignore), jamais ignorée d'office


class ImportDetailOut(ImportOut):
    items: list[ImportColumnOut]


class AnalyzeImportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    system_id: uuid.UUID


class ChooseTargetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_id: str = Field(min_length=1, max_length=120)


def target_out(t: ImportTarget) -> ImportTargetOut:
    return ImportTargetOut(id=t.id, label=t.label, group=GROUP_LABELS[t.group])


def _target_of(c: CatalogImportColumnRecord, vocab: Vocabulary) -> ImportTargetOut | None:
    if not c.target_id or not c.semantic_key:
        return None
    group = c.target_id.split(":", 1)[0]
    entry = vocab.get(c.semantic_key)
    return ImportTargetOut(
        id=c.target_id, label=entry.label if entry else c.semantic_key, group=GROUP_LABELS.get(group, group)
    )


def _state(c: CatalogImportColumnRecord) -> ColumnState:
    if c.status == "EMPTY":
        return "EMPTY"
    return "IGNORED" if c.ignored else c.status  # type: ignore[return-value]


def _message(
    c: CatalogImportColumnRecord, target: ImportTargetOut | None, recognized: str | None
) -> str | None:
    if c.status == "EMPTY":
        return "No values in this file."
    if c.ignored or c.status == "MATCHED":
        return None
    if c.status == "UNMATCHED":
        return "We couldn't find a matching field in your catalog."
    return {
        "LOW_CONFIDENCE": "Please check this suggestion.",
        "TYPE_MISMATCH": f"The values in this column don't look like “{target.label if target else ''}”.",
        "NOT_IN_SYSTEM": f"Looks like “{recognized}”, but your system has no field for it.",
        "TARGET_TAKEN": "Another column already uses this field.",
        "AI_SUGGESTION": "Please check this suggestion.",
        "AI_NOT_RELEVANT": "Probably not needed.",
    }.get(c.reason or "", "Please check this column.")


def _counts(columns: list[CatalogImportColumnRecord]) -> ImportCountsOut:
    states = [_state(c) for c in columns]
    return ImportCountsOut(
        matched=states.count("MATCHED"),
        review=states.count("REVIEW"),
        unmatched=states.count("UNMATCHED"),
        ignored=states.count("IGNORED"),
        empty=states.count("EMPTY"),
    )


def _analysis(v: ImportView) -> ImportAnalysisOut:
    r = v.record
    return ImportAnalysisOut(
        total=len(v.columns),
        automatic=r.resolved_auto or 0,
        learned=r.resolved_learned or 0,
        learned_ignored=r.resolved_ignored or 0,
        ai_matched=r.resolved_ai or 0,
        ai_suggested=r.suggested_ai or 0,
        ai_not_relevant=r.not_relevant_ai or 0,
        needs_attention=r.needs_attention or 0,
        empty=sum(1 for c in v.columns if c.status == "EMPTY"),
    )


SOURCE = {"AUTO": "auto", "LEARNED": "learned", "AI": "ai", "SUPPLIER": "you"}


def import_out(v: ImportView, last_run: CatalogImportRunRecord | None = None) -> ImportOut:
    r = v.record
    return ImportOut(
        last_run=run_summary_out(last_run) if last_run is not None else None,
        id=r.id,
        filename=r.filename,
        status=v.status,  # type: ignore[arg-type]
        rows=r.row_count,
        columns=r.column_count,
        system=ImportSystemOut(id=r.instance_id, name=v.system_name or "", kind=v.system_kind or "")
        if r.instance_id and v.status != "UPLOADED"
        else None,
        counts=_counts(v.columns) if v.columns else None,
        analysis=_analysis(v) if v.columns and r.needs_attention is not None else None,
        created_at=r.created_at,
        analyzed_at=r.analyzed_at if v.status != "UPLOADED" else None,
    )


def import_detail(v: ImportView, vocab: Vocabulary) -> ImportDetailOut:
    items = []
    for c in v.columns:
        target = _target_of(c, vocab)
        entry = vocab.get(c.semantic_key) if c.semantic_key and not target else None
        recognized = entry.label if entry else None
        items.append(
            ImportColumnOut(
                position=c.position,
                name=c.source_name,
                content=TYPE_LABEL.get(c.detected_type, c.detected_type),
                values=c.filled_values,
                unexpected_values=c.invalid_values,
                status=_state(c),
                target=target,
                recognized_as=recognized,
                message=_message(c, target, recognized),
                decided_by_you=c.decided_by == "SUPPLIER",
                source=SOURCE.get(c.decided_by, "auto"),  # type: ignore[arg-type]
                confidence_pct=round(c.confidence * 100)
                if c.decided_by == "AI" and c.confidence is not None
                else None,
                probably_not_needed=c.reason == "AI_NOT_RELEVANT" and not c.ignored,
            )
        )
    return ImportDetailOut(**import_out(v).model_dump(), items=items)


# --------------------------------------------------------------------------- #
# V2d.1 — preview d'import (lecture seule) : données préparées et problèmes, aucune promesse d'écriture
# --------------------------------------------------------------------------- #
class PreviewDataOut(_Out):
    label: str  # libellé métier (« Sales price »)
    section: str  # « Product » | « Supplier offer »
    rows_present: int  # produits dont la cellule est remplie (valeur valide ou non)
    rows_valid: int  # produits dont la valeur est valide (celle qui serait importée)
    distinct_values: int | None  # références et tags (« 14 different values »)
    processed_during_import: bool  # référence, tags : traitement à l'import (non décrit ici)
    not_imported_yet: (
        bool  # donnée préparée que cette version n'importe pas encore (images, offre fournisseur)
    )


class PreviewIssueOut(_Out):
    message: str
    count: int
    rows: list[int]  # quelques n° de ligne de produit
    blocking: bool  # la ligne n'est pas importable


class PreviewNotImportedOut(_Out):
    ignored: list[str]  # ignorées par le fournisseur
    no_target: list[str]  # aucune cible dans son système
    empty: list[str]  # vides dans ce fichier
    unavailable: list[str]  # cible validée qui n'existe plus dans le système (analyser de nouveau)


class ImportPreviewOut(_Out):
    import_id: uuid.UUID
    filename: str
    system: ImportSystemOut | None
    rows: int
    importable_rows: int
    rows_with_issues: int
    data: list[PreviewDataOut]
    issues: list[PreviewIssueOut]
    not_imported: PreviewNotImportedOut


_ISSUE_TEXT = {
    "MISSING_REQUIRED": "{n} {products} without {label} (not imported)",
    "MISSING_RECOMMENDED": "{n} {products} without {label}",
    "INVALID_NUMBER": "{n} invalid {label} {values}",
    "INVALID_BOOLEAN": "{n} invalid {label} {values}",
    "INVALID_DATE": "{n} invalid {label} {values}",
    "INVALID_CHOICE": "{n} {label} {values} not allowed in your system",
    "INVALID_URL": "{n} {label} {values} are not web addresses",
    "NO_COLUMN_FOR_REQUIRED": "No column provides {label}: no product can be imported",
}


def _issue_message(code: str, label: str, n: int) -> str:
    text = _ISSUE_TEXT.get(code, "{n} problems with {label}")
    return text.format(
        n=n,
        label=label.lower() if code != "NO_COLUMN_FOR_REQUIRED" else label,
        products="product" if n == 1 else "products",
        values="value" if n == 1 else "values",
    )


def import_preview_out(v: ImportView, p: ImportPreview) -> ImportPreviewOut:
    r = v.record
    by_outcome: dict[str, list[str]] = {}
    for f in p.fates:
        by_outcome.setdefault(f.outcome.value, []).append(f.source_name)
    return ImportPreviewOut(
        import_id=r.id,
        filename=r.filename,
        system=ImportSystemOut(id=r.instance_id, name=v.system_name or "", kind=v.system_kind or "")
        if r.instance_id
        else None,
        rows=p.rows,
        importable_rows=p.importable_rows,
        rows_with_issues=p.rows_with_issues,
        data=[
            PreviewDataOut(
                label=d.label,
                section=GROUP_LABELS.get(d.group, d.group),
                rows_present=d.rows_present,
                rows_valid=d.rows_valid,
                distinct_values=d.distinct_values,
                processed_during_import=d.kind in ("label", "labels", "link")
                and is_writable(d.group, d.kind),
                not_imported_yet=not is_writable(d.group, d.kind),
            )
            for d in p.data
        ],
        issues=[
            PreviewIssueOut(
                message=_issue_message(i.code.value, i.label, i.count),
                count=i.count,
                rows=i.rows,
                blocking=i.blocking,
            )
            for i in p.issues
        ],
        not_imported=PreviewNotImportedOut(
            ignored=by_outcome.get("IGNORED", []),
            no_target=by_outcome.get("NO_TARGET", []),
            empty=by_outcome.get("EMPTY", []),
            unavailable=by_outcome.get("TARGET_GONE", []),
        ),
    )


# --------------------------------------------------------------------------- #
# V2d.2 — exécution d'un import (écriture dans le système) : résultat en langage simple, aucun id du système
# --------------------------------------------------------------------------- #
class RunImportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # donnée par le portail à la confirmation : même clé = même exécution (double clic, nouvel essai réseau)
    request_key: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class RunMessageOut(_Out):
    row: int
    message: str


class RunRelationOut(_Out):
    label: str  # « Category », « Tags »
    values: int
    existing: int  # déjà dans le système
    created: int  # créés par cet import
    ambiguous: int  # plusieurs correspondances : non renseignés


class ImportRunOut(ImportRunSummaryOut):
    filename: str
    relations: list[RunRelationOut]
    not_imported_yet: list[str]  # données préparées que cette version n'importe pas encore (images…)
    warnings: list[RunMessageOut]
    failures: list[RunMessageOut]
    error: str | None  # phrase simple ; le détail technique reste côté serveur


_RUN_ERRORS = {
    "CONNECTION": "The connection to your system was lost. Continue the import to finish where it stopped.",
    "RIGHTS": "Your system refused the changes: the connected user is not allowed to change products.",
}
_ITEM_ERRORS = {
    "MISSING_REQUIRED": "Not imported: {label} is missing",
    "AMBIGUOUS_IDENTITY": "Not imported: several products in your system have this {label}",
    "VALIDATION": "Not imported: your system rejected this product",
    "RIGHTS": "Not imported: the connected user is not allowed to change products",
    "UNEXPECTED": "Not imported: unexpected error",
}
_WARNINGS = {
    "MISSING_RECOMMENDED": "Imported without {label}",
    "INVALID_NUMBER": "Invalid {label} ignored",
    "INVALID_BOOLEAN": "Invalid {label} ignored",
    "INVALID_DATE": "Invalid {label} ignored",
    "INVALID_CHOICE": "{label} not allowed in your system, ignored",
    "AMBIGUOUS_RELATION": "{label} not set: several matching values in your system",
    "DUPLICATE_IDENTITY": "Same {label} as an earlier row: the same product was updated",
}


def _label_of(target_id: str | None, vocab: Vocabulary) -> str:
    key = (target_id or "").split(":", 1)[-1]
    entry = vocab.get(key)
    return entry.label.lower() if entry else "a value"


def run_summary_out(run: CatalogImportRunRecord) -> ImportRunSummaryOut:
    return ImportRunSummaryOut(
        id=run.id,
        status=run.status,  # type: ignore[arg-type]
        started_at=run.started_at,
        finished_at=run.finished_at,
        rows=run.rows,
        created=run.created,
        updated=run.updated,
        skipped=run.skipped,
        failed=run.failed,
        pending=run.pending,
        with_warnings=run.with_warnings,
    )


def import_run_out(run: CatalogImportRunRecord, vocab: Vocabulary) -> ImportRunOut:
    warnings, failures = [], []
    for item in run.items:
        if item.status in ("SKIPPED", "FAILED"):
            code = item.error_code or "UNEXPECTED"
            own = next((w for w in item.warnings or [] if w.get("code") == code), {})
            text = _ITEM_ERRORS.get(code, _ITEM_ERRORS["UNEXPECTED"])
            label = _label_of(own.get("target_id"), vocab)
            failures.append(RunMessageOut(row=item.source_row, message=text.format(label=label)))
            continue
        for w in item.warnings or []:
            text = _WARNINGS.get(w.get("code", ""))
            if text:
                label = _label_of(w.get("target_id"), vocab)
                warnings.append(RunMessageOut(row=item.source_row, message=text.format(label=label)))
    relations = []
    for target_id, labels in (run.relations or {}).items():
        statuses = [v.get("status") for v in labels.values()]
        relations.append(
            RunRelationOut(
                label=_label_of(target_id, vocab).capitalize(),
                values=len(statuses),
                existing=statuses.count("FOUND"),
                created=statuses.count("CREATED"),
                ambiguous=statuses.count("AMBIGUOUS"),
            )
        )
    error = None
    if run.error_kind:
        error = _RUN_ERRORS.get(run.error_kind, "The import could not complete.")
    return ImportRunOut(
        **run_summary_out(run).model_dump(),
        filename=run.filename,
        relations=relations,
        not_imported_yet=list(run.not_imported_yet or []),
        warnings=warnings,
        failures=failures,
        error=error,
    )
