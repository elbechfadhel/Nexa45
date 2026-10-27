"""Lignes d'un RELEVÉ BANCAIRE (export d'une banque ou CAMT.053 lu sous la même forme) → Accounting CDM 1.4
(source FICHIER, aucun système), avec RAPPROCHEMENT des factures exportées du dossier (registre des factures
ouvertes), par RÈGLES seulement : jamais par le seul montant, jamais par l'IA.

Une ligne = un mouvement du compte (montant signé : entrée > 0, sortie < 0) :
- numéro(s) d'une facture exportée de ce dossier trouvé(s) dans le motif (mots entiers ; facture de sortie
  pour une entrée d'argent, d'achat pour une sortie ; un numéro partagé par deux fournisseurs est départagé
  par l'IBAN, puis le nom exact de la contrepartie) :
  * montant = montant ouvert (ou la somme exacte de plusieurs factures) → une ligne par facture réglée ;
  * une seule facture, montant inférieur → paiement PARTIEL réglé, reste ouvert dit en information ;
  * escompte (mots de données, « Skonto ») ou montant différent → jamais rapproché, motif ;
- sinon, mouvement HORS factures reconnu par des mots du motif ou de la contrepartie (frais, dépôt,
  impôt, loyer…) → une ligne de catégorie, dont le compte vient de la table de la cible ; versement des
  cartes : jamais écrit, motif ;
- sinon, numéro qui a la FORME d'une facture du dossier mais inconnu : facture d'un partenaire connu au même
  montant ouvert → PROPOSÉE, jamais rapprochée sans réponse ; sinon facture absente du journal, motif ;
- sinon : payeur inconnu, jamais deviné, motif.
Rien n'est corrigé ; la remarque libre n'est jamais lue."""

from __future__ import annotations

import hashlib
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict
from uie_cdm import (
    AccountingDocument,
    DocumentLine,
    DocumentStatus,
    DocumentType,
    LineKind,
    PartnerRef,
    PartnerRole,
    PostingCategoryRef,
    SettlementRef,
)

from uie_core.exports.open_items import OpenItem
from uie_core.mapping.suggestions import bank_category_ref
from uie_core.sources.table import BankCategory, FileTableSpec
from uie_core.wizard.text import say
from uie_core.wizard.values import ExcludedRow, QuestionOption, ValueIssue, ValueQuestion, ValueTransform

FILE_REF = "file:"
CONFIRM_KEY = "bank:confirm:"  # paiement proposé pour une facture (même partenaire, même montant) : décision
DUPLICATE_KEY = "bank:duplicates"  # mouvements identiques SANS référence de la banque : une fois, ou les deux


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BankTable(_Model):
    documents: list[AccountingDocument]
    row_of: dict[str, int]  # source_ref → ligne du fichier
    issues: list[ValueIssue]
    questions: list[ValueQuestion]
    skipped: list[ExcludedRow]
    categories: dict[str, str] = {}  # catégories hors factures → nom
    category_scope: dict[str, str] = {}  # catégorie → sens (SALE : entrée, PURCHASE : sortie)
    rates: dict[str, list[str]] = {}  # aucune TVA sur un mouvement bancaire
    scope: str = "BANK"
    transforms: list[ValueTransform] = []
    # paiement → factures réglées (registre) et montant réglé : inscrits au registre au téléchargement
    settlements: dict[str, list[tuple[str, Decimal]]] = {}
    # mouvements déjà exportés par un relevé précédent : date de cet export → nombre (résumé)
    already_exported: dict[str, int] = {}


def _amount(text: str) -> Decimal | None:
    try:
        return Decimal(text) if text else None
    except InvalidOperation:
        return None


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", text.casefold()))


def _has_words(text: str, words: list[str]) -> str | None:
    """Mot (ou suite de mots) de données présent dans le texte, en mots entiers ; None : aucun."""
    padded = f" {_norm(text)} "
    return next((w for w in words if _norm(w) and f" {_norm(w)} " in padded), None)


def _tokens(text: str) -> list[str]:
    """Références du motif : suites sans espace ni virgule (« RE-2026011, RE-2026012 » → deux), ponctuation
    finale retirée."""
    return [t.strip(".;:()") for t in re.split(r"[\s,]+", text) if t.strip(".;:()")]


def _shape(token: str) -> str:
    return re.sub(r"\d", "9", token.upper())


