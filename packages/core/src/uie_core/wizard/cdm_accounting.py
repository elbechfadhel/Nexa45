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
- taux de 0 % d'une vente : justifié par l'état TVA du client retenu à l'export des clients (USt-IdNr valide
  d'un autre pays de l'UE, ou pays hors UE) et classé par zone et NATURE (Leistungsart, sinon une question) ;
  USt-IdNr invalide ou absente, client du pays du dossier, client inconnu → non exportée avec son motif.
Factures d'ACHAT (`purchase`) : fournisseur (numéro de fournisseur), pièces VENDOR_BILL / VENDOR_CREDIT_NOTE,
charge au débit (le fournisseur au crédit), avoir fournisseur en sens inverse ; référence de la pièce = numéro
du fournisseur + numéro de sa facture (un numéro n'est unique que chez un fournisseur).
Références de la source préfixées « file: » : jamais confondues avec celles d'un système connecté."""

from __future__ import annotations

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
    SupplyKind,
)

from uie_core.mapping.suggestions import category_key, vat_category_ref
from uie_core.sources.table import FileTableSpec
from uie_core.wizard.text import say
from uie_core.wizard.values import ExcludedRow, QuestionOption, ValueIssue, ValueQuestion, ValueTransform

FILE_REF = "file:"
NUMBER_SPACES_KEY = "accounting:number_spaces"  # numéros avec des espaces : tiret ou tels quels (décision)
SUPPLY_KIND_KEY = "accounting:supply_kind"  # nature inconnue d'opérations avec l'étranger (décision)
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
    transforms: list[ValueTransform] = []  # transformations décidées (numéro : espace → tiret)


def category(rate: Decimal) -> PostingCategoryRef:
    """Catégorie d'une vente à ce taux : la cible lui fait correspondre un compte de produits."""
    r = rate.normalize()
    return PostingCategoryRef(source_ref=f"sale:{r:f}", name=f"Domestic sales at {r:f} % VAT")


def purchase_category(rate: Decimal, expense: str | None = None) -> PostingCategoryRef:
    """Catégorie d'un achat : sa catégorie de charge (« Kostenart ») à ce taux, sinon le taux seul (fichier
    sans colonne de catégorie). La cible lui fait correspondre un compte de charges, jamais deviné."""
    r = rate.normalize()
    if expense:
        return PostingCategoryRef(
            source_ref=f"purchase:{category_key(expense)}:{r:f}", name=f"{expense} at {r:f} % VAT"
        )
    return PostingCategoryRef(source_ref=f"purchase:{r:f}", name=f"purchases at {r:f} % VAT")


# opérations avec l'étranger (sens, zone du partenaire, nature) : nom de la catégorie (aucun compte ici)
VAT_TREATMENTS = {
    ("SALE", "EU", "GOODS"): "Intra-community supplies of goods",
    ("SALE", "EU", "SERVICES"): "Services to EU businesses",
    ("SALE", "NON_EU", "GOODS"): "Exports of goods outside the EU",
    ("SALE", "NON_EU", "SERVICES"): "Services to non-EU businesses",
    ("PURCHASE", "EU", "GOODS"): "Intra-community acquisitions of goods",
    ("PURCHASE", "EU", "SERVICES"): "Services from EU businesses",
    ("PURCHASE", "NON_EU", "SERVICES"): "Services from non-EU businesses",
}


def vat_category(scope: str, zone: str, nature: SupplyKind, rate: Decimal) -> PostingCategoryRef:
    """Catégorie d'une opération avec l'étranger : la cible lui fait correspondre un compte (table des
    comptes de TVA, à valider), jamais deviné ici."""
    name = VAT_TREATMENTS[(scope, zone, nature.value)]
    return PostingCategoryRef(
        source_ref=vat_category_ref(scope, zone, nature.value, rate),
        name=f"{name} at {rate.normalize():f} % VAT",
    )


