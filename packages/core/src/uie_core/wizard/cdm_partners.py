"""Lignes d'un fichier de clients / fournisseurs → Partner CDM (source FICHIER, aucun système).

Les colonnes ont déjà leur sens (clés du tableau de source du domaine, `FileTableSpec`) et leurs valeurs leur
forme (moteur de valeurs du Wizard). Ce module ne construit que ce que le fichier dit VRAIMENT :
- type d'adressat : personne morale si le nom porte une forme juridique (GmbH, AG, SARL…) ; sinon une
  DÉCISION de l'utilisateur pour ces lignes seulement (jamais devinée) ;
- pays : nom ou code connu du tableau → code ISO ; inconnu → non écrit, signalé ;
- code postal contrôlé par le motif du pays ; un code auquel il manque UN zéro initial (Excel) → décision
  (présélection : ajouter le zéro), jamais corrigé en silence ;
- numéro de TVA : espaces et points retirés (transformation listée) ; forme invalide → non écrit, signalé ;
- e-mail invalide, IBAN invalide → non écrits, signalés ;
- même numéro de TVA sur deux lignes → doublon probable, décision (présélection : n'exporter que le premier),
  jamais fusionné ;
- ligne sans numéro de client → décision (présélection : attribuer un numéro de compte libre) ;
- ligne sans nom → non exportée.
Les numéros de ligne sont ceux du fichier du client (1 = première ligne de données)."""

from __future__ import annotations

import re
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict
from uie_cdm import (
    Address,
    BankAccount,
    ContactInfo,
    Identifier,
    IdentifierScheme,
    Partner,
    PartnerKind,
    PartnerRole,
    PersonName,
    TaxInfo,
)
from uie_cdm.partner import is_valid_iban

from uie_core.sources.table import FileTableSpec
from uie_core.wizard.text import count, say
from uie_core.wizard.values import (
    EXAMPLES,
    ExcludedRow,
    QuestionOption,
    ValueIssue,
    ValueQuestion,
    ValueTransform,
    rows_of,
)

FILE_REF = "file:"  # références d'une source fichier (même préfixe que les pièces)
KINDS_KEY = "partner:kind"  # personnes morales / physiques des lignes sans forme juridique
POSTAL_KEY = "partner:postal_zero"  # zéro initial perdu (Excel)
DUPLICATES_KEY = "partner:duplicates"  # même numéro de TVA sur plusieurs lignes
NUMBER_KEY = "partner:no_number"  # ligne sans numéro de client / fournisseur

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_SPACES = re.compile(r"[\s.]+")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PartnerTable(_Model):
    partners: list[Partner]
    row_of: dict[str, int]  # source_ref → ligne du fichier
    issues: list[ValueIssue]
    questions: list[ValueQuestion]
    transforms: list[ValueTransform]
    skipped: list[ExcludedRow]  # lignes non exportées (sans nom, doublon écarté, sans numéro)
    # source_ref → (valid | invalid | missing, pays ISO) : retenu avec le compte, sert aux factures à 0 %
    vat: dict[str, tuple[str, str | None]] = {}


def _legal_form(name: str, forms: list[str]) -> bool:
    """Une forme juridique du tableau est un MOT du nom (« GmbH & Co. KG », « UG (haftungsbeschränkt) »)."""
    text = f" {re.sub(r'[(),]', ' ', name)} "
    return any(re.search(rf"(?<![\w.]){re.escape(f)}(?![\w])", text, re.IGNORECASE) for f in forms)


def _question(
    key: str,
    title: str,
    help_: str,
    rows: list[int],
    options: list[QuestionOption],
    default: str | None,
    choices: dict[str, str],
) -> ValueQuestion:
    return ValueQuestion(
        key=key,
        kind=key.split(":", 1)[1],
        field=None,
        column=None,
        title=title,
        help=help_,
        count=len(rows),
        options=options,
        suggested=default,
        suggested_by="rule" if default else None,
        answer=choices.get(key, default) if choices.get(key) in {o.value for o in options} else default,
    )


