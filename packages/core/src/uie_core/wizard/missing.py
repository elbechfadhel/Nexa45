"""Compléter les champs obligatoires manquants après les correspondances (« Complete missing information »).

    champs obligatoires de la structure sans colonne (+ champs à valeurs connues déductibles des réponses)
    ──► une question par champ, en mots simples :
        1. valeurs admises du Registry (sélection d'un schéma publié) → choix VÉRIFIÉS ;
        2. booléen → Yes / No ;
        3. référence (enregistrement existant de la cible : catégorie, unité…) → valeurs LUES dans un système
           connecté de l'organisation ; sinon réponse métier (ex. « à la pièce », gardée comme avertissement),
           « Je ne sais pas » (avec où trouver l'information) ou saisie de l'identifiant exact : JAMAIS
           d'identifiant proposé ni composé ;
        4. le LLM ne fait que formuler, expliquer et déduire des réponses PRÉCÉDENTES une valeur parmi les
           valeurs admises (validée ici ; pré-sélectionnée, à confirmer) ;
    ──► réponses : valeur commune ou par ligne ──► validation (type, longueur, valeurs admises) ; valeur non
        vérifiable = confirmation explicite + AVERTISSEMENT conservé.

Pur : ni base, ni réseau (le LLM et la lecture du système connecté passent par le service)."""

from __future__ import annotations

import re
from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from uie_core.wizard.model import TargetField

MAX_PER_ROW = 50  # au-delà, seule une valeur commune est proposée (saisie ligne par ligne trop longue)
MAX_SUGGESTIONS = 5
MAX_HINTS = 6
TRUE, FALSE = "true", "false"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MissingKind(StrEnum):
    CHOICE = "choice"  # valeurs admises connues (Registry)
    BOOLEAN = "boolean"
    REFERENCE = "reference"  # enregistrement existant de la cible : lu dans le système, saisi, jamais proposé
    NUMBER = "number"
    DATE = "date"
    TEXT = "text"


class MissingChoice(_Model):
    value: str
    label: str
    description: str | None = None
    verified: bool = False  # True : valeur admise du Registry, ou enregistrement lu dans le système connecté


class FileColumn(_Model):
    """Colonne du fichier (non importée, avec des valeurs) qui peut alimenter un champ obligatoire."""

    position: int
    header: str
    examples: list[str] = []


class MissingQuestion(_Model):
    field: str  # nom du champ cible
    label: str
    kind: MissingKind
    title: str
    help: str | None = None
    choices: list[MissingChoice] = []
    free_text: bool = True  # une autre valeur peut être saisie
    warning: str | None = None  # dit d'avance quand une valeur ne pourra pas être vérifiée
    optional: bool = (
        False  # champ facultatif proposé seulement parce qu'il se déduit des réponses précédentes
    )
    suggested: str | None = None  # valeur déduite des réponses précédentes (pré-sélectionnée, à confirmer)
    explanation: str | None = None  # pourquoi cette valeur : réponse métier → valeur technique
    hints: list[str] = []  # réponses métier d'une référence (ex. « Piece »), valeurs du système illisibles
    # réponse métier → nom STANDARD de la cible (référence par nom ; à confirmer, jamais un identifiant)
    hint_names: dict[str, str] = {}
    allow_unknown: bool = False  # « I don't know » (champ laissé vide et signalé)
    from_system: bool = False  # choix lus dans le système connecté de l'organisation
    # réponse déjà donnée sans valeur écrite (réponse métier ou « je ne sais pas ») : gardée, jamais
    # redemandée à vide
    previous_hint: str | None = None
    previous_unknown: bool = False
    resolution: str | None = None  # comment résoudre un champ resté « à compléter » (phrase simple)
    same_as: str | None = (
        None  # champ de la même sorte d'enregistrement déjà demandé : sa réponse est reprise
    )
    # donnée propre à chaque ligne (texte, nombre, date) sans colonne, alors que le fichier a des colonnes non
    # importées : on demande QUELLE colonne, jamais une valeur unique pour toutes les lignes
    file_columns: list[FileColumn] = []


class RowRef(_Model):
    index: int
    label: str


