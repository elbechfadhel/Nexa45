"""Pièces comptables d'une SOURCE À SCHÉMA FIXE → Accounting CDM (lecture seule, aucun nom de système).

Le sens des champs vient d'un binding de source (données) ; ce module ne fait que construire le CDM avec ce
qui existe VRAIMENT dans la source. Une source qui comptabilise par catégorie et par taux, sans plan
comptable, donne (Accounting CDM 1.1) :
- des lignes ITEM SANS compte, avec leur catégorie, montant hors taxe et TTC, TVA et taux (signes du CDM :
  vente et avoir fournisseur au crédit, achat et avoir client au débit) ;
- AUCUNE ligne TAX ni OPEN_ITEM (jamais de compte inventé) ;
- aucune anomalie de lecture pour l'absence de compte (1b.3) : c'est la CIBLE qui exige une correspondance
  catégorie → compte CONFIRMÉE et bloque les écritures sans correspondance ; les champs du CDM impossibles à
  remplir sont listés une fois (`cdm_gaps`) ;
- les lignes contrôlées (somme des lignes = total de la pièce), jamais corrigées.
Rien n'est deviné : valeur absente, hors traduction ou illisible → anomalie, jamais une valeur par défaut."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError
from uie_cdm import (
    AccountingDocument,
    DocumentLine,
    DocumentStatus,
    DocumentType,
    LineKind,
    PartnerRef,
    PostingCategoryRef,
)

from uie_core.sources import SourceBinding, SourceBindingError, SourceEntity, Unmapped

# Clés d'une pièce et de ses lignes qu'un binding de source peut déclarer (valeurs, jamais des liens)
DOCUMENT_KEYS = frozenset(
    {
        "document_number",
        "document_reference",
        "document_type",
        "document_status",
        "partner",  # identifiant du partenaire dans la source (même référence que le Partner CDM)
        "partner_name",
        "description",
        "accounting_date",
        "document_date",
        "due_date",
        "currency_code",
        "total_amount",
        "tax_amount",
        "amounts_include_tax",  # « true » : montants des lignes taxes comprises
    }
)
# lignes : faits de la source contrôlés ; aucune clé de compte (aucune ligne du CDM construite ici)
LINE_KEYS = frozenset({"line_amount", "line_tax_amount", "line_tax_rate", "line_category"})
REQUIRED = ("document_number", "document_type", "document_status", "accounting_date", "total_amount")
ENUMS = {"document_type": DocumentType, "document_status": DocumentStatus}


def document_spec(binding: SourceBinding) -> SourceEntity:
    """Entité DOCUMENT du binding, validée : clés connues, traductions vers des valeurs CANONIQUES."""
    spec = binding.entity("DOCUMENT")
    if spec is None:
        raise SourceBindingError(f"{binding.name}: no DOCUMENT entity")
    keys = {f.key for f in spec.fields}
    unknown = sorted(keys - DOCUMENT_KEYS)
    if spec.items is not None:
        unknown += sorted({f.key for f in spec.items.fields} - LINE_KEYS)
    if unknown:
        raise SourceBindingError(f"{binding.name}: unknown accounting key(s) {unknown}")
    missing = [k for k in REQUIRED if k not in keys]
    if missing:
        raise SourceBindingError(f"{binding.name}: required key(s) missing {missing}")
    for f in spec.fields:
        enum = ENUMS.get(f.key)
        if enum and f.map:
            bad = sorted(v for v in f.map.values() if v not in enum.__members__)
            if bad:
                raise SourceBindingError(f"{binding.name}: {f.key} maps to unknown value(s) {bad}")
    return spec


def cdm_gaps(binding: SourceBinding) -> list[tuple[str, str]]:
    """Champs de l'Accounting CDM qu'un binding ne PEUT PAS remplir sans inventer : (champ, raison)."""
    spec = document_spec(binding)
    line_keys = {f.key for f in spec.items.fields} if spec.items else set()
    keys = {f.key for f in spec.fields}
    reason = "The source has no ledger account on its lines"
    if "line_category" in line_keys:
        reason += ": lines keep their posting category; a confirmed category → account mapping is required"
    gaps = [
        ("lines[ITEM].account", reason),
        ("lines[TAX]", "The source has no tax lines (only a tax amount and rate per line) nor tax accounts"),
        ("lines[OPEN_ITEM]", "The source has no partner line nor receivable / payable account"),
        (
            "lines[].taxes[]",
            "The source gives a tax rate only (tax_rate): no tax identity (code, name, scope)",
        ),
    ]
    if "accounting_date" in keys and "document_date" in keys:
        acc = next(f for f in spec.fields if f.key == "accounting_date")
        doc = next(f for f in spec.fields if f.key == "document_date")
        if acc.paths() == doc.paths():
            gaps.append(
                ("accounting_date", "Same as the document date: the source has no separate posting date")
            )
    return gaps


def to_date(v: Any) -> date | None:
    """Jour d'une date ISO de la source (« 2026-01-08T00:00:00.000+01:00 » → 2026-01-08 : le jour LOCAL de
    la pièce, jamais converti de fuseau)."""
    if v is None or isinstance(v, Unmapped):
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def to_decimal(v: Any) -> Decimal | None:
    if v is None or isinstance(v, bool | Unmapped):
        return None
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return None


def text(v: Any) -> str | None:
    if v is None or isinstance(v, bool | Unmapped):
        return None
    s = str(v).strip()
    return s or None


def convert(
    rid: str,
    values: dict[str, Any],
    lines: list[dict[str, Any]],
    labels: dict[str, dict[str, str]] | None = None,
) -> tuple[
    str | None, DocumentStatus | None, DocumentType | None, AccountingDocument | None, list[tuple[str, str]]
]:
    """(numéro, statut, type, pièce CDM sans lignes ou None, anomalies (code, message)) d'une pièce."""
    issues: list[tuple[str, str]] = []
    number = text(values.get("document_number"))

    def enum(key: str) -> Any:
        v = values.get(key)
        if isinstance(v, Unmapped):
            what = "type" if key == "document_type" else "status"
            issues.append(
                (f"UNSUPPORTED_{what.upper()}", f"The document {what} « {v.raw} » is not supported")
            )
            return None
        return ENUMS[key][v] if v in ENUMS[key].__members__ else None

    doc_type, status = enum("document_type"), enum("document_status")
    partner = text(values.get("partner"))
    if partner is None:
        issues.append(("MISSING_PARTNER", "The document has no customer or supplier"))
    currency = (text(values.get("currency_code")) or "").upper()
    if len(currency) != 3 or not currency.isalpha():
        issues.append(("MISSING_CURRENCY", "The currency of the document is not recognized"))
    accounting_date = to_date(values.get("accounting_date"))
    if accounting_date is None:
        issues.append(("MISSING_DATE", "The document has no readable date"))
    total, tax = to_decimal(values.get("total_amount")), to_decimal(values.get("tax_amount"))
    if total is None:
        issues.append(("MISSING_TOTAL", "The document has no readable total"))

    cdm_lines = item_lines(doc_type, values.get("amounts_include_tax"), lines, issues, labels or {})
    if cdm_lines and total is not None:
        if sum((abs(ln.gross_amount or 0) for ln in cdm_lines), Decimal("0")) != abs(total):
            issues.append(("LINES_MISMATCH", "The lines do not add up to the document total"))

    document = None
    if doc_type is not None and status is not None and partner and accounting_date and total is not None:
        if not any(code == "MISSING_CURRENCY" for code, _ in issues):
            try:
                document = AccountingDocument(
                    source_ref=rid,
                    document_type=doc_type,
                    status=status,
                    number=number,
                    reference=text(values.get("document_reference")),
                    accounting_date=accounting_date,
                    document_date=to_date(values.get("document_date")),
                    due_date=to_date(values.get("due_date")),
                    currency=currency,
                    partner=PartnerRef(
                        source_ref=partner,
                        role=doc_type.partner_role,
                        name=text(values.get("partner_name")) or partner,
                    ),
                    total_amount=abs(total),
                    tax_amount=abs(tax or Decimal("0")),
                    lines=cdm_lines,  # ITEM par catégorie ; jamais de ligne TAX / OPEN_ITEM inventée
                    description=text(values.get("description")),
                )
            except (ValidationError, ValueError) as exc:
                issues.append(("INVALID_DOCUMENT", f"The document cannot be represented ({exc})"))
    return number, status, doc_type, document, issues


