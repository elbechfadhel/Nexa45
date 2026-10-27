"""Accounting CDM 1.1.0 : une pièce comptable (facture, avoir) et ses lignes, indépendante de tout système.

Ni notion Odoo (move_type, display_type, balance…), ni notion DATEV (Konto / Gegenkonto, Soll / Haben,
BU-Schlüssel, Belegfeld) : un système source REMPLIT la pièce, une cible la TRADUIT. Seuls les concepts du
premier cas d'usage (factures et avoirs clients / fournisseurs comptabilisés) sont présents.

Conventions :
- montants en `Decimal`, dans la devise de la pièce ; montant de ligne SIGNÉ : débit > 0, crédit < 0 ;
- `total_amount` / `tax_amount` : valeurs absolues de la pièce ;
- `source_ref` : identifiant opaque de la source (relecture, idempotence), jamais écrit dans un fichier ;
- le partenaire est une RÉFÉRENCE (même `source_ref` que le Partner CDM) : aucun numéro de compte d'une
  cible ici.

1.1.0 (ADDITIF, toute pièce 1.0.0 reste valide) : une source SANS plan comptable (comptabilité par catégorie
et par taux) garde ses lignes ITEM avec leur `category`, sans compte (`account` facultatif SEULEMENT sur une
ligne ITEM qui a une catégorie) et leur TVA par taux (`tax_rate`, `tax_amount`). Aucune ligne TAX ni
OPEN_ITEM n'est inventée : une telle pièce n'est pas une écriture complète (`missing_accounts`) tant qu'une
correspondance catégorie → compte n'a pas été confirmée.

1.3.0 (ADDITIF) : écritures d'un LIVRE DE CAISSE (`CASH_RECEIPT` recette, `CASH_PAYMENT` dépense) : SANS
partenaire (une caisse n'a ni client ni fournisseur) ; une ligne ITEM par catégorie et taux, comme une facture
d'une source sans plan comptable (recette au crédit, dépense au débit) ; le compte de la caisse est celui du
dossier, choisi par la cible.

1.2.0 (ADDITIF) : une ligne ITEM peut dire la NATURE de l'opération (`supply_kind` : marchandise ou prestation
de service) quand la source la donne ; la TVA d'une opération internationale en dépend. Absente : inconnue,
jamais déduite.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from uie_cdm.partner import PartnerRole

ACCOUNTING_MODEL_VERSION = "1.3.0"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DocumentType(StrEnum):
    CUSTOMER_INVOICE = "CUSTOMER_INVOICE"
    CUSTOMER_CREDIT_NOTE = "CUSTOMER_CREDIT_NOTE"
    VENDOR_BILL = "VENDOR_BILL"
    VENDOR_CREDIT_NOTE = "VENDOR_CREDIT_NOTE"
    CASH_RECEIPT = "CASH_RECEIPT"  # 1.3.0 : recette d'un livre de caisse (sans partenaire)
    CASH_PAYMENT = "CASH_PAYMENT"  # 1.3.0 : dépense d'un livre de caisse (sans partenaire)

    @property
    def is_cash(self) -> bool:
        return self in (DocumentType.CASH_RECEIPT, DocumentType.CASH_PAYMENT)

    @property
    def partner_role(self) -> PartnerRole | None:
        """Rôle du partenaire de la pièce : client pour les pièces de vente, fournisseur pour les achats ;
        None : écriture de caisse (aucun partenaire)."""
        if self.is_cash:
            return None
        customer = self in (DocumentType.CUSTOMER_INVOICE, DocumentType.CUSTOMER_CREDIT_NOTE)
        return PartnerRole.CUSTOMER if customer else PartnerRole.SUPPLIER

    @property
    def scope(self) -> str:
        """Sens de la pièce pour la TVA : SALE (vente, recette) ou PURCHASE (achat, dépense)."""
        sale = (DocumentType.CUSTOMER_INVOICE, DocumentType.CUSTOMER_CREDIT_NOTE, DocumentType.CASH_RECEIPT)
        return "SALE" if self in sale else "PURCHASE"

    @property
    def is_credit_note(self) -> bool:
        return self in (DocumentType.CUSTOMER_CREDIT_NOTE, DocumentType.VENDOR_CREDIT_NOTE)


class DocumentStatus(StrEnum):
    POSTED = "POSTED"  # comptabilisée (définitive)
    DRAFT = "DRAFT"  # brouillon (modifiable)
    CANCELLED = "CANCELLED"


class LineKind(StrEnum):
    ITEM = "ITEM"  # ligne de produit / service / charge (base hors taxe)
    TAX = "TAX"  # ligne de taxe
    OPEN_ITEM = "OPEN_ITEM"  # partie client / fournisseur (montant dû)
    ROUNDING = "ROUNDING"  # arrondi de la pièce


class SupplyKind(StrEnum):
    """Nature d'une opération (1.2.0) : livraison de marchandises ou prestation de service."""

    GOODS = "GOODS"
    SERVICES = "SERVICES"


class TaxComputation(StrEnum):
    PERCENT = "PERCENT"  # pourcentage de la base
    FIXED = "FIXED"  # montant fixe
    GROUP = "GROUP"  # groupe de taxes


class TaxScope(StrEnum):
    SALE = "SALE"
    PURCHASE = "PURCHASE"
    NONE = "NONE"