class MissingInfo(_Model):
    questions: list[MissingQuestion]
    rows: list[RowRef]  # lignes du fichier (pour une valeur par ligne), vide si trop nombreuses
    row_count: int
    # enregistrements liés que le fichier préparé créerait d'abord (« créer » choisi) : champ → nombre
    created: dict[str, int] = {}


class Fill(_Model):
    """Réponse à une question : valeur commune et / ou valeurs par ligne (index → valeur) ; ou réponse métier
    sans valeur écrite (`hint`) ; ou « je ne sais pas » (`unknown`)."""

    field: str = Field(max_length=120)
    common: str | None = Field(default=None, max_length=300)
    per_row: dict[int, str] = {}
    confirmed: bool = False  # l'utilisateur confirme une valeur que Nexa45 ne peut pas vérifier
    hint: str | None = Field(default=None, max_length=120)
    unknown: bool = False
    auto: bool = False  # appliquée d'office d'après les réponses du parcours (jamais « complétée par vous »)


class FillError(ValueError):
    """Réponse invalide (→ 422) : message simple, en anglais."""


_NUMBER = re.compile(r"^[+-]?\d+([.,]\d+)?$")
_INTEGER = re.compile(r"^[+-]?\d+$")
_DATE_DMY = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")
_ID_WORDS = re.compile(r"\s*\b(external\s+ids?|ids?|identifiers?)\s*$", re.IGNORECASE)
NUMBER_TYPES = frozenset({"number", "decimal", "float", "monetary", "integer"})
DATE_TYPES = frozenset({"date", "datetime"})


def kind_of(f: TargetField) -> MissingKind:
    t = f.type.casefold()
    if f.allowed:
        return MissingKind.CHOICE
    if f.reference or t == "reference":
        return MissingKind.REFERENCE
    if t == "boolean":
        return MissingKind.BOOLEAN
    if t in NUMBER_TYPES:
        return MissingKind.NUMBER
    if t in DATE_TYPES:
        return MissingKind.DATE
    return MissingKind.TEXT


def set_by_target(f: TargetField, fields: list[TargetField]) -> list[TargetField]:
    """Champ obligatoire que la cible CALCULE d'après d'autres champs de la même structure (Registry :
    `depends`) : jamais demandé, jamais rempli par le Wizard. Rend ces champs sources ; [] sinon (une
    dépendance absente de la structure : rien n'est supposé, le champ reste à compléter)."""
    if not f.required or not f.computed_from:
        return []
    by_base = {x.name.split("/", 1)[0]: x for x in fields if x.name != f.name}
    if not all(d in by_base for d in f.computed_from):
        return []
    return [by_base[d] for d in f.computed_from]


def reference_by(f: TargetField) -> str | None:
    """Une colonne « champ/attribut » désigne l'enregistrement par un identifiant ; une colonne simple, par
    son
    NOM affiché (format d'import de fichier natif). None : pas une référence."""
    if kind_of(f) != MissingKind.REFERENCE:
        return None
    return "identifier" if "/" in f.name else "name"


def plain_label(f: TargetField) -> str:
    """Libellé sans mots techniques de fin (« Unit of Measure External ID » → « Unit of Measure »)."""
    return _ID_WORDS.sub("", f.label).strip() or f.label


def rule_question(f: TargetField) -> MissingQuestion:
    """Question tirée des règles et des valeurs connues (le LLM ne fait ensuite que reformuler, expliquer ou
    déduire une valeur des réponses précédentes)."""
    kind = kind_of(f)
    name = plain_label(f)
    if kind == MissingKind.CHOICE:
        return MissingQuestion(
            field=f.name,
            label=f.label,
            kind=kind,
            title=f"Which {name.lower()} applies to your data?",
            choices=[MissingChoice(value=a.value, label=a.label, verified=True) for a in f.allowed],
            free_text=False,
        )
    if kind == MissingKind.BOOLEAN:
        return MissingQuestion(
            field=f.name,
            label=f.label,
            kind=kind,
            title=f"{name}: yes or no?",
            choices=[
                MissingChoice(value=TRUE, label="Yes", verified=f.verified),
                MissingChoice(value=FALSE, label="No", verified=f.verified),
            ],
            free_text=False,
        )
    if kind == MissingKind.REFERENCE:
        return MissingQuestion(
            field=f.name,
            label=f.label,
            kind=kind,
            title=f"Which {name.lower()} should your records use?",
            help=f"It must already exist in your target system ({name}).",
            free_text=True,
            allow_unknown=True,
            warning="Nexa45 cannot check this value here: confirm it matches a record of your target system.",
        )
    return MissingQuestion(
        field=f.name,
        label=f.label,
        kind=kind,
        title=f"What value should {name} have?",
        help=f.description,
        allow_unknown=True,
        warning=None
        if f.verified
        else "This value cannot be verified against the target format: confirm it.",
    )


