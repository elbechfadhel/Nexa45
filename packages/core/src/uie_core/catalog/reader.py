"""Contrat de lecture des données d'un système source (V2a) : le connecteur SAIT LIRE, il ne sait pas ce que
les données veulent dire. Le Core lui demande « entité E, champs [f…], page, filtre » ; le sens (quel champ
est le SKU, quelle entité est la variante) vient du plan de lecture, donc des semantic bindings.

Valeurs rendues normalisées par le connecteur :
    référence  → Ref(id, label) ou None      collection → liste d'ids (str)
    binaire    → bool (présence) : le contenu ne se lit qu'avec `read_binary`
    vide       → None (sauf booléen)
Lecture seule : aucune méthode d'écriture n'existe dans ce contrat.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from uie_core.instances.discovery import ConnectionContext


class CatalogReadError(Exception):
    """Échec de lecture (réseau, droits, réponse inattendue) ; message sans secret."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FieldRequest(_Model):
    name: str
    data_type: str  # type logique du Registry (string, decimal, reference, collection, binary...)


class Condition(_Model):
    """Condition de lecture GÉNÉRIQUE (aucun nom de système ni de domaine) :
    `in` (valeur parmi `values`), `eq` (une valeur), `between` (deux bornes INCLUSES, début ≤ fin).
    `data_type` (type logique du Registry) dit au connecteur comment comparer : une référence se compare par
    identifiant, une date / un nombre par valeur."""

    field: str
    op: Literal["in", "eq", "between"] = "in"
    values: list[str]
    data_type: str | None = None

    @model_validator(mode="after")
    def _shape(self) -> Condition:
        if self.op == "eq" and len(self.values) != 1:
            raise ValueError(f"{self.field} : « eq » attend une valeur")
        if self.op == "between":
            if len(self.values) != 2:
                raise ValueError(f"{self.field} : « between » attend deux bornes")
            low, high = (_ordered(v, self.data_type) for v in self.values)
            if low > high:
                raise ValueError(f"{self.field} : borne de début après la borne de fin")
        return self


def _ordered(value: str, data_type: str | None) -> Any:
    """Valeur comparable d'une borne : nombre pour integer / decimal, sinon texte (dates ISO comprises)."""
    if data_type in ("integer", "decimal"):
        try:
            return Decimal(value)
        except InvalidOperation:
            raise ValueError(f"borne non numérique : {value}") from None
    if data_type in ("date", "datetime"):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            raise ValueError(f"borne de date invalide : {value}") from None
    return value


class Ref(_Model):
    id: str
    label: str | None = None


class SourceRecord(_Model):
    id: str
    label: str | None = None  # libellé d'affichage de l'enregistrement dans le système source
    values: dict[str, Any] = {}


class CatalogSource(ABC):
    """Connexion ouverte en lecture sur un système (une par synchronisation ou par image)."""

    @abstractmethod
    def count(
        self, entity: str, where: list[Condition] | None = None, *, include_archived: bool = True
    ) -> int: ...

    @abstractmethod
    def read_page(
        self,
        entity: str,
        fields: list[FieldRequest],
        *,
        where: list[Condition] | None = None,
        offset: int = 0,
        limit: int = 200,
        include_archived: bool = True,
    ) -> list[SourceRecord]:
        """Page d'enregistrements, dans un ordre stable (identifiant croissant)."""

    @abstractmethod
    def read(
        self, entity: str, ids: list[str], fields: list[FieldRequest], *, include_archived: bool = True
    ) -> list[SourceRecord]: ...

    @abstractmethod
    def read_binary(self, entity: str, record_id: str, field: str) -> bytes | None: ...


class CatalogReader(ABC):
    """Lecteur de catalogue d'un système (enregistré par un connecteur, jamais importé par le Core)."""

    system_code: ClassVar[str]

    def supports(self, version: str, interface: str) -> bool:
        return True

    @abstractmethod
    def open(self, ctx: ConnectionContext) -> CatalogSource:
        """Ouvre une connexion en lecture (authentification comprise) ; lève CatalogReadError."""


class CatalogReaderRegistry:
    _readers: ClassVar[dict[str, type[CatalogReader]]] = {}

    @classmethod
    def register(cls, reader_cls: type[CatalogReader]) -> type[CatalogReader]:
        cls._readers[reader_cls.system_code] = reader_cls
        return reader_cls

    @classmethod
    def get(cls, system: str, version: str, interface: str) -> CatalogReader:
        reader_cls = cls._readers.get(system)
        reader = reader_cls() if reader_cls else None
        if reader is None or not reader.supports(version, interface):
            raise CatalogReadError(f"Aucun lecteur de catalogue pour {system} {version} ({interface})")
        return reader


class CatalogReaderResolver:
    """Point d'injection (tests, racines de composition) : résout le lecteur d'une interface."""

    def resolve(self, system: str, version: str, interface: str) -> CatalogReader:
        return CatalogReaderRegistry.get(system, version, interface)
