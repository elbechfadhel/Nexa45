"""Modèles de l'assistant de migration (AI Migration Wizard) : questions, réponses, structure cible proposée.

Toute réponse d'un LLM passe par `question_from_raw` / `proposal_from_raw` : forme contrôlée, longueurs
bornées, valeurs nettoyées ; une réponse invalide n'est JAMAIS montrée telle quelle (erreur classée).
Rien ici ne dit qu'un format proposé par l'IA est officiellement compatible avec la cible : seul le
Registry « vérifie »."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

MAX_QUESTIONS = 12  # au-delà, l'assistant passe d'office à la structure cible
MAX_CHOICES = 8
MAX_TARGET_CHOICES = 16  # question du système cible : liste plus large, groupée par catégorie
MAX_FIELDS = 60
SAMPLE_ROWS = 5
OTHER_VALUE = "__other__"  # réponse libre (« Other ») : sa valeur est le texte saisi


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ChoiceSource(StrEnum):
    REGISTRY = "registry"  # Registry de Nexa45 : schéma publié (vérifié)
    KNOWN = "known"  # Registry de Nexa45 : système identifié, SANS schéma (rien n'est vérifié)
    AI = "ai"  # proposé par l'IA (non vérifié)
    USER = "user"  # saisi par l'utilisateur
    FILE = "file"  # déduit du fichier de l'utilisateur par des règles (en-têtes), sans LLM


class ItemStatus(StrEnum):
    """Statut montré au fournisseur : trois niveaux distincts, jamais confondus."""

    VERIFIED_SCHEMA = "verified_schema"  # confirmé par un schéma publié du Registry
    KNOWN_SYSTEM = "known_system"  # système identifié par le Registry, sans schéma d'import vérifié
    AI_PROPOSAL = "ai_proposal"  # proposé par le LLM
    YOUR_ANSWER = "your_answer"  # saisi librement par l'utilisateur
    FROM_YOUR_FILE = "from_your_file"  # déduit du fichier de l'utilisateur (règles), modifiable


class Verification(StrEnum):
    VERIFIED = "verified"  # entité et tous les champs trouvés dans le Registry
    PARTIAL = "partial"  # entité ou une partie des champs trouvés dans le Registry
    AI = "ai"  # proposition de l'IA, rien n'a pu être vérifié


class WizardChoice(_Model):
    value: str = Field(max_length=160)
    label: str = Field(max_length=160)
    description: str | None = Field(default=None, max_length=400)
    group: str | None = Field(default=None, max_length=60)  # catégorie d'affichage (ex. ERP, Accounting)
    source: ChoiceSource = ChoiceSource.AI


class WizardQuestion(_Model):
    id: str = Field(max_length=60)
    topic: str = Field(max_length=60)  # sujet de la décision (ex. target_system, version, scope)
    title: str = Field(max_length=240)
    help: str | None = Field(default=None, max_length=500)
    choices: list[WizardChoice] = Field(min_length=1, max_length=MAX_TARGET_CHOICES + 14)
    allow_other: bool = True
    other_label: str | None = Field(default=None, max_length=60)  # réponse libre (défaut : « Other »)


class WizardAnswer(_Model):
    question_id: str = Field(max_length=60)
    topic: str = Field(max_length=60)
    question: str = Field(max_length=240)
    value: str = Field(max_length=300)
    label: str = Field(max_length=300)
    source: ChoiceSource = ChoiceSource.AI


class WizardStep(_Model):
    done: bool
    question: WizardQuestion | None = None
    answered: int
    remaining: int  # estimation du nombre de questions restantes (barre de progression)


class ConfigItem(_Model):
    label: str = Field(max_length=160)
    value: str = Field(max_length=300)
    verified: bool = False
    status: ItemStatus = ItemStatus.AI_PROPOSAL


class AllowedValue(_Model):
    value: str
    label: str


class TargetField(_Model):
    name: str = Field(max_length=120)
    label: str = Field(max_length=200)
    type: str = Field(max_length=60)
    length: int | None = None
    required: bool = False
    example: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=400)
    verified: bool = (
        False  # champ trouvé dans le Registry (type, obligatoire et libellé viennent alors de lui)
    )
    # valeurs admises LUES dans le Registry (jamais proposées par l'IA) ; vide : inconnues
    allowed: list[AllowedValue] = []
    # la colonne désigne un enregistrement EXISTANT de la cible (identifiant) : jamais deviné, jamais
    # vérifiable
    # sans connexion à la cible
    reference: bool = False
    relation: str | None = None  # entité désignée par la référence (Registry), ex. une table d'unités
    # champs dont la cible CALCULE ce champ (Registry : `depends` d'un champ calculé stocké) ; jamais de l'IA
    computed_from: list[str] = []


class TargetProposal(_Model):
    title: str = Field(max_length=200)
    summary: str = Field(max_length=1200)
    entity: str | None = Field(default=None, max_length=200)
    entity_verified: bool = False
    template_name: str | None = Field(default=None, max_length=200)
    verification: Verification = Verification.AI
    registry_reference: str | None = None  # snapshot du Registry qui a vérifié (libellé lisible)
    configuration: list[ConfigItem]
    fields: list[TargetField] = Field(min_length=1, max_length=MAX_FIELDS)
    rows: list[list[str]]  # exemples FICTIFS, une ligne par enregistrement, dans l'ordre des champs
    notes: list[str] = []


class WizardError(Exception):
    """L'assistant ne peut pas continuer (→ 502 / 409 : réponse de l'IA invalide, IA non configurée)."""


class WizardInputError(WizardError):
    """Entrée invalide du démarrage par le fichier (cible inconnue, type de données introuvable) → 422."""


class WizardDataTypeUnknown(WizardInputError):
    """Le type de données du fichier n'a pas pu être reconnu : l'utilisateur le choisit parmi `options` (types
    que la cible sait recevoir) ; le démarrage reprend avec ce choix (jamais un type deviné)."""

    def __init__(self, message: str, options: list[str]) -> None:
        super().__init__(message)
        self.options = options


class WizardUnavailable(WizardError):
    """Aucune IA configurée : l'assistant ne peut pas proposer de questions."""


