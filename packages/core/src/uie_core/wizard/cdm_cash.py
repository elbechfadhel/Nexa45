"""Lignes d'un LIVRE DE CAISSE (Kassenbuch) → Accounting CDM 1.3 (source FICHIER, aucun système).

Une ligne = une écriture de caisse : une RECETTE (colonne des recettes) ou une DÉPENSE (colonne des dépenses),
sans client ni fournisseur, classée par CATÉGORIE de caisse et taux de TVA ; la cible fait confirmer la
correspondance catégorie → compte (jamais devinée ici) et écrit le compte de la caisse du dossier. Le montant
de la ligne est brut (TTC) ; la TVA en est déduite au taux de la ligne.

Rien n'est corrigé ni deviné ; une ligne qui ne peut pas être écrite est LISTÉE avec son motif :
- ligne d'ouverture (Anfangsbestand, Übertrag…, sans montant ni pièce) : pas une écriture, point de départ du
  solde ;
- numéro de pièce absent (une pièce par écriture) ; date illisible ;
- recette ET dépense sur la même ligne, ou aucune des deux ;
- catégorie absente ;
- taux de TVA absent (admis seulement pour une catégorie déclarée sans TVA par la table de la cible, `no_vat`)
  ou non admis dans le pays à la date de la ligne ;
- même numéro de pièce deux fois : contenu identique → écrite une fois ; contenu différent → aucune des deux.
Références de la source préfixées « file: » ; la remarque libre n'est jamais lue pour décider."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict
from uie_cdm import (
    AccountingDocument,
    DocumentLine,
    DocumentStatus,
    DocumentType,
    LineKind,
    PostingCategoryRef,
)

from uie_core.mapping.suggestions import cash_category_ref
from uie_core.sources.table import FileTableSpec
from uie_core.wizard.values import ExcludedRow, ValueIssue, ValueQuestion, ValueTransform

FILE_REF = "file:"
CENT = Decimal("0.01")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CashTable(_Model):
    documents: list[AccountingDocument]
    row_of: dict[str, int]  # source_ref → ligne du fichier
    issues: list[ValueIssue]
    questions: list[ValueQuestion]
    skipped: list[ExcludedRow]
    # catégories des écritures lisibles → nom ; sens de chaque catégorie (SALE : recette, PURCHASE : dépense)
    categories: dict[str, str] = {}
    category_scope: dict[str, str] = {}
    rates: dict[str, list[str]] = {}  # sens → taux présents (clé de taxe de chaque taux)
    scope: str = "CASH"
    transforms: list[ValueTransform] = []
    opening_balance: Decimal | None = None  # solde de la ligne d'ouverture (None : aucune ligne d'ouverture)


def cash_category(name: str, rate: Decimal | None) -> PostingCategoryRef:
    """Catégorie d'une écriture de caisse à ce taux (ou sans TVA)."""
    label = f"{name} (no VAT)" if rate is None else f"{name} at {rate.normalize():f} % VAT"
    return PostingCategoryRef(source_ref=cash_category_ref(name, rate), name=label)


def _amount(text: str) -> Decimal | None:
    try:
        return Decimal(text) if text else None
    except InvalidOperation:
        return None


def is_opening(table: FileTableSpec, row: dict[str, str]) -> bool:
    """Ligne d'ouverture : son texte est un mot d'ouverture (données du tableau) ET elle n'a ni recette, ni
    dépense, ni numéro de pièce."""
    text = " ".join((row.get("booking_text") or "").split()).casefold()
    words = {" ".join(w.split()).casefold() for w in table.opening_words}
    empty = not any((row.get(k) or "").strip() for k in ("cash_in", "cash_out", "document_number"))
    return bool(text) and text in words and empty


