"""Accounting CDM : validation, conventions de signe, rôle du partenaire, contrat JSON Schema publié (à jour)
et indépendance vis-à-vis des systèmes (aucun nom de champ Odoo ni DATEV dans le contrat)."""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from uie_cdm import (
    ACCOUNTING_MODEL_VERSION,
    AccountingDocument,
    DocumentLine,
    DocumentStatus,
    DocumentType,
    LedgerAccountRef,
    LineKind,
    PartnerRef,
    PartnerRole,
    PostingCategoryRef,
    TaxComputation,
    TaxRef,
    accounting_json_schema,
)

SCHEMA = Path(__file__).resolve().parents[1] / "schemas" / f"accounting-{ACCOUNTING_MODEL_VERSION}.json"
TAX = TaxRef(source_ref="t1", name="VAT 19 %", computation=TaxComputation.PERCENT, rate=Decimal("19"))
REVENUE = LedgerAccountRef(source_ref="a1", code="REV-1", name="Revenue")
OPEN = LedgerAccountRef(source_ref="a2", code="REC-1", name="Receivables")
TAXES = LedgerAccountRef(source_ref="a3", code="VAT-1", name="Output VAT")


def _invoice(**kw) -> AccountingDocument:  # noqa: ANN003
    base = {
        "source_ref": "m1",
        "document_type": DocumentType.CUSTOMER_INVOICE,
        "status": DocumentStatus.POSTED,
        "number": "INV/2026/0001",
        "accounting_date": date(2026, 1, 15),
        "currency": "EUR",
        "partner": PartnerRef(source_ref="p10", role=PartnerRole.CUSTOMER, name="Holzbau Beispiel GmbH"),
        "total_amount": Decimal("119.00"),
        "tax_amount": Decimal("19.00"),
        "lines": [
            DocumentLine(
                kind=LineKind.ITEM,
                account=REVENUE,
                amount=Decimal("-100.00"),
                gross_amount=Decimal("-119.00"),
                taxes=[TAX],
            ),
            DocumentLine(kind=LineKind.TAX, account=TAXES, amount=Decimal("-19.00"), taxes=[TAX]),
            DocumentLine(kind=LineKind.OPEN_ITEM, account=OPEN, amount=Decimal("119.00")),
        ],
    }
    return AccountingDocument(**(base | kw))


def test_balanced_customer_invoice():
    doc = _invoice()
    assert doc.balance() == 0  # débit = crédit
    assert [line.kind for line in doc.lines_of(LineKind.ITEM)] == [LineKind.ITEM]
    assert doc.document_type.partner_role == PartnerRole.CUSTOMER and not doc.document_type.is_credit_note
    assert doc.model_version == ACCOUNTING_MODEL_VERSION


@pytest.mark.parametrize(
    ("kind", "role", "credit_note"),
    [
        (DocumentType.CUSTOMER_INVOICE, PartnerRole.CUSTOMER, False),
        (DocumentType.CUSTOMER_CREDIT_NOTE, PartnerRole.CUSTOMER, True),
        (DocumentType.VENDOR_BILL, PartnerRole.SUPPLIER, False),
        (DocumentType.VENDOR_CREDIT_NOTE, PartnerRole.SUPPLIER, True),
    ],
)
def test_document_types(kind, role, credit_note):  # noqa: ANN001
    assert (kind.partner_role, kind.is_credit_note) == (role, credit_note)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"document_type": DocumentType.VENDOR_BILL},  # partenaire client sur une pièce fournisseur
        {"number": None},  # pièce comptabilisée sans numéro
        {"currency": "euro"},
        {"total_amount": Decimal("-1")},
        {"unknown": 1},
    ],
)
def test_invalid_documents_are_refused(kwargs):  # noqa: ANN001
    with pytest.raises(ValidationError):
        _invoice(**kwargs)


def test_draft_without_number_is_valid():
    assert _invoice(status=DocumentStatus.DRAFT, number=None).number is None


def test_published_json_schema_is_up_to_date():
    """Contrat publié : regénérer avec `uv run python -m uie_cdm.accounting` si le modèle change (SemVer)."""
    assert json.loads(SCHEMA.read_text(encoding="utf-8")) == accounting_json_schema()