def fingerprint(row: dict[str, str]) -> str:
    """Identité d'un mouvement INDÉPENDANTE du format (CAMT ou export tabulaire) : compte, date, montant
    signé, IBAN de la contrepartie, motif normalisé ; jamais la référence propre à un format."""
    amount = _amount(row.get("amount", ""))
    parts = [
        (row.get("account_iban") or "").replace(" ", "").upper(),
        row.get("document_date") or "",
        f"{amount.normalize():f}" if amount is not None else "",
        (row.get("counterparty_iban") or "").replace(" ", "").upper(),
        _norm(row.get("purpose") or ""),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def _money(value: Decimal) -> str:
    return f"{value:.2f} €"


def _same_name(a: str | None, b: str | None) -> bool:
    return bool(a and b) and _norm(a or "") == _norm(b or "")


def _category_ref(cat: BankCategory, row: dict[str, str]) -> PostingCategoryRef:
    who = (row.get("counterparty_iban") or "").replace(" ", "").upper() or _norm(
        row.get("counterparty_name") or ""
    ).replace(" ", "-")
    name = cat.label + (
        f" ({row.get('counterparty_name')})" if cat.per_counterparty and row.get("counterparty_name") else ""
    )
    return PostingCategoryRef(
        source_ref=bank_category_ref(cat.key, who if cat.per_counterparty and who else None), name=name
    )


def _check_balance(
    rows: list[dict[str, str]],
    balances: tuple[Decimal | None, Decimal | None] | None,
    columns: dict[str, str],
) -> list[ValueIssue]:
    """Solde d'ouverture + mouvements = solde de clôture (relevé tel qu'il est écrit) ; écart : anomalie
    grave, jamais corrigée. Sans soldes dans le format : dit en information, jamais un solde supposé."""
    column = columns.get("amount", "")
    if balances is None or None in balances:
        return [
            ValueIssue(
                field="amount",
                column=column,
                kind="bank_no_balance",
                count=0,
                examples=[],
                message="This bank export has no opening or closing balance: the account balance cannot be "
                "checked in this format.",
                severity="info",
            )
        ]
    opening, closing = balances
    assert opening is not None and closing is not None
    moved = sum((_amount((r.get("amount") or "").strip()) or Decimal(0) for r in rows), Decimal(0))
    if opening + moved == closing:
        return [
            ValueIssue(
                field="amount",
                column=column,
                kind="bank_balance_checked",
                count=0,
                examples=[],
                message=f"Balance checked: opening {_money(opening)} + movements {_money(moved)} = closing "
                f"{_money(closing)}.",
                severity="info",
            )
        ]
    return [
        ValueIssue(
            field="amount",
            column=column,
            kind="bank_balance_differs",
            count=0,
            examples=[],
            message=f"The statement does not add up: opening {_money(opening)} + movements {_money(moved)} = "
            f"{_money(opening + moved)}, but the closing balance is {_money(closing)}. Nothing is corrected.",
            fix="Check that the file holds the whole statement (no movement missing or added).",
            severity="serious",
        )
    ]


def documents_from_bank_rows(  # noqa: C901, PLR0912, PLR0915
    table: FileTableSpec,
    rows: list[dict[str, str]],
    columns: dict[str, str],
    currency: str,
    open_items: list[OpenItem],
    choices: dict[str, str] | None = None,
    balances: tuple[Decimal | None, Decimal | None] | None = None,
) -> BankTable:
    """`rows` : une entrée par ligne (clé du tableau → valeur mise en forme) ; `open_items` : registre des
    factures ouvertes du dossier (montant ouvert, partenaire, compte, IBAN) ; `balances` : soldes d'ouverture
    et de clôture du relevé (CAMT) ; None : le format n'en donne pas (export tabulaire)."""
    choices = choices or {}
    skipped: list[ExcludedRow] = []
    already: dict[str, int] = {}
    issues: list[ValueIssue] = []
    questions: list[ValueQuestion] = []
    partial: list[tuple[int, str, Decimal]] = []

    def val(i: int, key: str) -> str:
        return (rows[i].get(key) or "").strip()

    def skip(i: int, reason: str, kind: str) -> None:
        skipped.append(ExcludedRow(row=i + 1, reason=reason, kind=kind))

    by_role: dict[str, list[OpenItem]] = {"CUSTOMER": [], "SUPPLIER": []}
    for item in open_items:
        by_role.setdefault(item.role, []).append(item)
    shapes = {role: {_shape(i.number) for i in items if i.number} for role, items in by_role.items()}

    documents: list[tuple[int, AccountingDocument]] = []
    settlements: dict[str, list[tuple[str, Decimal]]] = {}
    seen: dict[str, int] = {}
    first_row: dict[str, int] = {}
    # montant ouvert de chaque facture, diminué par les paiements déjà rapprochés DANS ce fichier
    left = {x.document_ref: x.open_amount for x in open_items}
    # même mouvement deux fois dans le fichier : une référence de la banque différente = deux mouvements
    # (jamais des doublons) ; sans référence = doublon probable, écrit une fois sauf décision « Export both »
    twins = [
        i
        for i in range(len(rows))
        if any(rows[i].values())
        and not val(i, "bank_reference")
        and any(
            j < i and not val(j, "bank_reference") and fingerprint(rows[j]) == fingerprint(rows[i])
            for j in range(len(rows))
        )
    ]
    keep_both = choices.get(DUPLICATE_KEY) == "both"
    if twins:
        questions.append(
            ValueQuestion(
                key=DUPLICATE_KEY,
                kind="bank_duplicates",
                field="amount",
                column=columns.get("amount"),
                title=say("{n} movement [appears|appear] twice in your file", len(twins)),
                help="Same date, amount, counterparty and payment reference, and no bank reference to tell "
                "them apart: usually the same movement exported twice.",
                count=len(twins),
                options=[
                    QuestionOption(value="once", label="Export it once"),
                    QuestionOption(value="both", label="Export both: they are two real movements"),
                ],
                suggested="once",
                suggested_by="rule",
                answer=choices.get(DUPLICATE_KEY) if choices.get(DUPLICATE_KEY) in ("once", "both") else None,
            )
        )
    for i in range(len(rows)):
        if not any(rows[i].values()):
            continue
        amount = _amount(val(i, "amount"))
        if amount is None or amount == 0:
            skip(i, "No amount for this movement: not exported.", "amount")
            continue
        try:
            when = date.fromisoformat(val(i, "document_date"))
        except ValueError:
            skip(i, "No readable booking date: not exported.", "date")
            continue
        fp = fingerprint(rows[i])
        if i in twins and not keep_both:
            skip(
                i,
                f"Same movement as row {first_row.get(fp, 0) + 1} (no bank reference to tell them apart): "
                "exported once.",
                "duplicate",
            )
            continue
        first_row.setdefault(fp, i)
        seen[fp] = seen.get(fp, 0) + 1
        source_ref = f"{FILE_REF}bank:{fp}" + (f"#{seen[fp]}" if seen[fp] > 1 else "")
        receipt = amount > 0
        role = "CUSTOMER" if receipt else "SUPPLIER"
        purpose, name, iban = val(i, "purpose"), val(i, "counterparty_name"), val(i, "counterparty_iban")
        iban = iban.replace(" ", "").upper()
        what = f"{'Payment from' if receipt else 'Payment to'} {name or 'an unknown counterparty'}"
        tokens = {t.upper() for t in _tokens(purpose)}
        # 1. factures exportées de ce dossier citées dans le motif (rôle du sens du mouvement)
        cited: dict[str, list[OpenItem]] = {}
        for item in by_role.get(role, []):
            if item.number and item.number.upper() in tokens:
                cited.setdefault(item.number.upper(), []).append(item)
        found: list[OpenItem] = []
        ambiguous = []
        for number, items in cited.items():
            if len(items) == 1:
                found.append(items[0])
                continue
            # même numéro chez plusieurs partenaires : IBAN, puis nom exact de la contrepartie (D5)
            by_iban = [x for x in items if iban and x.partner_iban == iban]
            by_name = [x for x in items if _same_name(x.partner_name, name)]
            pick = by_iban if len(by_iban) == 1 else by_name if len(by_name) == 1 else []
            if pick:
                found.append(pick[0])
            else:
                ambiguous.append(number)
        bank = abs(amount)
        line_kind = DocumentType.BANK_RECEIPT if receipt else DocumentType.BANK_PAYMENT
        if ambiguous:
            skip(
                i,
                f"{what}: invoice {', '.join(ambiguous)} exists for several "
                f"{'customers' if receipt else 'suppliers'} and the counterparty does not tell which: never "
                "guessed, not exported.",
                "ambiguous_invoice",
            )
            continue
        if found:
            if _has_words(purpose, table.discount_words):
                skip(
                    i,
                    f"{what}: invoice {', '.join(x.number for x in found)} paid with a discount (Skonto): "
                    "the discount account and its VAT correction are never guessed, not exported.",
                    "discount",
                )
                continue
            if len({x.partner_ref for x in found}) > 1:
                skip(
                    i, f"{what}: the invoices cited belong to different partners: never guessed.", "mismatch"
                )
                continue
            done = sorted(
                on
                for x in found
                for r, on in x.settled_by.items()
                if r.split("#")[0] == source_ref.split("#")[0]
            )
            if done:
                # même mouvement qu'un paiement déjà exporté (relevé précédent) : jamais deux fois ;
                # résumé avec les pièces déjà exportées, par date de l'export
                skip(
                    i,
                    f"{what}: same movement as a payment already exported for invoice "
                    f"{', '.join(x.number for x in found)}: never booked twice.",
                    "already_exported",
                )
                already[done[0]] = already.get(done[0], 0) + 1
                continue
            if any(left[x.document_ref] <= 0 for x in found):
                paid = ", ".join(x.number for x in found if left[x.document_ref] <= 0)
                skip(i, f"{what}: invoice {paid} is already settled: not exported.", "settled")
                continue
            open_total = sum((left[x.document_ref] for x in found), Decimal(0))
            if bank == open_total:
                parts = [(x, left[x.document_ref]) for x in found]
            elif len(found) == 1 and bank < open_total:
                parts = [(found[0], bank)]  # paiement partiel (D9) : le reste reste ouvert
                partial.append((i, found[0].number, open_total - bank))
            else:
                skip(
                    i,
                    f"{what}: {_money(bank)} differs from the open amount {_money(open_total)} of invoice "
                    f"{', '.join(x.number for x in found)}: never reconciled, not exported.",
                    "amount_differs",
                )
                continue
            partner = found[0]
            sign = (
                Decimal(-1) if receipt else Decimal(1)
            )  # entrée : le client au crédit ; sortie : le fournisseur au débit
            lines = [
                DocumentLine(
                    kind=LineKind.ITEM,
                    amount=sign * paid,
                    gross_amount=sign * paid,
                    settles=SettlementRef(source_ref=x.document_ref, number=x.number),
                    description=name or purpose or None,
                )
                for x, paid in parts
            ]
            documents.append(
                (
                    i,
                    AccountingDocument(
                        source_ref=source_ref,
                        document_type=line_kind,
                        status=DocumentStatus.POSTED,
                        number=parts[0][0].number,
                        accounting_date=when,
                        document_date=when,
                        currency=val(i, "currency") or currency,
                        partner=PartnerRef(
                            source_ref=partner.partner_ref,
                            role=PartnerRole(partner.role),
                            name=partner.partner_name or name or partner.partner_ref,
                        ),
                        total_amount=bank,
                        tax_amount=Decimal(0),
                        lines=lines,
                        description=name or purpose or None,
                    ),
                )
            )
            settlements[source_ref] = [(x.document_ref, paid) for x, paid in parts]
            for x, paid in parts:
                left[x.document_ref] -= paid
            continue
        # 2. mouvement hors factures (mots du motif, de la contrepartie ou du libellé de la banque)
        text = " ".join(p for p in (purpose, name, val(i, "bank_text")) if p)
        category = next((c for c in table.bank_categories if _has_words(text, c.words)), None)
        if category is not None and category.never_booked:
            skip(i, f"{what} ({category.label}): {category.never_booked}, not exported.", category.key)
            continue
        if category is not None:
            cat = _category_ref(category, rows[i])
            sign = Decimal(-1) if receipt else Decimal(1)
            documents.append(
                (
                    i,
                    AccountingDocument(
                        source_ref=source_ref,
                        document_type=line_kind,
                        status=DocumentStatus.POSTED,
                        number=val(i, "bank_reference") or f"BANK-{when:%Y%m%d}",
                        accounting_date=when,
                        document_date=when,
                        currency=val(i, "currency") or currency,
                        total_amount=bank,
                        tax_amount=Decimal(0),
                        lines=[
                            DocumentLine(
                                kind=LineKind.ITEM,
                                amount=sign * bank,
                                gross_amount=sign * bank,
                                category=cat,
                                description=purpose or name or None,
                            )
                        ],
                        description=purpose or name or None,
                    ),
                )
            )
            continue
        # 3. référence qui a la forme d'une facture du dossier, mais inconnue du registre
        unknown = [t for t in _tokens(purpose) if _shape(t) in shapes.get(role, set())]
        if unknown:
            same = [
                x
                for x in by_role.get(role, [])
                if left[x.document_ref] == bank
                and ((iban and x.partner_iban == iban) or _same_name(x.partner_name, name))
            ]
            if len(same) == 1:
                target = same[0]
                key = f"{CONFIRM_KEY}{fp}"
                answer = choices.get(key)
                questions.append(
                    ValueQuestion(
                        key=key,
                        kind="bank_confirm",
                        field="purpose",
                        column=columns.get("purpose"),
                        title=f"{what}: is it invoice {target.number}?",
                        help=f"The payment reference cites {', '.join(unknown)}, which is not an exported "
                        f"invoice of this folder; invoice {target.number} of the same partner is open for "
                        f"the same amount ({_money(bank)}). Never reconciled without your answer.",
                        count=1,
                        options=[
                            QuestionOption(value="settle", label=f"Yes: it pays invoice {target.number}"),
                            QuestionOption(value="skip", label="No: do not export it"),
                        ],
                        answer=answer if answer in ("settle", "skip") else None,
                    )
                )
                if answer != "settle":
                    skip(
                        i,
                        f"{what}: invoice {', '.join(unknown)} is unknown; invoice {target.number} has the "
                        "same amount"
                        + (
                            " (your choice): not exported."
                            if answer == "skip"
                            else ", waiting for your answer above: not exported."
                        ),
                        "bank_confirm",
                    )
                    continue
                sign = Decimal(-1) if receipt else Decimal(1)
                documents.append(
                    (
                        i,
                        AccountingDocument(
                            source_ref=source_ref,
                            document_type=line_kind,
                            status=DocumentStatus.POSTED,
                            number=target.number,
                            accounting_date=when,
                            document_date=when,
                            currency=val(i, "currency") or currency,
                            partner=PartnerRef(
                                source_ref=target.partner_ref,
                                role=PartnerRole(target.role),
                                name=target.partner_name or name or target.partner_ref,
                            ),
                            total_amount=bank,
                            tax_amount=Decimal(0),
                            lines=[
                                DocumentLine(
                                    kind=LineKind.ITEM,
                                    amount=sign * bank,
                                    gross_amount=sign * bank,
                                    settles=SettlementRef(
                                        source_ref=target.document_ref, number=target.number
                                    ),
                                    description=name or purpose or None,
                                )
                            ],
                            description=name or purpose or None,
                        ),
                    )
                )
                settlements[source_ref] = [(target.document_ref, bank)]
                left[target.document_ref] -= bank
                continue
            skip(
                i,
                f"{what}: invoice {', '.join(unknown)} is not in the export journal of this folder (never "
                "exported): never reconciled, not exported.",
                "unknown_invoice",
            )
            continue
        skip(
            i,
            f"{what}: no exported invoice and no rule for this movement: never guessed, not exported.",
            "unknown_payment",
        )

    issues += _check_balance(rows, balances, columns)
    if partial:
        issues.append(
            ValueIssue(
                field="amount",
                column=columns.get("amount", ""),
                kind="bank_partial",
                count=len(partial),
                examples=[],
                message=say("{n} partial [payment|payments]", len(partial))
                + ": "
                + "; ".join(f"invoice {n}, {_money(rest)} still open" for _, n, rest in partial)
                + ".",
                rows=[i + 1 for i, _, _ in partial],
                severity="info",
            )
        )
    categories = {}
    scopes = {}
    for _, d in documents:
        for line in d.lines:
            if line.category is not None:
                categories[line.category.source_ref] = line.category.name or line.category.source_ref
                scopes[line.category.source_ref] = d.document_type.scope
    return BankTable(
        documents=[d for _, d in documents],
        row_of={d.source_ref: i + 1 for i, d in documents},
        issues=issues,
        questions=questions,
        skipped=sorted(skipped, key=lambda e: e.row),
        categories=categories,
        category_scope=scopes,
        settlements=settlements,
        already_exported=already,
    )


__all__ = ["CONFIRM_KEY", "BankTable", "documents_from_bank_rows", "fingerprint"]
