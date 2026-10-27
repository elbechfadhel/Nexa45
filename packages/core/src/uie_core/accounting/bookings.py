"""Écritures dérivées d'une pièce comptable du CDM, selon un modèle d'écriture GÉNÉRIQUE (aucun format
cible).

Modèle « gross_by_account_and_tax » (brut par compte et par taxe) : les lignes ITEM d'une pièce sont
regroupées par (compte de contrepartie, taxe) ; chaque groupe donne UNE écriture dont le montant est le total
TTC du groupe, passée entre le compte du tiers (client / fournisseur, côté de la ligne d'échéance)
et le compte de contrepartie du groupe. Les lignes TAX et OPEN_ITEM ne deviennent pas des écritures : elles
servent aux CONTRÔLES (la taxe de chaque groupe = ses lignes TAX ; le total des groupes = la partie du
tiers). Rien n'est corrigé ni arrondi : une incohérence est signalée et l'écriture n'est pas prête.

Le signe reste celui du CDM (débit > 0, crédit < 0) : `gross_amount` est signé côté contrepartie ;
`partner_side` est le sens du compte du tiers (opposé). Aucune convention d'un format cible ici.

Source sans plan comptable (CDM 1.1) : une ligne ITEM sans compte est regroupée par (CATÉGORIE, taux de TVA) ;
l'écriture porte `counter_category` (jamais un compte) et `tax_rate` — sa contrepartie et sa clé de taxe sont
des correspondances que la CIBLE fait confirmer. Une pièce sans ligne TAX ni OPEN_ITEM est contrôlée sur ses
TOTAUX déclarés (TTC et TVA) ; aucune ligne de taxe ni de tiers n'est fabriquée.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict
from uie_cdm import AccountingDocument, LedgerAccountRef, LineKind, PostingCategoryRef, SettlementRef, TaxRef

ZERO = Decimal("0")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Side(StrEnum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


class BookingIssue(_Model):
    code: str
    message: str  # phrase simple (anglais)


class Booking(_Model):
    index: int  # 1..n dans la pièce, ordre de première apparition du groupe
    counter_account: LedgerAccountRef | None  # None : ligne(s) classée(s) par catégorie (CDM 1.1)
    tax: TaxRef | None
    counter_category: PostingCategoryRef | None = None  # catégorie de la source, sans compte
    # paiement (CDM 1.4) : facture réglée ; la contrepartie est le compte du partenaire, choisi par la cible
    settles: SettlementRef | None = None
    tax_rate: Decimal | None = None  # taux de TVA quand la source ne donne qu'un taux (sans TaxRef)
    net_amount: Decimal  # signé, côté contrepartie (somme des montants HT du groupe)
    gross_amount: Decimal  # signé, côté contrepartie (somme des montants TTC du groupe)
    partner_side: Side  # sens du compte du tiers pour cette écriture
    lines: list[int]  # positions (0..n-1) des lignes ITEM du groupe dans `document.lines` : provenance

    @property
    def amount(self) -> Decimal:
        """Montant de l'écriture, toujours positif (le sens est `partner_side`)."""
        return abs(self.gross_amount)

    @property
    def tax_amount(self) -> Decimal:
        return self.gross_amount - self.net_amount


class BookingSet(_Model):
    bookings: list[Booking]
    issues: list[BookingIssue]  # incohérences : aucune écriture de la pièce n'est prête
    partner_total: Decimal  # somme signée des lignes OPEN_ITEM (partie du tiers)


