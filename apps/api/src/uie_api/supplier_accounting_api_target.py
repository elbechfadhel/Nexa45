"""DTO fournisseur — pièces comptables préparées pour un système cible relié par son API (APERÇU).

Aucune donnée technique : ni snapshot, ni empreinte, ni nom de champ technique, ni identifiant de connexion.
Les options (ex. catégories) sont lues dans le système cible à la demande, jamais enregistrées en entier."""

from __future__ import annotations

import json
import uuid
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field
from uie_core.accounting.api_target import (
    AccountingApiPreview,
    AccountNeed,
    ApiSendResult,
    PreviewApiDocument,
    TargetOptionOut,
)
from uie_core.delivery import TargetChoice
from uie_core.exports.documents import DocumentExportState
from uie_core.mapping import IssueLevel, RecordStatus

from uie_api.supplier import _Out
from uie_api.supplier_partner_target import ITEM_STATUS, REASONS

STATUS = {RecordStatus.READY: "ready", RecordStatus.WARNING: "warning", RecordStatus.BLOCKED: "blocked"}
STATE = {
    DocumentExportState.NEW: "new",
    DocumentExportState.ALREADY_EXPORTED: "already_sent",
    DocumentExportState.CHANGED_SINCE_EXPORT: "changed_since_sent",
}


class AccountingApiTargetOut(_Out):
    id: uuid.UUID
    name: str
    system: str
    target: str
    connected: bool

    @classmethod
    def of(cls, t: TargetOptionOut) -> AccountingApiTargetOut:
        return cls(id=t.instance_id, name=t.name, system=t.system, target=t.target, connected=t.connected)


class TargetChoiceOut(_Out):
    id: str
    label: str
    group: str | None
    kind: str | None

    @classmethod
    def of(cls, c: TargetChoice) -> TargetChoiceOut:
        return cls(id=c.id, label=c.label, group=c.group, kind=c.kind)


class AccountChoiceIn(_Out):
    choice_id: str = Field(min_length=1, max_length=60)
    code: str | None = Field(default=None, max_length=120)
    name: str | None = Field(default=None, max_length=200)


class AccountNeedOut(_Out):
    account_reference: str
    code: str
    name: str | None
    expected_kind: Literal["customer", "supplier"]
    choice_id: str | None
    choice_label: str | None
    problem: str | None

    @classmethod
    def of(cls, a: AccountNeed) -> AccountNeedOut:
        return cls(
            account_reference=a.account_ref,
            code=a.code,
            name=a.name,
            expected_kind="customer" if a.role == "CUSTOMER" else "supplier",
            choice_id=a.choice_id,
            choice_label=a.choice_label,
            problem=a.problem,
        )


class ApiIssueOut(_Out):
    level: Literal["blocking", "warning"]
    message: str


def _issues(items: list) -> list[ApiIssueOut]:  # noqa: ANN001
    return [
        ApiIssueOut(level="blocking" if i.level == IssueLevel.BLOCKING else "warning", message=i.message)
        for i in items
    ]


class ApiItemOut(_Out):
    account_code: str
    account_name: str | None
    tax: str | None
    amount: Decimal  # TTC, positif
    tax_amount: Decimal
    tax_rate: Decimal | None
    category: str | None  # libellé de la valeur choisie dans la cible (None : non choisie)
    issues: list[ApiIssueOut]


class ApiDocumentOut(_Out):
    source_reference: str
    number: str | None
    document_type: str | None
    partner: str | None
    contact_linked: bool  # le partenaire a déjà son contact dans la cible
    document_date: date | None
    due_date: date | None
    currency: str | None
    total_amount: Decimal | None  # TTC
    tax_amount: Decimal | None
    status: Literal["ready", "warning", "blocked"]
    state: Literal["new", "already_sent", "changed_since_sent"]
    changed: list[str]
    issues: list[ApiIssueOut]
    items: list[ApiItemOut]
    request: str | None  # requête préparée (JSON lisible) ; absente si la pièce est bloquée
    sendable: bool  # seulement une pièce prête, sans avertissement, jamais envoyée
    not_sendable_reason: str | None  # phrase simple quand elle n'est pas envoyable
    outcome_unknown: bool  # un envoi précédent a une issue inconnue : à vérifier d'abord

    @classmethod
    def of(cls, r: PreviewApiDocument) -> ApiDocumentOut:
        p = r.prepared
        return cls(
            source_reference=p.source_ref,
            number=p.number,
            document_type=p.document_type,
            partner=p.partner_name,
            contact_linked=p.contact_linked,
            document_date=p.document_date,
            due_date=p.due_date,
            currency=p.currency,
            total_amount=p.total_amount,
            tax_amount=p.tax_amount,
            status=STATUS[p.status],  # type: ignore[arg-type]
            state=STATE[r.export_state],  # type: ignore[arg-type]
            changed=r.changed_fields,
            issues=_issues(p.issues),
            items=[
                ApiItemOut(
                    account_code=i.account_code,
                    account_name=i.account_name,
                    tax=i.tax_name,
                    amount=i.gross_amount,
                    tax_amount=i.tax_amount,
                    tax_rate=i.tax_rate,
                    category=i.choice_label,
                    issues=_issues(i.issues),
                )
                for i in p.items
            ],
            request=None if p.request is None else json.dumps(p.request, indent=2, ensure_ascii=False),
            sendable=r.sendable,
            not_sendable_reason=REASONS.get(r.skip_reason or ""),
            outcome_unknown=r.outcome_unknown,
        )


