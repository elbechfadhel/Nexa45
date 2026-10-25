"""Contrat générique de découverte d'instance : SchemaDiscoveryProvider + registre.

Le Core ne connaît aucun système. Un provider (Odoo, DATEV, ...) vit dans `uie_connectors`,
s'enregistre via `@DiscoveryProviderRegistry.register` et sait, pour SON système :
- tester réellement une connexion et vérifier des capabilities (lecture seule) ;
- découvrir le schéma réel d'une instance → `SchemaSnapshotModel` (même format que le standard) ;
- classifier les différences standard → instance (STANDARD / CUSTOM / THIRD_PARTY / UNKNOWN),
  car seules les conventions du système permettent de le faire (préfixes, modules...).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, SecretStr

from uie_core.instances.diff import DiffAnalysis
from uie_core.registry.compare import Change
from uie_core.registry.schema import Capability, SchemaSnapshotModel


class CapabilityStatus(StrEnum):
    """VERIFIED = opération réellement exercée (lecture seule) ; DECLARED = annoncée par le connecteur
    sans avoir été exercée ; UNAVAILABLE = non supportée ; UNKNOWN = non déterminable (ex. connexion KO)."""

    VERIFIED = "VERIFIED"
    DECLARED = "DECLARED"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class CapabilityCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    capability: Capability
    status: CapabilityStatus
    detail: str | None = None  # opération exercée ou raison (jamais de secret)


class ConnectionErrorKind(StrEnum):
    """Cause d'un échec de connexion, classée par le provider (V2b.1) : le portail fournisseur affiche une
    phrase simple, jamais le message technique."""

    UNREACHABLE = "UNREACHABLE"  # adresse injoignable ou pas le bon type de serveur
    AUTH_FAILED = "AUTH_FAILED"  # identifiant ou mot de passe refusé
    DATABASE_NOT_FOUND = "DATABASE_NOT_FOUND"
    INCOMPLETE = "INCOMPLETE"  # paramètre de connexion manquant
    ACCESS_DENIED = "ACCESS_DENIED"  # identifiants acceptés, mais droits ou contrat insuffisants
    RATE_LIMITED = "RATE_LIMITED"  # le système demande de réessayer plus tard
    UNEXPECTED = "UNEXPECTED"


class ConnectionTestResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool
    error: str | None = None
    error_kind: ConnectionErrorKind | None = None
    server_info: dict[str, Any] = {}  # version serveur, utilisateur... (jamais de secret)
    capabilities: list[CapabilityCheck] = []


@dataclass(frozen=True)
class ConnectionContext:
    """Paramètres résolus d'une connexion, passés au provider. Le secret reste un SecretStr
    (repr masqué) et n'est déréférencé que par le provider, au moment de l'appel réseau."""

    endpoint: str
    config: dict[str, Any] = field(default_factory=dict)
    secret: SecretStr | None = field(default=None, repr=False)


class ConnectionFieldRole(StrEnum):
    """Où va la valeur saisie : adresse du système, paramètre non secret, ou secret (SecretStore)."""

    ENDPOINT = "ENDPOINT"
    CONFIG = "CONFIG"
    SECRET = "SECRET"


class ConnectionField(BaseModel):
    """Un champ du formulaire de connexion, DÉCLARÉ par le connecteur (jamais codé dans une page).

    `name` = nom du champ dans le formulaire et l'API ; `key` = clé sous laquelle un paramètre CONFIG est
    conservé (défaut : `name`). Libellés et aides : phrases simples (anglais), sans nom technique."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    label: str
    role: ConnectionFieldRole
    required: bool = True
    help: str | None = None
    placeholder: str | None = None
    key: str | None = None
    summary: bool = True  # valeur non secrète rappelée après le test et à l'enregistrement

    @property
    def config_key(self) -> str:
        return self.key or self.name


class ConnectionFieldsError(Exception):
    """Formulaire de connexion incomplet ou champ inconnu (aucune valeur saisie dans le message)."""

    def __init__(self, missing: list[str], unknown: list[str] | None = None) -> None:
        super().__init__(f"champs manquants : {missing} ; champs inconnus : {unknown or []}")
        self.missing = missing
        self.unknown = unknown or []


@dataclass(frozen=True)
class ConnectionInput:
    """Valeurs du formulaire, réparties selon le rôle de chaque champ."""

    endpoint: str
    config: dict[str, Any]
    secret: SecretStr | None = field(default=None, repr=False)


class DiscoveryError(Exception):
    """Échec d'un test / d'une découverte côté système distant (message déjà sans secret)."""


