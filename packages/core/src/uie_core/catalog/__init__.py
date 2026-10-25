"""Catalogue réel (V2a) : modèle canonique, contrat de lecture, plan de lecture déduit des semantic bindings.

Aucun nom de système ici (test d'architecture) : un connecteur enregistre son `CatalogReader`, le Core décide
quoi lire à partir du Product Domain.
"""

from uie_core.catalog.model import (
    CATALOG_MODEL_VERSION,
    AttributeValue,
    CatalogProduct,
    CatalogVariant,
    ExtraAttribute,
    Money,
    ProductAttribute,
    SupplierOffer,
)
from uie_core.catalog.plan import (
    BoundField,
    CatalogPlanError,
    CatalogReadPlan,
    EntityPlan,
    MediaPlan,
    PlannedField,
    build_read_plan,
)
from uie_core.catalog.reader import (
    CatalogReader,
    CatalogReaderRegistry,
    CatalogReaderResolver,
    CatalogReadError,
    CatalogSource,
    Condition,
    FieldRequest,
    Ref,
    SourceRecord,
)

__all__ = [
    "CATALOG_MODEL_VERSION",
    "AttributeValue",
    "BoundField",
    "CatalogPlanError",
    "CatalogProduct",
    "CatalogReadError",
    "CatalogReadPlan",
    "CatalogReader",
    "CatalogReaderRegistry",
    "CatalogReaderResolver",
    "CatalogSource",
    "CatalogVariant",
    "Condition",
    "EntityPlan",
    "ExtraAttribute",
    "FieldRequest",
    "MediaPlan",
    "Money",
    "PlannedField",
    "ProductAttribute",
    "Ref",
    "SourceRecord",
    "SupplierOffer",
    "build_read_plan",
]
