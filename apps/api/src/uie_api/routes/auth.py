"""Routes /auth (V1d) : login, logout, utilisateur courant, sélection d'organisation.

Aucune réponse ne contient de hash, de jeton ni de secret : le jeton n'existe que dans Set-Cookie.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, SecretStr
from uie_core.db.base import utcnow
from uie_core.identity import AuthenticationError, MembershipRole, PlatformRole, Principal

from uie_api.auth import COOKIE_NAME, cookie_secure, login_limiter, session_token
from uie_api.deps import CurrentPrincipal, Identity

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    password: SecretStr


class OrganizationSelectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: uuid.UUID


class MembershipOut(BaseModel):
    organization_id: uuid.UUID
    organization_name: str
    organization_slug: str
    role: MembershipRole


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    platform_role: PlatformRole


class MeOut(BaseModel):
    user: UserOut
    memberships: list[MembershipOut]
    current_organization_id: uuid.UUID | None

    @classmethod
    def of(cls, p: Principal) -> MeOut:
        return cls(
            user=UserOut(
                id=p.user_id, email=p.email, display_name=p.display_name, platform_role=p.platform_role
            ),
            memberships=[
                MembershipOut(
                    organization_id=m.organization_id,
                    organization_name=m.organization_name,
                    organization_slug=m.organization_slug,
                    role=m.role,
                )
                for m in p.memberships
            ],
            current_organization_id=p.selected_organization_id,
        )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/login", summary="Connexion : ouvre une session (cookie HttpOnly)")
def login(body: LoginIn, request: Request, response: Response, identity: Identity) -> MeOut:
    ip = _client_ip(request)
    login_limiter.check(body.email, ip)  # RateLimited → 429
    try:
        new = identity.login(body.email, body.password)
    except AuthenticationError:
        login_limiter.failure(body.email, ip)
        raise
    login_limiter.success(body.email)
    response.set_cookie(
        COOKIE_NAME,
        new.token,
        max_age=int((new.expires_at - utcnow()).total_seconds()),
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
        path="/",
    )
    return MeOut.of(new.principal)


@router.post("/logout", status_code=204, summary="Déconnexion : révoque la session")
def logout(request: Request, response: Response, identity: Identity) -> Response:
    identity.logout(session_token(request))
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, secure=cookie_secure(), samesite="lax")
    response.status_code = 204
    return response


@router.get("/me", summary="Utilisateur connecté, rôle plateforme, memberships, organisation courante")
def me(principal: CurrentPrincipal) -> MeOut:
    return MeOut.of(principal)


@router.get("/context", summary="Organisation courante et organisations disponibles")
def get_context(principal: CurrentPrincipal) -> MeOut:
    return MeOut.of(principal)


@router.post("/context", summary="Sélectionne l'organisation courante (membership obligatoire, sinon 403)")
def select_context(body: OrganizationSelectIn, request: Request, identity: Identity) -> MeOut:
    return MeOut.of(identity.select_organization(session_token(request), body.organization_id))