def with_system_values(q: MissingQuestion, options: list[tuple[str, str]]) -> MissingQuestion:
    """Référence : valeurs RÉELLES lues dans le système connecté (choix vérifiés, aucune confirmation)."""
    if not options:
        return q
    return q.model_copy(
        update={
            "choices": [MissingChoice(value=v, label=label, verified=True) for v, label in options],
            "from_system": True,
            "warning": None,
            "hints": [],
        }
    )


def _forms(text: str) -> set[str]:
    """Formes comparables d'un nom : sans casse ni espaces doubles, singulier simple (« Boxes » = « Box »)."""
    t = " ".join(text.casefold().split())
    out = {t}
    if t.endswith("es") and len(t) > 4:
        out.add(t[:-2])
    if t.endswith("s") and len(t) > 3:
        out.add(t[:-1])
    return out


_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def _contains(text: str, phrase: str) -> bool:
    """La réponse contient-elle la valeur comme mot(s) entier(s) (sans casse, singulier simple) ?"""
    words = [w.casefold() for w in _WORD.findall(text)]
    target = [w.casefold() for w in _WORD.findall(phrase)]
    if not target or len(target) > len(words):
        return False
    for i in range(len(words) - len(target) + 1):
        if all(_forms(words[i + j]) & _forms(target[j]) for j in range(len(target))):
            return True
    return False


def deduce_choice(f: TargetField, answers: list[tuple[str, str]]) -> tuple[str, str, str] | None:
    """Valeur admise du Registry DÉDUITE sans LLM des réponses du parcours : une réponse qui nomme exactement
    UNE valeur admise (« Goods only » → Goods) ; plusieurs (« Goods and services ») ou des réponses
    contradictoires → None (jamais deviné). `answers` : (sujet, libellé). → (valeur, libellé, réponse)."""
    if not f.allowed:
        return None
    found: dict[str, tuple[str, str, str]] = {}
    for topic, label in answers:
        if topic in ("target_system", "version", "release", "target_version"):
            continue
        hits = [a for a in f.allowed if _contains(label, a.label)]
        if len(hits) == 1:
            found.setdefault(hits[0].value, (hits[0].value, hits[0].label, label))
        elif len(hits) > 1:
            return None
    return next(iter(found.values())) if len(found) == 1 else None


def match_hint(hint: str, options: list[tuple[str, str]]) -> tuple[str, str] | None:
    """Valeur du système dont le NOM est celui de la réponse métier (sans casse, singulier simple) : une
    seule, sinon None (jamais devinée parmi plusieurs, jamais une ressemblance)."""
    wanted = _forms(hint)
    found = [o for o in options if wanted & (_forms(o[1]) | _forms(o[0]))]
    return found[0] if len(found) == 1 else None