# --------------------------------------------------------------------------- #
# Validation des réponses brutes du LLM
# --------------------------------------------------------------------------- #
_SLUG = re.compile(r"[^a-z0-9_]+")


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _opt(value: Any, limit: int) -> str | None:
    text = _text(value, limit)
    return text or None


def _slug(value: Any, fallback: str) -> str:
    slug = _SLUG.sub("_", str(value or "").strip().lower()).strip("_")[:60]
    return slug or fallback


def question_from_raw(raw: Any, answered: int) -> tuple[bool, WizardQuestion | None, int]:
    """(done, question, remaining) d'une réponse brute ; `WizardError` si la forme est invalide."""
    if not isinstance(raw, dict):
        raise WizardError("The AI answer is not readable")
    remaining = raw.get("remaining")
    remaining = max(0, min(int(remaining), MAX_QUESTIONS)) if isinstance(remaining, int) else 3
    if raw.get("done") is True:
        return True, None, 0
    q = raw.get("question")
    if not isinstance(q, dict):
        raise WizardError("The AI did not propose a question")
    choices: list[WizardChoice] = []
    seen: set[str] = set()
    for c in q.get("choices") or []:
        if not isinstance(c, dict):
            continue
        label = _text(c.get("label"), 160)
        value = _text(c.get("value"), 160) or label
        if not label or value.casefold() in seen or value == OTHER_VALUE:
            continue
        seen.add(value.casefold())
        choices.append(
            WizardChoice(
                value=value,
                label=label,
                description=_opt(c.get("description"), 400),
                group=_opt(c.get("group"), 60),
            )
        )
    if not choices:
        raise WizardError("The AI proposed a question without choices")
    title = _text(q.get("title"), 240)
    if not title:
        raise WizardError("The AI proposed a question without text")
    topic = _slug(q.get("topic"), f"q{answered + 1}")
    limit = MAX_TARGET_CHOICES if topic == "target_system" else MAX_CHOICES
    question = WizardQuestion(
        id=_slug(q.get("id"), topic),
        topic=topic,
        title=title,
        help=_opt(q.get("help"), 500),
        choices=choices[:limit],
        allow_other=bool(q.get("allow_other", True)),
    )
    return False, question, max(remaining, 1)


def proposal_from_raw(raw: Any) -> TargetProposal:
    """Structure cible d'une réponse brute (sans vérification : c'est le service qui confronte au
    Registry)."""
    if not isinstance(raw, dict):
        raise WizardError("The AI answer is not readable")
    fields: list[TargetField] = []
    names: set[str] = set()
    for f in raw.get("fields") or []:
        if not isinstance(f, dict):
            continue
        name = _text(f.get("name"), 120)
        if not name or name.casefold() in names:
            continue
        names.add(name.casefold())
        length = f.get("length")
        fields.append(
            TargetField(
                name=name,
                label=_text(f.get("label"), 200) or name,
                type=_text(f.get("type"), 60) or "text",
                length=length if isinstance(length, int) and 0 < length < 100000 else None,
                required=f.get("required") is True,
                example=_opt(f.get("example"), 200),
                description=_opt(f.get("description"), 400),
            )
        )
        if len(fields) == MAX_FIELDS:
            break
    if not fields:
        raise WizardError("The AI did not propose any target field")
    rows: list[list[str]] = []
    for r in raw.get("sample_rows") or []:
        if isinstance(r, list):
            cells = [_text(c, 200) for c in r][: len(fields)]
            rows.append(cells + [""] * (len(fields) - len(cells)))
        if len(rows) == SAMPLE_ROWS:
            break
    config = [
        ConfigItem(label=_text(c.get("label"), 160), value=_text(c.get("value"), 300))
        for c in raw.get("configuration") or []
        if isinstance(c, dict) and _text(c.get("label"), 160) and _text(c.get("value"), 300)
    ]
    return TargetProposal(
        title=_text(raw.get("title"), 200) or "Target structure",
        summary=_text(raw.get("summary"), 1200),
        entity=_opt(raw.get("entity"), 200),
        template_name=_opt(raw.get("template_name"), 200),
        configuration=config,
        fields=fields,
        rows=rows,
        notes=[n for n in (_text(x, 400) for x in raw.get("notes") or []) if n][:8],
    )