class SchemaDiscoveryProvider(ABC):
    """Adapter d'un système pour les instances client."""

    system_code: ClassVar[str]
    connector_type: ClassVar[str]
    # Formulaire de connexion du système : chaque connecteur déclare SES champs (adresse, base, clé d'API…).
    connection_fields: ClassVar[tuple[ConnectionField, ...]] = ()
    # Adresse du système quand elle appartient au connecteur (API publique) : jamais saisie par l'utilisateur.
    fixed_endpoint: ClassVar[str | None] = None

    def endpoint(self) -> str | None:
        """Adresse imposée par le connecteur (None : elle vient d'un champ ENDPOINT du formulaire)."""
        return self.fixed_endpoint

    def connection_input(self, values: dict[str, SecretStr]) -> ConnectionInput:
        """Répartit les valeurs du formulaire d'après les champs déclarés. Champ obligatoire vide ou champ
        non déclaré → `ConnectionFieldsError` (les valeurs ne figurent jamais dans l'erreur)."""
        declared = {f.name: f for f in self.connection_fields}
        unknown = sorted(set(values) - set(declared))
        endpoint = self.endpoint() or ""
        config: dict[str, Any] = {}
        secret: SecretStr | None = None
        missing = []
        for f in self.connection_fields:
            raw = values.get(f.name)
            text = raw.get_secret_value() if raw is not None else ""
            text = text if f.role == ConnectionFieldRole.SECRET else text.strip()
            if not text:
                if f.required:
                    missing.append(f.name)
                continue
            if f.role == ConnectionFieldRole.ENDPOINT:
                endpoint = text
            elif f.role == ConnectionFieldRole.CONFIG:
                config[f.config_key] = text
            else:
                secret = SecretStr(text)
        if not endpoint:
            missing.append("endpoint")
        if missing or unknown:
            raise ConnectionFieldsError(missing, unknown)
        return ConnectionInput(endpoint=endpoint, config=config, secret=secret)

    def connection_summary(self, result: ConnectionTestResult) -> list[tuple[str, str]]:
        """Faits SÛRS à montrer après un test réussi (libellé, valeur) : jamais de secret ni d'identifiant
        de connexion. Défaut : rien."""
        return []

    def supports(self, version: str, interface: str) -> bool:
        """Versions / interfaces gérées (toutes par défaut)."""
        return True

    def account_identity(self, result: ConnectionTestResult) -> str | None:
        """Identifiant du COMPTE distant lu dans un test réussi (ex. organisation), quand l'adresse seule ne
        l'identifie pas (API à adresse fixe). None : l'adresse et la configuration identifient le compte."""
        return None

    def detected_version(self, result: ConnectionTestResult) -> str | None:
        """Version du système (au sens du Registry, ex. « 17 ») lue dans un test réussi ; None : inconnue."""
        return None

    @abstractmethod
    def declared_capabilities(self) -> dict[Capability, bool]:
        """Ce que le connecteur annonce supporter (True) ou non (False). N'est jamais une vérification."""

    @abstractmethod
    def test_connection(self, ctx: ConnectionContext) -> ConnectionTestResult:
        """Teste réellement l'instance et appelle `detect_capabilities` si la connexion aboutit."""

    @abstractmethod
    def detect_capabilities(self, ctx: ConnectionContext) -> list[CapabilityCheck]:
        """Exerce en LECTURE SEULE ce qui peut l'être. Ne jamais écrire, créer ou supprimer."""

    @abstractmethod
    def discover_schema(self, ctx: ConnectionContext) -> SchemaSnapshotModel:
        """Schéma réel de l'instance (provenance dans `source`, sans secret)."""

    @abstractmethod
    def classify(
        self, base: SchemaSnapshotModel, instance: SchemaSnapshotModel, changes: list[Change]
    ) -> DiffAnalysis:
        """Classe chaque changement standard → instance selon les conventions du système."""

    # --- utilitaire commun ------------------------------------------------
    def capabilities_without_connection(self, reason: str) -> list[CapabilityCheck]:
        """Connexion impossible : rien n'est vérifié ; les déclarations restent des déclarations."""
        out = []
        for cap in Capability:
            declared = self.declared_capabilities().get(cap)
            if declared is False:
                out.append(CapabilityCheck(capability=cap, status=CapabilityStatus.UNAVAILABLE))
            elif cap in VERIFIABLE_READ_ONLY:
                out.append(CapabilityCheck(capability=cap, status=CapabilityStatus.UNKNOWN, detail=reason))
            else:
                out.append(CapabilityCheck(capability=cap, status=CapabilityStatus.DECLARED))
        return out


# Capabilities vérifiables sans effet de bord ; les autres ne sont jamais exercées en V1.
VERIFIABLE_READ_ONLY = frozenset(
    {Capability.READ, Capability.SEARCH, Capability.BULK_READ, Capability.SCHEMA_DISCOVERY}
)


class DiscoveryProviderRegistry:
    """Registre des providers, résolus depuis system + version + interface."""

    _providers: ClassVar[dict[str, type[SchemaDiscoveryProvider]]] = {}

    @classmethod
    def register(cls, provider_cls: type[SchemaDiscoveryProvider]) -> type[SchemaDiscoveryProvider]:
        cls._providers[provider_cls.system_code] = provider_cls
        return provider_cls

    @classmethod
    def available(cls) -> list[str]:
        return sorted(cls._providers)

    @classmethod
    def resolve(cls, system: str, version: str, interface: str) -> SchemaDiscoveryProvider:
        try:
            provider = cls._providers[system]()
        except KeyError:
            raise DiscoveryError(
                f"Aucun SchemaDiscoveryProvider pour '{system}'. Disponibles : {cls.available()}"
            ) from None
        if not provider.supports(version, interface):
            raise DiscoveryError(f"Le provider '{system}' ne gère pas {version}/{interface}")
        return provider


class ProviderResolver:
    """Point d'injection : par défaut le registre ; les tests peuvent fournir un autre resolver."""

    def resolve(self, system: str, version: str, interface: str) -> SchemaDiscoveryProvider:
        return DiscoveryProviderRegistry.resolve(system, version, interface)
