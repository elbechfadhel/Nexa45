"""DTO de la Supplier API pour l'export des partenaires (PARTNER → fichier d'un format cible).

Libellés métier seulement : ni identifiant de snapshot, ni checksum, ni version de binding, ni référence
interne d'un partenaire. Les noms de colonnes affichés sont ceux du format (libellés officiels du Registry).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from uie_core.exports import ExportPreview, ExportRunRecord, ExportTarget
from uie_core.mapping import ProfileParam, RecordStatus, TargetBinding

Status = Literal["ready", "warning", "blocked"]
ROLE = {"CUSTOMER": "customer", "SUPPLIER": "supplier"}


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExportTargetOut(_Out):
    system: str
    product: str
    name: str
    format_version: str | None  # None : format absent du Registry (export indisponible)


def export_target(b: TargetBinding, target: ExportTarget | None) -> ExportTargetOut:
    return ExportTargetOut(
        system=b.target.system,
        product=b.target.product,
        name=b.name,
        format_version=target.resolved.format_version if target else None,
    )


class ProfileParamOut(_Out):
    name: str
    label: str
    help: str | None
    format_help: str | None  # format attendu (phrase simple), affiché comme aide
    type: str
    required: bool
    choices: list[str]
    default: Any = None


def profile_param(p: ProfileParam) -> ProfileParamOut:
    return ProfileParamOut(
        name=p.name,
        label=p.label,
        help=p.help,
        format_help=p.format_help,
        type=p.type.value,
        required=p.required,
        choices=list(p.choices),
        default=p.default,
    )


class ExportProfileOut(_Out):
    target: ExportTargetOut
    params: list[ProfileParamOut]
    values: dict[str, Any]
    complete: bool
    updated_at: datetime | None


class ExportProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict[str, Any] = Field(default_factory=dict)


class ExportFileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    system_id: str


class ColumnValueOut(_Out):
    label: str
    value: str


class PreviewIssueOut(_Out):
    level: Literal["blocking", "warning"]
    message: str


class PreviewRecordOut(_Out):
    name: str
    role: Literal["customer", "supplier"]
    account_number: str | None
    account_new: bool  # numéro qui sera attribué à la génération du fichier
    status: Status
    issues: list[PreviewIssueOut]
    columns: list[ColumnValueOut]  # colonnes renseignées, dans l'ordre du format


class NotExportedOut(_Out):
    name: str | None
    reason: str


class SourceIssueOut(_Out):
    name: str | None
    message: str


class PreviewCountsOut(_Out):
    partners: int
    records: int
    ready: int
    warnings: int
    blocked: int
    not_exported: int
    new_accounts: int


class ExportPreviewOut(_Out):
    target: str
    encoding: str
    counts: PreviewCountsOut
    records: list[PreviewRecordOut]
    not_exported: list[NotExportedOut]
    source_issues: list[SourceIssueOut]
    truncated: bool


def export_preview(p: ExportPreview, target: ExportTarget) -> ExportPreviewOut:
    labels = {f.technical_name: f.label or f.technical_name for f in target.resolved.fields}
    order = {f.technical_name: i for i, f in enumerate(target.resolved.fields)}
    names = {r.source_ref: r.name for r in p.records} | {n.source_ref: n.name for n in p.not_exported}
    records = [
        PreviewRecordOut(
            name=r.name,
            role=ROLE[r.role],  # type: ignore[arg-type]
            account_number=r.account_number,
            account_new=r.account_new,
            status=r.status.value.lower(),  # type: ignore[arg-type]
            issues=[
                PreviewIssueOut(level=i.level.value.lower(), message=i.message)  # type: ignore[arg-type]
                for i in r.issues
            ],
            columns=[
                ColumnValueOut(label=labels[k], value=str(v))
                for k, v in sorted(r.values.items(), key=lambda kv: order.get(kv[0], 0))
            ],
        )
        for r in p.records
    ]
    return ExportPreviewOut(
        target=p.target,
        encoding=p.encoding,
        counts=PreviewCountsOut(
            partners=p.partners_read,
            records=len(p.records),
            ready=p.count(RecordStatus.READY),
            warnings=p.count(RecordStatus.WARNING),
            blocked=p.count(RecordStatus.BLOCKED),
            not_exported=len(p.not_exported),
            new_accounts=sum(1 for r in p.records if r.account_new and r.status != RecordStatus.BLOCKED),
        ),
        records=records,
        not_exported=[NotExportedOut(name=n.name, reason=n.reason) for n in p.not_exported],
        source_issues=[
            SourceIssueOut(name=names.get(ref), message=msg)
            for ref, msgs in p.issues_by_ref.items()
            for msg in msgs
        ],
        truncated=p.truncated,
    )


class ExportRunOut(_Out):
    id: str
    created_at: datetime
    target: str
    system: str | None  # nom du système source (None : système supprimé depuis)
    records: int
    warnings: int
    blocked: int
    new_accounts: int
    encoding: str
    file_size: int


def export_run(r: ExportRunRecord, system: str | None) -> ExportRunOut:
    return ExportRunOut(
        id=str(r.id),
        created_at=r.created_at,
        target=r.target_label,
        system=system,
        records=r.records,
        warnings=r.warnings,
        blocked=r.blocked,
        new_accounts=r.new_accounts,
        encoding=r.encoding,
        file_size=r.file_size,
    )
