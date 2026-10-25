from uie_connectors.odoo.catalog import OdooCatalogReader
from uie_connectors.odoo.discovery import OdooSchemaDiscoveryProvider
from uie_connectors.odoo.loader import OdooSchemaLoader
from uie_connectors.odoo.metadata import JsonMetadataSource, XmlRpcMetadataSource
from uie_connectors.odoo.reference import OdooReferenceLookup
from uie_connectors.odoo.writer import OdooCatalogWriter

__all__ = [
    "JsonMetadataSource",
    "OdooCatalogReader",
    "OdooCatalogWriter",
    "OdooReferenceLookup",
    "OdooSchemaDiscoveryProvider",
    "OdooSchemaLoader",
    "XmlRpcMetadataSource",
]
