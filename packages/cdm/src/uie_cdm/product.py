"""Product CDM 1.0.0 : un produit ou un service vendu, indépendant de tout système.

Ni notion Odoo (template, detailed_type), ni notion de cible (article, type d'article) : un système source le
REMPLIT, une cible le TRADUIT. Le CDM reste fidèle à la source : un produit garde ses variantes, ses taxes de
vente sont des RÉFÉRENCES de la source (jamais un taux deviné), le prix n'a pas de base net / brut (elle
dépend de la configuration de la source et se décide explicitement, côté cible).

`items()` donne les unités VENDABLES (produit sans variante, ou chaque variante) : c'est la vue d'une cible
qui ne connaît pas les variantes. `source_ref` est un identifiant opaque de la source : jamais affiché comme
l'identifiant d'un système particulier.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

PRODUCT_MODEL_VERSION = "1.0.0"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProductKind(StrEnum):
    GOODS = "GOODS"  # bien (stocké ou non)
    SERVICE = "SERVICE"
    UNKNOWN = "UNKNOWN"  # sorte non traduisible depuis la source (ex. kit, combo) : jamais devinée


class Price(_Model):
    amount: Decimal
    currency: str | None = None  # code ISO 4217 si la source le donne


class ProductTaxRef(_Model):
    """Taxe de vente telle que la source la nomme : sa référence opaque et son libellé (jamais un taux)."""

    ref: str
    label: str | None = None


class VariantAttribute(_Model):
    name: str | None = None
    value: str


class ProductVariant(_Model):
    source_ref: str
    sku: str | None = None
    gtin: str | None = None
    price: Price | None = None  # absent : prix du produit
    active: bool | None = None
    attributes: list[VariantAttribute] = []


class ProductItem(_Model):
    """Unité vendable : un produit sans variante, ou une variante (avec les données de son produit)."""

    source_ref: str  # produit, ou « <produit>/<variante> »
    product_ref: str
    name: str
    variant_label: str | None = None  # « Color: Red, Size: L »
    kind: ProductKind
    sku: str | None = None
    gtin: str | None = None
    description: str | None = None
    unit: str | None = None
    price: Price | None = None
    sales_taxes: list[ProductTaxRef] = []
    active: bool | None = None

    @property
    def title(self) -> str:
        return f"{self.name} ({self.variant_label})" if self.variant_label else self.name

    def mapping_view(self) -> dict[str, Any]:
        """Vue plate pour un moteur de mapping : `title`, `name`, `variant_label`, `kind`, `sku`, `gtin`,
        `description`, `unit`, `price.amount`, `price.currency`, `sales_taxes` (liste), `active`. Valeurs
        absentes = clé absente."""
        view: dict[str, Any] = {
            "title": self.title,
            "name": self.name,
            "variant_label": self.variant_label,
            "kind": self.kind.value,
            "sku": self.sku,
            "gtin": self.gtin,
            "description": self.description,
            "unit": self.unit,
            "price.amount": self.price.amount if self.price else None,
            "price.currency": self.price.currency if self.price else None,
            "sales_taxes": [t.model_dump() for t in self.sales_taxes],
            "active": self.active,
        }
        return {k: v for k, v in view.items() if v is not None and v != []}


class Product(_Model):
    model_version: str = PRODUCT_MODEL_VERSION
    source_ref: str
    name: str = Field(min_length=1)
    kind: ProductKind = ProductKind.UNKNOWN
    sku: str | None = None
    gtin: str | None = None
    description: str | None = None
    unit: str | None = None  # libellé de l'unité de vente de la source (« Units »), jamais traduit
    price: Price | None = None
    sales_taxes: list[ProductTaxRef] = []
    active: bool | None = None
    variants: list[ProductVariant] = []

    def items(self) -> list[ProductItem]:
        """Unités vendables : le produit lui-même s'il n'a pas de variante, sinon une par variante (SKU, code
        et prix de la variante ; nom, sorte, unité, description et taxes du produit). Une variante archivée
        dans un produit actif reste archivée."""
        common = {
            "product_ref": self.source_ref,
            "name": self.name,
            "kind": self.kind,
            "description": self.description,
            "unit": self.unit,
            "sales_taxes": self.sales_taxes,
        }
        if not self.variants:
            return [
                ProductItem(
                    source_ref=self.source_ref,
                    sku=self.sku,
                    gtin=self.gtin,
                    price=self.price,
                    active=self.active,
                    **common,
                )
            ]
        out = []
        for v in self.variants:
            label = ", ".join(f"{a.name}: {a.value}" if a.name else a.value for a in v.attributes) or None
            active = (
                None
                if self.active is None and v.active is None
                else bool((self.active is not False) and (v.active is not False))
            )
            out.append(
                ProductItem(
                    source_ref=f"{self.source_ref}/{v.source_ref}",
                    variant_label=label,
                    sku=v.sku,
                    gtin=v.gtin,
                    price=v.price or self.price,
                    active=active,
                    **common,
                )
            )
        return out


def product_json_schema() -> dict:
    """Contrat publié (JSON Schema) du Product CDM."""
    schema = Product.model_json_schema()
    schema["$id"] = f"https://go45.io/uie/cdm/product-{PRODUCT_MODEL_VERSION}.json"
    schema["title"] = "Product"
    return schema


if __name__ == "__main__":  # régénère le contrat publié : uv run python -m uie_cdm.product
    import json
    from pathlib import Path

    out = Path(__file__).resolve().parents[2] / "schemas" / f"product-{PRODUCT_MODEL_VERSION}.json"
    out.write_text(json.dumps(product_json_schema(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(out)
