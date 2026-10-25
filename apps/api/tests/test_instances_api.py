"""API instances + équivalence API / CLI (même InstanceService du Core)."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner
from uie_api import deps
from uie_api.app import create_app
from uie_api.deps import require_admin
from uie_connectors.odoo import OdooSchemaDiscoveryProvider, OdooSchemaLoader
from uie_connectors.odoo.testing import FakeOdooServer
from uie_core.identity import AdminContext, SystemContext
from uie_core.instances import FernetSecretStore, InstanceService, ProviderResolver
from uie_core.registry import RegistryService

ADMIN = AdminContext(uuid.UUID(int=45))  # console admin authentifiée (V1d)
FIX = Path(__file__).parents[3] / "packages" / "connectors" / "tests" / "fixtures"
PASSWORD = "Api-Odoo-S3cret-42"


class Resolver(ProviderResolver):
    def __init__(self, fake: FakeOdooServer) -> None:
        self.fake = fake

    def resolve(self, system, version, interface):  # noqa: ANN001, ANN201
        return OdooSchemaDiscoveryProvider(proxy_factory=self.fake.proxy)


@pytest.fixture
def secrets():
    return FernetSecretStore(FernetSecretStore.generate_key())


@pytest.fixture
def fake():
    return FakeOdooServer.from_dump(FIX / "odoo17_customer_instance.json", password=PASSWORD)


@pytest.fixture
def client(session, secrets, fake):
    RegistryService(session, SystemContext("test")).publish_snapshot(
        OdooSchemaLoader({"json": FIX / "odoo17_ref.json"}).build_snapshot()
    )
    app = create_app()
    app.dependency_overrides[deps.get_session] = lambda: session
    app.dependency_overrides[require_admin] = lambda: ADMIN  # console admin authentifiée
    app.dependency_overrides[deps.get_secret_store] = lambda: secrets
    app.dependency_overrides[deps.get_provider_resolver] = lambda: Resolver(fake)
    with TestClient(app) as c:
        yield c


BODY = {
    "name": "customer17",
    "system": "odoo",
    "version": "17",
    "environment": "test",
    "endpoint": "http://odoo17-customer:8069",
    "config": {"db": "customer17", "username": "admin"},
    "password": PASSWORD,
}


def test_instance_lifecycle_over_api(client):
    r = client.post("/admin/instances", json=BODY)
    assert r.status_code == 201, r.text
    inst = r.json()
    assert inst["connection"]["has_secret"] and inst["status"] == "draft"
    assert {c["status"] for c in inst["capabilities"]} == {"DECLARED", "UNAVAILABLE"}
    iid = inst["id"]

    r = client.post(f"/admin/instances/{iid}/test-connection")
    assert r.status_code == 200 and r.json()["ok"]
    caps = {c["capability"]: c["status"] for c in r.json()["instance"]["capabilities"]}
    assert caps["READ"] == caps["SCHEMA_DISCOVERY"] == "VERIFIED" and caps["CREATE"] == "DECLARED"

    r1 = client.post(f"/admin/instances/{iid}/discover-schema")
    r2 = client.post(f"/admin/instances/{iid}/discover-schema")
    assert r1.status_code == r2.status_code == 201
    assert (r1.json()["instance_snapshot"]["sequence"], r2.json()["instance_snapshot"]["sequence"]) == (1, 2)

    detail = client.get(f"/admin/instances/{iid}").json()
    assert [s["sequence"] for s in detail["snapshots"]] == [1, 2]
    assert detail["last_discovery"]["sequence"] == 2 and detail["connection"]["last_test_status"] == "OK"
    assert [i["name"] for i in client.get("/admin/instances").json()] == ["customer17"]

    diff = client.get(f"/admin/instances/{iid}/schema-diff").json()
    assert set(diff) == {"instance", "base_snapshot", "instance_snapshot", "summary", "changes"}
    assert diff["base_snapshot"]["id"] == diff["instance_snapshot"]["base_snapshot_id"]
    assert sorted(f["path"] for f in diff["summary"]["custom_fields"]) == [
        "product.product.x_supplier_ref",
        "product.template.x_notes_internal",
        "product.template.x_pack_code",
        "product.template.x_supplier_ref",
        "res.partner.x_customer_no",
    ]
    x = next(c for c in diff["changes"] if c["path"] == "res.partner.x_customer_no")
    assert (x["change_type"], x["origin"]) == ("ADDED", "CUSTOM")
    assert (
        client.get(f"/admin/instances/{iid}/schema-diff", params={"sequence": 1}).json()["instance_snapshot"][
            "sequence"
        ]
        == 1
    )


def test_secret_never_returned_by_api(client, fake):
    responses = [client.post("/admin/instances", json=BODY)]
    iid = responses[0].json()["id"]
    responses += [
        client.post(f"/admin/instances/{iid}/test-connection"),
        client.post(f"/admin/instances/{iid}/discover-schema"),
        client.get(f"/admin/instances/{iid}"),
        client.get("/admin/instances"),
        client.get(f"/admin/instances/{iid}/schema-diff"),
        # 422 : FastAPI renverrait l'input par défaut
        client.post("/admin/instances", json={**BODY, "name": "", "unknown": 1}),
        client.post("/admin/instances", json=BODY),  # 400 : nom déjà pris
    ]
    fake.password = "rotated"  # échec d'authentification : l'erreur ne doit pas contenir le secret
    responses.append(client.post(f"/admin/instances/{iid}/test-connection"))
    assert [r.status_code for r in responses[-3:]] == [422, 400, 200]
    for r in responses:
        assert PASSWORD not in r.text
        assert "fernet:" not in r.text  # le secret_ref n'est pas exposé non plus


def test_errors(client):
    assert client.get("/admin/instances/00000000-0000-0000-0000-000000000000").status_code == 404
    r = client.post("/admin/instances", json={**BODY, "version": "42"})
    assert r.status_code == 404 and "42" in r.json()["detail"]
    iid = client.post("/admin/instances", json=BODY).json()["id"]
    assert client.get(f"/admin/instances/{iid}/schema-diff").status_code == 404  # pas encore de discovery


def test_api_and_cli_use_the_same_core_service(client, session, secrets, monkeypatch):
    """Les deux entrypoints construisent le même InstanceService et appellent la même méthode."""
    import uie_cli.instances as cli_instances
    from uie_cli.main import app as cli_app

    assert cli_instances.InstanceService is deps.InstanceService is InstanceService
    calls: list[str] = []
    original = InstanceService.list

    def spy(self):  # noqa: ANN001, ANN202
        calls.append(type(self).__module__)
        return original(self)

    monkeypatch.setattr(InstanceService, "list", spy)

    @contextmanager
    def test_session():
        yield session

    monkeypatch.setattr(cli_instances, "session_scope", test_session)
    monkeypatch.setattr(cli_instances, "default_secret_store", lambda: secrets)
    monkeypatch.setenv("COLUMNS", "250")  # tableau Rich non tronqué

    client.post("/admin/instances", json=BODY)
    api_names = [i["name"] for i in client.get("/admin/instances").json()]
    res = CliRunner().invoke(cli_app, ["instance", "list"])
    assert res.exit_code == 0, res.output
    assert api_names == ["customer17"] and "customer17" in res.output
    assert calls == ["uie_core.instances.service", "uie_core.instances.service"]


class SpySecretStore(FernetSecretStore):
    def __init__(self) -> None:
        super().__init__(FernetSecretStore.generate_key())
        self.deleted: list[str] = []

    def delete(self, secret_ref: str) -> None:
        super().delete(secret_ref)
        self.deleted.append(secret_ref)


def test_delete_instance_over_api(session, fake):
    RegistryService(session, SystemContext("test")).publish_snapshot(
        OdooSchemaLoader({"json": FIX / "odoo17_ref.json"}).build_snapshot()
    )
    store = SpySecretStore()

    def committing_session():
        # comme uie_core.db.session_scope en production : commit en fin de requête
        yield session
        session.commit()

    app = create_app()
    app.dependency_overrides[deps.get_session] = committing_session
    app.dependency_overrides[require_admin] = lambda: ADMIN  # console admin authentifiée
    app.dependency_overrides[deps.get_secret_store] = lambda: store
    app.dependency_overrides[deps.get_provider_resolver] = lambda: Resolver(fake)
    with TestClient(app) as client:
        iid = client.post("/admin/instances", json=BODY).json()["id"]
        client.post(f"/admin/instances/{iid}/test-connection")
        snap = client.post(f"/admin/instances/{iid}/discover-schema").json()["instance_snapshot"]

        r = client.delete(f"/admin/instances/{iid}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["name"] == "customer17" and body["deleted"]["snapshots"] == 1
        assert body["deleted"]["connections"] == body["deleted"]["secrets"] == 1
        assert body["deleted"]["diffs"] > 0 and body["deleted"]["capabilities"] == 9
        assert body["kept_base_snapshot_ids"] == [snap["base_snapshot_id"]]
        assert PASSWORD not in r.text and "fernet:" not in r.text

        assert client.get(f"/admin/instances/{iid}").status_code == 404
        assert client.get("/admin/instances").json() == []
        assert client.delete(f"/admin/instances/{iid}").status_code == 404
        # le snapshot standard est toujours là, inchangé
        base = client.get(f"/admin/schema-snapshots/{snap['base_snapshot_id']}")
        assert base.status_code == 200 and base.json()["stats"]["entities"] == 9
    assert len(store.deleted) == 1  # retiré du SecretStore après le commit de la requête


def test_cors_allows_delete_from_console(client):
    r = client.options(
        "/admin/instances/00000000-0000-0000-0000-000000000000",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "DELETE"},
    )
    assert r.status_code == 200 and "DELETE" in r.headers["access-control-allow-methods"]


def test_cli_delete_asks_for_confirmation(client, session, secrets, monkeypatch):
    import uie_cli.instances as cli_instances
    from uie_cli.main import app as cli_app

    @contextmanager
    def test_session():
        yield session
        session.commit()

    monkeypatch.setattr(cli_instances, "session_scope", test_session)
    monkeypatch.setattr(cli_instances, "default_secret_store", lambda: secrets)
    client.post("/admin/instances", json=BODY)

    refused = CliRunner().invoke(cli_app, ["instance", "delete", "customer17"], input="n\n")
    assert refused.exit_code == 1 and [i["name"] for i in client.get("/admin/instances").json()] == [
        "customer17"
    ]
    done = CliRunner().invoke(cli_app, ["instance", "delete", "customer17", "--yes"])
    assert done.exit_code == 0, done.output
    assert "supprimée" in done.output and client.get("/admin/instances").json() == []
