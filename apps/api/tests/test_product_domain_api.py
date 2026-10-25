"""API Product Domain : cycle analyze → get → entities / fields / extensions / missing / snapshots."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from uie_api import deps
from uie_api.app import create_app
from uie_api.deps import require_admin
from uie_connectors.odoo import OdooSchemaDiscoveryProvider, OdooSchemaLoader
from uie_connectors.odoo.testing import FakeOdooServer
from uie_core.identity import AdminContext, SystemContext
from uie_core.instances import FernetSecretStore, ProviderResolver
from uie_core.registry import RegistryService

ADMIN = AdminContext(uuid.UUID(int=45))  # console admin authentifiée (V1d)
FIX = Path(__file__).parents[3] / "packages" / "connectors" / "tests" / "fixtures"
PASSWORD = "Api-Domain-S3cret"


class Resolver(ProviderResolver):
    def __init__(self, fake: FakeOdooServer) -> None:
        self.fake = fake

    def resolve(self, system, version, interface):  # noqa: ANN001, ANN201
        return OdooSchemaDiscoveryProvider(proxy_factory=self.fake.proxy)


@pytest.fixture
def client(session):
    RegistryService(session, SystemContext("test")).publish_snapshot(
        OdooSchemaLoader({"json": FIX / "odoo17_ref.json"}).build_snapshot()
    )
    fake = FakeOdooServer.from_dump(FIX / "odoo17_customer_instance.json", password=PASSWORD)
    secrets = FernetSecretStore(FernetSecretStore.generate_key())
    app = create_app()
    app.dependency_overrides[deps.get_session] = lambda: session
    app.dependency_overrides[require_admin] = lambda: ADMIN  # console admin authentifiée
    app.dependency_overrides[deps.get_secret_store] = lambda: secrets
    app.dependency_overrides[deps.get_provider_resolver] = lambda: Resolver(fake)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def iid(client) -> str:
    r = client.post(
        "/admin/instances",
        json={
            "name": "customer17",
            "system": "odoo",
            "version": "17",
            "endpoint": "http://odoo17-customer:8069",
            "config": {"db": "customer17", "username": "admin"},
            "password": PASSWORD,
        },
    )
    assert r.status_code == 201, r.text
    iid = r.json()["id"]
    assert client.post(f"/admin/instances/{iid}/discover-schema").status_code == 201
    return iid


def test_standard_definition(client):
    r = client.get("/admin/product-domains/standard", params={"system": "odoo", "version": "17"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"]["entities_by_role"]["PRIMARY"] == ["product.template"]
    assert body["definition"]["pack"] == "catalog/odoo/product_domain.yaml"
    assert {m["path"]: m["media_role"] for m in body["summary"]["media"]}[
        "product.template.image_1920"
    ] == "main"
    assert (
        client.get("/admin/product-domains/standard", params={"system": "odoo", "version": "99"}).status_code
        == 404
    )


def test_analyze_get_and_lists(client, iid):
    assert client.get(f"/admin/instances/{iid}/product-domain").status_code == 404  # pas encore analysé
    r = client.post(f"/admin/instances/{iid}/product-domain/analyze")
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["created"] is True and first["instance_schema_snapshot_sequence"] == 1
    again = client.post(f"/admin/instances/{iid}/product-domain/analyze").json()
    assert again["id"] == first["id"] and again["created"] is False

    got = client.get(f"/admin/instances/{iid}/product-domain").json()
    assert got["id"] == first["id"] and got["summary"]["extension_count"] == 9

    roles = {
        e["technical_name"]: e["role"]
        for e in client.get(f"/admin/instances/{iid}/product-domain/entities").json()
    }
    assert roles["product.template"] == "PRIMARY" and "hr.employee" not in roles

    custom = client.get(f"/admin/instances/{iid}/product-domain/fields", params={"origin": "CUSTOM"}).json()
    assert [(f["entity_technical_name"], f["field_technical_name"]) for f in custom] == [
        ("product.template", "x_supplier_ref"),
        ("product.template", "x_pack_code"),
        ("product.template", "x_notes_internal"),
        ("product.product", "x_supplier_ref"),
    ]
    media = client.get(f"/admin/instances/{iid}/product-domain/fields", params={"group": "media"}).json()
    assert all(f["is_media"] for f in media) and media
    assert (
        client.get(f"/admin/instances/{iid}/product-domain/fields", params={"group": "nope"}).status_code
        == 422
    )

    ext = client.get(f"/admin/instances/{iid}/product-domain/extensions").json()
    assert {(f["field_technical_name"], f["origin"], f["relevance"]) for f in ext} >= {
        ("x_supplier_ref", "CUSTOM", "UNRESOLVED"),
        ("project_id", "THIRD_PARTY", "UNRESOLVED"),
    }
    assert client.get(f"/admin/instances/{iid}/product-domain/missing-standard").json() == []

    # nouvelle découverte → nouveau domain snapshot ; l'historique garde l'ancien
    assert client.post(f"/admin/instances/{iid}/discover-schema").status_code == 201
    second = client.post(f"/admin/instances/{iid}/product-domain/analyze").json()
    assert second["id"] != first["id"] and second["instance_schema_snapshot_sequence"] == 2
    history = client.get(f"/admin/instances/{iid}/product-domain/snapshots").json()
    assert [h["id"] for h in history] == [second["id"], first["id"]]
    old = client.get(f"/admin/instances/{iid}/product-domain", params={"snapshot_id": first["id"]}).json()
    assert old["snapshot"] == first["snapshot"]
