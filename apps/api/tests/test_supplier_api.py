"""V1d commit 4 : routes protégées (Admin / Supplier), Supplier API, administration des organisations.

Authentification réelle (login → cookie), aucune surcharge de contexte. Deux organisations possèdent chacune
une instance nommée « customer17 » (même schéma, décisions indépendantes).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from test_product_domain_api import PASSWORD, Resolver
from uie_ai import NullProposer
from uie_api import deps
from uie_api.app import create_app
from uie_api.auth import login_limiter
from uie_connectors.odoo import OdooSchemaLoader
from uie_connectors.odoo.testing import FakeOdooServer
from uie_core.identity import (
    MembershipRole,
    Organization,
    OrganizationMembership,
    PlatformRole,
    SystemContext,
    User,
    hash_password,
)
from uie_core.instances import FernetSecretStore, InstanceCreate, InstanceService
from uie_core.registry import RegistryService

FIX = Path(__file__).parents[3] / "packages" / "connectors" / "tests" / "fixtures"
PW = "Supplier-Portal-Pass-7"
TMPL = "product.template"
# clés techniques qui ne doivent JAMAIS apparaître dans une réponse de la Supplier API
TECHNICAL_KEYS = {
    "checksum",
    "snapshot",
    "snapshot_id",
    "snapshots",
    "baseline",
    "base_snapshot_id",
    "domain_snapshot_id",
    "diff",
    "origin",
    "rule_id",
    "rules_version",
    "rules_checksum",
    "semantic_rules_version",
    "prompt",
    "prompt_version",
    "prompt_checksum",
    "provider",
    "model",
    "endpoint",
    "config",
    "secret",
    "secret_ref",
    "password",
    "password_hash",
    "token",
    "meaning_status",
    "relevance",
    "native_type",
    "modules",
    "capabilities",
}
TECHNICAL_VALUES = {"STANDARD", "CUSTOM", "THIRD_PARTY", "UNKNOWN", PASSWORD, PW}


@pytest.fixture(autouse=True)
def _reset_limiter():
    login_limiter.reset()
    yield
    login_limiter.reset()


@pytest.fixture
def world(session) -> dict[str, Any]:
    RegistryService(session, SystemContext("test")).publish_snapshot(
        OdooSchemaLoader({"json": FIX / "odoo17_ref.json"}).build_snapshot()
    )
    fake = FakeOdooServer.from_dump(FIX / "odoo17_customer_instance.json", password=PASSWORD)
    secrets = FernetSecretStore(FernetSecretStore.generate_key())
    orgs = {slug: Organization(name=slug.title(), slug=slug) for slug in ("acme", "globex")}
    session.add_all(orgs.values())
    session.flush()

    def user(email: str, role: PlatformRole = PlatformRole.NONE) -> User:
        u = User(
            email=email,
            display_name=email.split("@")[0],
            password_hash=hash_password(SecretStr(PW)),
            platform_role=role.value,
        )
        session.add(u)
        session.flush()
        return u

    user("admin@go45.local", PlatformRole.ADMIN)
    for email, slug in (("alice@acme.io", "acme"), ("bob@globex.io", "globex")):
        session.add(
            OrganizationMembership(
                organization_id=orgs[slug].id, user_id=user(email).id, role=MembershipRole.OWNER.value
            )
        )
    svc = InstanceService(session, SystemContext("test"), secrets, Resolver(fake))
    instances = {}
    for slug, org in orgs.items():
        inst = svc.create(
            InstanceCreate(
                name="customer17",
                system="odoo",
                version="17",
                organization_id=org.id,
                endpoint="http://odoo17-customer:8069",
                config={"db": "customer17", "username": "admin"},
                password=SecretStr(PASSWORD),
            )
        )
        svc.discover_schema(inst.id)
        instances[slug] = str(inst.id)
    session.flush()

    app = create_app()
    app.dependency_overrides[deps.get_session] = lambda: session
    app.dependency_overrides[deps.get_secret_store] = lambda: secrets
    app.dependency_overrides[deps.get_provider_resolver] = lambda: Resolver(fake)
    app.dependency_overrides[deps.get_semantic_proposer] = NullProposer  # aucun LLM (indépendant du .env)
    return {
        "app": app,
        "orgs": orgs,
        "instances": instances,
        "session": session,
        "fake": fake,
        "secrets": secrets,
    }


SEEN: list[Any] = []  # corps JSON de toutes les réponses Supplier du module


class Supplier:
    """Client connecté ; chaque réponse /api/* est conservée pour le contrôle des clés techniques."""

    def __init__(self, app, email: str, password: str = PW) -> None:  # noqa: ANN001
        self.c = TestClient(app)
        r = self.c.post("/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text

    def call(self, method: str, url: str, **kw: Any):  # noqa: ANN201
        r = self.c.request(method, url, **kw)
        if url.startswith("/api/") and r.headers.get("content-type", "").startswith("application/json"):
            SEEN.append(r.json())
        return r

    def get(self, url: str, **kw: Any):  # noqa: ANN201
        return self.call("GET", url, **kw)

    def post(self, url: str, **kw: Any):  # noqa: ANN201
        return self.call("POST", url, **kw)


def _field(sid: str, field: str, action: str) -> str:
    return f"/api/systems/{sid}/catalog/fields/{TMPL}/{field}/{action}"


# --------------------------------------------------------------------------- #
# Protection des routes
# --------------------------------------------------------------------------- #
def test_admin_and_supplier_routes_require_the_right_context(world):
    anonymous = TestClient(world["app"])
    assert anonymous.get("/admin/systems").status_code == 401
    assert anonymous.get("/api/systems").status_code == 401

    alice = Supplier(world["app"], "alice@acme.io")
    for url in ("/admin/systems", "/admin/instances", "/admin/organizations", "/admin/vocabulary/product"):
        assert alice.get(url).status_code == 403, url
    iid = world["instances"]["acme"]
    assert alice.post(f"/admin/instances/{iid}/discover-schema").status_code == 403
    assert alice.get(f"/admin/instances/{iid}/schema-diff").status_code == 403

    admin = Supplier(world["app"], "admin@go45.local")
    assert admin.get("/admin/systems").status_code == 200
    assert len(admin.get("/admin/instances").json()) == 2  # l'admin voit tous les tenants
    assert admin.get("/api/systems").status_code == 403  # aucune organisation sélectionnée


# --------------------------------------------------------------------------- #
# Supplier API : cycle complet sur ses propres systèmes
# --------------------------------------------------------------------------- #
def test_supplier_catalog_cycle(world):
    alice = Supplier(world["app"], "alice@acme.io")
    sid = world["instances"]["acme"]

    systems = alice.get("/api/systems").json()
    assert [(s["id"], s["name"], s["system"]) for s in systems] == [(sid, "customer17", "Odoo 17")]
    assert systems[0]["catalog"] == {"status": "not_analyzed", "questions": None, "analyzed_at": None}
    assert systems[0]["connection"]["status"] == "not_tested"
    assert alice.get(f"/api/systems/{sid}/catalog").json()["status"] == "not_analyzed"

    r = alice.post(f"/api/systems/{sid}/test-connection")
    assert r.status_code == 200 and r.json()["status"] == "connected", r.text

    r = alice.post(f"/api/systems/{sid}/catalog/analyze")
    assert r.status_code == 200, r.text
    cat = r.json()
    assert (cat["status"], cat["questions"], cat["complete"]) == ("questions_open", 3, False)
    parts = ("identified", "excluded_automatically", "excluded_by_you", "possible", "questions")
    assert sum(cat[k] for k in parts) == cat["total"]
    assert {s["label"]: s["present"] for s in cat["structure"]}["Products"] is True

    questions = alice.get(f"/api/systems/{sid}/catalog/questions").json()
    assert [q["field"]["field"] for q in questions] == ["x_pack_code", "x_supplier_ref", "x_notes_internal"]
    sup = questions[1]
    assert (sup["field"]["label"], sup["field"]["type"]) == ("Supplier Reference", "Text")
    assert sup["suggestion"]["meaning"] == {"key": "supplier_reference", "label": "Supplier product code"}
    assert sup["suggestion"]["source"] == "rules"

    vocab = alice.get("/api/catalog/vocabulary").json()
    keys = {e["key"] for g in vocab["groups"] for e in g["entries"]}
    assert {"packaging_code", "supplier_reference"} <= keys and vocab["other"]["key"] == "other"

    assert alice.post(_field(sid, "x_supplier_ref", "confirm"), json={}).json()["answer"] == "confirmed"
    r = alice.post(_field(sid, "x_pack_code", "change"), json={"meaning": "packaging_code"})
    assert r.json()["meaning"]["key"] == "packaging_code"
    assert alice.post(_field(sid, "x_pack_code", "change"), json={"meaning": "free text"}).status_code == 400
    r = alice.post(_field(sid, "x_notes_internal", "ignore"), json={"note": "internal only"})
    assert (r.json()["answer"], r.json()["note"]) == ("ignored", "internal only")

    done = alice.get(f"/api/systems/{sid}/catalog").json()
    assert (done["status"], done["complete"], done["excluded_by_you"], done["answers"]) == (
        "complete",
        True,
        1,
        3,
    )
    answers = alice.get(f"/api/systems/{sid}/catalog/answers").json()
    assert [a["field"]["field"] for a in answers] == ["x_notes_internal", "x_pack_code", "x_supplier_ref"]

    r = alice.post(_field(sid, "x_pack_code", "reopen"), json={"note": "wrong meaning"})
    assert r.status_code == 200, r.text
    assert r.json()["reopened"] and r.json()["previous_answer"]["key"] == "packaging_code"
    assert alice.get(f"/api/systems/{sid}").json()["catalog"]["status"] == "questions_open"

    ai = alice.get(f"/api/systems/{sid}/catalog/ai").json()
    assert ai == {"available": False, "fields_to_clarify": 0, "ai_suggestions": 0}
    assert alice.post(f"/api/systems/{sid}/catalog/ask-ai").status_code == 400  # aucun fournisseur


# --------------------------------------------------------------------------- #
# Isolation : l'instance d'un autre tenant n'existe pas
# --------------------------------------------------------------------------- #
def test_other_tenant_system_is_indistinguishable_from_a_missing_one(world):
    bob = Supplier(world["app"], "bob@globex.io")
    theirs = world["instances"]["globex"]
    assert bob.post(f"/api/systems/{theirs}/catalog/analyze").status_code == 200
    bob_connection = bob.get(f"/api/systems/{theirs}").json()[
        "connection"
    ]  # Analyze teste la connexion (V2b.2)

    alice = Supplier(world["app"], "alice@acme.io")
    missing = str(uuid.uuid4())
    calls = [
        ("GET", "/api/systems/{}"),
        ("POST", "/api/systems/{}/test-connection"),
        ("POST", "/api/systems/{}/catalog/analyze"),
        ("GET", "/api/systems/{}/catalog"),
        ("GET", "/api/systems/{}/catalog/questions"),
        ("GET", "/api/systems/{}/catalog/answers"),
        ("GET", "/api/systems/{}/catalog/ai"),
        ("POST", "/api/systems/{}/catalog/ask-ai"),
        ("POST", "/api/systems/{}/catalog/fields/product.template/x_supplier_ref/confirm"),
        ("POST", "/api/systems/{}/catalog/fields/product.template/x_pack_code/change"),
        ("POST", "/api/systems/{}/catalog/fields/product.template/x_notes_internal/ignore"),
        ("POST", "/api/systems/{}/catalog/fields/product.template/x_supplier_ref/reopen"),
    ]
    for method, url in calls:
        body = {"meaning": "packaging_code"} if url.endswith("/change") else {}
        foreign = alice.call(method, url.format(theirs), json=body)
        unknown = alice.call(method, url.format(missing), json=body)
        assert foreign.status_code == unknown.status_code == 404, (url, foreign.text)
        assert foreign.json()["detail"].replace(theirs, "<id>") == unknown.json()["detail"].replace(
            missing, "<id>"
        ), url
    assert [s["id"] for s in alice.get("/api/systems").json()] == [world["instances"]["acme"]]

    # rien n'a bougé chez bob
    cat = bob.get(f"/api/systems/{theirs}/catalog").json()
    assert (cat["questions"], cat["answers"]) == (3, 0)
    assert bob.get(f"/api/systems/{theirs}").json()["connection"] == bob_connection  # aucun test par alice


def test_same_custom_field_in_two_tenants_has_independent_answers(world):
    alice, bob = Supplier(world["app"], "alice@acme.io"), Supplier(world["app"], "bob@globex.io")
    a, b = world["instances"]["acme"], world["instances"]["globex"]
    for client, sid in ((alice, a), (bob, b)):
        assert client.post(f"/api/systems/{sid}/catalog/analyze").status_code == 200
    alice.post(_field(a, "x_supplier_ref", "ignore"))
    bob.post(_field(b, "x_supplier_ref", "confirm"), json={})
    assert [x["answer"] for x in alice.get(f"/api/systems/{a}/catalog/answers").json()] == ["ignored"]
    assert [x["answer"] for x in bob.get(f"/api/systems/{b}/catalog/answers").json()] == ["confirmed"]


# --------------------------------------------------------------------------- #
# Administration des organisations
# --------------------------------------------------------------------------- #
def test_admin_creates_a_supplier_its_user_and_its_instance(world):
    admin = Supplier(world["app"], "admin@go45.local")
    r = admin.post("/admin/organizations", json={"name": "Initech GmbH"})
    assert r.status_code == 201, r.text
    org = r.json()
    assert (org["slug"], org["user_count"], org["instance_count"]) == ("initech-gmbh", 0, 0)
    assert admin.post("/admin/organizations", json={"name": "Initech GmbH"}).status_code == 400

    new_pw = "Initech-First-Login-1"
    users = f"/admin/organizations/{org['id']}/users"
    assert admin.post(users, json={"email": "peter@initech.io", "password": "short"}).status_code == 400
    assert admin.post(users, json={"email": "peter@initech.io"}).status_code == 400  # mot de passe requis
    r = admin.post(users, json={"email": "Peter@Initech.io", "display_name": "Peter", "password": new_pw})
    assert r.status_code == 201, r.text
    assert (r.json()["email"], r.json()["user_created"], r.json()["role"]) == (
        "peter@initech.io",
        True,
        "MEMBER",
    )
    assert new_pw not in r.text and "argon2" not in r.text
    # utilisateur existant : membership seulement, mot de passe inchangé
    r = admin.post(users, json={"email": "alice@acme.io", "role": "OWNER"})
    assert r.status_code == 201 and r.json()["user_created"] is False
    assert admin.post(users, json={"email": "alice@acme.io"}).status_code == 400  # déjà membre

    # instance déplacée d'acme vers initech : ses décisions la suivent, acme ne la voit plus
    acme_iid = world["instances"]["acme"]
    r = admin.post(f"/admin/organizations/{org['id']}/instances/{acme_iid}/attach")
    assert r.status_code == 200 and r.json()["organization_name"] == "Initech GmbH", r.text
    detail = admin.get(f"/admin/organizations/{org['id']}").json()
    assert [u["email"] for u in detail["users"]] == ["alice@acme.io", "peter@initech.io"]
    assert [i["id"] for i in detail["instances"]] == [acme_iid]
    assert detail["instance_count"] == 1 and detail["user_count"] == 2

    r = admin.post(
        f"/admin/organizations/{org['id']}/instances",
        json={"name": "initech-prod", "system": "odoo", "version": "17", "endpoint": "http://x:8069"},
    )
    assert r.status_code == 201 and r.json()["organization_id"] == org["id"], r.text
    missing = f"/admin/organizations/{uuid.uuid4()}"
    assert admin.get(missing).status_code == 404
    assert admin.post(f"{missing}/users", json={"email": "x@y.io", "password": new_pw}).status_code == 404

    peter = Supplier(world["app"], "peter@initech.io", new_pw)
    assert {s["name"] for s in peter.get("/api/systems").json()} == {"customer17", "initech-prod"}
    alice = Supplier(world["app"], "alice@acme.io")  # deux organisations : aucune sélection automatique
    assert alice.get("/api/systems").status_code == 403
    alice.post("/auth/context", json={"organization_id": str(world["orgs"]["acme"].id)})
    assert alice.get("/api/systems").json() == []


# --------------------------------------------------------------------------- #
# Aucune donnée technique dans la Supplier API (dernier test du module)
# --------------------------------------------------------------------------- #
def test_zz_supplier_responses_contain_no_technical_data():
    assert SEEN, "aucune réponse Supplier collectée"

    def walk(node: Any, path: str = "") -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                assert k not in TECHNICAL_KEYS, f"clé technique {k!r} en {path}"
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        elif isinstance(node, str):
            assert node not in TECHNICAL_VALUES, f"valeur technique {node!r} en {path}"
            assert "$argon2" not in node and "fernet:" not in node, path

    for body in SEEN:
        walk(body)
