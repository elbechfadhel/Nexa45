"""V2b.1 commit 2 : un fournisseur connecte son système depuis la Supplier API (authentification réelle).

Test sans enregistrement → sauvegarde → le système apparaît dans My systems, dans SON organisation seulement ;
erreurs lisibles ; aucun identifiant de connexion renvoyé ; adresses internes refusées sauf en développement.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from test_supplier_api import PASSWORD, TECHNICAL_KEYS, Supplier, _reset_limiter, world  # noqa: F401
from uie_core.instances import ALLOW_PRIVATE_ENV
from uie_core.registry import models as m

URL = "http://odoo.example.test:8069"
SEEN: list[Any] = []
# le fournisseur ne doit voir aucun de ces termes techniques
TECHNICAL_TEXT = ("PermissionError", "Fault", "xmlrpc", "Authentification", "Traceback", "ConnectionRefused")


@pytest.fixture(autouse=True)
def _private_endpoints_allowed(monkeypatch):
    monkeypatch.setenv(ALLOW_PRIVATE_ENV, "true")


def _form(w, **over: Any) -> dict[str, Any]:  # noqa: ANN001
    """Formulaire de connexion tel que le portail l'envoie : valeurs par champ DÉCLARÉ par le connecteur."""
    fields = {"url": URL, "database": w["fake"].db, "username": "admin", "password": PASSWORD, **over}
    return {"system": "odoo", "fields": fields}


def _call(client: Supplier, method: str, url: str, **kw: Any):  # noqa: ANN202
    r = client.call(method, url, **kw)
    if r.headers.get("content-type", "").startswith("application/json"):
        SEEN.append(r.json())
    return r


def test_connect_my_odoo_from_test_to_my_systems(world):  # noqa: F811
    alice = Supplier(world["app"], "alice@acme.io")
    before = [s["name"] for s in alice.get("/api/systems").json()]

    r = _call(alice, "POST", "/api/systems/check-connection", json=_form(world))
    assert r.status_code == 200, r.text
    assert r.json() == {
        "ok": True,
        "detected": "Odoo 17",
        "supported": True,
        "message": None,
        "details": [],
    }
    assert [s["name"] for s in alice.get("/api/systems").json()] == before  # rien n'est enregistré

    r = _call(alice, "POST", "/api/systems", json={**_form(world), "name": "My Odoo"})
    assert r.status_code == 201, r.text
    created = r.json()
    assert (created["name"], created["system"], created["connection"]["status"]) == (
        "My Odoo",
        "Odoo 17",
        "connected",
    )
    assert (created["server_url"], created["database"]) == (URL, world["fake"].db)
    assert created["catalog"]["status"] == "awaiting_setup"  # aucune analyse lancée (V2b.2)

    mine = {s["name"]: s for s in alice.get("/api/systems").json()}
    assert set(mine) == {*before, "My Odoo"}
    assert alice.get(f"/api/systems/{created['id']}").json()["connection"]["status"] == "connected"

    inst = world["session"].get(m.Instance, created["id"])
    assert inst.organization_id == world["orgs"]["acme"].id and inst.environment == "production"
    conn = inst.connections[0]
    assert PASSWORD not in str(conn.config) and PASSWORD not in (conn.secret_ref or "")

    # l'autre fournisseur ne voit rien : même 404 qu'un système inexistant
    bob = Supplier(world["app"], "bob@globex.io")
    assert "My Odoo" not in [s["name"] for s in bob.get("/api/systems").json()]
    assert bob.get(f"/api/systems/{created['id']}").status_code == 404
    assert bob.post(f"/api/systems/{created['id']}/test-connection").status_code == 404

    dup = _call(alice, "POST", "/api/systems", json={**_form(world), "name": "My Odoo"})
    assert (
        dup.status_code == 409
        and dup.json()["detail"] == "A system with this name already exists in your workspace."
    )


def test_failures_are_explained_simply_and_save_nothing(world):  # noqa: F811
    alice = Supplier(world["app"], "alice@acme.io")
    before = len(alice.get("/api/systems").json())
    wrong = _call(
        alice, "POST", "/api/systems/check-connection", json=_form(world, password="Wrong-Pass-123")
    )
    assert wrong.json() == {
        "ok": False,
        "detected": None,
        "supported": False,
        "message": "The credentials were not accepted. Check them and try again.",
        "details": [],
    }
    saved = _call(
        alice, "POST", "/api/systems", json={**_form(world, password="Wrong-Pass-123"), "name": "Nope"}
    )
    assert saved.status_code == 422
    assert saved.json()["detail"] == "The credentials were not accepted. Check them and try again."
    assert len(alice.get("/api/systems").json()) == before
    for r in (wrong, saved):
        assert "Wrong-Pass-123" not in r.text and not any(t in r.text for t in TECHNICAL_TEXT)
    # champs manquants : validation (422), sans jamais renvoyer la valeur saisie
    empty = alice.post("/api/systems/check-connection", json=_form(world, username=""))
    assert empty.status_code == 422 and PASSWORD not in empty.text


def test_organization_cannot_be_chosen_by_the_supplier(world):  # noqa: F811
    alice = Supplier(world["app"], "alice@acme.io")
    globex = str(world["orgs"]["globex"].id)
    r = alice.post("/api/systems", json={**_form(world), "name": "Sneaky", "organization_id": globex})
    assert r.status_code == 422
    assert world["session"].scalar(select(m.Instance).where(m.Instance.name == "Sneaky")) is None


def test_internal_addresses_are_refused_outside_development(world, monkeypatch):  # noqa: F811
    monkeypatch.delenv(ALLOW_PRIVATE_ENV)
    alice = Supplier(world["app"], "alice@acme.io")
    for url in ("http://localhost:8027", "http://192.168.1.10:8069", "ftp://odoo.example.test"):
        r = _call(alice, "POST", "/api/systems/check-connection", json=_form(world, url=url))
        assert r.status_code == 400, url
        assert "localhost" not in r.json()["detail"].lower() or url.startswith("ftp")
    assert alice.post("/api/systems/check-connection", json=_form(world)).json()["ok"] is True


def test_admin_instance_creation_is_unchanged(world, monkeypatch):  # noqa: F811
    monkeypatch.delenv(ALLOW_PRIVATE_ENV)  # l'Admin n'est jamais concerné par la garde des adresses
    admin = Supplier(world["app"], "admin@go45.local")
    r = admin.post(
        "/admin/instances",
        json={"name": "admin-local", "system": "odoo", "version": "17", "endpoint": "http://localhost:8027"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["environment"] == "test"


def test_zz_no_credentials_or_technical_data_in_responses():
    assert SEEN
    for body in SEEN:
        text = str(body)
        assert PASSWORD not in text and "fernet:" not in text and "secret_ref" not in text

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                for k, v in node.items():
                    assert k not in TECHNICAL_KEYS | {"password"}, k
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(body)


def test_connectable_systems_come_from_the_registry(world):  # noqa: F811
    alice = Supplier(world["app"], "alice@acme.io")
    r = _call(alice, "GET", "/api/connectors")
    assert r.status_code == 200
    (odoo,) = r.json()  # seul Odoo 17 est publié ici
    assert (odoo["system"], odoo["name"], odoo["versions"]) == ("odoo", "Odoo", ["17"])
    # le formulaire vient du CONNECTEUR : nom, sorte et obligation de chaque champ (jamais codé dans la page)
    assert [(f["name"], f["kind"], f["required"]) for f in odoo["fields"]] == [
        ("url", "url", True),
        ("database", "text", True),
        ("username", "text", True),
        ("password", "secret", True),
    ]
