"""V1d commit 1 : organisations, utilisateurs, memberships, sessions, ownership des instances, bootstrap."""

from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from test_instances_core import PASSWORD, _create, provider, svc  # noqa: F401 — fixtures partagées
from uie_core.db.base import utcnow
from uie_core.identity import (
    GO45_DEV_ORG_ID,
    MembershipRole,
    Organization,
    OrganizationMembership,
    PasswordError,
    PlatformRole,
    User,
    UserSession,
    bootstrap_platform_admin,
    hash_password,
    normalize_email,
    verify_password,
)
from uie_core.instances import InstanceCreate, InstanceError

MIGRATIONS = Path(__file__).resolve().parents[1] / "src" / "uie_core" / "db" / "migrations" / "versions"
ADMIN_PW = SecretStr("Adm1n-Initial-Pass!")


def _org(session, slug: str) -> Organization:
    org = Organization(name=slug.title(), slug=slug)
    session.add(org)
    session.flush()
    return org


# --------------------------------------------------------------------------- #
# Migration : organisation structurelle, aucun secret
# --------------------------------------------------------------------------- #
def test_structural_organization_exists_and_migration_needs_no_secret(session):
    org = session.get(Organization, GO45_DEV_ORG_ID)
    assert (org.name, org.slug, org.status) == ("GO45 Development", "go45-development", "ACTIVE")
    assert session.scalar(select(func.count()).select_from(User)) == 0  # la migration ne crée aucun compte
    source = next(MIGRATIONS.glob("*_v1d_*.py")).read_text("utf-8")
    assert "UIE_INITIAL_ADMIN_PASSWORD" not in source.split('"""', 2)[2]  # hors docstring : jamais lue


# --------------------------------------------------------------------------- #
# Mots de passe et utilisateurs
# --------------------------------------------------------------------------- #
def test_passwords_are_argon2_and_never_exposed(session):
    h = hash_password(ADMIN_PW)
    assert h.startswith("$argon2id$") and ADMIN_PW.get_secret_value() not in h
    assert verify_password(h, ADMIN_PW) and not verify_password(h, "wrong-password-123")
    assert not verify_password(None, ADMIN_PW)  # utilisateur inconnu : vérification factice, False
    with pytest.raises(PasswordError, match="trop court"):
        hash_password("short")
    user = User(email="a@b.io", display_name="A", password_hash=h)
    session.add(user)
    session.flush()
    assert h not in repr(user) and "argon2" not in repr(user)
    s = UserSession(user_id=user.id, token_hash="f" * 64, expires_at=utcnow() + timedelta(hours=1))
    session.add(s)
    session.flush()
    assert "f" * 64 not in repr(s)


def test_email_is_normalized_and_unique(session):
    assert normalize_email("  Jane.Doe@Example.COM ") == "jane.doe@example.com"
    with pytest.raises(PasswordError):
        normalize_email("not-an-email")
    session.add(User(email="x@y.io", display_name="X", password_hash=hash_password(ADMIN_PW)))
    session.flush()
    session.add(User(email="x@y.io", display_name="X2", password_hash=hash_password(ADMIN_PW)))
    with pytest.raises(IntegrityError):
        session.flush()


def test_membership_is_unique_per_organization_and_user_has_no_organization_column(session):
    org = _org(session, "acme")
    user = User(email="m@acme.io", display_name="M", password_hash=hash_password(ADMIN_PW))
    session.add(user)
    session.flush()
    session.add(
        OrganizationMembership(organization_id=org.id, user_id=user.id, role=MembershipRole.OWNER.value)
    )
    session.flush()
    assert not hasattr(User, "organization_id")
    session.add(
        OrganizationMembership(organization_id=org.id, user_id=user.id, role=MembershipRole.MEMBER.value)
    )
    with pytest.raises(IntegrityError):
        session.flush()


# --------------------------------------------------------------------------- #
# Bootstrap admin : idempotent
# --------------------------------------------------------------------------- #
def test_bootstrap_admin_is_idempotent(session):
    first = bootstrap_platform_admin(session, " Admin@GO45.local ", ADMIN_PW)
    assert first.created and first.email == "admin@go45.local" and first.platform_role == PlatformRole.ADMIN
    stored = session.scalar(select(User.password_hash).where(User.email == "admin@go45.local"))
    again = bootstrap_platform_admin(session, "admin@go45.local", SecretStr("An0ther-Password!"))
    assert (again.created, again.user_id) == (False, first.user_id)
    assert session.scalar(select(func.count()).select_from(User)) == 1
    assert session.scalar(select(User.password_hash).where(User.email == "admin@go45.local")) == stored


# --------------------------------------------------------------------------- #
# Ownership des instances
# --------------------------------------------------------------------------- #
def test_instance_belongs_to_an_organization(svc, session):  # noqa: F811
    inst = _create(svc)
    assert inst.organization_id == GO45_DEV_ORG_ID and inst.organization.name == "GO45 Development"


def test_instance_name_is_unique_per_organization_only(svc, session):  # noqa: F811
    _create(svc)
    other = _org(session, "supplier-b")
    same = InstanceCreate(
        name="demo-client",
        system="demo",
        version="1",
        endpoint="https://b.invalid",
        organization_id=other.id,
    )
    twin = svc.create(same)  # même nom, autre organisation : accepté
    assert twin.organization_id == other.id
    with pytest.raises(InstanceError, match="existe déjà dans cette organisation"):
        svc.create(same)
    with pytest.raises(InstanceError, match="ambigu"):
        svc.resolve("demo-client")  # la CLI par nom exige l'id quand le nom est partagé
    assert svc.resolve(str(twin.id)).id == twin.id


def test_unknown_organization_is_refused(svc):  # noqa: F811
    data = InstanceCreate(
        name="x",
        system="demo",
        version="1",
        endpoint="e",
        organization_id=uuid.uuid4(),
    )
    with pytest.raises(InstanceError, match="Organisation .* introuvable"):
        svc.create(data)


def test_organization_with_instances_cannot_be_deleted(svc, session):  # noqa: F811
    org = _org(session, "restricted")
    svc.create(
        InstanceCreate(
            name="kept",
            system="demo",
            version="1",
            endpoint="e",
            organization_id=org.id,
        )
    )
    session.flush()
    with pytest.raises(IntegrityError):
        session.execute(Organization.__table__.delete().where(Organization.id == org.id))
        session.flush()
