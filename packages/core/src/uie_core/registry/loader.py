"""Contrat SchemaLoader (document SKR §8.1) et registre de loaders.

Un loader sait, pour UN système, extraire la connaissance et la normaliser
en `SchemaSnapshotModel`. Le Core ne connaît aucun système : il ne connaît
que ce contrat. Les loaders vivent dans `uie_connectors`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from uie_core.registry.schema import (
    ConstraintDef,
    EntityDef,
    ModuleDef,
    RelationDef,
    SchemaSnapshotModel,
)


class SchemaLoaderError(Exception):
    pass


class SchemaLoader(ABC):
    """Pipeline : identify → extract_* → validate → build_snapshot."""

    system_code: ClassVar[str]

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}

    # --- identification ----------------------------------------------------
    @abstractmethod
    def identify_system(self) -> tuple[str, str]:
        """Retourne (system_code, product_code)."""

    @abstractmethod
    def identify_version(self) -> tuple[str, str, str, str]:
        """Retourne (version, edition, interface_code, interface_type)."""

    # --- extraction --------------------------------------------------------
    @abstractmethod
    def extract_modules(self) -> list[ModuleDef]: ...

    @abstractmethod
    def extract_entities(self) -> list[EntityDef]:
        """Entités avec leurs champs, relations, contraintes et capabilities."""

    def extract_relations(self, entities: list[EntityDef]) -> list[RelationDef]:
        return [r for e in entities for r in e.relations]

    def extract_constraints(self, entities: list[EntityDef]) -> list[ConstraintDef]:
        return [c for e in entities for c in e.constraints]

    def extract_capabilities(self) -> dict[str, bool]:
        return {}

    def source_description(self) -> dict[str, Any]:
        """Provenance : URL, modules installés, fichier de spec, date... (consignée)."""
        return {}

    # --- validation & construction ----------------------------------------
    def validate(self, snapshot: SchemaSnapshotModel) -> None:
        if not snapshot.entities:
            raise SchemaLoaderError("Le snapshot ne contient aucune entité")
        names = [e.technical_name for e in snapshot.entities]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise SchemaLoaderError(f"Entités en double : {sorted(dupes)}")
        known = set(names)
        for e in snapshot.entities:
            for r in e.relations:
                if r.target_entity not in known:
                    # Toléré (cible hors périmètre des modules chargés) mais tracé
                    e.metadata.setdefault("dangling_relations", []).append(r.target_entity)

    def build_snapshot(self) -> SchemaSnapshotModel:
        system, product = self.identify_system()
        version, edition, interface, interface_type = self.identify_version()
        snapshot = SchemaSnapshotModel(
            system=system,
            product=product,
            version=version,
            edition=edition,
            interface=interface,
            interface_type=interface_type,
            source=self.source_description(),
            modules=self.extract_modules(),
            entities=self.extract_entities(),
            capabilities=self.extract_capabilities(),  # type: ignore[arg-type]
        )
        self.validate(snapshot)
        return snapshot


class LoaderRegistry:
    """Enregistrement des loaders disponibles (plugin registry)."""

    _loaders: dict[str, type[SchemaLoader]] = {}

    @classmethod
    def register(cls, loader_cls: type[SchemaLoader]) -> type[SchemaLoader]:
        cls._loaders[loader_cls.system_code] = loader_cls
        return loader_cls

    @classmethod
    def get(cls, system_code: str) -> type[SchemaLoader]:
        try:
            return cls._loaders[system_code]
        except KeyError as exc:
            raise SchemaLoaderError(
                f"Aucun SchemaLoader pour '{system_code}'. Disponibles : {sorted(cls._loaders)}"
            ) from exc

    @classmethod
    def available(cls) -> list[str]:
        return sorted(cls._loaders)