def with_previous(
    q: MissingQuestion, fill: Fill, options: list[tuple[str, str]], *, system_connected: bool
) -> MissingQuestion:
    """Réponse déjà donnée SANS valeur écrite : gardée. Système connecté → la valeur du même nom est proposée
    (vérifiée, à confirmer d'un clic), sinon ses valeurs réelles sont listées ; sans système → solution simple
    (connecter le système ou saisir la valeur exacte). Rien n'est jamais inventé."""
    name = _ID_WORDS.sub("", q.label).strip() or q.label  # jamais un mot technique (« External ID »)
    if fill.unknown:
        return q.model_copy(
            update={
                "previous_unknown": True,
                "resolution": "Still to complete: find this value in your target system, or connect your "
                "system "
                "in My systems so Nexa45 can look it up.",
            }
        )
    hint = _text(fill.hint, 120)
    update: dict[str, Any] = {"previous_hint": hint}
    if not q.suggested and hint in q.hint_names:  # nom standard de la cible pour cette réponse métier
        q = q.model_copy(update={"suggested": q.hint_names[hint]})
        update["suggested"] = q.hint_names[hint]
        update["explanation"] = f"“{q.hint_names[hint]}” is the standard name used for “{hint}”."
    found = match_hint(hint, options) if options else None
    if found:
        update["suggested"] = found[0]
        update["explanation"] = f"Your answer “{hint}” matches “{found[1]}” in your connected system."
    elif options:
        update["resolution"] = f"Choose the value of your connected system that means “{hint}”."
    elif q.suggested:
        update["resolution"] = (
            f"For “{hint}”, Nexa45 suggests “{q.suggested}”: confirm it if your system uses this name, "
            "or "
            "enter the exact name used there."
        )
    elif system_connected:
        update["resolution"] = (
            f"“{hint}” could not be found in your connected system: enter its exact value, or create it "
            "there."
        )
    else:
        update["resolution"] = (
            f"You chose “{hint}”. To finish, connect your target system in My systems so Nexa45 can look "
            "up "
            f"its exact value, or enter the exact value of “{hint}” ({name}) yourself."
        )
    return q.model_copy(update=update)


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def with_ai(q: MissingQuestion, f: TargetField, raw: dict[str, Any], *, simple: bool) -> MissingQuestion:
    """Apport du LLM, VALIDÉ : formulation simple, aide, explication ; valeur déduite des réponses précédentes
    seulement parmi les valeurs admises (jamais une référence) ; suggestions libres seulement pour un champ
    sans valeurs connues, filtrées par type et longueur ; réponses métier d'une référence (jamais un
    identifiant)."""
    update: dict[str, Any] = {}
    title = _text(raw.get("title"), 240)
    if title and (simple or q.kind not in (MissingKind.CHOICE, MissingKind.BOOLEAN)):
        update["title"] = title
    help_text = _text(raw.get("help"), 400)
    if help_text:
        update["help"] = help_text
    explanation = _text(raw.get("explanation"), 300) or None
    suggested = _text(raw.get("suggested_value"), 300)
    # référence : seulement un NOM standard (jamais un identifiant), à confirmer et non vérifié
    by_identifier = q.kind == MissingKind.REFERENCE and reference_by(f) != "name"
    if suggested and not by_identifier and not q.from_system and check_value(f, suggested) is None:
        known = {c.value for c in q.choices}
        if q.kind in (MissingKind.CHOICE, MissingKind.BOOLEAN) and suggested not in known:
            suggested = ""  # hors des valeurs admises de CETTE version : jamais appliqué
        if suggested:
            update["suggested"] = suggested
            update["explanation"] = explanation
    if q.kind == MissingKind.REFERENCE and not q.from_system:
        hints: list[str] = []
        names: dict[str, str] = {}
        for h in raw.get("business_choices") or []:
            label = _text(h.get("label") if isinstance(h, dict) else h, 120)
            if not label or label in hints:
                continue
            hints.append(label)
            standard = _text(h.get("standard_name"), 120) if isinstance(h, dict) else ""
            if standard and reference_by(f) == "name" and check_value(f, standard) is None:
                names[label] = standard  # un NOM standard (jamais pour une référence par identifiant)
        update["hints"] = hints[:MAX_HINTS]
        update["hint_names"] = {k: v for k, v in names.items() if k in hints[:MAX_HINTS]}
    elif q.kind not in (MissingKind.CHOICE, MissingKind.BOOLEAN, MissingKind.REFERENCE):
        choices: list[MissingChoice] = []
        for c in raw.get("choices") or []:
            if not isinstance(c, dict):
                continue
            value = _text(c.get("value"), 300)
            if not value or check_value(f, value) is not None or any(x.value == value for x in choices):
                continue  # suggestion qui ne respecte pas le type ou la longueur connus : écartée
            choices.append(
                MissingChoice(
                    value=value,
                    label=_text(c.get("label") or value, 160),
                    description=_text(c.get("description"), 200) or None,
                )
            )
            if len(choices) == MAX_SUGGESTIONS:
                break
        if choices:
            update["choices"] = choices
            update["warning"] = (
                "Suggestions come from AI and cannot be verified against the target format: "
                "confirm the value."
            )
    return q.model_copy(update=update)


