"""DTO de la Supplier API pour la préparation d'un format cible des pièces comptables (ACCOUNTING, phase 4).

Ce que le fournisseur voit : le format et sa version, les réglages du dossier cible (partagés, en lecture
seule ici), les correspondances de comptes et de taxes (statut, suggestion, raison), et pour chaque pièce son
statut cible (prête, avertissement, bloquée, non préparée) avec ses écritures : montant, sens, comptes, clé de
taxe, valeurs telles qu'elles seraient écrites, lignes d'origine. Ni snapshot, ni empreinte, ni identifiant
interne de pièce ; `ref` d'une correspondance = référence opaque de la source, à renvoyer pour décider.
Aucun fichier n'est produit."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from uie_core.accounting import (
    ExportDocument,
    ExportFilePlan,
    MappingOrigin,
    PreparedBooking,
    PreparedDocument,
    TargetOption,
    TargetPreparation,
    TargetSettings,
    ValueMapping,
)
from uie_core.exports import ExportRunRecord
from uie_core.mapping import RecordStatus, TargetIssue

from uie_api.supplier_accounting import KIND_LABEL, STATUS_LABEL, TYPE_LABEL


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountingTargetOut(_Out):
    system: str
    product: str
    name: str
    versions: list[str]  # versions disponibles, la plus récente d'abord


def target_option(t: TargetOption) -> AccountingTargetOut:
    return AccountingTargetOut(system=t.system, product=t.product, name=t.name, versions=t.versions)


class TargetSettingOut(_Out):
    name: str
    label: str
    value: Any
    shared: bool  # réglage du dossier, saisi dans l'export des clients / fournisseurs


class TargetParamOut(_Out):
    name: str
    label: str
    help: str | None
    format_help: str | None
    required: bool
    required_for_file: bool  # exigé seulement pour créer le fichier
    choices: list[str]


class AccountingTargetSettingsOut(_Out):
    shared_with: str | None
    values: list[TargetSettingOut]
    params: list[TargetParamOut]
    missing: list[str]
    missing_for_file: list[str]
    ready: bool


def target_settings(t: TargetSettings) -> AccountingTargetSettingsOut:
    return AccountingTargetSettingsOut(
        shared_with=t.shared_with,
        values=[
            TargetSettingOut(name=v.name, label=v.label, value=v.value, shared=v.inherited) for v in t.values
        ],
        params=[TargetParamOut(**p) for p in t.params],
        missing=t.missing,
        missing_for_file=t.missing_for_file,
        ready=t.ready,
    )


class TargetSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict[str, Any] = {}


ORIGIN_LABEL = {
    MappingOrigin.MANUAL: "Entered by you",
    MappingOrigin.SUGGESTION_ACCEPTED: "Suggestion confirmed by you",
    MappingOrigin.IDENTICAL_CODE: "Same account number",
}


class MappingOut(_Out):
    kind: Literal["account", "tax", "category"]
    ref: str  # référence opaque de la source : à renvoyer pour décider
    source_code: str
    source_name: str | None
    status: Literal["confirmed", "suggested", "missing", "invalid"]
    target_value: str | None
    no_value: bool
    suggestion: str | None
    provenance: str | None  # d'où vient la décision ou la suggestion (libellé)
    reason: str
    documents: int


def mapping(m: ValueMapping) -> MappingOut:
    return MappingOut(
        kind=m.kind.value.lower(),  # type: ignore[arg-type]
        ref=m.source_ref,
        source_code=m.source_code,
        source_name=m.source_name,
        status=m.status.value.lower(),  # type: ignore[arg-type]
        target_value=m.target_value,
        no_value=m.no_value,
        suggestion=m.suggestion,
        provenance=ORIGIN_LABEL.get(m.origin) if m.origin else None,
        reason=m.reason,
        documents=m.documents,
    )


class MappingDecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str | None = Field(default=None, max_length=60)
    no_value: bool = False  # « aucune valeur » (ex. aucune clé de taxe), si le format l'admet
    accept_suggestion: bool = False  # la valeur est la suggestion affichée (provenance conservée)
    source_code: str | None = Field(default=None, max_length=120)  # libellés affichés, conservés
    source_name: str | None = Field(default=None, max_length=200)


class IssueOut(_Out):
    level: Literal["blocking", "warning"]
    message: str


def _issues(items: list[TargetIssue]) -> list[IssueOut]:
    seen, out = set(), []
    for i in items:
        if i.message not in seen:
            seen.add(i.message)
            out.append(IssueOut(level=i.level.value.lower(), message=i.message))  # type: ignore[arg-type]
    return out


class CellOut(_Out):
    field: str  # position du format
    label: str | None
    value: str  # telle qu'elle serait écrite


class SourceLineOut(_Out):
    kind: str
    description: str | None
    account_code: str
    amount: Decimal  # signé (débit > 0, crédit < 0), comme la lecture comptable
    gross_amount: Decimal | None


class BookingOut(_Out):
    index: int
    amount: Decimal  # toujours positif
    partner_side: Literal["debit", "credit"]  # sens du compte du tiers
    net_amount: Decimal
    gross_amount: Decimal
    counter_source: str  # compte de contrepartie dans la source (code et nom)
    tax: str | None
    partner_account: str | None
    counter_account: str | None  # compte cible confirmé
    tax_key: str | None
    tax_no_key: bool
    status: Literal["ready", "warning", "blocked"]
    issues: list[IssueOut]
    cells: list[CellOut]
    source_lines: list[SourceLineOut]


def _status(s: RecordStatus) -> str:
    return s.value.lower()


def booking(b: PreparedBooking) -> BookingOut:
    acc = b.counter_account
    cat = b.counter_category
    source = (
        " ".join(p for p in (acc.code, acc.name) if p)
        if acc
        else f"Category {cat.name or cat.source_ref}"
        if cat
        else ""
    )
    return BookingOut(
        index=b.index,
        amount=b.amount,
        partner_side=b.partner_side.value.lower(),  # type: ignore[arg-type]
        net_amount=b.net_amount,
        gross_amount=b.gross_amount,
        counter_source=source,
        tax=b.tax.name if b.tax else (f"{b.tax_rate.normalize():f} %" if b.tax_rate is not None else None),
        partner_account=b.partner_account,
        counter_account=b.counter_target,
        tax_key=b.tax_target,
        tax_no_key=b.tax_no_value,
        status=_status(b.status),  # type: ignore[arg-type]
        issues=_issues(b.issues),
        cells=[CellOut(field=c.field, label=c.label, value=c.value) for c in b.cells],
        source_lines=[
            SourceLineOut(
                kind=KIND_LABEL.get(sl.kind.value, sl.kind.value),
                description=sl.description,
                account_code=sl.account_code,
                amount=sl.amount,
                gross_amount=sl.gross_amount,
            )
            for sl in b.source_lines
        ],
    )


class PreparedDocumentOut(_Out):
    number: str | None
    reference: str | None
    type: str
    status: str  # statut dans la source (Posted, Draft, Cancelled)
    eligible: bool  # éligibilité SOURCE (lecture comptable), inchangée
    target_status: Literal["ready", "warning", "blocked"] | None  # None : non préparée
    not_prepared_reason: str | None
    partner: str | None
    partner_role: Literal["customer", "supplier"] | None
    accounting_date: date | None
    document_date: date | None
    currency: str | None
    total_amount: Decimal | None
    balance: Decimal | None
    issues: list[IssueOut]  # problèmes de la pièce entière
    bookings: list[BookingOut]


def document(d: PreparedDocument) -> PreparedDocumentOut:
    return PreparedDocumentOut(
        number=d.number,
        reference=d.reference,
        type=TYPE_LABEL.get(d.document_type or "", d.document_type or "Unknown"),
        status=STATUS_LABEL.get(d.source_status or "", d.source_status or "Unknown"),
        eligible=d.eligible,
        target_status=_status(d.target_status) if d.target_status else None,  # type: ignore[arg-type]
        not_prepared_reason=d.not_prepared_reason,
        partner=d.partner_name,
        partner_role=d.partner_role.lower() if d.partner_role else None,  # type: ignore[arg-type]
        accounting_date=d.accounting_date,
        document_date=d.document_date,
        currency=d.currency,
        total_amount=d.total_amount,
        balance=d.balance,
        issues=_issues(d.issues),
        bookings=[booking(b) for b in d.bookings],
    )


class PreparationCountsOut(_Out):
    documents: int
    eligible: int
    ready: int
    warnings: int
    blocked: int
    not_prepared: int


class AccountingPreparationOut(_Out):
    target: str  # format et version (libellé)
    version: str
    period: str
    company: str | None
    settings: list[TargetSettingOut]
    counts: PreparationCountsOut
    mappings: list[MappingOut]
    documents: list[PreparedDocumentOut]
    truncated: bool


def preparation(p: TargetPreparation) -> AccountingPreparationOut:
    return AccountingPreparationOut(
        target=p.target,
        version=p.format_version,
        period=p.period,
        company=p.company_name,
        settings=[
            TargetSettingOut(name=v.name, label=v.label, value=v.value, shared=v.inherited)
            for v in p.settings
        ],
        counts=PreparationCountsOut(
            documents=len(p.documents),
            eligible=p.eligible,
            ready=p.count(RecordStatus.READY),
            warnings=p.count(RecordStatus.WARNING),
            blocked=p.count(RecordStatus.BLOCKED),
            not_prepared=p.not_prepared,
        ),
        mappings=[mapping(m) for m in p.mappings],
        documents=[document(d) for d in p.documents],
        truncated=p.truncated,
    )


# --------------------------------------------------------------------------- #
# Fichier (phase 5) : aperçu exact du fichier, téléchargement, fichiers générés
# --------------------------------------------------------------------------- #
STATE_LABEL = {
    "NEW": "new",
    "ALREADY_EXPORTED": "already_exported",
    "CHANGED_SINCE_EXPORT": "changed_since_export",
}


def _state_message(d: ExportDocument) -> str:
    """Phrase simple sur l'historique d'export d'une pièce (jamais une empreinte)."""
    when = d.previous_exported_at.strftime("%d %b %Y") if d.previous_exported_at else None
    if d.state.value == "ALREADY_EXPORTED":
        text = f"Already exported on {when}"
        return text + (": written again at your request (duplicate)" if d.duplicate else "")
    if d.state.value == "CHANGED_SINCE_EXPORT":
        text = f"Exported on {when}, but its accounting data has changed since"
        return text + (": a new export is allowed" if d.released else "")
    if d.legacy_exported_at:
        return f"In a file generated on {d.legacy_exported_at.strftime('%d %b %Y')}, before export tracking"
    return "Not exported before"


