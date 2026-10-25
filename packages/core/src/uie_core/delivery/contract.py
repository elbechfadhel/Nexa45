"""Contrat d'ENVOI vers un système cible par son API (générique : tout domaine, tout système).

Le Core décide QUOI envoyer (requêtes préparées et validées, sélection humaine, historique D11) ; le
connecteur sait COMMENT parler à son API : créer, relire, mettre à jour avec contrôle de version, chercher
des enregistrements existants. Un `RecordSender` est enregistré par un connecteur
(`@RecordSenderRegistry.register`), jamais importé par le Core. Il ne connaît ni la source ni le domaine.

Erreurs : toujours une `SendError` classée (jamais une exception de transport) ; message sans secret ni
valeur métier. `UNKNOWN_OUTCOME` = l'écriture a pu aboutir (délai dépassé, passerelle, réseau) : le Core ne
la retente JAMAIS d'office."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from uie_core.instances import ConnectionContext


class SendErrorKind(StrEnum):
    REJECTED = "REJECTED"  # refus définitif de la cible (données) : rien n'a été écrit
    CONFLICT = "CONFLICT"  # l'enregistrement cible a changé depuis le dernier envoi (version)
    NOT_FOUND = "NOT_FOUND"  # l'enregistrement cible lié n'existe plus
    ACCESS = "ACCESS"  # clé refusée ou permission manquante : l'envoi s'arrête
    RATE_LIMITED = (
        "RATE_LIMITED"  # limite de débit malgré les attentes : rien n'a été écrit, l'envoi s'arrête
    )
    UNAVAILABLE = "UNAVAILABLE"  # lecture impossible (réseau, serveur) : rien n'a été écrit
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"  # écriture peut-être faite : jamais retentée automatiquement


STOPPING_KINDS = frozenset({SendErrorKind.ACCESS, SendErrorKind.RATE_LIMITED})


class SendError(Exception):
    def __init__(
        self,
        kind: SendErrorKind,
        message: str,
        *,
        status: int | None = None,
        trace_id: str | None = None,
        fields: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.trace_id = trace_id
        self.fields = fields


class RemoteRecord(BaseModel):
    """Enregistrement du système cible tel que le connecteur le décrit (jamais stocké en entier)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    version: int | None = None
    label: str | None = None  # libellé lisible (affichage seulement, jamais enregistré)
    archived: bool = False


class TargetChoice(BaseModel):
    """Valeur possible lue dans la cible (ex. une catégorie) : lecture seule, jamais stockée en entier."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    label: str
    group: str | None = None
    kind: str | None = None  # sorte déclarée par la cible (comparée à `option_kind_by_role` du binding)


class RecordSender(ABC):
    system_code: ClassVar[str]
    interface_code: ClassVar[str]

    def __init__(self, ctx: ConnectionContext) -> None:
        self.ctx = ctx

    @abstractmethod
    def create(self, resource: str, body: dict[str, Any]) -> RemoteRecord: ...

    @abstractmethod
    def update(
        self, resource: str, target_id: str, expected_version: int | None, body: dict[str, Any]
    ) -> RemoteRecord:
        """Met à jour SANS rien effacer de ce que la requête ne porte pas ; CONFLICT si la version actuelle
        de la cible diffère de `expected_version`."""

    @abstractmethod
    def get(self, resource: str, target_id: str) -> RemoteRecord: ...

    @abstractmethod
    def find_candidates(self, resource: str, body: dict[str, Any]) -> list[RemoteRecord]:
        """Enregistrements EXISTANTS de la cible qui pourraient être le même (lecture seule)."""

    def options(self, resource: str) -> list[TargetChoice]:
        """Valeurs possibles d'une ressource de la cible (LECTURE seule) ; facultatif."""
        raise SendError(SendErrorKind.UNAVAILABLE, f"No options for {resource}")

    def close(self) -> None:  # noqa: B027 — facultatif
        pass

    def __enter__(self) -> RecordSender:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class RecordSenderError(Exception):
    """Aucun envoi possible vers ce système (→ 400)."""


class RecordSenderRegistry:
    _senders: ClassVar[dict[tuple[str, str], type[RecordSender]]] = {}

    @classmethod
    def register(cls, sender_cls: type[RecordSender]) -> type[RecordSender]:
        cls._senders[(sender_cls.system_code, sender_cls.interface_code)] = sender_cls
        return sender_cls

    @classmethod
    def supports(cls, system: str, interface: str) -> bool:
        return (system, interface) in cls._senders

    @classmethod
    def create(cls, system: str, interface: str, ctx: ConnectionContext) -> RecordSender:
        try:
            sender_cls = cls._senders[(system, interface)]
        except KeyError:
            raise RecordSenderError(f"Aucun envoi disponible vers {system} ({interface})") from None
        return sender_cls(ctx)


class RecordSenderResolver:
    """Point d'injection (tests : faux expéditeur)."""

    def create(self, system: str, interface: str, ctx: ConnectionContext) -> RecordSender:
        return RecordSenderRegistry.create(system, interface, ctx)

    def supports(self, system: str, interface: str) -> bool:
        return RecordSenderRegistry.supports(system, interface)
