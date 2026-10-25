"""DTO fournisseur — préparation de clients / fournisseurs pour un système cible relié par son API (APERÇU).

Aucune donnée technique : ni snapshot, ni version de contrat, ni empreinte, ni nom de champ technique. La
« référence source » est l'identifiant de l'enregistrement dans SA source, quelle qu'elle soit (jamais
présentée comme l'identifiant d'un système particulier). La requête préparée est rendue en TEXTE (détail
technique dépliable), sans secret : l'aperçu n'utilise aucun identifiant de connexion."""

from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from datetime import datetime
from typing import Literal

from pydantic import Field
from uie_core.delivery import RemoteRecord, TargetSendRun
from uie_core.exports.documents import DocumentExportState
from uie_core.mapping import IssueLevel, RecordStatus
from uie_core.partners import PartnerTargetPreview, PreviewPartner, SendView, TargetOption

from uie_api.supplier import _Out

KIND = {"ORGANIZATION": "company", "PERSON": "person", "UNKNOWN": "unknown"}
ROLE = {"CUSTOMER": "customer", "SUPPLIER": "supplier"}
STATUS = {RecordStatus.READY: "ready", RecordStatus.WARNING: "warning", RecordStatus.BLOCKED: "blocked"}
STATE = {
    DocumentExportState.NEW: "new",
    DocumentExportState.ALREADY_EXPORTED: "already_sent",
    DocumentExportState.CHANGED_SINCE_EXPORT: "changed_since_sent",
}


class PartnerTargetOut(_Out):
    """Système connecté qui peut recevoir vos clients et fournisseurs."""

    system_id: uuid.UUID
    name: str
    system: str  # « Lexware Office »
    target: str  # ce que les partenaires y deviennent (« Lexware Office contacts »)
    connected: bool

    @classmethod
    def of(cls, t: TargetOption) -> PartnerTargetOut:
        return cls(
            system_id=t.instance_id, name=t.name, system=t.system, target=t.target, connected=t.connected
        )


class PreparedIssueOut(_Out):
    level: Literal["blocking", "warning"]
    message: str


class PreparedValueOut(_Out):
    label: str
    value: str


class PreparedPartnerOut(_Out):
    source_reference: str
    name: str
    kind: Literal["company", "person", "unknown"]
    roles: list[Literal["customer", "supplier"]]
    status: Literal["ready", "warning", "blocked"]
    state: Literal["new", "already_sent", "changed_since_sent"]
    changed: list[str]  # libellés des champs modifiés depuis le dernier envoi
    issues: list[PreparedIssueOut]
    source_notes: list[str]  # remarques de lecture de la source
    values: list[PreparedValueOut]
    request: str | None  # requête préparée (JSON lisible) ; absente si le partenaire est bloqué
    action: Literal["create", "update", "none"]  # ce qu'un envoi ferait (none : non envoyable)
    linked: bool  # déjà relié à un enregistrement de la cible
    linked_by_match: bool  # relié par votre rapprochement (pas encore par un envoi)
    outcome_unknown: bool  # un envoi précédent a une issue inconnue : à vérifier d'abord
    sendable: bool

    @classmethod
    def of(cls, r: PreviewPartner) -> PreparedPartnerOut:
        p = r.prepared
        return cls(
            source_reference=p.source_ref,
            name=p.name,
            kind=KIND[p.kind],  # type: ignore[arg-type]
            roles=[ROLE[x] for x in p.roles],  # type: ignore[misc]
            status=STATUS[p.status],  # type: ignore[arg-type]
            state=STATE[r.export_state],  # type: ignore[arg-type]
            changed=r.changed_fields,
            issues=[
                PreparedIssueOut(
                    level="blocking" if i.level == IssueLevel.BLOCKING else "warning", message=i.message
                )
                for i in p.issues
            ],
            source_notes=r.source_issues,
            values=[PreparedValueOut(label=v.label, value=_text(v.value)) for v in p.values],
            request=None
            if p.request is None
            else json.dumps(p.request, indent=2, ensure_ascii=False, sort_keys=True),
            action=("update" if r.linked else "create") if r.sendable else "none",
            linked=r.linked,
            linked_by_match=r.linked_by_match,
            outcome_unknown=r.outcome_unknown,
            sendable=r.sendable,
        )


def _text(value: object) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


class PreparedCountsOut(_Out):
    partners: int
    ready: int
    warning: int
    blocked: int


class PreparedHistoryOut(_Out):
    new: int
    already_sent: int
    changed_since_sent: int


class PartnerTargetPreviewOut(_Out):
    source_name: str
    source_system: str
    target_name: str
    target_system: str
    target: str
    notice: str  # « Preview only — nothing was sent to … »
    sending_available: bool  # False : l'envoi n'existe pas encore
    counts: PreparedCountsOut
    history: PreparedHistoryOut
    truncated: bool
    partners: list[PreparedPartnerOut]

    @classmethod
    def of(cls, p: PartnerTargetPreview) -> PartnerTargetPreviewOut:
        return cls(
            source_name=p.source_name,
            source_system=p.source_system,
            target_name=p.target_name,
            target_system=p.target_system,
            target=p.target,
            notice=f"Nothing is sent to {p.target_system} until you select records and confirm.",
            sending_available=True,
            counts=PreparedCountsOut(
                partners=len(p.records),
                ready=p.count(RecordStatus.READY),
                warning=p.count(RecordStatus.WARNING),
                blocked=p.count(RecordStatus.BLOCKED),
            ),
            history=PreparedHistoryOut(
                new=p.count_state(DocumentExportState.NEW),
                already_sent=p.count_state(DocumentExportState.ALREADY_EXPORTED),
                changed_since_sent=p.count_state(DocumentExportState.CHANGED_SINCE_EXPORT),
            ),
            truncated=p.truncated,
            partners=[PreparedPartnerOut.of(r) for r in p.records],
        )


