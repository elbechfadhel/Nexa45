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
- paiement par CARTE inscrit en caisse (mot de données dans le texte de l'écriture, jamais dans la remarque) ;
- même numéro de pièce deux fois : contenu identique → écrite une fois ; contenu différent → aucune des deux.
Contrôles du LIVRE (tel qu'il est écrit, dans l'ordre du fichier, depuis la ligne d'ouverture) :
- solde recalculé (ouverture + recettes − dépenses) comparé à la colonne du solde : écart signalé, jamais
  corrigé ; sans ligne d'ouverture, le solde n'est pas contrôlé, et c'est dit ;
- ordre chronologique : une écriture antérieure placée après une plus récente est signalée (information) ;
- solde NÉGATIF (une caisse ne l'est jamais) : anomalie grave en tête ; l'écriture qui le fait passer sous
  zéro attend une décision explicite, jamais présélectionnée ; les suivantes encore négatives sont nommées.
Références de la source préfixées « file: » ; la remarque libre n'est jamais lue pour décider."""

from __future__ import annotations

import re
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
from uie_core.wizard.text import say
from uie_core.wizard.values import ExcludedRow, QuestionOption, ValueIssue, ValueQuestion, ValueTransform

FILE_REF = "file:"
CENT = Decimal("0.01")
NEGATIVE_KEY = "cash:negative"  # écriture qui rend le solde négatif : à exporter ou non (décision)


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


def _words(text: str) -> str:
    return " " + " ".join(re.findall(r"[^\W_]+", text.casefold())) + " "


def card_word(table: FileTableSpec, text: str) -> str | None:
    """Mot de paiement par carte (données du tableau) trouvé dans le texte d'une ligne, mots entiers ; None :
    aucun."""
    words = _words(text)
    return next((w for w in table.card_words if _words(w) in words), None)


def is_opening(table: FileTableSpec, row: dict[str, str]) -> bool:
    """Ligne d'ouverture : son texte est un mot d'ouverture (données du tableau) ET elle n'a ni recette, ni
    dépense, ni numéro de pièce."""
    text = " ".join((row.get("booking_text") or "").split()).casefold()
    words = {" ".join(w.split()).casefold() for w in table.opening_words}
    empty = not any((row.get(k) or "").strip() for k in ("cash_in", "cash_out", "document_number"))
    return bool(text) and text in words and empty


def _money(value: Decimal) -> str:
    return f"{value:.2f} €"


def _check_balance(
    val: Callable[[int, str], str], entries: list[int], opening: Decimal | None, columns: dict[str, str]
) -> tuple[list[ValueIssue], list[int], list[int]]:
    """(points signalés, lignes qui rendent le solde négatif, lignes encore négatives) : solde recalculé dans
    l'ordre du fichier, comparé à la colonne du solde ; rien n'est corrigé."""
    if opening is None:
        return (
            [
                ValueIssue(
                    field="cash_balance",
                    column=columns.get("cash_balance", ""),
                    kind="cash_no_opening",
                    count=0,
                    examples=[],
                    message="No opening balance line (Anfangsbestand): the cash balance is not checked.",
                    fix="Add the opening balance as the first line of your cash book.",
                    severity="to_check",
                )
            ],
            [],
            [],
        )
    balance = opening
    differ: list[tuple[int, Decimal, Decimal]] = []
    crossing: list[int] = []
    still: list[int] = []
    lowest = Decimal(0)
    negative = False
    for i in entries:
        balance += (_amount(val(i, "cash_in")) or Decimal(0)) - (_amount(val(i, "cash_out")) or Decimal(0))
        written = _amount(val(i, "cash_balance"))
        if written is not None and abs(written - balance) >= CENT / 2:
            differ.append((i, written, balance))
        if balance < 0:
            lowest = min(lowest, balance)
            # passage sous zéro depuis un solde positif : l'écriture qui rend la caisse négative ; ensuite :
            # encore négative (nommée, exportée)
            (still if negative else crossing).append(i)
        negative = balance < 0
    issues = []
    if crossing:
        names = ", ".join(val(i, "document_number") or f"row {i + 1}" for i in crossing)
        after = ", ".join(val(i, "document_number") or f"row {i + 1}" for i in still)
        issues.append(
            ValueIssue(
                field="cash_balance",
                column=columns.get("cash_balance", ""),
                kind="cash_negative",
                count=len(crossing) + len(still),
                examples=[],
                message=f"The cash balance becomes negative with {names} (lowest {_money(lowest)})"
                + (f", and stays negative with {after}" if after else "")
                + ": a cash register can never be negative. Nothing is corrected.",
                rows=[i + 1 for i in crossing + still],
                fix="Check these entries in your cash book (a missing receipt, a wrong amount, a wrong "
                "date).",
                severity="serious",
            )
        )
    if differ:
        first = differ[0]
        issues.append(
            ValueIssue(
                field="cash_balance",
                column=columns.get("cash_balance", ""),
                kind="cash_balance_differs",
                count=len(differ),
                examples=[f"{_money(w)} instead of {_money(b)}" for _, w, b in differ[:5]],
                message=say(
                    "{n} cash [balance differs|balances differ] from the balance recalculated from the "
                    "opening balance",
                    len(differ),
                )
                + f" (row {first[0] + 1}: {_money(first[1])} in your file, {_money(first[2])} recalculated): "
                "never corrected, the entries are exported.",
                rows=[i + 1 for i, _, _ in differ],
                severity="to_check",
            )
        )
    return issues, crossing, still


def _check_order(
    val: Callable[[int, str], str], entries: list[int], columns: dict[str, str]
) -> list[ValueIssue]:
    """Ordre chronologique : une écriture datée AVANT une écriture placée plus haut est signalée en
    information (elle est exportée ; rien n'est réordonné)."""
    latest: tuple[date, int] | None = None
    late: list[tuple[int, date, date]] = []
    for i in entries:
        try:
            when = date.fromisoformat(val(i, "document_date"))
        except ValueError:
            continue
        if latest is not None and when < latest[0]:
            late.append((i, when, latest[0]))
        elif latest is None or when > latest[0]:
            latest = (when, i)
    if not late:
        return []
    i, when, before = late[0]
    who = val(i, "document_number") or f"row {i + 1}"
    return [
        ValueIssue(
            field="document_date",
            column=columns.get("document_date", ""),
            kind="cash_order",
            count=len(late),
            examples=[],
            message=say("{n} [entry is|entries are] not in date order", len(late))
            + f" ({who} of {when.strftime('%d.%m.%Y')} after an entry of {before.strftime('%d.%m.%Y')}): "
            "exported as they are.",
            rows=[r + 1 for r, _, _ in late],
            severity="info",
        )
    ]


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
    choices = choices or {}
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
            # pas une ligne refusée : le point de départ du solde, dit en information
            opening = _amount(val(i, "cash_balance"))
            issues.append(
                ValueIssue(
                    field="cash_balance",
                    column=columns.get("cash_balance", ""),
                    kind="cash_opening",
                    count=1,
                    examples=[],
                    message=f"Opening balance read: {_money(opening)} (row {i + 1})."
                    if opening is not None
                    else f"Opening balance line without an amount (row {i + 1}).",
                    rows=[i + 1],
                    severity="info",
                )
            )
            continue
        entries.append(i)

    balance_issues, crossing, _ = _check_balance(val, entries, opening, columns)
    issues += balance_issues
    issues += _check_order(val, entries, columns)
    negative_answer = choices.get(NEGATIVE_KEY)
    if crossing:
        listed = ", ".join(val(i, "document_number") or f"row {i + 1}" for i in crossing)
        q = ValueQuestion(
            key=NEGATIVE_KEY,
            kind="cash_negative",
            field="cash_balance",
            column=columns.get("cash_balance"),
            title=say("{n} [entry makes|entries make] the cash balance negative", len(crossing)),
            help=f"{listed}. A cash register can never be negative: check the cash book with your tax "
            "advisor before exporting.",
            count=len(crossing),
            options=[
                QuestionOption(value="export", label="Export it anyway (I checked the cash book)"),
                QuestionOption(value="skip", label="Do not export it"),
            ],
            answer=negative_answer if negative_answer in ("export", "skip") else None,
        )
        questions.append(q)

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
        if i in crossing and negative_answer != "export":
            # jamais exportée sans la décision explicite (aucune présélection)
            skip(
                i,
                f"Entry {number or f'of row {i + 1}'}: the cash balance becomes negative here"
                + (" (your choice)" if negative_answer == "skip" else ", waiting for your answer above")
                + ": not exported.",
                "cash_negative",
            )
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
        card = card_word(table, val(i, "booking_text"))
        if card:
            # paiement par carte inscrit dans la caisse : pas de l'argent liquide (texte de l'écriture)
            skip(
                i,
                f"Entry {number}: “{val(i, 'booking_text')}” is a card payment ({card}), not cash: never "
                "exported in the cash book (book it with your bank or card payments).",
                "card_payment",
            )
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
