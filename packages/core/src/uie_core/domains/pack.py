"""Contrat générique DomainPack + registre (même principe que LoaderRegistry / DiscoveryProviderRegistry).

Un domain pack sait, pour UN domaine et UN système, quelles entités et quels champs appartiennent au
domaine et avec quel rôle / groupe. Sa connaissance est de la donnée (fichiers du dossier domain-packs/),
chargée par son adaptateur ; le Core ne lit jamais ces fichiers et ne connaît aucun nom concret.

Règle de version : le pack DÉCLARE, `resolve` RÉSOUT contre un snapshot donné. Un élément déclaré mais
absent du snapshot est rendu `present=False`, jamais une erreur.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from uie_core.domains.model import DomainKey, ResolvedDomain
from uie_core.domains.proposer import PromptSpec
from uie_core.domains.semantics import SemanticSpec, Vocabulary
from uie_core.registry.schema import SchemaSnapshotModel

FIELD_PROMPT = "field_proposal"  # prompt de la proposition sémantique d'un champ (V1c)


class DomainPackError(Exception):
    pass


class ValueMap(BaseModel):
    """Traduction des VALEURS d'un champ source (ex. une sélection) en valeurs canoniques du domaine,
    déclarée par le pack d'un système (donnée), jamais par le Core. Valeur source :
    dans `values` → valeur canonique ; dans `ignore` → hors du domaine (l'enregistrement n'est pas retenu) ;
    ailleurs → NON PRISE EN CHARGE (le lecteur la signale, jamais devinée)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entity: str
    field: str
    key: str  # semantic key du vocabulaire portée par ce champ
    values: dict[str, str] = Field(min_length=1)  # valeur source → valeur canonique
    ignore: list[str] = []

    @property
    def path(self) -> str:
        return f"{self.entity}.{self.field}"


class ImportDefault(BaseModel):
    """Ce que l'import de fichier de la cible écrit dans un champ quand la colonne est ABSENTE (sa valeur par
    défaut) ou quand la cellule est VIDE. None : inconnu. Donnée du pack, jamais une valeur écrite par UIE."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    absent: str | None = None
    empty: str | None = None


class DomainPack(ABC):
    domain_key: ClassVar[DomainKey]
    system_code: ClassVar[str]

    @property
    @abstractmethod
    def name(self) -> str:
        """Identifiant lisible du pack (provenance consignée dans la définition)."""

    @property
    @abstractmethod
    def definition_version(self) -> str:
        """Version du contenu du pack : à incrémenter à chaque modification (définitions immutables)."""

    def supports(self, version: str) -> bool:
        """Versions du système gérées (toutes par défaut)."""
        return True

    @abstractmethod
    def scope_entities(self) -> list[str]:
        """Entités déclarées par le pack : le service ne charge que celles-ci depuis un snapshot."""

    @abstractmethod
    def resolve(self, snapshot: SchemaSnapshotModel) -> ResolvedDomain:
        """Entités et champs du domaine dans CE snapshot (rôle, groupe, pertinence, médias, métadonnées)."""

    def semantics(self) -> SemanticSpec | None:
        """Vocabulaire, règles de pertinence et clés déclarées (V1b). None : pas d'enrichissement."""
        return None

    def value_maps(self) -> list[ValueMap]:
        """Traductions de valeurs déclarées par le pack (aucune par défaut)."""
        return []

    def unit_names(self) -> dict[str, str]:
        """Nom, dans la cible, de chaque unité canonique (« unit » → nom de l'unité « pièce » du système) ;
        {} : inconnus (les valeurs d'unités du fichier sont alors gardées telles quelles)."""
        return {}

    def field_units(self) -> dict[str, str]:
        """Unité, dans la cible, d'un champ chiffré de l'entité principale (code canonique) ; {} :
        inconnue."""
        return {}

    def import_defaults(self) -> dict[str, ImportDefault]:
        """Champ de l'entité principale → valeur que l'import de fichier de la cible lui donne sans valeur
        (colonne absente / cellule vide) ; {} : inconnu."""
        return {}

    def media_size(self, field: str) -> tuple[str, int] | None:
        """Famille et taille d'un champ média (V2a : vignette / grand format du catalogue). None : inconnue,
        le catalogue lit alors le champ principal."""
        return None


