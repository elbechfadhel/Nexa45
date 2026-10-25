"""Provider Lexware Office du contrat générique `SchemaDiscoveryProvider` : connexion et capacités.

- formulaire de connexion : UNE clé d'API (secret). L'adresse de l'API appartient au connecteur ;
- test de connexion : `GET /v1/profile`, en lecture seule. Il prouve que la clé est acceptée, que l'API
  répond et que le compte est accessible ;
- du profil, seul le strict utile est conservé : société, utilisateur de la connexion, identifiant de
  l'organisation, fonctions du contrat. Jamais l'adresse e-mail, jamais la clé ;
- pas de découverte de schéma : le contrat de l'API vient du Registry (spec maintenue), pas du compte.

Aucune notion de partenaire, de facture ou de domaine ici : ce module ne connaît que la connexion."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from uie_core.instances import (
    CapabilityCheck,
    CapabilityStatus,
    ConnectionContext,
    ConnectionErrorKind,
    ConnectionField,
    ConnectionFieldRole,
    ConnectionTestResult,
    DiffAnalysis,
    DiscoveryError,
    DiscoveryProviderRegistry,
    SchemaDiscoveryProvider,
)
from uie_core.registry import Capability, SchemaSnapshotModel
from uie_core.registry.compare import Change

from uie_connectors.errors import (
    AuthenticationError,
    AuthorizationError,
    ConnectorError,
    NetworkError,
    RateLimitError,
    RemoteTimeoutError,
)
from uie_connectors.lexware.client import API_VERSION, LexwareClient, api_base_url

PROFILE_PATH = f"/{API_VERSION}/profile"

_KINDS: dict[type[ConnectorError], ConnectionErrorKind] = {
    AuthenticationError: ConnectionErrorKind.AUTH_FAILED,
    AuthorizationError: ConnectionErrorKind.ACCESS_DENIED,
    RateLimitError: ConnectionErrorKind.RATE_LIMITED,
    NetworkError: ConnectionErrorKind.UNREACHABLE,
    RemoteTimeoutError: ConnectionErrorKind.UNREACHABLE,
}


@DiscoveryProviderRegistry.register
class LexwareDiscoveryProvider(SchemaDiscoveryProvider):
    system_code = "lexware"
    connector_type = "lexware.rest"
    connection_fields = (
        ConnectionField(
            name="api_key",
            label="API key",
            role=ConnectionFieldRole.SECRET,
            help="Create a key in Lexware Office under Extensions → Public API. "
            "Give it only the permissions you need; you can revoke it there at any time.",
        ),
    )

    def __init__(self, client_factory: Callable[[ConnectionContext], LexwareClient] | None = None) -> None:
        self._client_factory = client_factory or _default_client

    def endpoint(self) -> str:
        return api_base_url()

    def supports(self, version: str, interface: str) -> bool:
        return version in ("", API_VERSION)

    # ------------------------------------------------------------------ #
    def declared_capabilities(self) -> dict[Capability, bool]:
        # Ce que l'API sait faire d'après son contrat ; en phase 1a le connecteur n'exerce que la lecture.
        offered = {
            Capability.READ,
            Capability.SEARCH,
            Capability.CREATE,
            Capability.UPDATE,
            Capability.EVENTS,
        }
        return {c: c in offered for c in Capability}

    def test_connection(self, ctx: ConnectionContext) -> ConnectionTestResult:
        if ctx.secret is None or not ctx.secret.get_secret_value():
            reason = "clé d'API manquante"
            return ConnectionTestResult(
                ok=False,
                error=reason,
                error_kind=ConnectionErrorKind.INCOMPLETE,
                capabilities=self.capabilities_without_connection(reason),
            )
        try:
            with self._client_factory(ctx) as client:
                profile = client.get(PROFILE_PATH)
        except ConnectorError as exc:
            reason = f"{type(exc).__name__}: {exc}"  # déjà sans secret (« HTTP 401 »…)
            return ConnectionTestResult(
                ok=False,
                error=reason,
                error_kind=_KINDS.get(type(exc), ConnectionErrorKind.UNEXPECTED),
                capabilities=self.capabilities_without_connection(reason),
            )
        if not isinstance(profile, dict) or not profile.get("organizationId"):
            reason = "profil du compte illisible"
            return ConnectionTestResult(
                ok=False,
                error=reason,
                error_kind=ConnectionErrorKind.UNEXPECTED,
                capabilities=self.capabilities_without_connection(reason),
            )
        return ConnectionTestResult(ok=True, server_info=_server_info(profile), capabilities=self._verified())

    def detected_version(self, result: ConnectionTestResult) -> str | None:
        """Version de ROUTE de l'API que le connecteur appelle (celle du Registry)."""
        return str(result.server_info.get("api_version") or "") or None

    def account_identity(self, result: ConnectionTestResult) -> str | None:
        """Adresse fixe : seule l'organisation du profil identifie le compte (remplacement de clé)."""
        return str(result.server_info.get("organization_id") or "") or None

    def connection_summary(self, result: ConnectionTestResult) -> list[tuple[str, str]]:
        info = result.server_info
        facts = [("Company", info.get("company_name")), ("Connected by", info.get("user_name"))]
        return [(label, str(value)) for label, value in facts if value]

    def detect_capabilities(self, ctx: ConnectionContext) -> list[CapabilityCheck]:
        return self.test_connection(ctx).capabilities

    def _verified(self) -> list[CapabilityCheck]:
        """Seule la LECTURE a été exercée (le profil). Le reste est déclaré par le contrat, jamais essayé."""
        out = []
        for cap, offered in self.declared_capabilities().items():
            if cap == Capability.READ:
                out.append(
                    CapabilityCheck(
                        capability=cap, status=CapabilityStatus.VERIFIED, detail=f"GET {PROFILE_PATH}"
                    )
                )
            elif offered:
                out.append(CapabilityCheck(capability=cap, status=CapabilityStatus.DECLARED))
            else:
                out.append(CapabilityCheck(capability=cap, status=CapabilityStatus.UNAVAILABLE))
        return out

    # ------------------------------------------------------------------ #
    def discover_schema(self, ctx: ConnectionContext) -> SchemaSnapshotModel:
        raise DiscoveryError(
            "Pas de découverte de schéma pour ce système : le contrat de son API vient du Registry"
        )

    def classify(
        self, base: SchemaSnapshotModel, instance: SchemaSnapshotModel, changes: list[Change]
    ) -> DiffAnalysis:
        raise DiscoveryError("Pas de schéma d'instance à comparer pour ce système")


def _default_client(ctx: ConnectionContext) -> LexwareClient:
    assert ctx.secret is not None  # vérifié par l'appelant
    # toujours l'adresse du CONNECTEUR : celle enregistrée avec l'instance n'est qu'un rappel affichable
    return LexwareClient(ctx.secret)


def _server_info(profile: dict[str, Any]) -> dict[str, Any]:
    """Strict utile du profil (aucune adresse e-mail, aucun secret)."""
    created = profile.get("created") if isinstance(profile.get("created"), dict) else {}
    # constat réel (phase 1a.1) : `userName` peut être l'adresse e-mail de l'utilisateur : jamais conservé
    user_name = created.get("userName")
    if not isinstance(user_name, str) or "@" in user_name:
        user_name = None
    features = profile.get("businessFeatures")
    return {
        "api_version": API_VERSION,
        "organization_id": profile.get("organizationId"),
        "company_name": profile.get("companyName"),
        "user_name": user_name,
        "business_features": [f for f in features if isinstance(f, str)]
        if isinstance(features, list)
        else [],
    }
