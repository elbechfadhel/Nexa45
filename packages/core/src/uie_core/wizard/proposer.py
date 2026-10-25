"""Contrat entre l'assistant de migration et un LLM (implémentations dans `uie_ai`, jamais importées ici).

Le Core prépare la requête (réponses déjà données + faits du Registry) ; le proposer renvoie une réponse BRUTE
(JSON structuré) avec sa provenance. Le Core la valide (`uie_core.wizard.model`). Un proposer ne lève jamais :
il renvoie une erreur classée. Données envoyées : les réponses de l'utilisateur et les noms / versions
connus du Registry, jamais un secret, une connexion, une valeur métier d'un système connecté."""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from uie_core.domains import ProposalMeta
from uie_core.wizard.model import WizardAnswer


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RegistrySystemFact(_Model):
    """Système du Registry de Nexa45 : avec schéma publié (versions, interfaces vérifiées) ou seulement CONNU
    (identifié : aucune version, aucun champ, aucune capacité supposés)."""

    code: str
    name: str
    versions: list[str]  # « 17 » ou « Buchungsstapel 13 » (produit ≠ système) ; vide si seulement connu
    interfaces: list[str]
    has_schema: bool = True  # au moins un snapshot publié
    category: str | None = None
    description: str | None = None


class WizardRequest(_Model):
    answers: list[WizardAnswer]
    registry: list[RegistrySystemFact]
    target: RegistrySystemFact | None = None  # système cible choisi, s'il est connu du Registry
    max_questions: int
    sample_rows: int
    # simple : questions courtes pour un utilisateur non technique ; expert : vocabulaire de l'éditeur
    mode: Literal["simple", "expert"] = "simple"


class WizardRaw(_Model):
    data: Any = None
    meta: ProposalMeta
    error: str | None = None


@runtime_checkable
class MigrationWizardProposer(Protocol):
    def wizard_question(self, request: WizardRequest) -> WizardRaw:
        """Question suivante (ou fin) d'après les réponses déjà données."""
        ...

    def wizard_target(self, request: WizardRequest) -> WizardRaw:
        """Structure cible proposée (champs, types, exemples, lignes fictives)."""
        ...


class MissingFieldFact(_Model):
    """Champ sans valeur, tel que la structure le décrit (aucune donnée du client)."""

    name: str
    label: str
    type: str
    kind: str  # choice | boolean | reference | number | date | text
    required: bool
    allowed: list[dict[str, str]] = []  # valeurs admises du Registry (value, label)
    reference_by: Literal["name", "identifier"] | None = (
        None  # référence désignée par son nom ou un identifiant
    )
    description: str | None = None
    example: str | None = None


class AnswerFact(_Model):
    question: str
    answer: str


class WizardMissingRequest(_Model):
    system: str
    version: str
    data_type: str
    mode: Literal["simple", "expert"] = "simple"
    answers: list[AnswerFact] = []  # réponses déjà données dans le Wizard (pour ne pas les redemander)
    fields: list[MissingFieldFact]


@runtime_checkable
class WizardMissingProposer(Protocol):
    def wizard_missing(self, request: WizardMissingRequest) -> WizardRaw:
        """Question simple et suggestions (non vérifiées) pour chaque champ manquant ; jamais
        d'identifiant."""
        ...


class WizardDataTypeRequest(_Model):
    """Type de données d'un fichier quand les règles n'ont pas tranché : en-têtes et exemples MASQUÉS
    seulement, parmi des types offerts (jamais un autre)."""

    system: str
    version: str
    columns: list[dict[str, Any]]  # {header, examples} (exemples masqués)
    options: list[str]  # types de données acceptés pour cette cible


@runtime_checkable
class WizardDataTypeProposer(Protocol):
    def wizard_data_type(self, request: WizardDataTypeRequest) -> WizardRaw:
        """{data_type: une des options | null, confidence: 0..1}."""
        ...
