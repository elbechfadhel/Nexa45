"""Lignes d'un journal de factures de sortie, ou d'achat, → Accounting CDM 1.1 (source FICHIER, aucun
système).

Une ligne = une facture (ou un avoir) à UN taux de TVA, sans plan comptable : une ligne ITEM classée par
CATÉGORIE (« ventes à 19 % »), avec son taux et sa TVA ; la cible fait confirmer la correspondance
catégorie → compte (jamais devinée ici). Le brut fait foi (un brut négatif = un avoir) ; le net et la TVA
servent au contrôle.

Rien n'est corrigé ni deviné ; une facture qui ne peut pas être écrite est LISTÉE avec son motif :
- numéro de facture ou numéro de client absent ;
- date illisible ;
- brut ≠ net + TVA (au-delà d'un centime d'arrondi) ;
- taux de TVA non admis dans le pays à la date de la facture (16 % en 2026) ;
- même numéro de facture deux fois : contenu identique → écrite une fois (la seconde listée) ; contenu
  différent → aucune des deux ;
- taux de 0 % : livraison intracommunautaire (ou autre exonération) à justifier par l'USt-IdNr du client,
  que ce fichier ne contient pas → DÉCISION de l'utilisateur pour ces factures, jamais automatique.
Factures d'ACHAT (`purchase`) : fournisseur (numéro de fournisseur), pièces VENDOR_BILL / VENDOR_CREDIT_NOTE,
charge au débit (le fournisseur au crédit), avoir fournisseur en sens inverse ; référence de la pièce = numéro
du fournisseur + numéro de sa facture (un numéro n'est unique que chez un fournisseur).
Références de la source préfixées « file: » : jamais confondues avec celles d'un système connecté."""

from __future__ import annotations

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
)

from uie_core.mapping.suggestions import category_key
from uie_core.sources.table import FileTableSpec
from uie_core.wizard.text import say
from uie_core.wizard.values import ExcludedRow, QuestionOption, ValueIssue, ValueQuestion

FILE_REF = "file:"
ZERO_RATE_KEY = "accounting:zero_rate"  # factures à 0 % : à exporter ou non (décision)
CENT = Decimal("0.01")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InvoiceTable(_Model):
    documents: list[AccountingDocument]
    row_of: dict[str, int]  # source_ref → ligne du fichier
    issues: list[ValueIssue]
    questions: list[ValueQuestion]
    skipped: list[ExcludedRow]
    # catégories de TOUTES les factures lisibles (0 % compris, même en attente de décision) : leurs comptes
    # sont demandés une fois, présélectionnés
    categories: dict[str, str] = {}
    rates: list[str] = []
    scope: str = "SALE"  # SALE | PURCHASE : sens des pièces (clés de taxe différentes chez une cible)


def category(rate: Decimal) -> PostingCategoryRef:
    """Catégorie d'une vente à ce taux : la cible lui fait correspondre un compte de produits."""
    r = rate.normalize()
    return PostingCategoryRef(source_ref=f"sale:{r:f}", name=f"sales at {r:f} % VAT")


def purchase_category(rate: Decimal, expense: str | None = None) -> PostingCategoryRef:
    """Catégorie d'un achat : sa catégorie de charge (« Kostenart ») à ce taux, sinon le taux seul (fichier
    sans colonne de catégorie). La cible lui fait correspondre un compte de charges, jamais deviné."""
    r = rate.normalize()
    if expense:
        return PostingCategoryRef(
            source_ref=f"purchase:{category_key(expense)}:{r:f}", name=f"{expense} at {r:f} % VAT"
        )
    return PostingCategoryRef(source_ref=f"purchase:{r:f}", name=f"purchases at {r:f} % VAT")


def _amount(text: str) -> Decimal | None:
    try:
        return Decimal(text) if text else None
    except InvalidOperation:
        return None


