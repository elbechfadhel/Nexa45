"""Valeurs de référence RÉELLES d'un système connecté (ex. catégories, unités) pour « Complete missing
information » : lues en lecture seule dans le système de l'organisation, jamais inventées.

Contrat `ReferenceLookup` implémenté par un connecteur (`@ReferenceLookupRegistry.register`, jamais
importé par le Core). `ReferenceFinder` choisit le système connecté de l'organisation qui correspond à la
cible (même système
et même version, dernière connexion réussie) ; sans système connecté, aucune valeur n'est proposée."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from uie_core.instances import ConnectionContext, InstanceService

MAX_OPTIONS = 200
MAX_CHECK = 5000  # vérification des valeurs d'un fichier : lecture plus large (au-delà : non prouvé)


class ReferenceOption(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    value: str  # valeur à écrire dans la colonne (ex. identifiant externe), lue dans le système
    label: str  # nom lisible de l'enregistrement


class InstanceTarget(BaseModel):
    """Cible = une instance de l'organisation : système, version (du Registry), connexion et devise de la
    société lue dans le système (lecture seule ; None : inconnue)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    system: str
    product: str
    version: str
    name: str
    connected: bool
    currency: str | None = None


class InstanceChoice(BaseModel):
    """Instance de l'organisation proposée comme cible (aucune lecture du système : état de la dernière
    connexion seulement)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: uuid.UUID
    system: str
    product: str
    version: str
    name: str
    connected: bool


class ReferenceLookup(ABC):
    system_code: ClassVar[str]

    def company_currency(self, ctx: ConnectionContext) -> str | None:
        """Devise (ISO 4217) de la société du système connecté ; None si le connecteur ne sait pas la lire."""
        return None

    @abstractmethod
    def options(
        self, ctx: ConnectionContext, entity: str, attribute: str | None, limit: int = MAX_OPTIONS
    ) -> list[ReferenceOption]:
        """Enregistrements existants de `entity` ; `attribute` : attribut désigné par la colonne (ex. un
        identifiant externe) ; seuls les enregistrements qui en ont un sont rendus. Lecture seule."""


class ReferenceLookupRegistry:
    _lookups: ClassVar[dict[str, type[ReferenceLookup]]] = {}

    @classmethod
    def register(cls, lookup_cls: type[ReferenceLookup]) -> type[ReferenceLookup]:
        cls._lookups[lookup_cls.system_code] = lookup_cls
        return lookup_cls

    @classmethod
    def get(cls, system: str) -> ReferenceLookup | None:
        lookup_cls = cls._lookups.get(system)
        return lookup_cls() if lookup_cls else None


class ReferenceFinder:
    """Options réelles d'une référence, lues dans un système CONNECTÉ de l'organisation (même système, même
    version). Aucune erreur ne remonte : sans système, sans lecteur ou en échec → None (rien n'est
    proposé)."""

    def __init__(
        self, instances: InstanceService, lookups: type[ReferenceLookupRegistry] = ReferenceLookupRegistry
    ):
        self.instances = instances
        self.lookups = lookups

    def _candidates(self, system: str, version: str, instance_id: uuid.UUID | None = None) -> list[Any]:
        """Instances lisibles (même système, même version, dernière connexion réussie). `instance_id` : la
        cible choisie par l'utilisateur, SEULE lue (filtrée par l'organisation : une autre → aucune)."""
        if instance_id is not None:
            try:
                found = [self.instances.get(instance_id)]
            except Exception:  # noqa: BLE001 — instance d'un autre tenant ou supprimée : rien n'est lu
                return []
        else:
            found = list(self.instances.list())
        out = []
        for inst in found:
            ver = inst.interface.version
            conn = inst.connections[0] if inst.connections else None
            if (
                ver.product.system.code == system
                and ver.version == version
                and conn
                and conn.last_test_status == "OK"
            ):
                out.append(inst)
        return out

    def instance_choices(self) -> list[InstanceChoice]:
        """Instances de l'organisation (point de filtrage unique de `InstanceService`), connectées d'abord."""
        out = []
        for inst in self.instances.list():
            ver = inst.interface.version
            conn = inst.connections[0] if inst.connections else None
            out.append(
                InstanceChoice(
                    id=inst.id,
                    system=ver.product.system.code,
                    product=ver.product.code,
                    version=ver.version,
                    name=inst.name,
                    connected=bool(conn and conn.last_test_status == "OK"),
                )
            )
        return sorted(out, key=lambda c: (not c.connected, c.name.casefold()))

    def instance_target(self, instance_id: uuid.UUID) -> InstanceTarget:
        """Instance de l'organisation (filtrée : une autre organisation → NotFound) ; devise lue seulement si
        la dernière connexion a réussi ; une lecture impossible ne fait jamais échouer (devise inconnue)."""
        inst = self.instances.get(instance_id)
        ver = inst.interface.version
        conn = inst.connections[0] if inst.connections else None
        connected = bool(conn and conn.last_test_status == "OK")
        currency = None
        lookup = self.lookups.get(ver.product.system.code)
        if connected and lookup is not None:
            try:
                _, ctx = self.instances.connection_context(inst.id)
                found = lookup.company_currency(ctx)
                currency = found.upper() if found and len(found) == 3 and found.isalpha() else None
            except Exception:  # noqa: BLE001 — lecture impossible : devise inconnue, jamais de secret
                currency = None
        return InstanceTarget(
            system=ver.product.system.code,
            product=ver.product.code,
            version=ver.version,
            name=inst.name,
            connected=connected,
            currency=currency,
        )

    def connected(self, system: str, version: str, instance_id: uuid.UUID | None = None) -> bool:
        """Un système connecté (dernière connexion réussie) correspond-il à ce système et cette version ?"""
        return self.lookups.get(system) is not None and bool(self._candidates(system, version, instance_id))

    def options(
        self,
        system: str,
        version: str,
        entity: str,
        attribute: str | None,
        instance_id: uuid.UUID | None = None,
    ) -> list[ReferenceOption] | None:
        return self._read(system, version, entity, attribute, MAX_OPTIONS, instance_id)

    def all_options(
        self,
        system: str,
        version: str,
        entity: str,
        attribute: str | None,
        instance_id: uuid.UUID | None = None,
    ) -> list[ReferenceOption] | None:
        """Valeurs existantes pour VÉRIFIER celles d'un fichier (au plus MAX_CHECK ; à ce plafond, une valeur
        absente n'est pas prouvée inconnue)."""
        return self._read(system, version, entity, attribute, MAX_CHECK, instance_id)

    def _read(  # noqa: PLR0913
        self,
        system: str,
        version: str,
        entity: str,
        attribute: str | None,
        limit: int,
        instance_id: uuid.UUID | None = None,
    ) -> list[ReferenceOption] | None:
        """Lecture dans l'instance choisie ; sans choix, la première instance qui RÉPOND (une instance
        injoignable n'empêche pas de lire la suivante)."""
        lookup = self.lookups.get(system)
        if lookup is None:
            return None
        for inst in self._candidates(system, version, instance_id):
            try:
                _, ctx = self.instances.connection_context(inst.id)
                return lookup.options(ctx, entity, attribute, limit)[:limit]
            except Exception:  # noqa: BLE001, S112 — lecture impossible : instance suivante, jamais de secret
                continue
        return None
