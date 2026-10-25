"""DTO de la Supplier API pour la lecture comptable (ACCOUNTING, phase 3 : source → Accounting CDM).

Ce que le fournisseur voit d'une pièce lue dans son système : numéro, type, statut, partenaire, dates,
devise, montants, lignes (compte SOURCE, taxes, montants signés) et anomalies. Ni référence interne, ni plan
de lecture, ni empreinte ; aucun compte ni code de taxe d'une cible (pas encore de mapping)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict
from uie_core.accounting import AccountingReadItem, AccountingReadResult, CompanyRef

TYPE_LABEL = {
    "CUSTOMER_INVOICE": "Customer invoice",
    "CUSTOMER_CREDIT_NOTE": "Customer credit note",
    "VENDOR_BILL": "Vendor bill",
    "VENDOR_CREDIT_NOTE": "Vendor credit note",
}
STATUS_LABEL = {"POSTED": "Posted", "DRAFT": "Draft", "CANCELLED": "Cancelled"}
KIND_LABEL = {"ITEM": "Item", "TAX": "Tax", "OPEN_ITEM": "Amount due", "ROUNDING": "Rounding"}


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountingCompanyOut(_Out):
    id: str  # identité de la société dans le système source : à renvoyer pour lire ses pièces
    name: str | None


def company(c: CompanyRef) -> AccountingCompanyOut:
    return AccountingCompanyOut(id=c.source_ref, name=c.name)


class TaxOut(_Out):
    name: str
    rate: Decimal | None
    computation: str | None
    usage: str | None
    price_included: bool


class LineOut(_Out):
    kind: str
    account_code: str | None  # None : ligne classée par catégorie, sans compte dans la source
    account_name: str | None
    category: str | None = None  # catégorie de la source (sans compte de grand livre)
    description: str | None
    amount: Decimal  # signé : débit > 0, crédit < 0
    gross_amount: Decimal | None
    taxes: list[TaxOut]


class IssueOut(_Out):
    message: str


class PartnerOut(_Out):
    name: str
    role: Literal["customer", "supplier"]


class AccountingDocumentOut(_Out):
    number: str | None
    reference: str | None
    type: str
    status: str
    # éligible (phase 3) : comptabilisée et sans construction non prise en charge, donc prête pour le
    # mapping ; ce n'est pas une validation DATEV
    exportable: bool
    partner: PartnerOut | None
    accounting_date: date | None
    document_date: date | None
    due_date: date | None
    currency: str | None
    total_amount: Decimal | None
    tax_amount: Decimal | None
    balance: Decimal | None  # somme signée des lignes (CDM) : 0 = pièce équilibrée
    issues: list[IssueOut]
    lines: list[LineOut]


class AccountingCountsOut(_Out):
    documents: int
    exportable: int
    with_issues: int
    by_status: dict[str, int]
    by_type: dict[str, int]


class AccountingReadOut(_Out):
    period: str
    company: AccountingCompanyOut
    counts: AccountingCountsOut
    documents: list[AccountingDocumentOut]
    truncated: bool
    # ce que le système source ne fournit pas (source à schéma fixe : ex. comptes de grand livre), en phrases
    unavailable: list[IssueOut] = []


def _document(i: AccountingReadItem) -> AccountingDocumentOut:
    d = i.document
    return AccountingDocumentOut(
        number=i.number if i.number and i.number != "/" else None,
        reference=d.reference if d else None,
        type=TYPE_LABEL.get(i.document_type.value, i.document_type.value) if i.document_type else "Unknown",
        status=STATUS_LABEL.get(i.status.value, i.status.value) if i.status else "Unknown",
        exportable=i.exportable,
        partner=PartnerOut(name=d.partner.name, role=d.partner.role.value.lower()) if d else None,  # type: ignore[arg-type]
        accounting_date=d.accounting_date if d else None,
        document_date=d.document_date if d else None,
        due_date=d.due_date if d else None,
        currency=d.currency if d else None,
        total_amount=d.total_amount if d else None,
        tax_amount=d.tax_amount if d else None,
        balance=d.balance() if d else None,
        issues=[IssueOut(message=x.message) for x in i.issues],
        lines=[
            LineOut(
                kind=KIND_LABEL.get(ln.kind.value, ln.kind.value),
                account_code=ln.account.code if ln.account else None,
                account_name=ln.account.name if ln.account else None,
                category=(ln.category.name or ln.category.source_ref) if ln.category else None,
                description=ln.description,
                amount=ln.amount,
                gross_amount=ln.gross_amount,
                taxes=[
                    TaxOut(
                        name=t.name,
                        rate=t.rate,
                        computation=t.computation.value.lower() if t.computation else None,
                        usage=t.scope.value.lower() if t.scope else None,
                        price_included=t.price_included,
                    )
                    for t in ln.taxes
                ],
            )
            for ln in (d.lines if d else [])
        ],
    )


def accounting_read(r: AccountingReadResult) -> AccountingReadOut:
    return AccountingReadOut(
        period=r.period,
        company=company(r.company),
        counts=AccountingCountsOut(
            documents=len(r.items),
            exportable=sum(1 for i in r.items if i.exportable),
            with_issues=sum(1 for i in r.items if i.issues),
            by_status={STATUS_LABEL.get(k, k): v for k, v in r.by_status().items()},
            by_type={TYPE_LABEL.get(k, k): v for k, v in r.by_type().items()},
        ),
        documents=[_document(i) for i in r.items],
        truncated=r.truncated,
        unavailable=[IssueOut(message=g.message) for g in r.gaps],
    )
