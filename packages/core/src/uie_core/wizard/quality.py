"""Contrôles avant l'import, sur les valeurs ÉCRITES du fichier préparé (fonctions pures, aucun système
nommé).

- Codes-barres : code abîmé par un tableur (notation scientifique), plusieurs codes dans une cellule, code
  numérique invalide (longueur GS1, chiffre de contrôle), même code sur plusieurs lignes → NON écrits (jamais
  corrigés, jamais choisis).
- Prix : ligne sans montant → signalée, jamais remplie (aucune valeur inventée).
- Références (catégories, unités…) : valeurs comparées à celles lues dans le système connecté ; sans système
  connecté → signalées « non vérifiées ». Les valeurs restent écrites : seul l'utilisateur corrige.

Chaque anomalie porte ses numéros de ligne et une solution simple (`ValueIssue.rows`, `fix`)."""

from __future__ import annotations

import re
from collections import defaultdict

from uie_core.wizard.model import TargetField
from uie_core.wizard.text import say
from uie_core.wizard.values import EXAMPLES, ValueIssue, _clip, is_money, rows_of

# mots qui désignent un code-barres dans le nom ou le libellé d'un champ (normes GS1 / ISBN, aucun système)
BARCODE_WORDS = frozenset({"barcode", "barcodes", "ean", "ean13", "gtin", "upc", "isbn"})
GS1_LENGTHS = frozenset({8, 12, 13, 14})
_SPLIT = re.compile(r"[,;|\n]+|\s+")
# notation scientifique (« 4,00638E+12 », « 8.85909E+11 ») : code abîmé par un tableur, chiffres perdus
_SCIENTIFIC = re.compile(r"^\d+(?:[.,]\d+)?[eE][+-]?\d+$")
_WORDS = re.compile(r"[^a-z0-9]+")

BARCODE_KINDS = frozenset({"barcode_damaged", "barcode_multiple", "barcode_invalid", "barcode_duplicate"})
PRICE_KINDS = frozenset({"missing_price", "no_price_column"})
REFERENCE_KINDS = frozenset({"reference_unknown", "reference_unverified"})


def _issue(  # noqa: PLR0913
    f: TargetField,
    column: str,
    kind: str,
    rows: list[int] | set[int],
    examples: list[str],
    message: str,
    fix: str,
    severity: str,
    count: int | None = None,
) -> ValueIssue:
    return ValueIssue(
        field=f.name,
        column=column,
        kind=kind,
        count=count if count is not None else len(set(rows)),
        examples=[_clip(x) for x in list(dict.fromkeys(e for e in examples if e))[:EXAMPLES]],
        message=message,
        rows=rows_of(rows),
        fix=fix,
        severity=severity,
    )


# --------------------------------------------------------------------------- #
# Codes-barres
# --------------------------------------------------------------------------- #
def is_barcode(f: TargetField) -> bool:
    words = set(_WORDS.split(f"{f.name} {f.label}".casefold()))
    return bool(words & BARCODE_WORDS)


def gs1_valid(code: str) -> bool:
    """Code GS1 (EAN-8, UPC-A, EAN-13, GTIN-14) : longueur admise et chiffre de contrôle exact."""
    if not code.isdigit() or len(code) not in GS1_LENGTHS:
        return False
    body, check = code[:-1], int(code[-1])
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def check_barcodes(
    f: TargetField, column: str, values: list[str], skip: set[int]
) -> tuple[list[str], list[ValueIssue]]:
    """Valeurs écrites d'un champ code-barres → (valeurs, anomalies). Un code non numérique (code interne)
    est gardé : seule une forme GS1 se vérifie."""
    out = list(values)
    damaged: dict[int, str] = {}
    multiple: dict[int, str] = {}
    invalid: dict[int, str] = {}
    for i, v in enumerate(values):
        if not v or i in skip:
            continue
        if _SCIENTIFIC.match(v.strip()):
            damaged[i] = v  # jamais reconstruit : les derniers chiffres sont perdus
            continue
        parts = [p for p in _SPLIT.split(v.strip()) if p]
        if len(parts) > 1:
            multiple[i] = v
        elif parts[0].isdigit() and not gs1_valid(parts[0]):
            invalid[i] = v
    for i in [*damaged, *multiple, *invalid]:
        out[i] = ""
    seen: dict[str, list[int]] = defaultdict(list)
    for i, v in enumerate(out):
        if v and i not in skip:
            seen[v.strip()].append(i)
    duplicate = {i: code for code, rows in seen.items() if len(rows) > 1 for i in rows}
    for i in duplicate:
        out[i] = ""
    issues = []
    if damaged:
        n = len(damaged)
        issues.append(
            _issue(
                f,
                column,
                "barcode_damaged",
                set(damaged),
                list(damaged.values()),
                say(
                    "{n} [barcode|barcodes] of {label} [was|were] damaged by Excel (shown as a number like "
                    "4.00638E+12, the last digits are lost): not written.",
                    n,
                    label=f.label,
                ),
                "In your spreadsheet, format the barcode column as text, type the full codes again, then "
                "export the file again.",
                "not_written",
            )
        )
    if multiple:
        n = len(multiple)
        issues.append(
            _issue(
                f,
                column,
                "barcode_multiple",
                set(multiple),
                list(multiple.values()),
                say(
                    "{n} [cell|cells] of {label} [contains|contain] several barcodes: not written.",
                    n,
                    label=f.label,
                ),
                "Keep one barcode per product in your file (one per row).",
                "not_written",
            )
        )
    if invalid:
        n = len(invalid)
        issues.append(
            _issue(
                f,
                column,
                "barcode_invalid",
                set(invalid),
                list(invalid.values()),
                say(
                    "{n} [barcode|barcodes] of {label} [is|are] not valid (length or check digit): not "
                    "written.",
                    n,
                    label=f.label,
                ),
                "Correct these barcodes in your file (EAN / UPC: 8, 12, 13 or 14 digits). If your "
                "spreadsheet shows numbers like 8.85909E+11, format the column as text and export the file "
                "again.",
                "not_written",
            )
        )
    if duplicate:
        n = len(duplicate)
        issues.append(
            _issue(
                f,
                column,
                "barcode_duplicate",
                set(duplicate),
                list(duplicate.values()),
                say(
                    "{n} [row shares|rows share] the same {label} with another row: not written (never "
                    "chosen for you).",
                    n,
                    label=f.label,
                ),
                "Give each product its own barcode, or remove the duplicate rows from your file.",
                "not_written",
            )
        )
    return out, issues


