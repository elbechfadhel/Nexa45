"""OrganizationService (V1d) : administration des organisations (suppliers) et de leurs utilisateurs.

Réservé à la plateforme : toute opération en TenantContext → PermissionDenied (403), y compris les lectures
(un tenant ne voit pas la liste des autres organisations). Les instances d'une organisation se créent et se
rattachent par InstanceService (seul point d'accès aux instances). Aucun framework web ici.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime

from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from uie_core.identity.context import Context, ensure_context, require_platform
from uie_core.identity.models import (
    MembershipRole,
    Organization,
    OrganizationMembership,
    OrganizationStatus,
    PlatformRole,
    User,
    UserStatus,
)
from uie_core.identity.passwords import hash_password, normalize_email
from uie_core.registry import models as m
from uie_core.registry.service import NotFound, RegistryError


class OrganizationError(RegistryError):
    """Donnée d'organisation ou d'utilisateur invalide (→ HTTP 400)."""


@dataclass(frozen=True)
class OrganizationView:
    id: uuid.UUID
    name: str
    slug: str
    status: str
    created_at: datetime
    user_count: int
    instance_count: int


@dataclass(frozen=True)
class MemberView:
    user_id: uuid.UUID
    email: str
    display_name: str
    status: str
    role: MembershipRole
    joined_at: datetime


@dataclass(frozen=True)
class UserView:
    user_id: uuid.UUID
    email: str
    display_name: str
    status: str
    platform_role: PlatformRole
    organizations: tuple[str, ...]  # slugs


@dataclass(frozen=True)
class AddedMember:
    member: MemberView
    user_created: bool  # False : utilisateur existant, mot de passe inchangé


def slugify(name: str) -> str:
    ascii_ = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-")[:100]


class OrganizationService:
    def __init__(self, session: Session, ctx: Context) -> None:
        self.s = session
        self.ctx = ensure_context(ctx)
        require_platform(self.ctx, "administration des organisations")

    # ------------------------------------------------------------------ #
    # Organisations
    # ------------------------------------------------------------------ #
    def list(self) -> list[OrganizationView]:
        orgs = self.s.scalars(select(Organization).order_by(Organization.name))
        return [self._view(o) for o in orgs]

    def get(self, organization_id: uuid.UUID) -> OrganizationView:
        return self._view(self._org(organization_id))

    def resolve(self, ref: str) -> OrganizationView:
        """id (UUID) ou slug — pratique pour la CLI."""
        try:
            return self.get(uuid.UUID(ref))
        except ValueError:
            org = self.s.scalar(select(Organization).where(Organization.slug == ref.strip().lower()))
            if org is None:
                raise NotFound(f"Organisation '{ref}' introuvable") from None
            return self._view(org)

    def create(self, name: str, slug: str | None = None) -> OrganizationView:
        name = name.strip()
        if not name:
            raise OrganizationError("Nom d'organisation obligatoire")
        slug = slugify(slug or name)
        if not slug:
            raise OrganizationError(f"Impossible de dériver un identifiant (slug) de '{name}'")
        if self.s.scalar(select(Organization.id).where(Organization.slug == slug)):
            raise OrganizationError(f"Une organisation avec l'identifiant '{slug}' existe déjà")
        org = Organization(name=name, slug=slug, status=OrganizationStatus.ACTIVE.value)
        self.s.add(org)
        self.s.flush()
        return self._view(org)

    # ------------------------------------------------------------------ #
    # Utilisateurs
    # ------------------------------------------------------------------ #
    def list_members(self, organization_id: uuid.UUID) -> list[MemberView]:
        org = self._org(organization_id)
        rows = self.s.execute(
            select(OrganizationMembership, User)
            .join(User, User.id == OrganizationMembership.user_id)
            .where(OrganizationMembership.organization_id == org.id)
            .order_by(User.email)
        ).all()
        return [self._member(ms, u) for ms, u in rows]

    def add_member(
        self,
        organization_id: uuid.UUID,
        email: str,
        *,
        display_name: str | None = None,
        password: SecretStr | None = None,
        role: MembershipRole = MembershipRole.MEMBER,
    ) -> AddedMember:
        """Ajoute un utilisateur à l'organisation. Email inconnu : crée l'utilisateur (mot de passe initial
        obligatoire). Email connu : ajoute seulement le membership (mot de passe jamais modifié ici)."""
        org = self._org(organization_id)
        normalized = normalize_email(email)
        user = self.s.scalar(select(User).where(User.email == normalized))
        created = user is None
        if user is None:
            if password is None:
                raise OrganizationError(
                    f"Utilisateur {normalized} inconnu : mot de passe initial obligatoire pour le créer"
                )
            user = self._new_user(normalized, display_name, password, PlatformRole.NONE)
        elif self.s.scalar(
            select(OrganizationMembership.id).where(
                OrganizationMembership.organization_id == org.id, OrganizationMembership.user_id == user.id
            )
        ):
            raise OrganizationError(f"{normalized} est déjà membre de cette organisation")
        ms = OrganizationMembership(organization_id=org.id, user_id=user.id, role=role.value)
        self.s.add(ms)
        self.s.flush()
        return AddedMember(self._member(ms, user), created)

    def create_user(
        self,
        email: str,
        password: SecretStr,
        *,
        display_name: str | None = None,
        platform_role: PlatformRole = PlatformRole.NONE,
    ) -> UserView:
        """Crée un utilisateur sans organisation (ex. un administrateur plateforme supplémentaire)."""
        normalized = normalize_email(email)
        if self.s.scalar(select(User.id).where(User.email == normalized)):
            raise OrganizationError(f"Un utilisateur {normalized} existe déjà")
        return self._user_view(self._new_user(normalized, display_name, password, platform_role))

    def list_users(self, organization_id: uuid.UUID | None = None) -> list[UserView]:
        q = select(User).order_by(User.email)
        if organization_id is not None:
            org = self._org(organization_id)
            q = q.join(OrganizationMembership, OrganizationMembership.user_id == User.id).where(
                OrganizationMembership.organization_id == org.id
            )
        return [self._user_view(u) for u in self.s.scalars(q)]

    # ------------------------------------------------------------------ #
    # interne
    # ------------------------------------------------------------------ #
    def _org(self, organization_id: uuid.UUID) -> Organization:
        org = self.s.get(Organization, organization_id)
        if org is None:
            raise NotFound(f"Organisation {organization_id} introuvable")
        return org

    def _new_user(
        self, email: str, display_name: str | None, password: SecretStr, platform_role: PlatformRole
    ) -> User:
        user = User(
            email=email,
            display_name=(display_name or "").strip() or email,
            password_hash=hash_password(password),
            status=UserStatus.ACTIVE.value,
            platform_role=platform_role.value,
        )
        self.s.add(user)
        self.s.flush()
        return user

    def _user_view(self, user: User) -> UserView:
        slugs = self.s.scalars(
            select(Organization.slug)
            .join(OrganizationMembership, OrganizationMembership.organization_id == Organization.id)
            .where(OrganizationMembership.user_id == user.id)
            .order_by(Organization.slug)
        )
        return UserView(
            user.id,
            user.email,
            user.display_name,
            user.status,
            PlatformRole(user.platform_role),
            tuple(slugs),
        )

    def _view(self, org: Organization) -> OrganizationView:
        users = self.s.scalar(
            select(func.count())
            .select_from(OrganizationMembership)
            .where(OrganizationMembership.organization_id == org.id)
        )
        instances = self.s.scalar(
            select(func.count()).select_from(m.Instance).where(m.Instance.organization_id == org.id)
        )
        return OrganizationView(
            org.id, org.name, org.slug, org.status, org.created_at, users or 0, instances or 0
        )

    @staticmethod
    def _member(ms: OrganizationMembership, user: User) -> MemberView:
        return MemberView(
            user.id, user.email, user.display_name, user.status, MembershipRole(ms.role), ms.created_at
        )
