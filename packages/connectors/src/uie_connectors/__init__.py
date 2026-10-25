"""Connecteurs, Schema Loaders et Discovery Providers. Importer ce package les enregistre."""

from uie_connectors import (
    datev,  # noqa: F401  (enregistre DatevSchemaLoader)
    lexware,  # noqa: F401  (enregistre LexwareSchemaLoader + LexwareDiscoveryProvider + requête)
    odoo,  # noqa: F401  (enregistre OdooSchemaLoader + OdooSchemaDiscoveryProvider)
)
