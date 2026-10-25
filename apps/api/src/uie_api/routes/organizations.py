"""Routes Admin des organisations (V1d) : suppliers, leurs utilisateurs, leurs instances.

API → OrganizationService / InstanceService (AdminContext). Aucune réponse ne contient de hash de mot de
passe, de jeton ni de secret : le mot de passe initial est écrit seulement.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from uie_core.identity import MembershipRole
from uie_core.identity.organizations import MemberView, OrganizationView

from uie_api.deps import Instances, Organizations
from uie_api.schemas_instances import InstanceCreateIn, InstanceDetail, InstanceOut

router = APIRouter(prefix="/admin/organizations", tags=["organizations"])


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OrganizationCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    slug: str | None = Field(default=None, max_length=100, description="Dérivé du nom si absent")


class MemberCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    display_name: str | None = None
    password: SecretStr | None = Field(
        default=None, description="Mot de passe initial (nouvel utilisateur seulement) ; jamais renvoyé"
    )
    role: MembershipRole = MembershipRole.MEMBER


class OrganizationInstanceIn(BaseModel):
    """Création d'une instance dans l'organisation : l'organisation vient du chemin, jamais du corps."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    system: str
    version: str
    product: str | None = None
    interface: str | None = None
    environment: str = "test"
    endpoint: str
    config: dict[str, str | int | bool] = Field(default_factory=dict)
    password: SecretStr | None = Field(default=None, description="Écrit seulement : jamais renvoyé")


class OrganizationOut(_Out):
    id: uuid.UUID
    name: str
    slug: str
    status: str
    created_at: datetime
    user_count: int
    instance_count: int

    @classmethod
    def of(cls, o: OrganizationView) -> OrganizationOut:
        return cls(
            id=o.id,
            name=o.name,
            slug=o.slug,
            status=o.status,
            created_at=o.created_at,
            user_count=o.user_count,
            instance_count=o.instance_count,
        )


class MemberOut(_Out):
    user_id: uuid.UUID
    email: str
    display_name: str
    status: str
    role: MembershipRole
    joined_at: datetime

    @classmethod
    def of(cls, mv: MemberView) -> MemberOut:
        return cls(
            user_id=mv.user_id,
            email=mv.email,
            display_name=mv.display_name,
            status=mv.status,
            role=mv.role,
            joined_at=mv.joined_at,
        )


class MemberAddedOut(MemberOut):
    user_created: bool  # False : utilisateur existant ajouté, mot de passe inchangé


class OrganizationDetail(OrganizationOut):
    users: list[MemberOut]
    instances: list[InstanceOut]


@router.get("", summary="Organisations (suppliers) : utilisateurs et instances comptés")
def list_organizations(svc: Organizations) -> list[OrganizationOut]:
    return [OrganizationOut.of(o) for o in svc.list()]


@router.post("", status_code=status.HTTP_201_CREATED, summary="Crée une organisation (supplier)")
def create_organization(svc: Organizations, body: OrganizationCreateIn) -> OrganizationOut:
    return OrganizationOut.of(svc.create(body.name, body.slug))


@router.get("/{organization_id}", summary="Organisation : utilisateurs et instances")
def get_organization(
    svc: Organizations, instances: Instances, organization_id: uuid.UUID
) -> OrganizationDetail:
    org = svc.get(organization_id)
    return OrganizationDetail(
        **OrganizationOut.of(org).model_dump(),
        users=[MemberOut.of(mv) for mv in svc.list_members(org.id)],
        instances=[InstanceOut.of(i) for i in instances.list() if i.organization_id == org.id],
    )


@router.get("/{organization_id}/users", summary="Utilisateurs de l'organisation")
def list_users(svc: Organizations, organization_id: uuid.UUID) -> list[MemberOut]:
    return [MemberOut.of(mv) for mv in svc.list_members(organization_id)]


@router.post(
    "/{organization_id}/users",
    status_code=status.HTTP_201_CREATED,
    summary="Ajoute un utilisateur (créé avec son mot de passe initial s'il n'existe pas)",
)
def add_user(svc: Organizations, organization_id: uuid.UUID, body: MemberCreateIn) -> MemberAddedOut:
    added = svc.add_member(
        organization_id, body.email, display_name=body.display_name, password=body.password, role=body.role
    )
    return MemberAddedOut(**MemberOut.of(added.member).model_dump(), user_created=added.user_created)


@router.post(
    "/{organization_id}/instances",
    status_code=status.HTTP_201_CREATED,
    summary="Crée une instance appartenant à l'organisation (secret chiffré, jamais renvoyé)",
)
def create_instance(
    svc: Organizations, instances: Instances, organization_id: uuid.UUID, body: OrganizationInstanceIn
) -> InstanceDetail:
    svc.get(organization_id)  # 404 explicite si l'organisation n'existe pas
    data = InstanceCreateIn(**body.model_dump(exclude={"password"}), organization_id=organization_id)
    inst = instances.create(data.model_copy(update={"password": body.password}).to_core())
    return InstanceDetail.build(instances.get(inst.id))


@router.post(
    "/{organization_id}/instances/{instance_id}/attach",
    summary="Rattache une instance existante à l'organisation (snapshots, domaine et décisions la suivent)",
)
def attach_instance(
    svc: Organizations, instances: Instances, organization_id: uuid.UUID, instance_id: uuid.UUID
) -> InstanceDetail:
    svc.get(organization_id)
    inst = instances.assign_organization(instance_id, organization_id)
    return InstanceDetail.build(instances.get(inst.id))