def documents_from_cash_rows(  # noqa: C901, PLR0912, PLR0915
    table: FileTableSpec,
    rows: list[dict[str, str]],
    columns: dict[str, str],
    currency: str,
    choices: dict[str, str] | None = None,
    no_vat: Callable[[str], bool] | None = None,
) -> CashTable:
    """`rows` : une entrée par ligne (clé du tableau → valeur mise en forme : nombres en notation standard,
    dates ISO) ; `no_vat` : catégorie déclarée sans TVA par la table de la cible (un taux vide n'est admis que
    pour elle ; None : jamais admis)."""
    del choices  # décisions propres à la caisse : commits suivants
    skipped: list[ExcludedRow] = []
    issues: list[ValueIssue] = []
    questions: list[ValueQuestion] = []

    def val(i: int, key: str) -> str:
        return (rows[i].get(key) or "").strip()

    def skip(i: int, reason: str, kind: str) -> None:
        skipped.append(ExcludedRow(row=i + 1, reason=reason, kind=kind))

    used = [i for i in range(len(rows)) if any(rows[i].values())]
    opening: Decimal | None = None
    entries: list[int] = []
    for i in used:
        if opening is None and not entries and is_opening(table, rows[i]):
            opening = _amount(val(i, "cash_balance"))
            skip(
                i,
                "Opening balance of the cash book: not a booking, it is where the balance starts.",
                "opening",
            )
            continue
        entries.append(i)

    # même numéro de pièce plusieurs fois : contenu identique = saisie en double ; sinon conflit
    content_keys = ("document_date", "cash_in", "cash_out", "tax_rate", "cash_category")
    by_number: dict[str, list[int]] = {}
    for i in entries:
        if val(i, "document_number"):
            by_number.setdefault(val(i, "document_number"), []).append(i)
    twice: dict[int, tuple[str, str]] = {}
    for number, idx in by_number.items():
        if len(idx) < 2:
            continue
        same = all(
            tuple(val(j, k) for k in content_keys) == tuple(val(idx[0], k) for k in content_keys) for j in idx
        )
        for j in idx[1:] if same else idx:
            twice[j] = (
                (
                    f"Entry {number} entered twice (same content as row {idx[0] + 1}): exported once.",
                    "duplicate",
                )
                if same
                else (
                    f"Document number {number} is used for different entries: none of them is exported.",
                    "probable_duplicate",
                )
            )

    candidates: list[tuple[int, AccountingDocument]] = []
    for i in entries:
        number = val(i, "document_number")
        if i in twice:
            skip(i, *twice[i])
            continue
        if not number:
            skip(
                i,
                "No document number (Belegfeld 1 is required, one voucher per entry): not exported.",
                "missing_number",
            )
            continue
        try:
            when = date.fromisoformat(val(i, "document_date"))
        except ValueError:
            skip(i, f"Entry {number}: no readable date: not exported.", "date")
            continue
        cash_in, cash_out = _amount(val(i, "cash_in")), _amount(val(i, "cash_out"))
        if cash_in and cash_out:
            skip(
                i,
                f"Entry {number}: cash in AND cash out on the same line: never guessed, not exported.",
                "cash_both",
            )
            continue
        gross = cash_in or cash_out
        if not gross or gross < 0:
            skip(i, f"Entry {number}: no positive amount in cash in or cash out: not exported.", "amount")
            continue
        name = val(i, "cash_category")
        if not name:
            skip(
                i,
                f"Entry {number}: no category: its account is never guessed, not exported.",
                "missing_category",
            )
            continue
        rate = _amount(val(i, "tax_rate"))
        if rate is None:
            if not (no_vat and no_vat(name)):
                skip(i, f"Entry {number}: no VAT rate: not exported.", "rate")
                continue
        else:
            allowed = table.rates(table.home_country or "", when) if table.home_country else None
            if allowed is not None and rate not in allowed:
                skip(
                    i,
                    f"Entry {number}: {rate.normalize():f} % is not a valid VAT rate on {when.isoformat()} "
                    f"(valid: {', '.join(f'{r.normalize():f} %' for r in allowed)}): not exported.",
                    "rate",
                )
                continue
        net = gross if not rate else (gross * 100 / (100 + rate)).quantize(CENT, ROUND_HALF_UP)
        tax = gross - net
        receipt = bool(cash_in)
        sign = Decimal(-1) if receipt else Decimal(1)  # recette au crédit (caisse au débit), dépense au débit
        line = DocumentLine(
            kind=LineKind.ITEM,
            amount=sign * net,
            gross_amount=sign * gross,
            tax_rate=rate,
            tax_amount=sign * tax,
            category=cash_category(name, rate),
            description=val(i, "booking_text") or None,
        )
        doc = AccountingDocument(
            source_ref=f"{FILE_REF}cash:{number}",
            document_type=DocumentType.CASH_RECEIPT if receipt else DocumentType.CASH_PAYMENT,
            status=DocumentStatus.POSTED,
            number=number,
            accounting_date=when,
            document_date=when,
            currency=currency,
            total_amount=gross,
            tax_amount=tax,
            lines=[line],
            description=val(i, "booking_text") or None,
        )
        candidates.append((i, doc))

    categories: dict[str, str] = {}
    scopes: dict[str, str] = {}
    rates: dict[str, set[str]] = {}
    for _, d in candidates:
        line = d.lines[0]
        assert line.category is not None
        categories[line.category.source_ref] = line.category.name or line.category.source_ref
        scopes[line.category.source_ref] = d.document_type.scope
        if line.tax_rate is not None:
            rates.setdefault(d.document_type.scope, set()).add(f"{line.tax_rate.normalize():f}")
    return CashTable(
        documents=[d for _, d in candidates],
        row_of={d.source_ref: i + 1 for i, d in candidates},
        issues=issues,
        questions=questions,
        skipped=sorted(skipped, key=lambda e: e.row),
        categories=categories,
        category_scope=scopes,
        rates={s: sorted(r, key=float, reverse=True) for s, r in rates.items()},
        opening_balance=opening,
    )


__all__ = ["CashTable", "cash_category", "documents_from_cash_rows", "is_opening"]
