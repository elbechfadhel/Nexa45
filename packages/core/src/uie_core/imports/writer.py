"""Contrat d'ÉCRITURE dans un système cible (V2d.2), symétrique de la lecture (`uie_core.catalog.reader`).

Le connecteur SAIT écrire ; il ne sait pas ce que les données veulent dire. Le Core lui demande, avec les
entités et champs techniques venus du plan d'import (donc des bindings de l'instance) :
    find_by          enregistrements dont un champ vaut une des valeurs (identité de mise à jour)
    resolve_labels   libellés → enregistrements d'une entité de relation ; création des manquants si permis ;
                     plusieurs correspondances = AMBIGUOUS, jamais deviné
    create_with_key  création IDEMPOTENTE par clé stable : rejouée avec la même clé, elle ne crée pas un
                     second enregistrement (reprise après une réponse perdue)
    update           mise à jour d'un enregistrement (seules les valeurs données sont écrites)
Aucune suppression dans ce contrat. Valeurs : scalaires, `RecordId`, `RecordIds` ; le connecteur les
traduit dans le format de son système. Toute erreur devient `CatalogWriteError` (classée, sans secret).

Les implémentations vivent dans les connecteurs (`@CatalogWriterRegistry.register`) ; le Core ne les importe
jamais.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from uie_core.instances.discovery import ConnectionContext


class WriteErrorKind(StrEnum):
    CONNECTION = "CONNECTION"  # système injoignable, coupure : la suite de l'exécution s'arrête (reprise)
    RIGHTS = "RIGHTS"  # droits insuffisants
    VALIDATION = "VALIDATION"  # valeur refusée par le système
    UNEXPECTED = "UNEXPECTED"


class CatalogWriteError(Exception):
    """Échec d'écriture ; `message` est assaini (jamais de secret), le portail n'affiche que `kind`."""

    def __init__(self, kind: WriteErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message[:500]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RecordId(_Model):
    """Référence à un enregistrement du système cible (identifiant opaque, jamais montré au fournisseur)."""

    kind: Literal["record_id"] = "record_id"
    id: str


class RecordIds(_Model):
    kind: Literal["record_ids"] = "record_ids"
    ids: list[str]


WriteValue = str | int | float | bool | RecordId | RecordIds


class LabelStatus(StrEnum):
    FOUND = "FOUND"
    CREATED = "CREATED"
    AMBIGUOUS = "AMBIGUOUS"  # plusieurs enregistrements portent ce libellé : rien n'est écrit
    NOT_FOUND = "NOT_FOUND"  # absent et création non permise


class LabelResolution(_Model):
    label: str
    status: LabelStatus
    id: str | None = None


class CatalogTarget(ABC):
    """Connexion ouverte en écriture sur un système (une par exécution d'import)."""

    @abstractmethod
    def find_by(
        self, entity: str, field: str, values: list[str], *, include_archived: bool = True
    ) -> dict[str, list[str]]:
        """Valeur → identifiants des enregistrements dont `field` vaut exactement cette valeur."""

    @abstractmethod
    def resolve_labels(
        self, entity: str, labels: list[str], *, create_missing: bool
    ) -> list[LabelResolution]:
        """Un résultat par libellé, dans l'ordre (comparaison sans casse, décidée par le connecteur)."""

    @abstractmethod
    def create_with_key(self, entity: str, key: str, values: dict[str, WriteValue]) -> str:
        """Crée (ou, si la clé existe déjà, met à jour) l'enregistrement de clé `key` ; rend son id."""

    @abstractmethod
    def update(self, entity: str, record_id: str, values: dict[str, WriteValue]) -> None: ...


class CatalogWriter(ABC):
    """Écrivain d'un système (enregistré par un connecteur, jamais importé par le Core)."""

    system_code: ClassVar[str]

    def supports(self, version: str, interface: str) -> bool:
        return True

    @abstractmethod
    def open(self, ctx: ConnectionContext) -> CatalogTarget:
        """Ouvre une connexion en écriture (authentification comprise) ; lève CatalogWriteError."""


class CatalogWriterRegistry:
    _writers: ClassVar[dict[str, type[CatalogWriter]]] = {}

    @classmethod
    def register(cls, writer_cls: type[CatalogWriter]) -> type[CatalogWriter]:
        cls._writers[writer_cls.system_code] = writer_cls
        return writer_cls

    @classmethod
    def get(cls, system: str, version: str, interface: str) -> CatalogWriter:
        writer_cls = cls._writers.get(system)
        writer = writer_cls() if writer_cls else None
        if writer is None or not writer.supports(version, interface):
            message = f"Aucun écrivain de catalogue pour {system} {version} ({interface})"
            raise CatalogWriteError(WriteErrorKind.UNEXPECTED, message)
        return writer


class CatalogWriterResolver:
    """Point d'injection (tests, racines de composition) : résout l'écrivain d'une interface."""

    def resolve(self, system: str, version: str, interface: str) -> CatalogWriter:
        return CatalogWriterRegistry.get(system, version, interface)
