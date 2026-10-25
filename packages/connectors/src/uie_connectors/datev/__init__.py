"""Connecteur DATEV (D1 : Registry seulement) : représentation minimale des formats EXTF, loader, générateur.

Les specs complètes sont générées localement à partir des sources officielles DATEV (non versionnées).
"""

from uie_connectors.datev.extf import ExtfWriter
from uie_connectors.datev.loader import DatevSchemaLoader
from uie_connectors.datev.spec import DatevField, DatevFormatSpec

__all__ = ["DatevField", "DatevFormatSpec", "DatevSchemaLoader", "ExtfWriter"]
