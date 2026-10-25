"""Connecteur Lexware Office (API publique) : contrat du Registry, connexion, lecture (1a), envoi (1b),
lecture comme SOURCE à schéma fixe (`RecordSource`).

Ce paquet ne dépend d'AUCUNE source (aucun autre connecteur) ni d'aucun domaine métier (partenaires,
comptabilité…) : il connaît l'API de son système, pas ce que les données veulent dire ni d'où elles viennent.
Importer ce paquet enregistre le loader, le provider de connexion, le constructeur de requête et
l'expéditeur et le lecteur de source.
"""

from uie_connectors.lexware.client import API_BASE_URL, API_VERSION, LexwareClient, ReadOnlyViolation
from uie_connectors.lexware.loader import LexwareSchemaLoader
from uie_connectors.lexware.provider import LexwareDiscoveryProvider
from uie_connectors.lexware.request import LexwareRequestBuilder
from uie_connectors.lexware.sender import LexwareRecordSender
from uie_connectors.lexware.source import LexwareRecordSource, LexwareRecordSourceReader
from uie_connectors.lexware.spec import LexwareApiSpec

__all__ = [
    "API_BASE_URL",
    "API_VERSION",
    "LexwareApiSpec",
    "LexwareClient",
    "LexwareDiscoveryProvider",
    "LexwareRecordSender",
    "LexwareRecordSource",
    "LexwareRecordSourceReader",
    "LexwareRequestBuilder",
    "LexwareSchemaLoader",
    "ReadOnlyViolation",
]
