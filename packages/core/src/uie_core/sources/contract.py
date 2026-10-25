"""Contrat de LECTURE d'une source à schéma fixe (générique, aucun nom de système).

Une source à schéma fixe (API publique d'un éditeur) n'a rien à découvrir ni à analyser : son contrat est
dans le Registry et le sens de ses champs est DÉCLARÉ par un binding de source (données, `SourceBinding`).
Le connecteur ne sait que LIRE des enregistrements bruts d'une ressource (JSON) ; il n'en connaît pas le
sens, et le Core n'en connaît pas le protocole.

    connecteur ──► RecordSource.records(ressource) ──► enregistrements bruts (dict)
    Core       ──► SourceBinding (chemins → semantic keys) ──► CDM

Lecture seule : un `RecordSource` n'écrit jamais. Une erreur est rendue classée (`RecordSourceError`), sans
secret ni corps de réponse."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any, ClassVar

from uie_core.instances.discovery import ConnectionContext


class RecordSourceError(Exception):
    """Lecture impossible (accès refusé, réseau, réponse inattendue) : message simple, sans secret."""

    def __init__(self, message: str, *, kind: str = "UNEXPECTED") -> None:
        super().__init__(message)
        self.kind = kind  # ACCESS | UNREACHABLE | RATE_LIMITED | UNEXPECTED


@dataclass(frozen=True)
class RecordBatch:
    records: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False  # plafond atteint : la source contient d'autres enregistrements


@dataclass(frozen=True)
class RecordPeriod:
    """Filtre de période : la date au chemin `path` (contrat) entre `start` et `end` inclus. Un lecteur qui ne
    sait pas filtrer sur ce chemin lève `RecordSourceError` (jamais une lecture non filtrée en silence)."""

    path: str
    start: date
    end: date


class RecordSource(ABC):
    """Connexion ouverte en lecture sur une source à schéma fixe."""

    @abstractmethod
    def records(
        self,
        resource: str,
        *,
        limit: int,
        period: RecordPeriod | None = None,
        detail: str | None = None,
    ) -> RecordBatch:
        """Enregistrements bruts d'une ressource du contrat (au plus `limit`), dans l'ordre de la source.
        `detail` : ressource relue par identifiant pour chaque enregistrement, champs fusionnés (le détail
        prime sur la liste)."""

    def close(self) -> None:  # noqa: B027 — optionnel
        pass

    def __enter__(self) -> RecordSource:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class RecordSourceReader(ABC):
    """Lecteur d'une source à schéma fixe (enregistré par un connecteur, jamais importé par le Core)."""

    system_code: ClassVar[str]
    interface_code: ClassVar[str]

    @abstractmethod
    def open(self, ctx: ConnectionContext) -> RecordSource:
        """Ouvre une connexion en lecture (authentification comprise) ; lève RecordSourceError."""


class RecordSourceRegistry:
    _readers: ClassVar[dict[tuple[str, str], type[RecordSourceReader]]] = {}

    @classmethod
    def register(cls, reader_cls: type[RecordSourceReader]) -> type[RecordSourceReader]:
        cls._readers[(reader_cls.system_code, reader_cls.interface_code)] = reader_cls
        return reader_cls

    @classmethod
    def supports(cls, system: str, interface: str) -> bool:
        return (system, interface) in cls._readers

    @classmethod
    def get(cls, system: str, interface: str) -> RecordSourceReader:
        try:
            return cls._readers[(system, interface)]()
        except KeyError:
            raise RecordSourceError(f"No reader available for {system} ({interface})") from None


class RecordSourceResolver:
    """Point d'injection (tests : fausse source)."""

    def open(self, system: str, interface: str, ctx: ConnectionContext) -> RecordSource:
        return RecordSourceRegistry.get(system, interface).open(ctx)