class ExportChangeOut(_Out):
    booking: int
    label: str  # champ du format (libellé)
    change: Literal["changed", "added", "removed"]


class ExportDocumentOut(_Out):
    ref: str  # référence opaque : à renvoyer pour autoriser un nouvel export
    number: str | None
    partner: str | None
    target_status: Literal["ready", "warning"]  # statut cible (préparation) : axe distinct
    state: Literal["new", "already_exported", "changed_since_export"]  # historique d'export
    included: bool  # écrite dans ce fichier
    released: bool
    duplicate: bool
    message: str
    previous_exported_at: datetime | None
    bookings: int
    previous_bookings: int | None
    changes: list[ExportChangeOut]
    legacy_file: str | None  # fichier généré avant le suivi qui contenait cette pièce


class ExportHistoryCountsOut(_Out):
    new: int
    already_exported: int
    changed_since_export: int
    released: int  # modifiées, nouvel export autorisé
    duplicates: int  # déjà exportées, réécrites à la demande
    will_be_exported: int


class LegacyFileOut(_Out):
    id: str
    created_at: datetime
    documents: int  # pièces de la période citées par ce fichier et pas encore suivies


class FileRowOut(_Out):
    row: int  # rang dans le fichier
    document: str | None  # numéro de la pièce
    partner: str | None
    booking: int  # rang de l'écriture dans la pièce
    status: Literal["ready", "warning"]
    state: Literal["new", "already_exported", "changed_since_export"]
    cells: list[CellOut]  # valeurs telles qu'écrites


