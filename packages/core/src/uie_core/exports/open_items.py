"""Registre des factures OUVERTES d'un dossier (source FICHIER) : ce qu'il faut savoir d'une facture exportée
pour rapprocher un paiement d'un relevé bancaire (Offene Posten), et RIEN de plus.

Une ligne par facture écrite dans un fichier (numéro, partenaire, rôle, montant brut signé, devise, date), un
règlement par paiement rapproché et exporté. Le montant ouvert = montant − règlements. Le nom et l'IBAN du
partenaire viennent de la liste des partenaires exportée (attribution du compte) : jamais recopiés ici.
Voir docs/PROTECTION_DES_DONNEES_REGISTRE_DES_FACTURES.md. Aucun nom de système ni de format."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from uie_cdm import AccountingDocument

from uie_core.exports.records import (
    ExportAccountAssignmentRecord,
    ExportDocumentRecord,
    ExportOpenItemRecord,
    ExportOpenItemSettlementRecord,
    ExportProfileRecord,
)

ZERO = Decimal("0")
# références des pièces d'une source FICHIER (tableau de source) : facture de vente « file:<n°> », d'achat
# « file:purchase:<fournisseur>:<n°> », écritures de caisse et mouvements bancaires (jamais des factures)
FILE_PREFIX, PURCHASE_PREFIX, CASH_PREFIX, BANK_PREFIX = "file:", "purchase:", "cash:", "bank:"


@dataclass(frozen=True)
class OpenItem:
    id: uuid.UUID
    document_ref: str
    number: str
    role: str  # CUSTOMER | SUPPLIER
    partner_ref: str
    partner_name: str | None  # nom écrit sur la facture, sinon celui de la liste exportée
    partner_iban: str | None  # IBAN de la liste des partenaires exportée
    account: str | None  # compte du partenaire attribué par l'export de la liste
    amount: Decimal  # brut signé (avoir < 0)
    open_amount: Decimal  # montant − règlements exportés
    currency: str
    document_date: date
    # mouvements bancaires qui l'ont réglée (références opaques) → date de leur export (AAAA-MM-JJ)
    settled_by: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Settlement:
    open_item_id: uuid.UUID
    payment_ref: str
    amount: Decimal  # positif : montant réglé de la facture
    settled_on: date


class OpenItemRegister:
    """Registre d'UN dossier (organisation + mandant) pour UN format cible."""

    def __init__(
        self, session: Session, organization_id: uuid.UUID, client_key: str | None, system: str, product: str
    ) -> None:
        self.s = session
        self.org, self.client, self.system, self.product = organization_id, client_key, system, product

    def _scope(self) -> list:  # type: ignore[type-arg]
        return [
            ExportOpenItemRecord.organization_id == self.org,
            ExportOpenItemRecord.client_key.is_(None)
            if self.client is None
            else ExportOpenItemRecord.client_key == self.client,
            ExportOpenItemRecord.target_system == self.system,
            ExportOpenItemRecord.target_product == self.product,
        ]

    def exported_before_register(self) -> set[tuple[str, str]]:
        """Factures de ce dossier écrites dans un fichier (journal des pièces) mais ABSENTES du registre :
        exportées avant qu'il existe. (rôle, numéro lisible) ; jamais une écriture de caisse ou de banque."""
        known = set(self.s.scalars(select(ExportOpenItemRecord.document_ref).where(*self._scope())))
        rows = self.s.execute(
            select(ExportDocumentRecord.source_ref, ExportDocumentRecord.source_number).where(
                ExportDocumentRecord.organization_id == self.org,
                ExportDocumentRecord.client_key.is_(None)
                if self.client is None
                else ExportDocumentRecord.client_key == self.client,
                ExportDocumentRecord.target_system == self.system,
                ExportDocumentRecord.target_product == self.product,
                ExportDocumentRecord.instance_id.is_(None),
                ExportDocumentRecord.source_ref.like(f"{FILE_PREFIX}%"),
            )
        )
        out: set[tuple[str, str]] = set()
        for ref, number in rows:
            kind = ref[len(FILE_PREFIX) :]
            if ref in known or not number or kind.startswith((CASH_PREFIX, BANK_PREFIX)):
                continue
            out.add(("SUPPLIER" if kind.startswith(PURCHASE_PREFIX) else "CUSTOMER", number))
        return out

    def record_invoices(self, documents: list[AccountingDocument], run_id: uuid.UUID | None) -> int:
        """Factures écrites dans un fichier : une ligne chacune, une seule fois (présente : inchangée)."""
        known = set(self.s.scalars(select(ExportOpenItemRecord.document_ref).where(*self._scope())))
        added = 0
        for d in documents:
            if (
                d.partner is None
                or d.document_type.is_bank
                or d.document_type.is_cash
                or d.source_ref in known
            ):
                continue
            sign = Decimal(-1) if d.document_type.is_credit_note else Decimal(1)
            self.s.add(
                ExportOpenItemRecord(
                    organization_id=self.org,
                    client_key=self.client,
                    target_system=self.system,
                    target_product=self.product,
                    document_ref=d.source_ref,
                    number=d.number or "",
                    role=d.partner.role.value,
                    partner_ref=d.partner.source_ref,
                    partner_name=d.partner.name,
                    amount=sign * d.total_amount,
                    currency=d.currency,
                    document_date=d.document_date or d.accounting_date,
                    export_run_id=run_id,
                )
            )
            known.add(d.source_ref)
            added += 1
        self.s.flush()
        return added

    def items(self) -> list[OpenItem]:
        """Factures du registre avec leur montant ouvert, le nom, l'IBAN et le compte du partenaire."""
        settled = (
            select(
                ExportOpenItemSettlementRecord.open_item_id,
                func.coalesce(func.sum(ExportOpenItemSettlementRecord.amount), 0).label("paid"),
            )
            .group_by(ExportOpenItemSettlementRecord.open_item_id)
            .subquery()
        )
        rows = self.s.execute(
            select(ExportOpenItemRecord, func.coalesce(settled.c.paid, 0))
            .outerjoin(settled, settled.c.open_item_id == ExportOpenItemRecord.id)
            .where(*self._scope())
            .order_by(ExportOpenItemRecord.document_date, ExportOpenItemRecord.number)
        ).all()
        partners = self._partners({r.partner_ref for r, _ in rows})
        payments: dict[uuid.UUID, dict[str, str]] = {}
        for item_id, ref, at in self.s.execute(
            select(
                ExportOpenItemSettlementRecord.open_item_id,
                ExportOpenItemSettlementRecord.payment_ref,
                ExportOpenItemSettlementRecord.created_at,
            ).where(ExportOpenItemSettlementRecord.open_item_id.in_([r.id for r, _ in rows]))
        ):
            payments.setdefault(item_id, {})[ref] = at.date().isoformat() if at else ""
        out = []
        for r, paid in rows:
            name, iban, account = partners.get((r.partner_ref, r.role), (None, None, None))
            out.append(
                OpenItem(
                    id=r.id,
                    document_ref=r.document_ref,
                    number=r.number,
                    role=r.role,
                    partner_ref=r.partner_ref,
                    partner_name=r.partner_name or name,
                    partner_iban=iban,
                    account=account,
                    amount=r.amount,
                    open_amount=r.amount - Decimal(paid),
                    currency=r.currency,
                    document_date=r.document_date,
                    settled_by=payments.get(r.id, {}),
                )
            )
        return out

    def _partners(self, refs: set[str]) -> dict[tuple[str, str], tuple[str | None, str | None, str]]:
        """(partenaire, rôle) → (nom, IBAN, compte) retenus à l'export de la liste des partenaires."""
        if not refs:
            return {}
        profiles = select(ExportProfileRecord.id).where(
            ExportProfileRecord.organization_id == self.org,
            ExportProfileRecord.client_key.is_(None)
            if self.client is None
            else ExportProfileRecord.client_key == self.client,
        )
        rows = self.s.scalars(
            select(ExportAccountAssignmentRecord).where(
                ExportAccountAssignmentRecord.profile_id.in_(profiles),
                ExportAccountAssignmentRecord.instance_id.is_(None),
                ExportAccountAssignmentRecord.source_ref.in_(refs),
            )
        )
        return {(a.source_ref, a.role): (a.partner_name, a.iban, a.account_number) for a in rows}

    def record_settlements(self, settlements: list[Settlement], run_id: uuid.UUID | None) -> int:
        """Paiements rapprochés écrits dans un fichier : un règlement par (facture, paiement), une fois."""
        added = 0
        for st in settlements:
            exists = self.s.scalar(
                select(ExportOpenItemSettlementRecord.id).where(
                    ExportOpenItemSettlementRecord.open_item_id == st.open_item_id,
                    ExportOpenItemSettlementRecord.payment_ref == st.payment_ref,
                )
            )
            if exists is None:
                self.s.add(
                    ExportOpenItemSettlementRecord(
                        open_item_id=st.open_item_id,
                        payment_ref=st.payment_ref,
                        amount=st.amount,
                        settled_on=st.settled_on,
                        export_run_id=run_id,
                    )
                )
                added += 1
        self.s.flush()
        return added


__all__ = ["OpenItem", "OpenItemRegister", "Settlement"]
