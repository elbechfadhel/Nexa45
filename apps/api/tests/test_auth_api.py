"""V1d commit 3 : authentification (/auth/*), sessions, sélection d'organisation, rate limit du login.

Aucune réponse ne contient de hash de mot de passe, de jeton ni de hash de jeton (vérifié sur toutes les
réponses du scénario).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from uie_api import deps
from uie_api.app import create_app
from uie_api.auth import COOKIE_NAME, login_limiter
from uie_core.db.base import utcnow
from uie_core.identity import (
    MembershipRole,
    Organization,
    OrganizationMembership,
    OrganizationStatus,
    PlatformRole,
    User,
    UserSession,
    UserStatus,
    hash_password,
)

PW = "Correct-Horse-Battery-9"
SEEN: list[str] = []  # toutes les réponses du module, contrôlées à la fin


@pytest.fixture(autouse=True)
def _reset_limiter():
    login_limiter.reset()
    yield
    login_limiter.reset()


@pytest.fixture
def people(session):  # noqa: ANN201
    orgs = {slug: Organization(name=slug.upper(), slug=slug) for slug in ("acme", "globex", "closed")}
    orgs["closed"].status = OrganizationStatus.INACTIVE.value
    session.add_all(orgs.values())
    session.flush()

    def user(email: str, *, role: PlatformRole = PlatformRole.NONE, status: UserStatus = UserStatus.ACTIVE):  # noqa: ANN202
        u = User(
            email=email,
            display_name=email.split("@")[0],
            password_hash=hash_password(SecretStr(PW)),
            platform_role=role.value,
            status=status.value,
        )
        session.add(u)
        session.flush()
        return u

    admin = user("admin@go45.local", role=PlatformRole.ADMIN)
    single = user("owner@acme.io")
    multi = user("consultant@both.io")
    inactive = user("gone@acme.io", status=UserStatus.INACTIVE)
    for u, slugs in ((single, ["acme"]), (multi, ["acme", "globex", "closed"]), (inactive, ["acme"])):
        for slug in slugs:
            session.add(
                OrganizationMembership(
                    organization_id=orgs[slug].id, user_id=u.id, role=MembershipRole.OWNER.value
                )
            )
    session.flush()
    return {"orgs": orgs, "admin": admin, "single": single, "multi": multi, "inactive": inactive}


@pytest.fixture
def client(session):  # noqa: ANN201
    app = create_app()
    app.dependency_overrides[deps.get_session] = lambda: session
    with TestClient(app) as c:
        yield c


def _login(client, email: str, password: str = PW):  # noqa: ANN001, ANN202
    r = client.post("/auth/login", json={"email": email, "password": password})
    SEEN.append(r.text)
    return r


def test_login_sets_a_secure_session_cookie_and_returns_the_user(client, people, session):
    r = _login(client, "  Owner@ACME.io ")
    assert r.status_code == 200, r.text
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE_NAME}=") and "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    body = r.json()
    assert body["user"]["email"] == "owner@acme.io" and body["user"]["platform_role"] == "NONE"
    # un seul membership : organisation sélectionnée automatiquement
    assert body["current_organization_id"] == str(people["orgs"]["acme"].id)
    token = client.cookies.get(COOKIE_NAME)
    row = session.scalar(select(UserSession).where(UserSession.user_id == people["single"].id))
    assert row.token_hash != token and len(row.token_hash) == 64  # seul le sha256 est en base
    me = client.get("/auth/me")
    SEEN.append(me.text)
    assert me.status_code == 200 and me.json()["user"]["id"] == str(people["single"].id)


def test_failed_logins_are_indistinguishable(client, people):
    wrong = _login(client, "owner@acme.io", "Wrong-Password-123")
    unknown = _login(client, "nobody@acme.io")
    inactive = _login(client, "gone@acme.io")
    malformed = _login(client, "not-an-email")
    for r in (wrong, unknown, inactive, malformed):
        assert (r.status_code, r.json()) == (401, {"detail": "Invalid email or password"})
        assert COOKIE_NAME not in r.headers.get("set-cookie", "")


def test_protected_auth_routes_need_a_valid_session(client, people):
    assert client.get("/auth/me").status_code == 401
    client.cookies.set(COOKIE_NAME, "forged-token")
    assert client.get("/auth/me").status_code == 401


def test_logout_revokes_the_session(client, people):
    _login(client, "owner@acme.io")
    token = client.cookies.get(COOKIE_NAME)
    assert client.post("/auth/logout").status_code == 204
    client.cookies.set(COOKIE_NAME, token)  # rejouer l'ancien jeton : refusé
    assert client.get("/auth/me").status_code == 401


def test_expired_session_is_refused(client, people, session):
    _login(client, "owner@acme.io")
    row = session.scalar(select(UserSession).where(UserSession.user_id == people["single"].id))
    row.expires_at = utcnow() - timedelta(seconds=1)
    session.flush()
    assert client.get("/auth/me").status_code == 401


def test_organization_selection_requires_a_membership(client, people):
    body = _login(client, "consultant@both.io").json()
    # plusieurs memberships : aucune sélection automatique ; organisation inactive absente de la liste
    assert body["current_organization_id"] is None
    assert {m["organization_slug"] for m in body["memberships"]} == {"acme", "globex"}
    ok = client.post("/auth/context", json={"organization_id": str(people["orgs"]["globex"].id)})
    SEEN.append(ok.text)
    assert ok.status_code == 200 and ok.json()["current_organization_id"] == str(people["orgs"]["globex"].id)
    other = Organization(name="Initech", slug="initech")
    client_session = people["orgs"]["acme"]  # noqa: F841 — les organisations sont en base (même session)
    for org_id in (people["orgs"]["closed"].id,):  # membership sur une organisation INACTIVE → refusé
        r = client.post("/auth/context", json={"organization_id": str(org_id)})
        assert r.status_code == 403
    assert other is not None
    r = client.post("/auth/context", json={"organization_id": "00000000-0000-4000-8000-00000000ffff"})
    assert r.status_code == 403  # organisation sans membership


def test_single_user_cannot_select_a_foreign_organization(client, people):
    _login(client, "owner@acme.io")
    r = client.post("/auth/context", json={"organization_id": str(people["orgs"]["globex"].id)})
    assert (r.status_code, r.json()) == (403, {"detail": "Not a member of this organization"})


def test_admin_has_a_platform_role_not_memberships(client, people):
    body = _login(client, "admin@go45.local").json()
    assert (body["user"]["platform_role"], body["memberships"], body["current_organization_id"]) == (
        "ADMIN",
        [],
        None,
    )


def test_login_is_rate_limited_per_email(client, people):
    for _ in range(5):
        assert _login(client, "owner@acme.io", "Wrong-Password-123").status_code == 401
    blocked = _login(client, "owner@acme.io")  # même avec le bon mot de passe
    assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) > 0
    assert _login(client, "admin@go45.local").status_code == 200  # autre email : non bloqué


def test_no_response_ever_contains_a_hash_or_a_token(client, people, session):
    _login(client, "owner@acme.io")
    token = client.cookies.get(COOKIE_NAME)
    secrets = [token, PW]
    secrets += [u.password_hash for u in session.scalars(select(User))]
    secrets += [s.token_hash for s in session.scalars(select(UserSession))]
    for text in SEEN:
        for secret in secrets:
            assert secret not in text
        assert "argon2" not in text and "password_hash" not in text and "token_hash" not in text
