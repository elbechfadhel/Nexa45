"""Identité et isolation multi-tenant (V1d) : organisations, utilisateurs, memberships, sessions.

Aucun framework web ici : l'API et la CLI s'appuient sur ce module (règle d'entrée unique).

OrganizationService (administration) : importer `uie_core.identity.organizations` (dépend du Registry,
qui dépend lui-même des contextes de ce paquet).
"""

from uie_core.identity.bootstrap import BootstrapResult, bootstrap_platform_admin
from uie_core.identity.context import (
    AdminContext,
    Context,
    PermissionDenied,
    SystemContext,
    TenantContext,
    ensure_context,
    require_platform,
    tenant_organization,
)
from uie_core.identity.models import (
    GO45_DEV_ORG_ID,
    GO45_DEV_ORG_NAME,
    GO45_DEV_ORG_SLUG,
    MembershipRole,
    Organization,
    OrganizationMembership,
    OrganizationStatus,
    PlatformRole,
    User,
    UserSession,
    UserStatus,
)
from uie_core.identity.passwords import (
    MIN_PASSWORD_LENGTH,
    PasswordError,
    hash_password,
    needs_rehash,
    normalize_email,
    verify_password,
)
from uie_core.identity.service import (
    DEFAULT_SESSION_TTL,
    AuthenticationError,
    IdentityService,
    MembershipView,
    NewSession,
    Principal,
    token_hash,
)

__all__ = [
    "DEFAULT_SESSION_TTL",
    "AuthenticationError",
    "IdentityService",
    "MembershipView",
    "NewSession",
    "Principal",
    "token_hash",
    "AdminContext",
    "Context",
    "PermissionDenied",
    "SystemContext",
    "TenantContext",
    "ensure_context",
    "require_platform",
    "tenant_organization",
    "GO45_DEV_ORG_ID",
    "GO45_DEV_ORG_NAME",
    "GO45_DEV_ORG_SLUG",
    "MIN_PASSWORD_LENGTH",
    "BootstrapResult",
    "MembershipRole",
    "Organization",
    "OrganizationMembership",
    "OrganizationStatus",
    "PasswordError",
    "PlatformRole",
    "User",
    "UserSession",
    "UserStatus",
    "bootstrap_platform_admin",
    "hash_password",
    "needs_rehash",
    "normalize_email",
    "verify_password",
]