class ExcludedDocumentOut(_Out):
    number: str | None
    reference: str | None
    type: str
    status: str  # statut dans la source
    target_status: Literal["ready", "warning", "blocked"] | None  # None : non préparée
    state: Literal["already_exported", "changed_since_export"] | None  # exclue par l'historique d'export
    reasons: list[str]


class FileCountsOut(_Out):
    source_documents: int
    exported_documents: int
    excluded_documents: int
    warnings: int  # pièces exportées avec avertissement
    blocked: int
    not_prepared: int
    rows: int
    columns: int


class AccountingFilePreviewOut(_Out):
    target: str
    version: str
    period: str
    company: str | None
    encoding: str
    counts: FileCountsOut
    header: list[CellOut]
    changes_every_time: list[str]  # libellés des valeurs d'en-tête propres à chaque génération
    rows: list[FileRowOut]
    excluded: list[ExcludedDocumentOut]
    content_token: str  # à renvoyer au téléchargement : refusé si le contenu a changé depuis
    size: int
    previous_exports: int
    last_export_at: datetime | None
    history: ExportHistoryCountsOut  # historique d'export par pièce (axe distinct du statut cible)
    documents: list[ExportDocumentOut]
    include_exported: bool
    legacy_files: list[LegacyFileOut]


def file_preview(p: ExportFilePlan) -> AccountingFilePreviewOut:
    labels = {c.field: c.label or c.field for c in p.header}
    return AccountingFilePreviewOut(
        target=p.target,
        version=p.format_version,
        period=p.period,
        company=p.company_name,
        encoding=p.encoding,
        counts=FileCountsOut(
            source_documents=p.source_documents,
            exported_documents=p.exported_documents,
            excluded_documents=p.excluded_documents,
            warnings=p.warning_documents,
            blocked=p.blocked_documents,
            not_prepared=p.not_prepared_documents,
            rows=p.row_count,
            columns=p.column_count,
        ),
        header=[CellOut(field=c.field, label=c.label, value=c.value) for c in p.header],
        changes_every_time=[labels[f] for f in p.volatile_header_fields if f in labels],
        rows=[
            FileRowOut(
                row=r.row,
                document=r.document_number,
                partner=r.partner_name,
                booking=r.booking_index,
                status=_status(r.status),  # type: ignore[arg-type]
                state=STATE_LABEL[r.export_state.value],  # type: ignore[arg-type]
                cells=[CellOut(field=c.field, label=c.label, value=c.value) for c in r.cells],
            )
            for r in p.rows
        ],
        excluded=[
            ExcludedDocumentOut(
                number=e.number,
                reference=e.reference,
                type=TYPE_LABEL.get(e.document_type or "", e.document_type or "Unknown"),
                status=STATUS_LABEL.get(e.source_status or "", e.source_status or "Unknown"),
                target_status=_status(e.target_status) if e.target_status else None,  # type: ignore[arg-type]
                state=STATE_LABEL[e.export_state.value] if e.export_state else None,  # type: ignore[arg-type]
                reasons=e.reasons,
            )
            for e in p.excluded
        ],
        content_token=p.content_digest,
        size=p.size,
        previous_exports=p.previous_exports,
        last_export_at=p.last_export_at,
        history=ExportHistoryCountsOut(
            new=p.new_documents,
            already_exported=p.already_exported_documents,
            changed_since_export=p.changed_documents,
            released=p.released_documents,
            duplicates=p.duplicate_documents,
            will_be_exported=p.exported_documents,
        ),
        documents=[_export_document(d) for d in p.documents],
        include_exported=p.include_exported,
        legacy_files=_legacy_files(p),
    )


class AccountingFileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company_id: str
    period: str
    version: str | None = None
    content_token: str | None = Field(default=None, max_length=64)  # celui de l'aperçu affiché
    include_exported: bool = False  # réécrire aussi les pièces déjà exportées (doublon volontaire)
    confirm_duplicates: bool = False  # confirmation explicite, exigée avec include_exported


class AccountingExportRunOut(_Out):
    id: str
    created_at: datetime
    target: str
    period: str | None
    company: str | None
    rows: int
    exported_documents: int | None
    excluded_documents: int | None
    warnings: int
    encoding: str
    file_size: int
    tracked: bool  # suivi pièce par pièce (False : généré avant le suivi)
    new_documents: int | None
    already_exported_skipped: int | None
    changed_blocked: int | None
    released_documents: int | None
    duplicate_documents: int | None


def accounting_run(r: ExportRunRecord, tracked: bool) -> AccountingExportRunOut:
    d = r.details or {}
    return AccountingExportRunOut(
        id=str(r.id),
        created_at=r.created_at,
        target=r.target_label,
        period=r.period,
        company=d.get("company"),
        rows=r.records,
        exported_documents=d.get("exported_documents"),
        excluded_documents=d.get("excluded_documents"),
        warnings=r.warnings,
        encoding=r.encoding,
        file_size=r.file_size,
        tracked=tracked,
        new_documents=d.get("new_documents"),
        already_exported_skipped=d.get("already_exported_skipped"),
        changed_blocked=d.get("changed_blocked"),
        released_documents=d.get("released_documents"),
        duplicate_documents=d.get("duplicate_documents"),
    )


def _export_document(d: ExportDocument) -> ExportDocumentOut:
    return ExportDocumentOut(
        ref=d.document_ref,
        number=d.number,
        partner=d.partner_name,
        target_status=_status(d.target_status),  # type: ignore[arg-type]
        state=STATE_LABEL[d.state.value],  # type: ignore[arg-type]
        included=d.included,
        released=d.released,
        duplicate=d.duplicate,
        message=_state_message(d),
        previous_exported_at=d.previous_exported_at,
        bookings=d.bookings,
        previous_bookings=d.previous_bookings,
        changes=[
            ExportChangeOut(booking=c.booking, label=c.label or c.field, change=c.change.lower())  # type: ignore[arg-type]
            for c in d.changes
        ],
        legacy_file=str(d.legacy_run_id) if d.legacy_run_id else None,
    )


def _legacy_files(p: ExportFilePlan) -> list[LegacyFileOut]:
    files: dict[str, LegacyFileOut] = {}
    for d in p.documents:
        if d.legacy_run_id and d.legacy_exported_at:
            key = str(d.legacy_run_id)
            n = files[key].documents + 1 if key in files else 1
            files[key] = LegacyFileOut(id=key, created_at=d.legacy_exported_at, documents=n)
    return sorted(files.values(), key=lambda f: f.created_at)


class DocumentReleaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company_id: str
    period: str
    version: str | None = None
    note: str | None = Field(default=None, max_length=300)
