"""V1d : OrganizationService (plateforme seulement) et rattachement d'une instance à une organisation."""

from __future__ import annotations

import uuid

import pytest
from pydantic import SecretStr
from test_instances_core import _create, provider, svc  # noqa: F401 — fixtures partagées
from uie_core.identity import (
    GO45_DEV_ORG_ID,
    AdminContext,
    MembershipRole,
    PasswordError,
    PermissionDenied,
    PlatformRole,
    SystemContext,
    TenantContext,
)
from uie_core.identity.organizations import OrganizationError, OrganizationService, slugify
from uie_core.instances import InstanceService
from uie_core.registry import NotFound

PW = SecretStr("Organization-Test-Pass-1")


def _tenant(org_id: uuid.UUID) -> TenantContext:
    return TenantContext(uuid.uuid4(), org_id, MembershipRole.OWNER)


def test_tenant_cannot_administer_organizations(session):
    with pytest.raises(PermissionDenied):
        OrganizationService(session, _tenant(GO45_DEV_ORG_ID))
    with pytest.raises(TypeError):
        OrganizationService(session, None)  # type: ignore[arg-type]
    OrganizationService(session, AdminContext(uuid.uuid4()))  # admin : autorisé


def test_organizations_users_and_memberships(session):
    orgs = OrganizationService(session, SystemContext("test"))
    assert slugify("Müller & Söhne GmbH") == "muller-sohne-gmbh"
    org = orgs.create("Müller & Söhne GmbH")
    assert (org.slug, org.user_count, org.instance_count) == ("muller-sohne-gmbh", 0, 0)
    assert orgs.resolve("muller-sohne-gmbh").id == orgs.resolve(str(org.id)).id == org.id
    with pytest.raises(OrganizationError):
        orgs.create("Muller Sohne GmbH")  # même slug
    with pytest.raises(NotFound):
        orgs.resolve("nope")

    with pytest.raises(OrganizationError):
        orgs.add_member(org.id, "new@ms.de")  # inconnu sans mot de passe
    with pytest.raises(PasswordError):
        orgs.add_member(org.id, "new@ms.de", password=SecretStr("short"))
    added = orgs.add_member(org.id, "New@MS.de", display_name="New", password=PW, role=MembershipRole.OWNER)
    assert added.user_created and (added.member.email, added.member.role) == (
        "new@ms.de",
        MembershipRole.OWNER,
    )
    with pytest.raises(OrganizationError):
        orgs.add_member(org.id, "new@ms.de")  # déjà membre

    admin = orgs.create_user("ops@go45.local", PW, platform_role=PlatformRole.ADMIN)
    assert admin.platform_role == PlatformRole.ADMIN and admin.organizations == ()
    with pytest.raises(OrganizationError):
        orgs.create_user("OPS@go45.local", PW)
    again = orgs.add_member(org.id, "ops@go45.local", password=SecretStr("ignored-for-existing-user"))
    assert not again.user_created

    assert [u.email for u in orgs.list_users(org.id)] == ["new@ms.de", "ops@go45.local"]
    assert {u.email: u.organizations for u in orgs.list_users()}["new@ms.de"] == ("muller-sohne-gmbh",)
    assert orgs.get(org.id).user_count == 2


def test_attach_instance_to_another_organization(session, svc):  # noqa: F811
    orgs = OrganizationService(session, SystemContext("test"))
    other = orgs.create("Other Supplier")
    inst = _create(svc)
    tenant = InstanceService(session, _tenant(GO45_DEV_ORG_ID), svc.secrets, svc.providers)
    with pytest.raises(PermissionDenied):
        tenant.assign_organization(inst.id, other.id)
    with pytest.raises(NotFound):
        svc.assign_organization(inst.id, uuid.uuid4())

    moved = svc.assign_organization(inst.id, other.id)
    assert moved.organization_id == other.id and moved.organization.name == "Other Supplier"
    assert orgs.get(other.id).instance_count == 1
    with pytest.raises(NotFound):
        tenant.get(inst.id)  # l'ancien propriétaire ne la voit plus
    assert InstanceService(session, _tenant(other.id), svc.secrets, svc.providers).get(inst.id).id == inst.id