def check_value(f: TargetField, value: str) -> str | None:
    """Raison pour laquelle la valeur est refusée (type, longueur, valeurs admises connues), sinon None."""
    v = value.strip()
    if not v:
        return "is empty"
    if f.allowed and v not in {a.value for a in f.allowed}:
        return "is not one of the allowed values"
    kind = kind_of(f)
    t = f.type.casefold()
    if kind == MissingKind.NUMBER and not (_INTEGER if t == "integer" else _NUMBER).match(v):
        return "is not a whole number" if t == "integer" else "is not a number"
    if kind == MissingKind.BOOLEAN and v not in (TRUE, FALSE):
        return "must be yes or no"
    if kind == MissingKind.DATE and not _is_date(v):
        return "is not a date (YYYY-MM-DD)"
    if f.length and len(v) > f.length:
        return f"is longer than {f.length} characters"
    return None


def _is_date(v: str) -> bool:
    try:
        date.fromisoformat(v)
        return True
    except ValueError:
        m = _DATE_DMY.match(v)
        if not m:
            return False
        try:
            date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            return True
        except ValueError:
            return False


def is_verified(f: TargetField, value: str, known: set[str] | None = None) -> bool:
    """Vérifiée : valeur admise du Registry, oui / non d'un champ vérifié, ou valeur LUE dans le système."""
    if known and value in known:
        return True
    if f.allowed:
        return value in {a.value for a in f.allowed}
    return kind_of(f) == MissingKind.BOOLEAN and f.verified


def resolve_fills(
    fields: list[TargetField],
    fills: list[Fill],
    rows: int,
    known: dict[str, set[str]] | None = None,
) -> tuple[dict[str, list[str]], list[str], list[str]]:
    """Réponses validées → valeurs par champ et par ligne ; + champs complétés ; + avertissements conservés.
    `known` : valeurs lues dans le système connecté (vérifiées). `FillError` : valeur invalide, ligne
    inconnue,
    ou valeur non vérifiable sans confirmation."""
    by_name = {f.name: f for f in fields}
    values: dict[str, list[str]] = {}
    completed: list[str] = []
    warnings: list[str] = []
    for fill in fills:
        f = by_name.get(fill.field)
        if f is None:
            raise FillError(f"Unknown target field {fill.field}")
        name = plain_label(f)
        if fill.unknown:
            warnings.append(f"{name}: not known yet. Find it in your target system before importing.")
            continue
        if fill.hint:
            warnings.append(
                f"{name}: you chose “{_text(fill.hint, 120)}”. Its exact value in your target system could "
                "not be looked up here: add it before importing."
            )
            continue
        column = [fill.common.strip() if fill.common and fill.common.strip() else "" for _ in range(rows)]
        for index, value in fill.per_row.items():
            if not 0 <= index < rows:
                raise FillError(f"Unknown row {index + 1}")
            if value.strip():
                column[index] = value.strip()
        used = {v for v in column if v}
        if not used:
            continue
        for v in used:
            reason = check_value(f, v)
            if reason:
                raise FillError(f"{name}: “{v}” {reason}")
        system_values = (known or {}).get(f.name)
        if system_values:  # valeurs du système connecté lisibles : toute saisie y est vérifiée
            absent = sorted(v for v in used if v not in system_values)
            if absent:
                raise FillError(f"{name}: “{absent[0]}” was not found in your connected system")
        unverified = sorted(v for v in used if not is_verified(f, v, system_values))
        if unverified:
            if not fill.confirmed:
                raise FillError(f"{name}: confirm the value, it cannot be verified here")
            shown = ", ".join(unverified[:3]) + ("…" if len(unverified) > 3 else "")
            warnings.append(f"{name}: {shown} entered by you, not verified against the target")
        values[f.name] = column
        completed.append(f.name)
    return values, completed, warnings
