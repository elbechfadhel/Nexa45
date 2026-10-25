"""Comptes et isolation multi-tenant (V1d) : Organization, User, OrganizationMembership, UserSession.

    Organization ── Memberships (OWNER | MEMBER) ── Users
         └── Instances → snapshots, domaine, bindings (ownership par la chaîne instance_id)

Un user n'a pas d'organization_id : il appartient à des organisations par ses memberships. `platform_role
= ADMIN` (administrateur GO45) est un rôle distinct, pas un membership sur toutes les organisations.
Les sessions sont stockées côté serveur : seul le sha256 du jeton est en base (révocables au logout).
Le Registry standard, les domain packs, le vocabulaire et les prompts restent globaux (pas d'organization_id).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from uie_core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utcnow

_UUID = UUID(as_uuid=True)

# Organisation structurelle créée par la migration V1d : toutes les instances existantes y sont rattachées.
GO45_DEV_ORG_ID = uuid.UUID("00000000-0000-4000-8000-000000000045")
GO45_DEV_ORG_SLUG = "go45-development"
GO45_DEV_ORG_NAME = "GO45 Development"


class OrganizationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class UserStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class PlatformRole(StrEnum):
    NONE = "NONE"
    ADMIN = "ADMIN"  # administrateur GO45 : voit toutes les organisations et les outils techniques


class MembershipRole(StrEnum):
    OWNER = "OWNER"
    MEMBER = "MEMBER"


class _Updated:
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, _Updated, Base):
    __tablename__ = "organizations"
    __table_args__ = (UniqueConstraint("slug"),)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=OrganizationStatus.ACTIVE.value)

    memberships: Mapped[list[OrganizationMembership]] = relationship(
        back_populates="organization", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"Organization(id={self.id}, slug={self.slug!r}, status={self.status})"


class User(UUIDPrimaryKeyMixin, TimestampMixin, _Updated, Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email"),)

    email: Mapped[str] = mapped_column(String(320), nullable=False)  # normalisé (trim, minuscules)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)  # argon2id, jamais exposé
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=UserStatus.ACTIVE.value)
    platform_role: Mapped[str] = mapped_column(String(20), nullable=False, default=PlatformRole.NONE.value)

    memberships: Mapped[list[OrganizationMembership]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:  # jamais le hash
        return f"User(id={self.id}, email={self.email!r}, platform_role={self.platform_role})"


class OrganizationMembership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organization_memberships"
    __table_args__ = (UniqueConstraint("organization_id", "user_id"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False, default=MembershipRole.MEMBER.value)

    organization: Mapped[Organization] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")


class UserSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Session de connexion : le cookie porte un jeton aléatoire, la base n'en garde que le sha256."""

    __tablename__ = "user_sessions"
    __table_args__ = (UniqueConstraint("token_hash"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # organisation sélectionnée (TenantContext), toujours vérifiée contre un membership à chaque requête
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("organizations.id", ondelete="SET NULL")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship()

    def __repr__(self) -> str:  # jamais le hash du jeton
        return f"UserSession(id={self.id}, user_id={self.user_id}, expires_at={self.expires_at})"