# vente et avoir fournisseur : contrepartie au CRÉDIT (−) ; achat et avoir client : au DÉBIT (+)
CREDIT_SIDE = (DocumentType.CUSTOMER_INVOICE, DocumentType.VENDOR_CREDIT_NOTE)


def item_lines(
    doc_type: DocumentType | None,
    gross: Any,
    lines: list[dict[str, Any]],
    issues: list[tuple[str, str]],
    labels: dict[str, dict[str, str]] | None = None,
) -> list[DocumentLine]:
    """Lignes ITEM du CDM 1.1 (catégorie, sans compte) ; une ligne illisible → aucune ligne (jamais une pièce
    partielle) et anomalie. `gross` : « true » montants TTC, « false » montants HT (+ TVA)."""
    if not lines or doc_type is None:
        return []
    if gross not in ("true", "false"):
        issues.append(("LINE_UNREADABLE", "The tax mode of the lines (net or gross) is not recognized"))
        return []
    sign = Decimal("-1") if doc_type in CREDIT_SIDE else Decimal("1")
    out: list[DocumentLine] = []
    for ln in lines:
        amount, tax = to_decimal(ln.get("line_amount")), to_decimal(ln.get("line_tax_amount"))
        rate, category = to_decimal(ln.get("line_tax_rate")), text(ln.get("line_category"))
        if amount is None or tax is None or category is None:
            issues.append(("LINE_UNREADABLE", "A line has no readable amount, tax amount or category"))
            return []
        net, total = (amount - tax, amount) if gross == "true" else (amount, amount + tax)
        try:
            out.append(
                DocumentLine(
                    kind=LineKind.ITEM,
                    # nom lu dans la source pour l'affichage ; l'identifiant reste la référence décidée
                    category=PostingCategoryRef(
                        source_ref=category, name=(labels or {}).get("line_category", {}).get(category)
                    ),
                    amount=sign * net,
                    gross_amount=sign * total,
                    tax_rate=rate,
                    tax_amount=sign * tax,
                )
            )
        except (ValidationError, ValueError):
            issues.append(("LINE_UNREADABLE", "A line cannot be represented"))
            return []
    return out
