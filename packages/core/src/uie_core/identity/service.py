"""IdentityService (V1d) : authentification, sessions, sélection d'organisation, construction des contextes.

    login (email, mot de passe) → session (jeton aléatoire ; en base : sha256 du jeton seulement)
    → à chaque requête : resolve_session(jeton) → Principal
    → AdminContext (platform_role = ADMIN) ou TenantContext (organisation sélectionnée + membership revérifié)

Le contexte d'un tenant n'est JAMAIS construit depuis un organization_id envoyé par le client : seule la
sélection (select_organization) l'accepte, après vérification d'un membership. Aucun framework web ici :
l'API ne fait que lire / poser le cookie.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from uie_core.db.base import utcnow
from uie_core.identity.context import (
    AdminContext,
    Context,
    PermissionDenied,
    TenantContext,
    ensure_context,
)
from uie_core.identity.models import (
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
    PasswordError,
    hash_password,
    needs_rehash,
    normalize_email,
    verify_password,
)

DEFAULT_SESSION_TTL = timedelta(hours=12)


class AuthenticationError(Exception):
    """Session absente, expirée ou révoquée, ou compte inactif (→ HTTP 401)."""


@dataclass(frozen=True)
class MembershipView:
    organization_id: uuid.UUID
    organization_name: str
    organization_slug: str
    role: MembershipRole


@dataclass(frozen=True)
class Principal:
    """Utilisateur authentifié d'une requête (aucun hash, aucun jeton)."""

    user_id: uuid.UUID
    email: str
    display_name: str
    platform_role: PlatformRole
    session_id: uuid.UUID
    selected_organization_id: uuid.UUID | None
    memberships: tuple[MembershipView, ...]

    @property
    def is_admin(self) -> bool:
        return self.platform_role == PlatformRole.ADMIN


@dataclass(frozen=True)
class NewSession:
    token: str  # à poser dans le cookie, jamais stocké ni journalisé
    expires_at: datetime
    principal: Principal


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class IdentityService:
    def __init__(self, session: Session, ctx: Context, session_ttl: timedelta = DEFAULT_SESSION_TTL) -> None:
        self.s = session
        self.ctx = ensure_context(ctx)
        self.session_ttl = session_ttl

    # ------------------------------------------------------------------ #
    # Authentification et sessions
    # ------------------------------------------------------------------ #
    def login(self, email: str, password: SecretStr) -> NewSession:
        """Vérifie le mot de passe et ouvre une session. Email inconnu, mot de passe faux ou compte inactif :
        même AuthenticationError, même travail (vérification argon2 factice)."""
        try:
            normalized = normalize_email(email)
        except PasswordError:
            normalized = None
        user = self.s.scalar(select(User).where(User.email == normalized)) if normalized else None
        ok = verify_password(user.password_hash if user else None, password)
        if not ok or user is None or user.status != UserStatus.ACTIVE:
            raise AuthenticationError("Invalid email or password")
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
        memberships = self._memberships(user.id)
        token = secrets.token_urlsafe(32)
        row = UserSession(
            user_id=user.id,
            token_hash=token_hash(token),
            organization_id=memberships[0].organization_id if len(memberships) == 1 else None,
            expires_at=utcnow() + self.session_ttl,
        )
        self.s.add(row)
        self.s.flush()
        return NewSession(token, row.expires_at, self._principal(user, row, memberships))

    def resolve_session(self, token: str | None) -> Principal:
        row = self._session_row(token)
        user = self.s.get(User, row.user_id)
        if user is None or user.status != UserStatus.ACTIVE:
            raise AuthenticationError("Not authenticated")
        return self._principal(user, row, self._memberships(user.id))

    def logout(self, token: str | None) -> None:
        """Révoque la session (idempotent)."""
        try:
            row = self._session_row(token)
        except AuthenticationError:
            return
        row.revoked_at = utcnow()
        self.s.flush()

    def select_organization(self, token: str | None, organization_id: uuid.UUID) -> Principal:
        """Choisit l'organisation courante parmi les memberships du user ; sinon PermissionDenied (403)."""
        row = self._session_row(token)
        user = self.s.get(User, row.user_id)
        if user is None or user.status != UserStatus.ACTIVE:
            raise AuthenticationError("Not authenticated")
        memberships = self._memberships(user.id)
        if organization_id not in {m.organization_id for m in memberships}:
            raise PermissionDenied("Not a member of this organization")
        row.organization_id = organization_id
        self.s.flush()
        return self._principal(user, row, memberships)

    # ------------------------------------------------------------------ #
    # Contextes
    # ------------------------------------------------------------------ #
    def admin_context(self, principal: Principal) -> AdminContext:
        if not principal.is_admin:
            raise PermissionDenied("Platform administrator required")
        return AdminContext(principal.user_id)

    def tenant_context(self, principal: Principal) -> TenantContext:
        """Organisation sélectionnée ET membership revérifié à chaque requête (organisation ACTIVE)."""
        if principal.selected_organization_id is None:
            raise PermissionDenied("No organization selected")
        hit = self.s.execute(
            select(OrganizationMembership.role)
            .join(Organization, Organization.id == OrganizationMembership.organization_id)
            .where(
                OrganizationMembership.user_id == principal.user_id,
                OrganizationMembership.organization_id == principal.selected_organization_id,
                Organization.status == OrganizationStatus.ACTIVE.value,
            )
        ).first()
        if hit is None:
            raise PermissionDenied("Not a member of this organization")
        return TenantContext(principal.user_id, principal.selected_organization_id, MembershipRole(hit[0]))

    # ------------------------------------------------------------------ #
    # interne
    # ------------------------------------------------------------------ #
    def _session_row(self, token: str | None) -> UserSession:
        if not token:
            raise AuthenticationError("Not authenticated")
        row = self.s.scalar(select(UserSession).where(UserSession.token_hash == token_hash(token)))
        if row is None or row.revoked_at is not None or row.expires_at <= utcnow():
            raise AuthenticationError("Not authenticated")
        return row

    def _memberships(self, user_id: uuid.UUID) -> tuple[MembershipView, ...]:
        rows = self.s.execute(
            select(OrganizationMembership.role, Organization)
            .join(Organization, Organization.id == OrganizationMembership.organization_id)
            .where(
                OrganizationMembership.user_id == user_id,
                Organization.status == OrganizationStatus.ACTIVE.value,
            )
            .order_by(Organization.name)
        ).all()
        return tuple(MembershipView(org.id, org.name, org.slug, MembershipRole(role)) for role, org in rows)

    @staticmethod
    def _principal(user: User, row: UserSession, memberships: tuple[MembershipView, ...]) -> Principal:
        selected = row.organization_id
        if selected is not None and selected not in {m.organization_id for m in memberships}:
            selected = None  # membership retiré depuis la sélection
        return Principal(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            platform_role=PlatformRole(user.platform_role),
            session_id=row.id,
            selected_organization_id=selected,
            memberships=memberships,
        )