# --------------------------------------------------------------------------- #
# Envoi (phase 1b) : aucune donnée technique (ni clé de requête, ni version, ni empreinte)
# --------------------------------------------------------------------------- #
ITEM_STATUS = {
    "PENDING": "pending",
    "SENT": "sent",
    "FAILED": "failed",
    "UNKNOWN": "unknown",
    "SKIPPED": "skipped",
    "NEEDS_MATCH": "needs_decision",
}
REASONS = {
    "BLOCKED": "Blocked: it needs a fix in the source system first.",
    "UNCHANGED": "Already sent and unchanged since.",
    "OUTCOME_UNKNOWN": "A previous send has an unknown outcome: check it first.",
    "POSSIBLE_DUPLICATE": "A record that may be the same already exists in the target system: choose what "
    "to do.",
    "MATCH_CHECK_FAILED": "The target system could not be searched for an existing record: nothing was sent.",
    "NOT_ATTEMPTED": "Not sent: the send stopped before this record. It can be sent again.",
    "REJECTED": "Refused by the target system: nothing was written.",
    "CONFLICT": "Changed in the target system since the last send: nothing was written.",
    "NOT_FOUND": "The linked record no longer exists in the target system: nothing was written.",
    "ACCESS": "The target system refused the access (key or permission): nothing was written.",
    "RATE_LIMITED": "The target system asked to slow down: nothing was written. Try again later.",
    "UNAVAILABLE": "The target system could not be reached: nothing was written.",
    "UNKNOWN_OUTCOME": "No reliable answer from the target system: the record may or may not have been "
    "written. Check in the target system, then tell us.",
    "INTERRUPTED": "The send was interrupted while writing this record: check in the target system, then "
    "tell us.",
    "UNEXPECTED": "Unexpected error while writing: check in the target system, then tell us.",
    "CHANGED_BEFORE_RESUME": "Changed in the source before the send was resumed: not sent.",
    "CHANGED_NEEDS_APPROVAL": "Changed since it was sent: sending it again needs a specific approval.",
    "WARNING_NOT_SENT": "It has warnings: only records without warnings are sent from here.",
}


class SendIn(_Out):
    request_key: str = Field(min_length=8, max_length=80)  # généré par le navigateur à l'ouverture
    references: list[str] = Field(min_length=1, max_length=500)
    create_new: list[str] = Field(default_factory=list, max_length=500)  # « create a new one anyway »
    confirm: bool  # confirmation explicite de l'utilisateur


class LinkIn(_Out):
    reference: str
    target_record_id: str = Field(min_length=1, max_length=120)


class ResolveIn(_Out):
    created: bool  # vérifié dans la cible : créé (True) ou non (False)
    target_record_id: str | None = Field(default=None, max_length=120)


class MatchOut(_Out):
    target_record_id: str
    label: str | None
    archived: bool

    @classmethod
    def of(cls, r: RemoteRecord) -> MatchOut:
        return cls(target_record_id=r.id, label=r.label, archived=r.archived)


class SendItemOut(_Out):
    id: uuid.UUID
    source_reference: str
    name: str | None  # nom ACTUEL dans la source (jamais stocké)
    operation: Literal["create", "update", "none"]
    status: Literal["pending", "sent", "failed", "unknown", "skipped", "needs_decision"]
    message: str | None
    fields: list[str]  # champs en cause (libellés du contrat cible), jamais de valeur
    target_record_id: str | None
    resolution: Literal["created", "not_created"] | None


class SendCountsOut(_Out):
    selected: int
    sent: int
    failed: int
    unknown: int
    skipped: int
    needs_decision: int


class SendRunOut(_Out):
    id: uuid.UUID
    status: Literal["running", "completed", "partial", "not_sent"]
    created_at: datetime
    finished_at: datetime | None
    counts: SendCountsOut
    items: list[SendItemOut]

    @classmethod
    def of(cls, v: SendView) -> SendRunOut:
        items = [
            SendItemOut(
                id=i.id,
                source_reference=i.source_ref,
                name=v.names.get(i.source_ref),
                operation=i.operation.lower(),
                status=ITEM_STATUS[i.status],  # type: ignore[arg-type]
                message=REASONS.get(i.reason or "", None),
                fields=[_field_label(f, v.labels) for f in i.error_fields or []],
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
            counts=SendCountsOut(
                selected=len(items),
                sent=n["sent"],
                failed=n["failed"],
                unknown=n["unknown"],
                skipped=n["skipped"],
                needs_decision=n["needs_decision"],
            ),
            items=items,
        )


def _field_label(name: str, labels: dict[str, str]) -> str:
    """Libellé du contrat d'un champ signalé par la cible (indices ignorés), sinon « Other field »."""
    plain = re.sub(r"\[\d+\]", "", name)
    return next(
        (label for tech, label in labels.items() if re.sub(r"\[\d+\]", "", tech) == plain), "Other field"
    )


class SendRunSummaryOut(_Out):
    id: uuid.UUID
    status: Literal["running", "completed", "partial", "not_sent"]
    created_at: datetime
    finished_at: datetime | None

    @classmethod
    def of(cls, r: TargetSendRun) -> SendRunSummaryOut:
        return cls(id=r.id, status=r.status.lower(), created_at=r.created_at, finished_at=r.finished_at)  # type: ignore[arg-type]