class DomainPackRegistry:
    """Registre des domain packs, résolus par domaine + système + version."""

    _packs: ClassVar[dict[tuple[DomainKey, str], type[DomainPack]]] = {}
    _vocabularies: ClassVar[dict[DomainKey, Callable[[], Vocabulary]]] = {}
    # (domaine, rôle du prompt) : "field_proposal" (V1c) | "import_columns" (V2c.3)
    _prompts: ClassVar[dict[tuple[DomainKey, str], Callable[[], PromptSpec]]] = {}

    @classmethod
    def register(cls, pack_cls: type[DomainPack]) -> type[DomainPack]:
        cls._packs[(pack_cls.domain_key, pack_cls.system_code)] = pack_cls
        return pack_cls

    @classmethod
    def register_vocabulary(cls, domain_key: DomainKey, loader: Callable[[], Vocabulary]) -> None:
        """Vocabulaire contrôlé d'un domaine (commun à tous les systèmes)."""
        cls._vocabularies[domain_key] = loader

    @classmethod
    def vocabulary(cls, domain_key: DomainKey) -> Vocabulary:
        try:
            return cls._vocabularies[domain_key]()
        except KeyError:
            raise DomainPackError(
                f"Aucun vocabulaire enregistré pour le domaine {domain_key.value}"
            ) from None

    @classmethod
    def register_prompt(
        cls, domain_key: DomainKey, loader: Callable[[], PromptSpec], purpose: str = FIELD_PROMPT
    ) -> None:
        """Prompt LLM d'un domaine (fichiers du domain pack) : proposition sémantique d'un champ (V1c) ou
        correspondance des colonnes d'un import (V2c.3)."""
        cls._prompts[(domain_key, purpose)] = loader

    @classmethod
    def prompt(cls, domain_key: DomainKey, purpose: str = FIELD_PROMPT) -> PromptSpec:
        try:
            return cls._prompts[(domain_key, purpose)]()
        except KeyError:
            raise DomainPackError(
                f"Aucun prompt LLM « {purpose} » enregistré pour le domaine {domain_key.value}"
            ) from None

    @classmethod
    def available(cls) -> list[str]:
        return sorted(f"{d.value}/{s}" for d, s in cls._packs)

    @classmethod
    def domains_for(cls, system: str) -> list[DomainKey]:
        """Domaines qu'un système sait FOURNIR (un domain pack existe pour lui), dans un ordre stable."""
        return sorted((d for d, s in cls._packs if s == system), key=lambda d: d.value)

    @classmethod
    def resolve(cls, domain_key: DomainKey, system: str, version: str) -> DomainPack:
        try:
            pack = cls._packs[(domain_key, system)]()
        except KeyError:
            raise DomainPackError(
                f"Aucun domain pack {domain_key.value} pour '{system}'. Disponibles : {cls.available()}"
            ) from None
        if not pack.supports(version):
            raise DomainPackError(f"Le domain pack {pack.name} ne gère pas la version {version}")
        return pack


class DomainPackResolver:
    """Point d'injection : par défaut le registre ; les tests peuvent fournir un autre resolver."""

    def resolve(self, domain_key: DomainKey, system: str, version: str) -> DomainPack:
        return DomainPackRegistry.resolve(domain_key, system, version)

    def vocabulary(self, domain_key: DomainKey) -> Vocabulary:
        return DomainPackRegistry.vocabulary(domain_key)

    def prompt(self, domain_key: DomainKey, purpose: str = FIELD_PROMPT) -> PromptSpec:
        return DomainPackRegistry.prompt(domain_key, purpose)
