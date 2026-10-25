"""Domaine PRODUCT vers une cible : lecture d'un système source vers le Product CDM, préparation (aperçu) pour
une cible par API. Aucun nom de système ; aucune donnée de produit persistée (lecture à la demande).
"""

from uie_core.products.service import (
    PRODUCT_KEYS,
    ProductError,
    ProductIssue,
    ProductReadResult,
    ProductReadService,
    read_products,
)
from uie_core.products.target import (
    PRICE_BASIS,
    PreparedProduct,
    PreparedValue,
    PreviewProduct,
    ProductTargetError,
    ProductTargetPreview,
    ProductTargetService,
    SendView,
    TargetOption,
    TaxRateDecision,
    prepare_products,
)

__all__ = [
    "PRICE_BASIS",
    "PRODUCT_KEYS",
    "PreparedProduct",
    "PreparedValue",
    "PreviewProduct",
    "ProductError",
    "ProductIssue",
    "ProductReadResult",
    "ProductReadService",
    "ProductTargetError",
    "ProductTargetPreview",
    "ProductTargetService",
    "SendView",
    "TargetOption",
    "TaxRateDecision",
    "prepare_products",
    "read_products",
]
