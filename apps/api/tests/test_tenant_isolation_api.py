"""V1d commit 7 : isolation multi-tenant de bout en bout, au niveau HTTP (§15 du plan).

Complète le Core (test_tenant_context_core) et la Supplier API (test_supplier_api) :
- TOUTES les routes, découvertes dans l'application (aucune liste à maintenir) : /admin/* refusé à un tenant
  (403) et sans session (401) ; /api/* refusé sans session (401) et à un admin sans organisation (403) ;
- un accès à l'instance d'un autre tenant ne lit aucun secret et ne contacte jamais le système (espions) ;
- symétrie A → B et B → A ; données de l'autre tenant intactes ;
- révocation en cours de session : logout, membership retiré, organisation ou utilisateur désactivés ;
- architecture : aucun module de uie_core ne dépend d'un framework web ou CLI.
"""

from __future__ import annotations

import ast
import re
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from test_supplier_api import TMPL, Supplier, _reset_limiter, world  # noqa: F401 — fixtures partagées
from uie_connectors.odoo.testing import FakeOdooServer
from uie_core.identity import Organization, OrganizationMembership, OrganizationStatus, User, UserStatus
from uie_core.instances import FernetSecretStore

CORE_SRC = Path(__file__).resolve().parents[3] / "packages" / "core" / "src" / "uie_core"
FORBIDDEN_IMPORTS = {"fastapi", "starlette", "typer", "rich", "uvicorn", "uie_api", "uie_cli"}
# valeurs qui ne doivent jamais apparaître dans une réponse de refus
LEAKS = ("customer17", "x_supplier_ref", "odoo17-customer", "$argon2", "fernet:")


def _routes(app, prefix: str) -> list[tuple[str, str]]:  # noqa: ANN001
    """Toutes les opérations publiées par l'application (schéma OpenAPI : aucune liste à maintenir)."""
    return sorted(
        (method.upper(), path)
        for path, ops in app.openapi()["paths"].items()
        if path.startswith(prefix)
        for method in ops
    )


def _url(path: str, instance_id: str) -> str:
    """Remplit les paramètres de chemin : les instances visent l'instance de l'AUTRE tenant."""

    def value(m: re.Match[str]) -> str:
        name = m.group(1)
        if name in ("instance_id", "system_id"):
            return instance_id
        if name == "entity":
            return TMPL
        if name == "field":
            return "x_supplier_ref"
        return str(uuid.uuid4())

    return re.sub(r"\{(\w+)\}", value, path)


def _assert_refused(r, status: int, where: str) -> None:  # noqa: ANN001
    assert r.status_code == status, f"{where} → {r.status_code} {r.text[:200]}"
    assert set(r.json()) == {"detail"}, where
    assert not any(leak in r.text for leak in LEAKS), f"{where} : {r.text[:200]}"


# --------------------------------------------------------------------------- #
# Balayage de toutes les routes
# --------------------------------------------------------------------------- #
def test_every_admin_route_is_refused_to_tenants_and_anonymous(world):  # noqa: F811
    app = world["app"]
    routes = _routes(app, "/admin")
    assert len(routes) >= 35  # garde-fou : la découverte des routes fonctionne
    alice = Supplier(app, "alice@acme.io")
    anonymous = TestClient(app)
    theirs = world["instances"]["globex"]
    for method, path in routes:
        url = _url(path, theirs)
        _assert_refused(alice.c.request(method, url, json={}), 403, f"tenant {method} {url}")
        _assert_refused(anonymous.request(method, url, json={}), 401, f"anonyme {method} {url}")


def test_every_supplier_route_needs_a_tenant_context(world):  # noqa: F811
    app = world["app"]
    routes = _routes(app, "/api/")
    assert len(routes) >= 14
    admin = Supplier(app, "admin@go45.local")  # administrateur plateforme, sans organisation
    anonymous = TestClient(app)
    mine = world["instances"]["acme"]
    for method, path in routes:
        url = _url(path, mine)
        _assert_refused(anonymous.request(method, url, json={}), 401, f"anonyme {method} {url}")
        _assert_refused(admin.c.request(method, url, json={}), 403, f"admin {method} {url}")


# --------------------------------------------------------------------------- #
# Accès croisés : ni secret, ni réseau, dans les deux sens
# --------------------------------------------------------------------------- #
class _Spies:
    def __init__(self, monkeypatch, fake: FakeOdooServer) -> None:  # noqa: ANN001
        self.secret_reads = 0
        self.connections = 0
        spies = self
        real_get, real_proxy = FernetSecretStore.get, fake.proxy

        def get(store, secret_ref):  # noqa: ANN001, ANN202
            spies.secret_reads += 1
            return real_get(store, secret_ref)

        def proxy(url: str) -> Any:
            spies.connections += 1
            return real_proxy(url)

        monkeypatch.setattr(FernetSecretStore, "get", get)
        monkeypatch.setattr(fake, "proxy", proxy)


