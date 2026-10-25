"""Modèle canonique du catalogue (V2a) : un produit métier et ses variantes, indépendant de tout système.

Construit par le Core à partir des données lues par un connecteur et du plan de lecture (semantic bindings).
Aucun nom de système, d'entité ou de champ source n'y figure, hormis `source_ref` (identifiant de
l'enregistrement dans le système source) : il sert à relire une image et n'est JAMAIS exposé au fournisseur.
Les champs secondaires restent `None` quand l'information n'existe pas : ils ne bloquent jamais le parcours.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict

CATALOG_MODEL_VERSION = "1.0.0"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Money(_Model):
    amount: float
    currency: str | None = None


class AttributeValue(_Model):
    """Valeur d'attribut d'une variante : « Color » / « Red »."""

    name: str | None = None
    value: str


class ProductAttribute(_Model):
    """Attribut du produit et ses valeurs (déduit des variantes)."""

    name: str
    values: list[str]


class SupplierOffer(_Model):
    supplier: str | None = None
    supplier_reference: str | None = None
    price: Money | None = None
    min_quantity: float | None = None
    lead_time_days: float | None = None


class ExtraAttribute(_Model):
    """Donnée propre au fournisseur (extension confirmée), libellée par le vocabulaire."""

    key: str
    label: str
    value: Any


class CatalogVariant(_Model):
    id: uuid.UUID  # identifiant interne stable (jamais l'identifiant du système source)
    source_ref: str
    name: str | None = None
    sku: str | None = None
    barcode: str | None = None
    price: Money | None = None
    active: bool | None = None
    weight: float | None = None
    attributes: list[AttributeValue] = []
    has_image: bool = False


class CatalogProduct(_Model):
    model_version: str = CATALOG_MODEL_VERSION
    source_ref: str
    name: str | None = None
    sku: str | None = None
    barcode: str | None = None
    category: str | None = None  # chemin complet, ex. « All / Furniture / Chairs »
    price: Money | None = None
    description: str | None = None
    weight: float | None = None
    volume: float | None = None
    unit: str | None = None
    product_type: str | None = None  # libellé lisible (valeur de sélection traduite)
    active: bool | None = None
    has_image: bool = False
    variants: list[CatalogVariant] = []
    attributes: list[ProductAttribute] = []
    supplier_offers: list[SupplierOffer] = []
    extra: list[ExtraAttribute] = []

    @property
    def search_text(self) -> str:
        """Texte de recherche : nom, SKU et codes-barres du produit et de ses variantes."""
        parts = [self.name, self.sku, self.barcode]
        for v in self.variants:
            parts += [v.name, v.sku, v.barcode]
        return " ".join(p for p in parts if p).lower()
