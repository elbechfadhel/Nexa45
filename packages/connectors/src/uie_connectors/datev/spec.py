"""Notre représentation MINIMALE d'un format DATEV (fichier EXTF) et de sa version : `uie-datev-format/1`.

Un fichier = un format à une version (ex. Buchungsstapel v13) : la ligne d'en-tête EXTF (version 700) et les
colonnes du format. Chaque champ : numéro DATEV (position, identifiant stable, les formats n'ajoutent qu'en
fin),
libellé, type DATEV, longueur, décimales, obligatoire, motif (expression régulière du portail, version
courante
seulement), identifiant DATEV du champ, alias (autres libellés officiels).

Ces fichiers sont GÉNÉRÉS localement par `spec_builder` à partir des sources officielles DATEV téléchargées
et ne
sont pas versionnés (licence de redistribution non établie) ; le dépôt ne contient que des mini-specs
inventées
pour les tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

SPEC_FORMAT = "uie-datev-format/1"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DatevField(_Model):
    position: int = Field(ge=1)
    label: str
    datev_type: str  # Betrag | Text | Datum | Konto | Zahl | (en-tête : déduit du motif)
    length: int | None = None
    decimals: int | None = None
    required: bool = False
    importable: bool | None = None
    exportable: bool | None = None
    pattern: str | None = None
    source_field_id: str | None = None
    aliases: list[str] = []


class DatevFormatSpec(_Model):
    spec: Literal["uie-datev-format/1"] = SPEC_FORMAT
    system: Literal["datev"] = "datev"
    format: str  # code produit, ex. « buchungsstapel »
    format_name: str  # nom DATEV, ex. « Buchungsstapel »
    data_category: int  # ex. 21
    format_version: str  # ex. « 13 »
    header_version: str  # ex. « 700 »
    entity: str  # nom technique de l'entité du format, ex. « buchungsstapel »
    # dialecte des motifs : notation officielle DATEV (« Notation für Ausdrücke ») :
    # \w = [a-zA-Z0-9_], \d = [0-9] (ASCII), jamais les classes Unicode de Python
    pattern_syntax: Literal["ascii"] = "ascii"
    header: list[DatevField]
    fields: list[DatevField]
    source: dict[str, Any] = {}  # provenance : fichiers sources (nom, SHA-256), version du programme, date

    @classmethod
    def load(cls, path: str | Path) -> DatevFormatSpec:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(data)

    def dump(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        text = yaml.safe_dump(self.model_dump(mode="json"), allow_unicode=True, sort_keys=False, width=110)
        Path(path).write_text(text, encoding="utf-8")

    @property
    def file_name(self) -> str:
        return f"{self.format}_v{self.format_version}.yaml"