def partners_from_rows(  # noqa: C901, PLR0912, PLR0915
    table: FileTableSpec,
    rows: list[dict[str, str]],
    columns: dict[str, str],
    role: PartnerRole | None,
    choices: dict[str, str] | None = None,
) -> PartnerTable:
    """`rows` : une entrée par ligne du fichier (clé du tableau → valeur déjà mise en forme, "" = vide) ;
    `columns` : clé → en-tête de la colonne du client ; `role` : rôle de toutes les lignes (type de données),
    None : d'après les colonnes de numéro présentes. `choices` : réponses aux décisions."""
    choices = choices or {}
    issues: list[ValueIssue] = []
    questions: list[ValueQuestion] = []
    transforms: list[ValueTransform] = []
    skipped: list[ExcludedRow] = []

    def issue(
        key: str,
        kind: str,
        found: list[tuple[int, str]],
        message: Callable[[int], str],
        fix: str,
        severity: str = "not_written",
    ) -> None:
        if found:
            issues.append(
                ValueIssue(
                    field=key,
                    column=columns.get(key, key),
                    kind=kind,
                    count=len(found),
                    examples=[v for _, v in found[:EXAMPLES]],
                    message=message(len(found)),
                    rows=rows_of([i for i, _ in found]),
                    fix=fix,
                    severity=severity,
                )
            )

    def val(i: int, key: str) -> str:
        return (rows[i].get(key) or "").strip()

    # --------------------------------------------------------------- rôle, nom, numéros
    number_key = {PartnerRole.CUSTOMER: "customer_number", PartnerRole.SUPPLIER: "supplier_number"}
    roles_of: dict[int, list[PartnerRole]] = {}
    for i in range(len(rows)):
        if role is not None:
            roles_of[i] = [role]
        else:
            found = [r for r, k in number_key.items() if k in columns and val(i, k)]
            mapped = [r for r, k in number_key.items() if k in columns]
            roles_of[i] = found or (mapped if len(mapped) == 1 else [])
    no_name = [i for i in range(len(rows)) if not val(i, "name") and any(rows[i].values())]
    skipped += [ExcludedRow(row=i + 1, reason="No name: not exported.", kind="no_name") for i in no_name]
    no_number = [
        i
        for i in range(len(rows))
        if i not in no_name and any(rows[i].values()) and not any(val(i, number_key[r]) for r in roles_of[i])
    ]
    if no_number:
        q = _question(
            NUMBER_KEY,
            say("{n} [row has|rows have] no customer or supplier number", len(no_number)),
            "Without a number, the account number cannot follow your rule. Nexa45 can give "
            + ("it" if len(no_number) == 1 else "them")
            + " the next free account number of the range; nothing is assigned without your answer.",
            [i + 1 for i in no_number],
            [
                QuestionOption(value="assign", label="Assign the next free account number"),
                QuestionOption(value="skip", label="Do not export these rows"),
            ],
            "assign",
            choices,
        )
        questions.append(q)
        if q.answer != "assign":
            skipped += [
                ExcludedRow(
                    row=i + 1,
                    reason="No customer or supplier number: not exported (your choice).",
                    kind="no_number",
                )
                for i in no_number
            ]

    # --------------------------------------------------------------- doublons probables (même n° de TVA)
    def vat_of(i: int) -> str:
        return _SPACES.sub("", val(i, "vat_id")).upper()

    first: dict[str, int] = {}
    duplicates: dict[int, int] = {}
    for i in range(len(rows)):
        v = vat_of(i)
        if len(v) < 4 or i in no_name:
            continue
        if v in first:
            duplicates[i] = first[v]
        else:
            first[v] = i
    if duplicates:
        q = _question(
            DUPLICATES_KEY,
            say("{n} [row has|rows have] the same VAT ID as an earlier row", len(duplicates)),
            "Probably the same company entered twice ("
            + "; ".join(f"row {i + 1} = row {j + 1}" for i, j in list(duplicates.items())[:5])
            + "). Nothing is merged: export the first row only, or both as separate accounts.",
            [i + 1 for i in duplicates],
            [
                QuestionOption(value="first", label="Export the first row only"),
                QuestionOption(value="both", label="Export both as separate accounts"),
            ],
            "first",
            choices,
        )
        questions.append(q)
        if q.answer == "first":
            skipped += [
                ExcludedRow(
                    row=i + 1,
                    reason=f"Probable duplicate (same VAT ID as row {j + 1}): not exported.",
                    kind="probable_duplicate",
                )
                for i, j in duplicates.items()
            ]
    out = {e.row - 1 for e in skipped}

    # --------------------------------------------------------------- type d'adressat
    unknown_kind = [
        i
        for i in range(len(rows))
        if i not in out and val(i, "name") and not _legal_form(val(i, "name"), table.legal_forms)
    ]
    kinds_answer = None
    if unknown_kind:
        q = _question(
            KINDS_KEY,
            say("{n} [name does|names do] not show a legal form", len(unknown_kind)),
            "Companies are recognized by their legal form (GmbH, AG, KG, UG, e.K., SARL…). For these "
            + count(len(unknown_kind), "name")
            + ": "
            + ", ".join(val(i, "name") for i in unknown_kind[:6])
            + ("…" if len(unknown_kind) > 6 else "")
            + ". Are they companies or persons?",
            [i + 1 for i in unknown_kind],
            [
                QuestionOption(value="ORGANIZATION", label="Companies"),
                QuestionOption(value="PERSON", label="Persons"),
                QuestionOption(value="UNKNOWN", label="Do not say (no addressee type)"),
            ],
            "ORGANIZATION",  # liste de clients d'un cabinet : surtout des sociétés (toujours modifiable)
            choices,
        )
        questions.append(q)
        kinds_answer = q.answer

    # --------------------------------------------------------------- codes postaux sans zéro initial
    countries: dict[int, str | None] = {}
    bad_country: list[tuple[int, str]] = []
    for i in range(len(rows)):
        raw = val(i, "country")
        code = table.country_code(raw) if raw else None
        if raw and code is None:
            bad_country.append((i, raw))
        countries[i] = code
    lost_zero: list[int] = []
    bad_postal: list[tuple[int, str]] = []
    for i in range(len(rows)):
        pc, code = (
            val(i, "postal_code"),
            countries[i] or (table.home_country if not val(i, "country") else None),
        )
        pattern = table.postal_codes.get(code or "")
        if not pc or i in out or pattern is None or re.match(pattern, pc):
            continue
        if pc.isdigit() and re.match(pattern, "0" + pc):
            lost_zero.append(i)
        else:
            bad_postal.append((i, pc))
    zero = None
    if lost_zero:
        q = _question(
            POSTAL_KEY,
            say("{n} postal [code has|codes have] lost [its|their] leading zero", len(lost_zero)),
            "Spreadsheets drop the leading zero of numbers: "
            + ", ".join(f"{val(i, 'postal_code')} → 0{val(i, 'postal_code')}" for i in lost_zero[:4])
            + ".",
            [i + 1 for i in lost_zero],
            [
                QuestionOption(value="add", label="Add the leading zero"),
                QuestionOption(value="empty", label="Leave these postal codes empty"),
            ],
            "add",
            choices,
        )
        questions.append(q)
        zero = q.answer

    # --------------------------------------------------------------- partenaires
    partners: list[Partner] = []
    row_of: dict[str, int] = {}
    bad_vat: list[tuple[int, str]] = []
    vat_state: dict[str, tuple[str, str | None]] = {}
    bad_email: list[tuple[int, str]] = []
    bad_iban: list[tuple[int, str]] = []
    # fournisseur d'un autre pays de l'UE sans numéro de TVA : ses achats ne peuvent pas être traités comme
    # des acquisitions intracommunautaires (signalé, jamais bloquant pour la liste)
    eu_without_vat: list[tuple[int, str]] = []
    vat_pairs: list[list[str]] = []
    zero_pairs: list[list[str]] = []
    for i in range(len(rows)):
        if i in out or not val(i, "name") or not any(rows[i].values()):
            continue
        name = val(i, "name")
        roles = roles_of[i]
        numbers = {r: val(i, number_key[r]) for r in roles if val(i, number_key[r])}
        # préfixe « file: » : jamais confondu avec la référence d'un système connecté (même numéro de compte
        # pour le même numéro de client, d'un fichier à l'autre)
        source_ref = FILE_REF + (next(iter(numbers.values()), None) or f"row-{i + 1}")
        if source_ref in row_of:  # même numéro sur deux lignes : la seconde n'est jamais fusionnée
            skipped.append(
                ExcludedRow(
                    row=i + 1,
                    reason=f"Probable duplicate (same number {source_ref.removeprefix(FILE_REF)} as row "
                    f"{row_of[source_ref]}): not exported.",
                    kind="probable_duplicate",
                )
            )
            continue
        if _legal_form(name, table.legal_forms):
            kind = PartnerKind.ORGANIZATION
        elif kinds_answer in {k.value for k in PartnerKind}:
            kind = PartnerKind(kinds_answer)
        else:
            kind = PartnerKind.UNKNOWN
        pc = val(i, "postal_code")
        if i in lost_zero:
            if zero == "add":
                zero_pairs.append([pc, "0" + pc])
                pc = "0" + pc
            else:
                pc = ""
        if any(j == i for j, _ in bad_postal):
            pc = ""
        vat = vat_of(i)
        status = "missing"
        if vat:
            pattern = table.vat_ids.get(vat[:2])
            if pattern is None or not re.match(pattern, vat):
                bad_vat.append((i, val(i, "vat_id")))
                vat, status = "", "invalid"
            else:
                status = "valid"
                if vat != val(i, "vat_id"):
                    vat_pairs.append([val(i, "vat_id"), vat])
        # pays de l'état : celui de l'adresse, sinon le préfixe du numéro valide (« EL » = Grèce : l'adresse)
        vat_state[source_ref] = (status, countries[i] or (vat[:2] if status == "valid" else None))
        abroad = countries[i] and countries[i] != table.home_country and countries[i] in table.eu_members
        if status == "missing" and abroad and PartnerRole.SUPPLIER in roles:
            eu_without_vat.append((i, name))
        email = val(i, "email")
        if email and not _EMAIL.match(email):
            bad_email.append((i, email))
            email = ""
        iban = _SPACES.sub("", val(i, "iban")).upper()
        if iban and not is_valid_iban(iban):
            bad_iban.append((i, val(i, "iban")))
            iban = ""
        address = Address(
            street=val(i, "street") or None,
            postal_code=pc or None,
            city=val(i, "city") or None,
            country_code=countries[i],
        )
        partners.append(
            Partner(
                source_ref=source_ref,
                kind=kind,
                name=name,
                organization_name=name if kind == PartnerKind.ORGANIZATION else None,
                person=PersonName(full_name=name) if kind == PartnerKind.PERSON else None,
                roles=roles,
                identifiers=[
                    Identifier(
                        scheme=IdentifierScheme.CUSTOMER_NUMBER
                        if r == PartnerRole.CUSTOMER
                        else IdentifierScheme.SUPPLIER_NUMBER,
                        value=n,
                    )
                    for r, n in numbers.items()
                ],
                addresses=[] if address.is_empty() else [address],
                tax=TaxInfo(vat_id=vat or None, tax_number=val(i, "tax_number") or None),
                contact=ContactInfo(
                    contact_person=val(i, "contact_person") or None,
                    email=email or None,
                    phone=val(i, "phone") or None,
                    mobile=val(i, "mobile") or None,
                    fax=val(i, "fax") or None,
                    website=val(i, "website") or None,
                ),
                bank_accounts=[
                    BankAccount(
                        iban=iban,
                        bic=val(i, "bic") or None,
                        bank_name=val(i, "bank_name") or None,
                        is_main=True,
                    )
                ]
                if iban
                else [],
            )
        )
        row_of[source_ref] = i + 1

    issue(
        "country",
        "country_unknown",
        bad_country,
        lambda n: say("{n} [country is|countries are] not recognized: not written.", n),
        "Write the country as a name or ISO code (DE, AT, FR…).",
    )
    issue(
        "postal_code",
        "postal_invalid",
        bad_postal,
        lambda n: say("{n} postal [code does|codes do] not match [its|their] country: not written.", n),
        "Correct these postal codes in your file.",
    )
    issue(
        "vat_id",
        "vat_invalid",
        bad_vat,
        lambda n: say("{n} VAT [ID is|IDs are] not valid for [its|their] country: not written.", n),
        "Correct the VAT ID (country code + number). Without a valid VAT ID, a tax-free intra-community "
        "delivery cannot be justified.",
    )
    issue(
        "vat_id",
        "vat_missing_eu",
        eu_without_vat,
        lambda n: say(
            "{n} [supplier|suppliers] from another EU country [has|have] no VAT ID: [its|their] purchases "
            "cannot be treated as intra-community acquisitions.",
            n,
        ),
        "Ask the supplier for its VAT ID and add it to your file.",
        severity="to_check",
    )
    issue(
        "email",
        "email_invalid",
        bad_email,
        lambda n: say("{n} email [address is|addresses are] not valid: not written.", n),
        "Correct these email addresses in your file (name@domain.tld).",
    )
    issue(
        "iban",
        "iban_invalid",
        bad_iban,
        lambda n: say("{n} [IBAN is|IBANs are] not valid (check digits): not written.", n),
        "Correct these IBANs in your file.",
    )
    if vat_pairs:
        transforms.append(
            ValueTransform(
                field="vat_id",
                column=columns.get("vat_id", "vat_id"),
                kind="vat_spaces",
                label="VAT ID written without spaces",
                count=len(vat_pairs),
                examples=vat_pairs[:EXAMPLES],
            )
        )
    if zero_pairs:
        transforms.append(
            ValueTransform(
                field="postal_code",
                column=columns.get("postal_code", "postal_code"),
                kind="postal_zero",
                label="Leading zero added to the postal code (your choice)",
                count=len(zero_pairs),
                examples=zero_pairs[:EXAMPLES],
            )
        )
    return PartnerTable(
        vat={ref: state for ref, state in vat_state.items() if ref in row_of},
        partners=partners,
        row_of=row_of,
        issues=issues,
        questions=questions,
        transforms=transforms,
        skipped=sorted(skipped, key=lambda e: e.row),
    )


__all__ = ["DUPLICATES_KEY", "KINDS_KEY", "NUMBER_KEY", "POSTAL_KEY", "PartnerTable", "partners_from_rows"]
