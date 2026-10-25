"""Format des fichiers du domain pack catalog (validés au chargement, erreurs explicites)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from uie_core.domains import (
    DomainKey,
    DomainPackError,
    EntityRole,
    FieldGroup,
    FieldOrigin,
    Relevance,
    RelevanceRule,
    VocabularyEntry,
)


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EntitySpec(_Spec):
    technical_name: str
    role: EntityRole


class FieldSpec(_Spec):
    entity: str
    field: str
    group: FieldGroup
    key: str | None = None  # semantic_key déclarée par le pack (V1b) : prime sur les alias génériques


class MediaDetectSpec(_Spec):
    native_types: list[str] = []
    name_patterns: list[str] = []

    @field_validator("name_patterns")
    @classmethod
    def _regex(cls, value: list[str]) -> list[str]:
        for p in value:
            re.compile(p)
        return value


class MediaSpec(_Spec):
    detect: MediaDetectSpec = MediaDetectSpec()
    main: str = "highest_resolution"
    variant_level_roles: list[EntityRole] = []

    @field_validator("main")
    @classmethod
    def _main(cls, value: str) -> str:
        if value != "highest_resolution":
            raise ValueError("seule la règle 'highest_resolution' est supportée")
        return value


class ValueMapSpec(_Spec):
    """Traduction des valeurs d'un champ source (sélection) en valeurs canoniques du vocabulaire."""

    entity: str
    field: str
    key: str
    values: dict[str, str] = Field(min_length=1)
    ignore: list[str] = []


class FieldDefaultsSpec(_Spec):
    listed_field_relevance: Relevance = Relevance.RELEVANT
    unlisted_standard_field_relevance: Relevance = Relevance.POSSIBLE
    unlisted_field_group: FieldGroup = FieldGroup.OTHER
    technical_fields_group: FieldGroup = FieldGroup.TECHNICAL
    technical_fields_relevance: Relevance = Relevance.NOT_RELEVANT


class SemanticsSpec(_Spec):
    """Règles de pertinence ordonnées (V1b) ; le vocabulaire vient de catalog/vocabulary.yaml."""

    known_threshold: float = Field(default=0.90, gt=0, le=1)
    structure_checks: list[EntityRole] = []
    rules: list[RelevanceRule]


class ImportDefaultSpec(_Spec):
    absent: str | None = None
    empty: str | None = None


class DomainPackSpec(_Spec):
    domain: DomainKey
    system: str
    definition_version: str
    versions: list[str] = []  # vide = toutes les versions
    entities: list[EntitySpec]
    fields: list[FieldSpec] = []
    media: MediaSpec = MediaSpec()
    field_defaults: FieldDefaultsSpec = FieldDefaultsSpec()
    semantics: SemanticsSpec | None = None
    value_maps: list[ValueMapSpec] = []
    # nom, dans la cible (installation standard), de chaque unité canonique (« unit » → « Units »)
    unit_names: dict[str, str] = {}
    # unité, dans la cible, d'un champ chiffré de l'entité principale (« weight » → « kg ») : code canonique
    field_units: dict[str, str] = {}
    # import de fichier de la cible : valeur d'un champ quand sa colonne est absente / sa cellule vide
    import_defaults: dict[str, ImportDefaultSpec] = {}

    @model_validator(mode="after")
    def _consistency(self) -> DomainPackSpec:
        names = [e.technical_name for e in self.entities]
        if len(names) != len(set(names)):
            raise ValueError("entité déclarée deux fois")
        keys = [(f.entity, f.field) for f in self.fields]
        dupes = sorted({f"{e}.{f}" for e, f in keys if keys.count((e, f)) > 1})
        if dupes:
            raise ValueError(f"champs déclarés deux fois : {dupes}")
        unknown = sorted({f.entity for f in self.fields} - set(names))
        if unknown:
            raise ValueError(f"champs sur des entités hors scope : {unknown}")
        declared = {(f.entity, f.field): f.key for f in self.fields}
        for vm in self.value_maps:
            if declared.get((vm.entity, vm.field)) != vm.key:
                raise ValueError(f"value_map {vm.entity}.{vm.field} : champ non déclaré avec la clé {vm.key}")
            overlap = sorted(set(vm.values) & set(vm.ignore))
            if overlap:
                raise ValueError(
                    f"value_map {vm.entity}.{vm.field} : valeurs à la fois traduites et ignorées {overlap}"
                )
        return self


class ProtectedFieldsSpec(_Spec):
    """<système>/protected_fields.yaml : liste de sûreté (jamais NOT_RELEVANT par une règle)."""

    domain: DomainKey
    system: str
    origins: list[FieldOrigin] = []
    media: bool = False
    fields: dict[str, list[str]]  # entité → champs

    def paths(self) -> list[str]:
        return sorted(f"{entity}.{field}" for entity, names in self.fields.items() for field in names)


class LlmPromptFileSpec(_Spec):
    """catalog/llm/<nom>.yaml : prompt de la proposition sémantique par LLM d'un domaine (V1c)."""

    domain: DomainKey
    # rôle du prompt : proposition sémantique d'un champ (V1c) ou colonnes d'un import (V2c.3)
    purpose: Literal["field_proposal", "import_columns"] = "field_proposal"
    version: str
    system: str = Field(min_length=1)
    few_shots: list[dict[str, Any]] = []


# Une clé du vocabulaire = le contrat du Core (validé ici au chargement du fichier)
VocabularyKey = VocabularyEntry


class VocabularySpec(_Spec):
    domain: DomainKey
    vocabulary_version: str
    keys: list[VocabularyEntry]
    # valeurs canoniques des clés énumérées (ex. document_type) : cibles possibles des value_maps des packs ;
    # hors du modèle Vocabulary du Core (checksum des règles sémantiques inchangé)
    values: dict[str, list[str]] = {}

    @model_validator(mode="after")
    def _unique(self) -> VocabularySpec:
        names = [k.key for k in self.keys]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"semantic_key en double : {dupes}")
        unknown = sorted(set(self.values) - set(names))
        if unknown:
            raise ValueError(f"valeurs canoniques de clés inconnues : {unknown}")
        return self


def read_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DomainPackError(f"{path} : lecture impossible ({exc})") from None
    if not isinstance(data, dict):
        raise DomainPackError(f"{path} : document YAML attendu (mapping)")
    return data


def load_pack_spec(path: Path) -> DomainPackSpec:
    try:
        return DomainPackSpec.model_validate(read_yaml(path))
    except ValidationError as exc:
        raise DomainPackError(f"{path} : domain pack invalide\n{exc}") from None


def load_protected_fields_spec(path: Path) -> ProtectedFieldsSpec:
    try:
        return ProtectedFieldsSpec.model_validate(read_yaml(path))
    except ValidationError as exc:
        raise DomainPackError(f"{path} : champs protégés invalides\n{exc}") from None


def load_llm_prompt_spec(path: Path) -> LlmPromptFileSpec:
    try:
        return LlmPromptFileSpec.model_validate(read_yaml(path))
    except ValidationError as exc:
        raise DomainPackError(f"{path} : prompt LLM invalide\n{exc}") from None


def load_vocabulary_spec(path: Path) -> VocabularySpec:
    try:
        return VocabularySpec.model_validate(read_yaml(path))
    except ValidationError as exc:
        raise DomainPackError(f"{path} : vocabulaire invalide\n{exc}") from None
