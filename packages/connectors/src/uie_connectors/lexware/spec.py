"""Notre représentation MINIMALE du contrat de l'API publique Lexware Office : `uie-lexware-api/1`.

Lexware ne publie aucune spécification lisible par machine. Ce contrat est donc écrit et relu par nous, à
partir de la documentation officielle. Il ne contient QUE ce dont UIE a besoin pour préparer et valider une
requête : nom des ressources, opérations, chemin JSON, type, lecture seule, longueur, valeurs admises, et
l'adresse de la page officielle où chaque ressource est décrite. Il ne reproduit AUCUN texte de la
documentation : les libellés sont les nôtres.

Deux versions sont distinctes :
- `api_version` : la version de ROUTE de l'API (« v1 », segment de l'adresse) → `version` du Registry ;
- `contract_revision` : la date de l'état du contrat (tête du journal des changements officiel) → consignée
  dans la provenance du snapshot ; une évolution de l'API à route constante donne un NOUVEAU snapshot."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SPEC_FORMAT = "uie-lexware-api/1"
DEFAULT_CONTRACT = Path(__file__).parent / "contract" / "public_api_v1.yaml"

FieldType = Literal["string", "uuid", "integer", "number", "boolean", "datetime", "enum", "object", "list"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ApiField(_Model):
    path: str = Field(min_length=1)  # chemin JSON, « [0] » pour le seul élément d'une liste
    label: str  # NOTRE libellé (anglais)
    type: FieldType = "string"
    readonly: bool = False  # jamais envoyé : attribué ou calculé par le système
    protocol: bool = False  # champ technique de l'échange (ex. numéro de révision), pas une donnée métier
    presence: bool = False  # objet vide dont la seule présence a un sens
    required: bool = False  # obligatoire à la création, d'après la documentation officielle
    required_group: str | None = None  # obligatoire dès qu'une autre valeur de ce groupe est transmise
    length: int | None = Field(default=None, ge=1)
    decimals: int | None = Field(
        default=None, ge=0
    )  # décimales au plus (montants) : jamais d'arrondi silencieux
    enum: list[str] = []
    pattern: str | None = None  # forme exigée par la documentation (ex. longueurs d'un code GTIN)

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, value: str | None) -> str | None:
        if value is not None:
            re.compile(value)
        return value

    @property
    def technical_name(self) -> str:
        """Nom stable du champ dans le Registry : le chemin sans indice de liste."""
        return self.path.replace("[0]", "")


class Operations(_Model):
    read: bool = False
    search: bool = False
    create: bool = False
    update: bool = False
    delete: bool = False
    events: bool = False
    extra: list[str] = []  # opérations sans capacité générique (ex. finalize, download, upload)


class ApiResource(_Model):
    name: str = Field(pattern=r"^[a-z][a-z-]*$")
    label: str
    path: str  # adresse de la collection, version de route comprise
    doc: str  # page officielle qui décrit la ressource (référence, pas une copie)
    operations: Operations
    fields: list[ApiField] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> ApiResource:
        names = [f.technical_name for f in self.fields]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"{self.name} : champs en double {dupes}")
        return self


class LexwareApiSpec(_Model):
    spec: Literal["uie-lexware-api/1"] = SPEC_FORMAT
    system: Literal["lexware"] = "lexware"
    system_name: str
    product: str
    product_name: str
    api_version: str  # version de route
    interface: str
    contract_revision: str  # AAAA-MM-JJ : état du contrat officiel décrit ici
    sources: list[str] = Field(min_length=1)  # adresses officielles consultées
    resources: list[ApiResource] = Field(min_length=1)

    @classmethod
    def load(cls, path: str | Path | None = None) -> LexwareApiSpec:
        data: Any = yaml.safe_load(Path(path or DEFAULT_CONTRACT).read_text(encoding="utf-8"))
        return cls.model_validate(data)