@pytest.mark.parametrize(
    ("me", "email", "other"), [("acme", "alice@acme.io", "globex"), ("globex", "bob@globex.io", "acme")]
)
def test_cross_tenant_calls_touch_no_secret_no_network_and_no_data(world, monkeypatch, me, email, other):  # noqa: F811
    app = world["app"]
    owner_email = "bob@globex.io" if other == "globex" else "alice@acme.io"
    owner = Supplier(app, owner_email)
    theirs = world["instances"][other]
    assert owner.post(f"/api/systems/{theirs}/catalog/analyze").status_code == 200
    before = owner.get(f"/api/systems/{theirs}/catalog/questions").json()

    spies = _Spies(monkeypatch, world["fake"])
    intruder = Supplier(app, email)
    calls = [
        ("POST", f"/api/systems/{theirs}/test-connection", None),
        ("POST", f"/api/systems/{theirs}/catalog/analyze", None),
        ("POST", f"/api/systems/{theirs}/catalog/ask-ai", None),
        ("POST", f"/api/systems/{theirs}/catalog/fields/{TMPL}/x_supplier_ref/confirm", {}),
        (
            "POST",
            f"/api/systems/{theirs}/catalog/fields/{TMPL}/x_pack_code/change",
            {"meaning": "packaging_code"},
        ),
        ("POST", f"/api/systems/{theirs}/catalog/fields/{TMPL}/x_notes_internal/ignore", {}),
        ("POST", f"/api/systems/{theirs}/catalog/fields/{TMPL}/x_supplier_ref/reopen", {}),
        ("GET", f"/api/systems/{theirs}/catalog/questions", None),
    ]
    for method, url, body in calls:
        _assert_refused(intruder.call(method, url, json=body), 404, f"{me} → {other} {method} {url}")
    assert (spies.secret_reads, spies.connections) == (0, 0)

    # le propriétaire retrouve exactement ses données ; son propre test de connexion, lui, passe
    assert owner.get(f"/api/systems/{theirs}/catalog/questions").json() == before
    assert owner.get(f"/api/systems/{theirs}/catalog/answers").json() == []
    assert owner.post(f"/api/systems/{theirs}/test-connection").json()["status"] == "connected"
    assert spies.secret_reads >= 1 and spies.connections >= 1  # les espions fonctionnent


# --------------------------------------------------------------------------- #
# Révocation pendant une session : vérifiée à CHAQUE requête
# --------------------------------------------------------------------------- #
def test_logout_revokes_access(world):  # noqa: F811
    alice = Supplier(world["app"], "alice@acme.io")
    assert alice.get("/api/systems").status_code == 200
    assert alice.post("/auth/logout").status_code == 204
    assert alice.get("/api/systems").status_code == 401


def test_removed_membership_or_inactive_organization_is_refused_at_once(world):  # noqa: F811
    session, acme = world["session"], world["orgs"]["acme"]
    alice = Supplier(world["app"], "alice@acme.io")
    assert alice.get("/api/systems").status_code == 200

    acme.status = OrganizationStatus.INACTIVE.value
    session.flush()
    assert alice.get("/api/systems").status_code == 403
    acme.status = OrganizationStatus.ACTIVE.value
    session.flush()
    assert alice.get("/api/systems").status_code == 200

    session.execute(delete(OrganizationMembership).where(OrganizationMembership.organization_id == acme.id))
    session.flush()
    assert alice.get("/api/systems").status_code == 403
    # re-sélectionner une organisation dont on n'est plus membre : refusé
    assert alice.post("/auth/context", json={"organization_id": str(acme.id)}).status_code == 403


def test_deactivated_user_loses_the_session(world):  # noqa: F811
    session = world["session"]
    bob = Supplier(world["app"], "bob@globex.io")
    assert bob.get("/api/systems").status_code == 200
    session.scalar(select(User).where(User.email == "bob@globex.io")).status = UserStatus.INACTIVE.value
    session.flush()
    assert bob.get("/api/systems").status_code == 401
    assert bob.c.post("/auth/login", json={"email": "bob@globex.io", "password": "x" * 12}).status_code == 401


def test_organization_chosen_by_the_client_is_never_trusted(world):  # noqa: F811
    """Aucun paramètre ni corps ne peut remplacer l'organisation de la session."""
    alice = Supplier(world["app"], "alice@acme.io")
    globex = str(world["orgs"]["globex"].id)
    assert alice.post("/auth/context", json={"organization_id": globex}).status_code == 403
    systems = alice.get("/api/systems", params={"organization_id": globex}).json()
    assert [s["id"] for s in systems] == [world["instances"]["acme"]]
    assert world["session"].get(Organization, uuid.UUID(globex)).status == "ACTIVE"


# --------------------------------------------------------------------------- #
# Architecture
# --------------------------------------------------------------------------- #
def test_core_depends_on_no_web_or_cli_framework():
    offenders = []
    for path in CORE_SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names = [node.module]
            else:
                continue
            offenders += [
                f"{path.relative_to(CORE_SRC)}: {n}" for n in names if n.split(".")[0] in FORBIDDEN_IMPORTS
            ]
    assert offenders == []