# --------------------------------------------------------------------------- #
# Prix
# --------------------------------------------------------------------------- #
def missing_prices(
    fields: list[TargetField],
    series: dict[str, list[str]],
    columns: dict[str, str],
    rows: list[int],
    reported: dict[str, set[int]],
) -> list[ValueIssue]:
    """Montants manquants sur les lignes écrites (`rows`) : jamais remplis. Une ligne déjà signalée pour ce
    champ (valeur illisible, autre devise…) ne l'est pas deux fois. Aucune colonne de prix dans le fichier →
    signalé une fois par champ de prix de la structure."""
    money = [f for f in fields if is_money(f)]
    mapped = [f for f in money if f.name in series]
    issues: list[ValueIssue] = []
    if money and not mapped:
        for f in money:
            issues.append(
                _issue(
                    f,
                    "",
                    "no_price_column",
                    [],
                    [],
                    f"Your file has no column for {f.label}: no amount will be written (none is invented).",
                    f"Add a {f.label} column to your file, or enter it in the target system after the "
                    "import.",
                    "to_check",
                    count=len(rows),
                )
            )
        return issues
    for f in mapped:
        empty = [i for i in rows if not series[f.name][i] and i not in reported.get(f.name, set())]
        if empty:
            issues.append(
                _issue(
                    f,
                    columns.get(f.name, ""),
                    "missing_price",
                    empty,
                    [],
                    say(
                        "{n} [row has|rows have] no {label}: left empty, no amount is invented.",
                        len(empty),
                        label=f.label,
                    ),
                    f"Enter the {f.label} of these rows in your file, or set it in the target system after "
                    "the import.",
                    "to_check",
                )
            )
    return issues


# --------------------------------------------------------------------------- #
# Références (catégories, unités…)
# --------------------------------------------------------------------------- #
def check_reference(
    f: TargetField,
    column: str,
    used: dict[str, list[int]],
    known: list[str] | None,
    system: str,
    complete: bool = True,
) -> list[ValueIssue]:
    """Valeurs d'une référence (valeur → lignes) comparées aux valeurs RÉELLES du système connecté (`known`) ;
    `known` None = aucun système connecté (ou lecture impossible) → non vérifiées. `complete` False : la
    lecture a été plafonnée, une valeur absente n'est donc pas prouvée inconnue."""
    if not used:
        return []
    if known is None or not complete:
        rows = {i for idx in used.values() for i in idx}
        return [
            _issue(
                f,
                column,
                "reference_unverified",
                rows,
                list(used),
                say("{n} [row uses|rows use] ", len(rows))
                + say("{n} [value|values] of {label} that could not be checked: ", len(used), label=f.label)
                + f"no {system} is connected.",
                f"Connect your {system} in “My systems” so these values can be checked, or make sure they "
                f"exist in {system} before the import.",
                "to_check",
            )
        ]
    exact = set(known)
    loose = {k.casefold().strip(): k for k in known}
    unknown = {v: idx for v, idx in used.items() if v not in exact}
    if not unknown:
        return []
    rows = {i for idx in unknown.values() for i in idx}
    close = {v: loose[v.casefold().strip()] for v in unknown if v.casefold().strip() in loose}
    fix = f"Create these values in {system} first, or change them in your file to existing ones."
    if close:
        fix += " Did you mean: " + ", ".join(f"“{v}” → “{k}”" for v, k in list(close.items())[:3]) + "?"
    return [
        _issue(
            f,
            column,
            "reference_unknown",
            rows,
            list(unknown),
            say("{n} [row uses|rows use] ", len(rows))
            + say(
                "{n} [value|values] of {label} that [does|do] not exist in your ", len(unknown), label=f.label
            )
            + f"{system}: {system} will refuse "
            + ("this row." if len(rows) == 1 else "these rows."),
            fix,
            "to_check",
        )
    ]