class ApiCountsOut(_Out):
    documents: int
    ready: int
    warning: int
    blocked: int


class AccountingApiPreviewOut(_Out):
    source_name: str
    target_name: str
    target_system: str
    target: str
    notice: str
    company: str | None
    period: str
    options_checked: bool
    counts: ApiCountsOut
    accounts: list[AccountNeedOut]
    documents: list[ApiDocumentOut]
    truncated: bool

    @classmethod
    def of(cls, p: AccountingApiPreview) -> AccountingApiPreviewOut:
        return cls(
            source_name=p.source_name,
            target_name=p.target_name,
            target_system=p.target_system,
            target=p.target,
            notice=f"Preview only — nothing is sent to {p.target_system} from this page.",
            company=p.company,
            period=p.period,
            options_checked=p.options_checked,
            counts=ApiCountsOut(
                documents=len(p.records),
                ready=p.count(RecordStatus.READY),
                warning=p.count(RecordStatus.WARNING),
                blocked=p.count(RecordStatus.BLOCKED),
            ),
            accounts=[AccountNeedOut.of(a) for a in p.accounts],
            documents=[ApiDocumentOut.of(r) for r in p.records],
            truncated=p.truncated,
        )


# --------------------------------------------------------------------------- #
# Envoi (phase 2c, étape 2) : aucune donnée technique (ni clé d'envoi, ni version, ni empreinte)
# --------------------------------------------------------------------------- #
class ApiSendIn(_Out):
    request_key: str = Field(min_length=8, max_length=80)  # généré par le navigateur à l'ouverture
    company_id: str = Field(min_length=1, max_length=120)
    period: str = Field(pattern=r"^\d{4}-\d{2}$")
    references: list[str] = Field(min_length=1, max_length=200)
    confirm: bool  # confirmation explicite de l'utilisateur


class ApiResolveIn(_Out):
    company_id: str = Field(min_length=1, max_length=120)
    period: str = Field(pattern=r"^\d{4}-\d{2}$")
    created: bool  # vérifié dans la cible : créée (True) ou non (False)


class ApiSendItemOut(_Out):
    id: uuid.UUID
    source_reference: str
    number: str | None
    status: Literal["pending", "sent", "failed", "unknown", "skipped", "needs_decision"]
    message: str | None
    target_record_id: str | None
    resolution: Literal["created", "not_created"] | None


class ApiSendCountsOut(_Out):
    selected: int
    sent: int
    failed: int
    unknown: int
    skipped: int
    needs_decision: int


class ApiSendRunOut(_Out):
    id: uuid.UUID
    status: Literal["running", "completed", "partial", "not_sent"]
    created_at: datetime
    finished_at: datetime | None
    counts: ApiSendCountsOut
    items: list[ApiSendItemOut]

    @classmethod
    def of(cls, v: ApiSendResult) -> ApiSendRunOut:
        items = [
            ApiSendItemOut(
                id=i.id,
                source_reference=i.source_ref,
                number=v.numbers.get(i.source_ref),
                status=ITEM_STATUS[i.status],  # type: ignore[arg-type]
                message=REASONS.get(i.reason or ""),
                target_record_id=i.target_id,
                resolution=i.resolution.lower() if i.resolution else None,  # type: ignore[arg-type]
            )
            for i in v.items
        ]
        n = Counter(i.status for i in items)
        return cls(
            id=v.run.id,
            status=v.run.status.lower(),
            created_at=v.run.created_at,
            finished_at=v.run.finished_at,
            counts=ApiSendCountsOut(
                selected=len(items),
                sent=n["sent"],
                failed=n["failed"],
                unknown=n["unknown"],
                skipped=n["skipped"],
                needs_decision=n["needs_decision"],
            ),
            items=items,
        )


class ApiSendSummaryOut(_Out):
    id: uuid.UUID
    status: Literal["running", "completed", "partial", "not_sent"]
    created_at: datetime
    finished_at: datetime | None