def documents_from_rows(  # noqa: C901, PLR0912, PLR0915
    table: FileTableSpec,
    rows: list[dict[str, str]],
    columns: dict[str, str],
    currency: str,
    choices: dict[str, str] | None = None,
    partner_vat: dict[str, tuple[str | None, str | None]] | None = None,
    purchase: bool = False,
) -> InvoiceTable:
    """`rows` : une entrée par ligne (clé du tableau → valeur mise en forme : nombres en notation standard,
    dates ISO) ; `currency` : devise des montants (choisie ou lue) ; `choices` : réponses aux décisions ;
    `partner_vat` : numéro du client → état de son numéro de TVA retenu à l'export des clients. Une facture à
    0 % est écrite d'office si le client a un numéro de TVA VALIDE d'un autre pays de l'UE, refusée s'il est
    invalide ou absent, sinon décidée par l'utilisateur."""
    choices = choices or {}
    # ventes : numéro du client ; achats : numéro du fournisseur
    pkey, who_kind = ("supplier_number", "supplier") if purchase else ("partner_number", "customer")
    # achats avec une colonne de catégorie de charge : chaque facture doit en avoir une (compte jamais deviné)
    with_expense = purchase and (
        "expense_category" in columns or any(r.get("expense_category") for r in rows)
    )
    skipped: list[ExcludedRow] = []
    issues: list[ValueIssue] = []
    questions: list[ValueQuestion] = []

    def val(i: int, key: str) -> str:
        return (rows[i].get(key) or "").strip()

    def skip(i: int, reason: str, kind: str) -> None:
        skipped.append(ExcludedRow(row=i + 1, reason=reason, kind=kind))

    used = [i for i in range(len(rows)) if any(rows[i].values())]
    # même numéro de facture plusieurs fois : contenu identique = saisie en double ; sinon conflit. Achat : le
    # numéro est celui du FOURNISSEUR, unique chez lui seulement (deux fournisseurs peuvent avoir « 1001 »)
    by_number: dict[tuple[str, str], list[int]] = {}
    for i in used:
        if val(i, "document_number"):
            key = (val(i, pkey) if purchase else "", val(i, "document_number"))
            by_number.setdefault(key, []).append(i)
    content_keys = ("document_date", pkey, "net_amount", "tax_rate", "tax_amount", "gross_amount")
    # ligne → (motif, sorte) : « duplicate » = lignes IDENTIQUES (écrite une fois) ; même numéro pour des
    # factures différentes = « probable_duplicate » (aucune n'est écrite)
    twice: dict[int, tuple[str, str]] = {}
    for (supplier, number), idx in by_number.items():
        if len(idx) < 2:
            continue
        same = all(
            tuple(val(j, k) for k in content_keys) == tuple(val(idx[0], k) for k in content_keys) for j in idx
        )
        of = f" of supplier {supplier}" if supplier else ""
        for j in idx[1:] if same else idx:
            twice[j] = (
                (
                    f"Invoice {number}{of} entered twice (same content as row {idx[0] + 1}): exported once.",
                    "duplicate",
                )
                if same
                else (
                    f"Invoice number {number}{of} is used for different invoices: none of them is exported.",
                    "probable_duplicate",
                )
            )

    zero_rows: list[int] = []
    zero_docs: list[tuple[int, AccountingDocument]] = []
    candidates: list[tuple[int, AccountingDocument]] = []
    for i in used:
        number = val(i, "document_number")
        if i in twice:
            skip(i, *twice[i])
            continue
        if not number:
            skip(i, "No invoice number (Belegfeld 1 is required): not exported.", "missing_number")
            continue
        partner = val(i, pkey)
        if not partner:
            skip(i, f"Invoice {number}: no {who_kind} number: not exported.", "missing_partner")
            continue
        try:
            when = date.fromisoformat(val(i, "document_date"))
        except ValueError:
            skip(i, f"Invoice {number}: no readable date: not exported.", "date")
            continue
        gross = _amount(val(i, "gross_amount"))
        net = _amount(val(i, "net_amount"))
        tax = _amount(val(i, "tax_amount"))
        rate = _amount(val(i, "tax_rate"))
        if gross is None or gross == 0:
            skip(i, f"Invoice {number}: no gross amount: not exported.", "amount")
            continue
        if net is not None and tax is not None and abs(net + tax - gross) > CENT:
            skip(
                i,
                f"Invoice {number}: net {net} + VAT {tax} = {net + tax}, not the gross amount {gross}: never "
                "corrected, not exported.",
                "inconsistent",
            )
            continue
        if net is None and tax is not None:
            net = gross - tax
        if tax is None and net is not None:
            tax = gross - net
        if net is None or tax is None:
            skip(i, f"Invoice {number}: neither the net amount nor the VAT amount: not exported.", "amount")
            continue
        if rate is None:
            skip(i, f"Invoice {number}: no VAT rate: not exported.", "rate")
            continue
        allowed = table.rates(table.home_country or "", when) if table.home_country else None
        if allowed is not None and rate not in allowed:
            skip(
                i,
                f"Invoice {number}: {rate.normalize():f} % is not a valid VAT rate on {when.isoformat()} "
                f"(valid: {', '.join(f'{r.normalize():f} %' for r in allowed)}): not exported.",
                "rate",
            )
            continue
        expense = val(i, "expense_category")
        if with_expense and not expense:
            skip(
                i,
                f"Invoice {number}: no expense category (Kostenart): its account is never guessed, not "
                "exported.",
                "missing_category",
            )
            continue
        credit = gross < 0
        if purchase:
            sign = Decimal(-1) if credit else Decimal(1)  # charge au débit, avoir fournisseur au crédit
            kind = DocumentType.VENDOR_CREDIT_NOTE if credit else DocumentType.VENDOR_BILL
        else:
            sign = Decimal(1) if credit else Decimal(-1)  # vente au crédit, avoir client au débit
            kind = DocumentType.CUSTOMER_CREDIT_NOTE if credit else DocumentType.CUSTOMER_INVOICE
        line = DocumentLine(
            kind=LineKind.ITEM,
            amount=sign * abs(net),
            gross_amount=sign * abs(gross),
            tax_rate=rate,
            tax_amount=sign * abs(tax),
            category=purchase_category(rate, expense or None) if purchase else category(rate),
            description=val(i, "partner_name") or None,
        )
        doc = AccountingDocument(
            # achat : le numéro n'est unique que chez son fournisseur
            source_ref=FILE_REF + (f"purchase:{partner}:{number}" if purchase else number),
            document_type=kind,
            status=DocumentStatus.POSTED,
            number=number,
            accounting_date=when,
            document_date=when,
            currency=currency,
            partner=PartnerRef(
                source_ref=FILE_REF + partner,
                role=PartnerRole.SUPPLIER if purchase else PartnerRole.CUSTOMER,
                name=val(i, "partner_name") or partner,
            ),
            total_amount=abs(gross),
            tax_amount=abs(tax),
            lines=[line],
        )
        if rate == 0 and purchase:
            # achat sans TVA d'un fournisseur du pays du dossier (loyer sans TVA, Kleinunternehmer) : charge
            # sans TVA déductible, exportée sans question ; pays lu dans la liste des fournisseurs exportée,
            # jamais dans une remarque
            _, country = (partner_vat or {}).get(partner, (None, None))
            if not (table.home_country and country == table.home_country):
                reason = f"Invoice {number}: 0 % VAT on a purchase: not supported yet, not exported."
                skip(i, reason, "zero_rate")
                continue
        elif rate == 0:
            # état du numéro de TVA du client, retenu à l'export de la liste des clients (même source fichier)
            status, country = (partner_vat or {}).get(partner, (None, None))
            who = val(i, "partner_name") or partner
            # numéro invalide, ou absent pour un client d'un autre pays : livraison intracommunautaire
            # impossible à justifier ; client du même pays sans numéro : autre exonération possible → décision
            if status == "invalid" or (status == "missing" and country and country != table.home_country):
                what = "is not valid" if status == "invalid" else "is missing"
                skip(
                    i,
                    f"Invoice {number}: 0 % VAT, but the VAT ID of {who} {what} in your customer list"
                    + (f" ({country})" if country else "")
                    + ": a tax-free intra-community delivery cannot be justified, not exported.",
                    "zero_rate_vat",
                )
                continue
            if not (status == "valid" and country and country != table.home_country):
                zero_rows.append(i)  # rien de connu (ou client du même pays) : décision de l'utilisateur
        candidates.append((i, doc))

    if zero_rows:
        listed = ", ".join(
            f"{val(i, 'document_number')} ({val(i, 'partner_name') or val(i, pkey)})" for i in zero_rows[:5]
        )
        q = ValueQuestion(
            key=ZERO_RATE_KEY,
            kind="zero_rate",
            field=None,
            column=columns.get("tax_rate"),
            title=say("{n} [invoice has|invoices have] no VAT (0 %)", len(zero_rows)),
            help=f"{listed}. A tax-free intra-community delivery needs a valid VAT ID of the customer, which "
            "this file does not contain: check it before exporting.",
            count=len(zero_rows),
            options=[
                QuestionOption(value="export", label="Export them: I checked the customers' VAT IDs"),
                QuestionOption(value="skip", label="Do not export them"),
            ],
            answer=choices.get(ZERO_RATE_KEY) if choices.get(ZERO_RATE_KEY) in ("export", "skip") else None,
        )
        questions.append(q)
        if q.answer != "export":
            for i in zero_rows:
                skip(
                    i,
                    f"Invoice {val(i, 'document_number')}: 0 % VAT"
                    + (" (your choice)" if q.answer == "skip" else ", waiting for your answer above")
                    + ": not exported.",
                    "zero_rate",
                )
            zero_docs = [(i, d) for i, d in candidates if i in zero_rows]
            candidates = [(i, d) for i, d in candidates if i not in zero_rows]

    every = [line for _, d in candidates for line in d.lines] + [
        line for i, d in zero_docs for line in d.lines
    ]
    documents = [d for _, d in candidates]
    rates = sorted(
        {f"{x.tax_rate.normalize():f}" for x in every if x.tax_rate is not None}, key=float, reverse=True
    )
    return InvoiceTable(
        scope="PURCHASE" if purchase else "SALE",
        categories={
            x.category.source_ref: x.category.name or x.category.source_ref for x in every if x.category
        },
        rates=rates,
        documents=documents,
        row_of={d.source_ref: i + 1 for i, d in candidates},
        issues=issues,
        questions=questions,
        skipped=sorted(skipped, key=lambda e: e.row),
    )


__all__ = [
    "FILE_REF",
    "ZERO_RATE_KEY",
    "InvoiceTable",
    "category",
    "documents_from_rows",
    "purchase_category",
]