def _import_reason(number: str, who: str, where: str) -> str:
    return (
        f"Invoice {number}: goods bought from {who} outside the EU ({where}): import VAT "
        "(Einfuhrumsatzsteuer) is not supported, not exported."
    )


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
    `partner_vat` : numéro du partenaire → état de son numéro de TVA (valid, invalid, missing, non_eu) et son
    pays, retenus à l'export des clients ou des fournisseurs. Une vente à 0 % est écrite si le client a une
    USt-IdNr VALIDE d'un autre pays de l'UE, ou s'il est hors UE (catégorie par zone et nature) ; refusée
    sinon, avec son motif, jamais par une question « exporter quand même »."""
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

    # numéro de pièce avec des espaces (« Miete 07/2026 ») : certaines cibles le refusent ; le remplacer par
    # un tiret est une DÉCISION (présélectionnée), listée comme transformation, jamais faite en silence
    transforms: list[ValueTransform] = []
    spaced = [i for i, r in enumerate(rows) if re.search(r"\s", (r.get("document_number") or "").strip())]
    if spaced:
        before = [(rows[i].get("document_number") or "").strip() for i in spaced]
        after = [re.sub(r"\s+", "-", n) for n in before]
        pairs = [[a, b] for a, b in dict(zip(before, after, strict=True)).items()]
        answer = choices.get(NUMBER_SPACES_KEY)
        questions.append(
            ValueQuestion(
                key=NUMBER_SPACES_KEY,
                kind="number_spaces",
                field="document_number",
                column=columns.get("document_number"),
                title=say("{n} invoice [number contains|numbers contain] spaces", len(spaced)),
                help="Some targets do not accept spaces in a document number: these invoices would be "
                "refused.",
                count=len(spaced),
                options=[
                    QuestionOption(
                        value="hyphen", label="Replace each space by a hyphen", examples=pairs[:3]
                    ),
                    QuestionOption(value="keep", label="Keep the numbers as they are"),
                ],
                suggested="hyphen",
                suggested_by="rule",
                answer=answer if answer in ("hyphen", "keep") else None,
            )
        )
        if answer == "hyphen":
            rows = [
                {**r, "document_number": re.sub(r"\s+", "-", r["document_number"].strip())}
                if i in spaced
                else r
                for i, r in enumerate(rows)
            ]
            transforms.append(
                ValueTransform(
                    field="document_number",
                    column=columns.get("document_number", "document_number"),
                    kind="number_spaces",
                    label="Space replaced by a hyphen in the invoice number (your choice)",
                    count=len(spaced),
                    examples=pairs[:5],
                )
            )

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

    # nature de l'opération (colonne « Leistungsart ») : valeur reconnue, sinon inconnue (jamais devinée)
    kind_column = table.column("supply_kind")
    unknown_kinds: list[int] = []

    def supply_kind(i: int) -> SupplyKind | None:
        raw = val(i, "supply_kind")
        found = kind_column.value_of(raw) if kind_column else None
        if raw and found is None:
            unknown_kinds.append(i)
        return SupplyKind(found) if found else None

    zero_docs: list[tuple[int, AccountingDocument]] = []
    # opérations avec l'étranger dont la nature manque : ligne → (sens, zone, taux) ; une seule question
    no_kind: dict[int, tuple[str, str, Decimal, str]] = {}
    # en attente de cette réponse : comptes des natures POSSIBLES demandés d'avance (présélectionnés), comme
    # ceux d'une facture à 0 % en attente ; jamais appliqués sans la réponse
    pending: dict[str, str] = {}
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
        if purchase and rate > 0 and table.home_country:
            # fournisseur d'un autre pays (liste des fournisseurs exportée) qui facture de la TVA : TVA de SON
            # pays, jamais déductible comme Vorsteuer ici (règle simple, aucune table des taux étrangers)
            _, abroad = (partner_vat or {}).get(partner, (None, None))
            if abroad and abroad != table.home_country:
                skip(
                    i,
                    f"Invoice {number}: {rate.normalize():f} % VAT invoiced by "
                    f"{val(i, 'partner_name') or partner} ({abroad}): foreign VAT is not deductible in your "
                    f"country ({table.home_country}), never booked as input tax: not exported.",
                    "foreign_vat",
                )
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
            supply_kind=supply_kind(i),
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
            status, country = (partner_vat or {}).get(partner, (None, None))
            who = val(i, "partner_name") or partner
            if country is None:
                # fournisseur pas encore exporté (ou sans pays) : son pays n'est jamais deviné
                skip(
                    i,
                    f"Invoice {number}: 0 % VAT from {who}, whose country is not known: export your "
                    "suppliers first, not exported.",
                    "zero_rate",
                )
                continue
            if not (table.home_country and country == table.home_country):
                # achat à l'étranger : acquisition intracommunautaire ou autoliquidation (§ 13b), selon la
                # zone du fournisseur et la NATURE de l'achat ; jamais un achat national sans TVA
                if status in ("invalid", "missing"):
                    what = "is not valid" if status == "invalid" else "is missing"
                    skip(
                        i,
                        f"Invoice {number}: 0 % VAT from {who} ({country}), but its VAT ID {what} in your "
                        "supplier list: an intra-community acquisition or reverse charge cannot be "
                        "justified, not exported.",
                        "vat_abroad",
                    )
                    continue
                zone = "NON_EU" if status == "non_eu" else "EU"
                nature = line.supply_kind
                if nature is None:
                    no_kind[i] = (
                        "PURCHASE",
                        zone,
                        rate,
                        country,
                    )  # décision de l'utilisateur, jamais présélectionnée
                elif ("PURCHASE", zone, nature.value) not in VAT_TREATMENTS:
                    skip(i, _import_reason(number, who, country), "import_vat")
                    continue
                else:
                    cat = vat_category("PURCHASE", zone, nature, rate)
                    doc = doc.model_copy(update={"lines": [line.model_copy(update={"category": cat})]})
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
            zone = (
                "EU"
                if status == "valid" and country and country != table.home_country
                else "NON_EU"
                if status == "non_eu"
                else None
            )
            if zone is None:
                # client du pays du dossier : aucune exonération que ce fichier puisse justifier ; client pas
                # encore exporté (ou sans pays) : son pays n'est jamais deviné. Jamais une question « exporter
                # quand même » : refusée avec son motif
                if country and country == table.home_country:
                    skip(
                        i,
                        f"Invoice {number}: 0 % VAT for {who}, a customer in your own country ({country}): a "
                        "domestic sale without VAT cannot be justified by this file, not exported.",
                        "zero_rate_domestic",
                    )
                else:
                    skip(
                        i,
                        f"Invoice {number}: 0 % VAT for {who}, whose country is not known: export your "
                        "customers first, not exported.",
                        "zero_rate",
                    )
                continue
            else:
                # client étranger justifié : le compte dépend de la NATURE de l'opération (Leistungsart),
                # jamais devinée (ni depuis une remarque, ni depuis le montant)
                nature = line.supply_kind
                if nature is None:
                    no_kind[i] = (
                        "SALE",
                        zone,
                        rate,
                        country or "",
                    )  # décision de l'utilisateur, jamais présélectionnée
                else:
                    cat = vat_category("SALE", zone, nature, rate)
                    doc = doc.model_copy(update={"lines": [line.model_copy(update={"category": cat})]})
        candidates.append((i, doc))

    if no_kind:
        listed = ", ".join(
            f"{val(i, 'document_number')} ({val(i, 'partner_name') or val(i, pkey)})"
            for i in list(no_kind)[:5]
        )
        answer = choices.get(SUPPLY_KIND_KEY)
        q = ValueQuestion(
            key=SUPPLY_KIND_KEY,
            kind="supply_kind",
            field="supply_kind",
            column=columns.get("supply_kind"),
            title=say("{n} [invoice|invoices] with another country: goods or services?", len(no_kind)),
            help=f"{listed}. The account depends on it and it is never guessed. If they differ, add a "
            "Leistungsart column (Ware / Dienstleistung) to your file.",
            count=len(no_kind),
            options=[
                QuestionOption(value=SupplyKind.GOODS.value, label="Goods (Ware)"),
                QuestionOption(value=SupplyKind.SERVICES.value, label="Services (Dienstleistung)"),
                QuestionOption(value="skip", label="Do not export them"),
            ],
            answer=answer if answer in (SupplyKind.GOODS.value, SupplyKind.SERVICES.value, "skip") else None,
        )
        questions.append(q)
        if q.answer in (SupplyKind.GOODS.value, SupplyKind.SERVICES.value):
            nature = SupplyKind(q.answer)
            answered = []
            for i, d in candidates:
                if i in no_kind:
                    scope, zone, rate, where = no_kind[i]
                    if (scope, zone, nature.value) not in VAT_TREATMENTS:
                        # marchandise achetée hors UE (TVA à l'importation) : non prise en charge
                        skip(i, _import_reason(d.number or "", d.partner.name, where), "import_vat")
                        continue
                    cat = vat_category(scope, zone, nature, rate)
                    d = d.model_copy(update={"lines": [d.lines[0].model_copy(update={"category": cat})]})
                answered.append((i, d))
            candidates = answered
        else:
            for i in no_kind:
                skip(
                    i,
                    f"Invoice {val(i, 'document_number')}: goods or services is not known (Leistungsart), "
                    + (
                        "not exported (your choice)."
                        if q.answer == "skip"
                        else "waiting for your answer above."
                    ),
                    "supply_kind",
                )
            zero_docs += [(i, d) for i, d in candidates if i in no_kind]
            candidates = [(i, d) for i, d in candidates if i not in no_kind]
            if q.answer is None:
                for scope, zone, rate, _ in no_kind.values():
                    for nature in SupplyKind:
                        if (scope, zone, nature.value) in VAT_TREATMENTS:
                            cat = vat_category(scope, zone, nature, rate)
                            pending[cat.source_ref] = cat.name or cat.source_ref

    if unknown_kinds:
        found = list(dict.fromkeys(val(i, "supply_kind") for i in unknown_kinds))
        issues.append(
            ValueIssue(
                field="supply_kind",
                column=columns.get("supply_kind", "supply_kind"),
                kind="supply_kind_unknown",
                count=len(unknown_kinds),
                examples=found[:5],
                message=say(
                    "{n} kind of supply [is|are] neither goods nor services: treated as unknown, never "
                    "guessed.",
                    len(unknown_kinds),
                ),
                rows=[i + 1 for i in unknown_kinds],
                severity="to_check",
            )
        )

    every = [line for _, d in candidates for line in d.lines] + [
        line for i, d in zero_docs for line in d.lines if i not in no_kind
    ]
    waiting = [line for i, d in zero_docs for line in d.lines if i in no_kind]
    documents = [d for _, d in candidates]
    rates = sorted(
        {f"{x.tax_rate.normalize():f}" for x in [*every, *waiting] if x.tax_rate is not None},
        key=float,
        reverse=True,
    )
    return InvoiceTable(
        scope="PURCHASE" if purchase else "SALE",
        transforms=transforms,
        categories={
            x.category.source_ref: x.category.name or x.category.source_ref for x in every if x.category
        }
        | pending,
        rates=rates,
        documents=documents,
        row_of={d.source_ref: i + 1 for i, d in candidates},
        issues=issues,
        questions=questions,
        skipped=sorted(skipped, key=lambda e: e.row),
    )


__all__ = [
    "FILE_REF",
    "InvoiceTable",
    "category",
    "documents_from_rows",
    "purchase_category",
]