def test_contract_has_no_source_or_target_system_names():
    text = json.dumps(accounting_json_schema()).lower()
    forbidden = [
        "odoo", "move_type", "display_type", "account.move", "balance", "price_total", "res.partner",
        "datev", "konto", "gegenkonto", "soll", "haben", "belegfeld", "bu-schl", "buchung", "skr",
    ]  # fmt: skip
    found = [w for w in forbidden if re.search(rf"\b{re.escape(w)}", text)]
    assert found == []


# --------------------------------------------------------------------------- #
# 1.1.0 : sources sans plan comptable (lignes ITEM par catégorie, TVA par taux)
# --------------------------------------------------------------------------- #
CATEGORY = PostingCategoryRef(source_ref="cat-1")
V100 = Path(__file__).resolve().parents[1] / "schemas" / "accounting-1.0.0.json"


def _category_line(**kw) -> DocumentLine:  # noqa: ANN003
    base = {
        "kind": LineKind.ITEM,
        "category": CATEGORY,
        "amount": Decimal("-100.00"),
        "gross_amount": Decimal("-119.00"),
        "tax_rate": Decimal("19"),
        "tax_amount": Decimal("-19.00"),
    }
    return DocumentLine(**(base | kw))


def test_every_1_0_document_is_still_valid_and_complete():
    """Compatibilité : une pièce 1.0 (JSON sans les champs 1.1) se relit, tous comptes présents."""
    data = _invoice().model_dump(mode="json", exclude={"model_version"})
    for line in data["lines"]:
        for key in ("category", "tax_rate", "tax_amount"):
            line.pop(key)
    doc = AccountingDocument.model_validate({**data, "model_version": "1.0.0"})
    assert doc.missing_accounts() == [] and doc.balance() == 0
    assert all(line.account is not None for line in doc.lines)
    assert V100.exists()  # le contrat 1.0.0 publié reste disponible


def test_category_line_without_account_is_kept_and_flagged():
    doc = _invoice(lines=[_category_line()])
    assert doc.missing_accounts() == [0]
    assert doc.lines[0].account is None and doc.lines[0].category == CATEGORY


@pytest.mark.parametrize(
    "line",
    [
        {"kind": LineKind.ITEM, "amount": Decimal("1")},  # ni compte ni catégorie
        {"kind": LineKind.TAX, "amount": Decimal("1")},  # TVA sans compte : jamais inventé
        {"kind": LineKind.OPEN_ITEM, "amount": Decimal("1"), "category": CATEGORY},  # catégorie hors ITEM
        {"kind": LineKind.TAX, "account": TAXES, "amount": Decimal("1"), "tax_rate": Decimal("19")},
    ],
)
def test_unclassified_or_misplaced_lines_are_refused(line):  # noqa: ANN001
    with pytest.raises(ValidationError):
        DocumentLine(**line)


def test_a_cash_book_entry_has_no_partner_and_says_its_direction():
    from uie_cdm import PostingCategoryRef

    line = DocumentLine(
        kind=LineKind.ITEM,
        amount=Decimal("-357"),
        gross_amount=Decimal("-357"),
        tax_rate=Decimal("19"),
        tax_amount=Decimal("-57"),
        category=PostingCategoryRef(source_ref="cash:barverkauf:19", name="Barverkauf at 19 % VAT"),
    )
    receipt = AccountingDocument(
        source_ref="file:cash:K-028",
        document_type=DocumentType.CASH_RECEIPT,
        status=DocumentStatus.POSTED,
        number="K-028",
        accounting_date=date(2026, 9, 22),
        currency="EUR",
        total_amount=Decimal("357"),
        tax_amount=Decimal("57"),
        lines=[line],
    )
    assert receipt.partner is None and receipt.document_type.is_cash
    assert receipt.document_type.partner_role is None and receipt.document_type.scope == "SALE"
    assert DocumentType.CASH_PAYMENT.scope == "PURCHASE" and DocumentType.VENDOR_BILL.scope == "PURCHASE"
    with pytest.raises(ValueError, match="pas de partenaire"):
        AccountingDocument(
            **receipt.model_dump(exclude={"partner"}),
            partner=PartnerRef(source_ref="file:1", role=PartnerRole.CUSTOMER, name="X"),
        )
    with pytest.raises(ValueError, match="a un partenaire"):
        AccountingDocument(
            **receipt.model_dump(exclude={"document_type"}), document_type=DocumentType.CUSTOMER_INVOICE
        )