def gross_bookings(doc: AccountingDocument) -> BookingSet:
    """Écritures TTC par (compte ou catégorie, taxe ou taux) d'une pièce, avec les contrôles d'équilibre et de
    taxe (lignes TAX / OPEN_ITEM, ou totaux déclarés de la pièce quand la source n'en a pas)."""
    issues: list[BookingIssue] = []
    groups: dict[tuple[str, str], list[int]] = {}
    for i, line in enumerate(doc.lines):
        if line.kind == LineKind.ROUNDING:
            issues.append(BookingIssue(code="ROUNDING_LINE", message="Rounding lines are not supported yet"))
        if line.kind != LineKind.ITEM:
            continue
        if len(line.taxes) > 1:
            issues.append(BookingIssue(code="MULTIPLE_TAXES", message="A line has several taxes"))
            continue
        if line.gross_amount is None:
            issues.append(BookingIssue(code="MISSING_GROSS", message="A line has no amount including tax"))
            continue
        if line.account is not None:
            counter = f"account:{line.account.source_ref}"
        elif line.settles is not None:
            counter = f"settles:{line.settles.source_ref}"  # une écriture par facture réglée
        else:
            assert line.category is not None  # garanti par le CDM 1.1
            counter = f"category:{line.category.source_ref}"
        if line.taxes:
            tax = f"tax:{line.taxes[0].source_ref}"
        elif line.tax_rate is not None:
            tax = f"rate:{line.tax_rate.normalize()}"
        else:
            tax = ""
        groups.setdefault((counter, tax), []).append(i)

    bookings: list[Booking] = []
    for n, ((_, _), idx) in enumerate(groups.items(), start=1):
        first = doc.lines[idx[0]]
        gross = sum((doc.lines[i].gross_amount or ZERO for i in idx), ZERO)
        net = sum((doc.lines[i].amount for i in idx), ZERO)
        if gross == 0:
            issues.append(BookingIssue(code="ZERO_AMOUNT", message="A booking would have a zero amount"))
            continue
        bookings.append(
            Booking(
                index=n,
                counter_account=first.account,
                tax=first.taxes[0] if first.taxes else None,
                counter_category=first.category if first.account is None else None,
                settles=first.settles,
                tax_rate=first.tax_rate if not first.taxes else None,
                net_amount=net,
                gross_amount=gross,
                partner_side=Side.DEBIT if gross < 0 else Side.CREDIT,
                lines=idx,
            )
        )
    if not bookings and not issues:
        issues.append(BookingIssue(code="NO_BOOKINGS", message="The document has no line to book"))

    if not doc.lines_of(LineKind.TAX) and not doc.lines_of(LineKind.OPEN_ITEM) and doc.missing_accounts():
        # source sans plan comptable : contrôle sur les totaux DÉCLARÉS (jamais une ligne inventée)
        gross_total = sum((b.gross_amount for b in bookings), ZERO)
        partner_total = -gross_total
        if bookings and abs(gross_total) != doc.total_amount:
            issues.append(
                BookingIssue(
                    code="TOTAL_MISMATCH", message="The bookings do not add up to the document total"
                )
            )
        if bookings and abs(sum((b.tax_amount for b in bookings), ZERO)) != doc.tax_amount:
            issues.append(
                BookingIssue(code="TAX_MISMATCH", message="The line taxes do not add up to the document tax")
            )
        return BookingSet(bookings=bookings, issues=_unique(issues), partner_total=partner_total)

    partner_total = sum((ln.amount for ln in doc.lines_of(LineKind.OPEN_ITEM)), ZERO)
    if bookings and sum((b.gross_amount for b in bookings), ZERO) + partner_total != 0:
        issues.append(
            BookingIssue(
                code="UNBALANCED_BOOKINGS",
                message="The bookings do not add up to the amount due by the partner",
            )
        )
    # taxe de chaque groupe = lignes TAX de cette taxe (même signe que la contrepartie)
    tax_lines: dict[str, Decimal] = {}
    for ln in doc.lines_of(LineKind.TAX):
        key = ln.taxes[0].source_ref if ln.taxes else ""
        tax_lines[key] = tax_lines.get(key, ZERO) + ln.amount
    by_tax: dict[str, Decimal] = {}
    for b in bookings:
        key = b.tax.source_ref if b.tax else ""
        by_tax[key] = by_tax.get(key, ZERO) + b.tax_amount
    if bookings and {k: v for k, v in by_tax.items() if v} != {k: v for k, v in tax_lines.items() if v}:
        issues.append(
            BookingIssue(
                code="TAX_MISMATCH",
                message="The tax lines do not match the taxes included in the line amounts",
            )
        )
    return BookingSet(bookings=bookings, issues=_unique(issues), partner_total=partner_total)


def _unique(issues: list[BookingIssue]) -> list[BookingIssue]:
    seen: set[str] = set()
    out = []
    for i in issues:
        if i.code not in seen:
            seen.add(i.code)
            out.append(i)
    return out