class PartnerRef(_Model):
    source_ref: str = Field(min_length=1)  # même référence que le Partner CDM de la même source
    role: PartnerRole
    name: str = Field(min_length=1)


class LedgerAccountRef(_Model):
    source_ref: str = Field(min_length=1)
    code: str = Field(min_length=1)  # code du compte DANS LA SOURCE (jamais supposé égal à celui d'une cible)
    name: str | None = None


class PostingCategoryRef(_Model):
    """Catégorie de comptabilisation de la SOURCE (1.1.0) : classe une ligne sans être un compte."""

    source_ref: str = Field(min_length=1)
    name: str | None = None


class TaxRef(_Model):
    source_ref: str = Field(min_length=1)
    name: str = Field(min_length=1)
    computation: TaxComputation | None = None
    rate: Decimal | None = None  # pourcentage (ex. 19.0) quand computation = PERCENT
    scope: TaxScope | None = None
    price_included: bool = False


class DocumentLine(_Model):
    kind: LineKind
    # obligatoire, sauf (1.1.0) sur une ligne ITEM qui a une catégorie : jamais un compte inventé
    account: LedgerAccountRef | None = None
    amount: Decimal  # signé (débit > 0, crédit < 0), hors taxe pour ITEM
    gross_amount: Decimal | None = None  # ITEM : montant taxes comprises, même signe que `amount`
    taxes: list[TaxRef] = []  # ITEM : taxes appliquées ; TAX : la taxe de la ligne
    description: str | None = None
    category: PostingCategoryRef | None = None  # 1.1.0 : ITEM, catégorie de la source
    tax_rate: Decimal | None = None  # 1.1.0 : ITEM, taux en % quand la source ne donne qu'un taux
    tax_amount: Decimal | None = None  # 1.1.0 : ITEM, TVA de la ligne, même signe que `amount`
    supply_kind: SupplyKind | None = None  # 1.2.0 : ITEM, nature de l'opération quand la source la donne

    @model_validator(mode="after")
    def _classified(self) -> DocumentLine:
        if self.account is None and not (self.kind == LineKind.ITEM and self.category is not None):
            raise ValueError("une ligne a un compte (ou, pour une ligne ITEM, au moins une catégorie)")
        if self.kind != LineKind.ITEM and (
            self.category is not None
            or self.tax_rate is not None
            or self.tax_amount is not None
            or self.supply_kind is not None
        ):
            raise ValueError("catégorie, TVA par taux et nature ne s'appliquent qu'à une ligne ITEM")
        return self


class AccountingDocument(_Model):
    model_version: str = ACCOUNTING_MODEL_VERSION
    source_ref: str = Field(min_length=1)
    document_type: DocumentType
    status: DocumentStatus
    number: str | None = None  # numéro de la pièce (absent pour un brouillon)
    reference: str | None = None  # référence externe (ex. numéro de facture du fournisseur)
    accounting_date: date
    document_date: date | None = None
    due_date: date | None = None
    currency: str = Field(pattern=r"^[A-Z]{3}$")  # ISO 4217
    partner: PartnerRef | None = None  # 1.3.0 : absent pour une écriture de caisse, obligatoire sinon
    total_amount: Decimal = Field(ge=0)
    tax_amount: Decimal = Field(ge=0)
    lines: list[DocumentLine] = []
    description: str | None = None

    @model_validator(mode="after")
    def _consistency(self) -> AccountingDocument:
        if self.document_type.is_cash:
            if self.partner is not None:
                raise ValueError("une écriture de caisse n'a pas de partenaire")
        elif self.partner is None:
            raise ValueError("une facture ou un avoir a un partenaire")
        elif self.partner.role != self.document_type.partner_role:
            raise ValueError("le rôle du partenaire ne correspond pas au type de pièce")
        if self.status == DocumentStatus.POSTED and not self.number:
            raise ValueError("une pièce comptabilisée a un numéro")
        return self

    def lines_of(self, kind: LineKind) -> list[DocumentLine]:
        return [line for line in self.lines if line.kind == kind]

    def missing_accounts(self) -> list[int]:
        """Positions des lignes sans compte de grand livre (1.1.0) : la pièce n'est pas une écriture
        complète tant qu'une correspondance n'a pas été confirmée pour elles."""
        return [i for i, line in enumerate(self.lines) if line.account is None]

    def balance(self) -> Decimal:
        """Somme signée de toutes les lignes : 0 pour une pièce équilibrée (débit = crédit)."""
        return sum((line.amount for line in self.lines), Decimal("0"))


def accounting_json_schema() -> dict:
    """Contrat publié (JSON Schema) de l'Accounting CDM."""
    schema = AccountingDocument.model_json_schema()
    schema["$id"] = f"https://go45.io/uie/cdm/accounting-{ACCOUNTING_MODEL_VERSION}.json"
    schema["title"] = "AccountingDocument"
    return schema


if __name__ == "__main__":  # régénère le contrat publié : uv run python -m uie_cdm.accounting
    import json
    from pathlib import Path

    out = Path(__file__).resolve().parents[2] / "schemas" / f"accounting-{ACCOUNTING_MODEL_VERSION}.json"
    out.write_text(
        json.dumps(accounting_json_schema(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(out)
