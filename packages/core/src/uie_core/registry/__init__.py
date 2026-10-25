"""System Knowledge Registry — première brique fonctionnelle du produit."""

from uie_core.registry.compare import ChangeType, CompareResult, compare_snapshots
from uie_core.registry.loader import LoaderRegistry, SchemaLoader, SchemaLoaderError
from uie_core.registry.schema import (
    Capability,
    DataType,
    EntityDef,
    FieldDef,
    ModuleDef,
    RelationDef,
    RelationType,
    SchemaSnapshotModel,
)
from uie_core.registry.service import NotFound, RegistryError, RegistryService, SnapshotRef

__all__ = [
    "Capability",
    "ChangeType",
    "CompareResult",
    "DataType",
    "EntityDef",
    "FieldDef",
    "LoaderRegistry",
    "ModuleDef",
    "NotFound",
    "RegistryError",
    "RegistryService",
    "RelationDef",
    "RelationType",
    "SchemaLoader",
    "SchemaLoaderError",
    "SchemaSnapshotModel",
    "SnapshotRef",
    "compare_snapshots",
]
