"""DTO fournisseur — préparation de produits pour un système cible relié par son API (APERÇU, articles 1a).

Aucune donnée technique : ni snapshot, ni version de contrat, ni empreinte, ni nom de champ technique, ni nom
du système source dans les références. La requête préparée est rendue en TEXTE (détail dépliable), sans secret
: l'aperçu n'utilise aucun identifiant de connexion. Envoi (phase 1b) : mêmes DTO que les partenaires
(`SendIn`, `SendRunOut`… de `uie_api.supplier_partner_target`, sans donnée technique)."""

from __future__ import annotations

import json
import uuid
from typing import Literal

from pydantic import Field
from uie_core.exports.documents import DocumentExportState
from uie_core.mapping import IssueLevel, RecordStatus
from uie_core.products import PreviewProduct, ProductTargetPreview, TargetOption, TaxRateDecision

from uie_api.supplier import _In, _Out

KIND = {"GOODS": "goods", "SERVICE": "service", "UNKNOWN": "unknown"}
STATUS = {RecordStatus.READY: "ready", RecordStatus.WARNING: "warning", RecordStatus.BLOCKED: "blocked"}
STATE = {
    DocumentExportState.NEW: "new",
    DocumentExportState.ALREADY_EXPORTED: "already_sent",
    DocumentExportState.CHANGED_SINCE_EXPORT: "changed_since_sent",
}
BASIS = {"NET": "net", "GROSS": "gross"}


class ProductTargetOut(_Out):
    """Système connecté qui peut recevoir vos produits."""

    system_id: uuid.UUID
    name: str
    system: str  # « Lexware Office »
    target: str  # ce que les produits y deviennent (« Lexware Office articles »)
    connected: bool

    @classmethod
    def of(cls, t: TargetOption) -> ProductTargetOut:
        return cls(
            system_id=t.instance_id, name=t.name, system=t.system, target=t.target, connected=t.connected
        )


class ProductIssueOut(_Out):
    level: Literal["blocking", "warning"]
    message: str


class ProductValueOut(_Out):
    label: str
    value: str


class PreparedProductOut(_Out):
    source_reference: str  # référence de l'unité vendable dans SA source (opaque)
    title: str
    sku: str | None
    kind: Literal["goods", "service", "unknown"]
    status: Literal["ready", "warning", "blocked"]
    state: Literal["new", "already_sent", "changed_since_sent"]
    changed: list[str]
    issues: list[ProductIssueOut]
    source_notes: list[str]
    values: list[ProductValueOut]
    request: str | None  # requête préparée (JSON lisible) ; absente si le produit est bloqué
    action: Literal["create", "update", "none"]  # ce qu'un envoi ferait (none : non envoyable)
    linked: bool  # déjà relié à un article de la cible
    linked_by_match: bool  # relié par votre rapprochement (pas encore par un envoi)
    outcome_unknown: bool  # un envoi précédent a une issue inconnue : à vérifier d'abord
    sendable: bool

    @classmethod
    def of(cls, r: PreviewProduct) -> PreparedProductOut:
        p = r.prepared
        return cls(
            source_reference=p.source_ref,
            title=p.title,
            sku=p.sku,
            kind=KIND[p.kind],  # type: ignore[arg-type]
            status=STATUS[p.status],  # type: ignore[arg-type]
            state=STATE[r.export_state],  # type: ignore[arg-type]
            changed=r.changed_fields,
            issues=[
                ProductIssueOut(
                    level="blocking" if i.level == IssueLevel.BLOCKING else "warning", message=i.message
                )
                for i in p.issues
            ],
            source_notes=r.source_issues,
            values=[ProductValueOut(label=v.label, value=str(v.value)) for v in p.values],
            request=None
            if p.request is None
            else json.dumps(p.request, indent=2, ensure_ascii=False, sort_keys=True),
            action=("update" if r.linked else "create") if r.sendable else "none",
            linked=r.linked,
            linked_by_match=r.linked_by_match,
            outcome_unknown=r.outcome_unknown,
            sendable=r.sendable,
        )


class TaxRateOut(_Out):
    """Taxe de vente de la source et le taux choisi pour la cible (null : à choisir)."""

    tax_reference: str
    label: str
    products: int
    rate: str | None

    @classmethod
    def of(cls, t: TaxRateDecision) -> TaxRateOut:
        return cls(tax_reference=t.ref, label=t.label or t.ref, products=t.products, rate=t.rate)


class ProductCountsOut(_Out):
    products: int  # unités vendables préparées (variantes comprises)
    ready: int
    warning: int
    blocked: int
    archived: int  # archivées dans la source : jamais préparées


class ProductHistoryOut(_Out):
    new: int
    already_sent: int
    changed_since_sent: int


class ProductTargetPreviewOut(_Out):
    source_name: str
    source_system: str
    target_name: str
    target_system: str
    target: str
    notice: str
    sending_available: bool
    price_basis: Literal["net", "gross"] | None
    price_bases: list[Literal["net", "gross"]]
    tax_rates: list[str]  # taux admis par le système cible
    taxes: list[TaxRateOut]
    counts: ProductCountsOut
    history: ProductHistoryOut
    truncated: bool
    products: list[PreparedProductOut]

    @classmethod
    def of(cls, p: ProductTargetPreview) -> ProductTargetPreviewOut:
        return cls(
            source_name=p.source_name,
            source_system=p.source_system,
            target_name=p.target_name,
            target_system=p.target_system,
            target=p.target,
            notice=f"Nothing is sent to {p.target_system} until you select products and confirm.",
            sending_available=True,
            price_basis=BASIS[p.price_basis] if p.price_basis else None,  # type: ignore[arg-type]
            price_bases=[BASIS[b] for b in p.bases],  # type: ignore[misc]
            tax_rates=p.rates,
            taxes=[TaxRateOut.of(t) for t in p.taxes],
            counts=ProductCountsOut(
                products=len(p.records),
                ready=p.count(RecordStatus.READY),
                warning=p.count(RecordStatus.WARNING),
                blocked=p.count(RecordStatus.BLOCKED),
                archived=p.archived,
            ),
            history=ProductHistoryOut(
                new=p.count_state(DocumentExportState.NEW),
                already_sent=p.count_state(DocumentExportState.ALREADY_EXPORTED),
                changed_since_sent=p.count_state(DocumentExportState.CHANGED_SINCE_EXPORT),
            ),
            truncated=p.truncated,
            products=[PreparedProductOut.of(r) for r in p.records],
        )


class PriceBasisIn(_In):
    price_basis: Literal["net", "gross"]


class TaxRateIn(_In):
    rate: str = Field(min_length=1, max_length=10, description="One of the tax rates of the target system")
    label: str | None = Field(default=None, max_length=200, description="Name of the tax in your system")
