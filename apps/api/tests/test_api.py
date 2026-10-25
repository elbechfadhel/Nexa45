"""API Admin (lecture seule) : TestClient sur la base de test, snapshots issus des extraits réels Odoo."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import uie_connectors  # noqa: F401  (enregistre le loader)
from fastapi.testclient import TestClient
from uie_api.app import create_app, openapi_json
from uie_api.deps import get_session, require_admin
from uie_connectors.odoo import OdooSchemaLoader
from uie_core.identity import AdminContext, SystemContext
from uie_core.registry import RegistryService

ADMIN = AdminContext(uuid.UUID(int=45))  # console admin authentifiée (V1d)
FIX = Path(__file__).parents[3] / "packages" / "connectors" / "tests" / "fixtures"


@pytest.fixture
def snapshots(session):
    svc = RegistryService(session, SystemContext("test"))
    out = {}
    for v in ("17", "19"):
        snap = OdooSchemaLoader({"json": FIX / f"odoo{v}_real_extract.json"}).build_snapshot()
        out[v] = svc.publish_snapshot(snap).id
    session.flush()
    return out


@pytest.fixture
def client(session):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_admin] = lambda: ADMIN  # console admin authentifiée
    with TestClient(app) as c:
        yield c


def test_openapi_lists_registry_routes(client):
    paths = {
        p
        for p in client.get("/openapi.json").json()["paths"]
        if not p.startswith(
            (
                "/admin/instances",
                "/admin/product-domains",
                "/admin/vocabulary",
                "/auth",
                "/admin/organizations",
                "/api/",
            )
        )
    }
    assert paths == {
        "/admin/systems",
        "/admin/snapshots/resolve",
        "/admin/schema-snapshots/{snapshot_id}",
        "/admin/schema-snapshots/{snapshot_id}/entities",
        "/admin/schema-snapshots/{snapshot_id}/fields",
        "/admin/entities/{entity_id}",
        "/admin/entities/{entity_id}/fields",
        "/admin/schema-compare",
    }


def test_systems_tree(client, snapshots):
    r = client.get("/admin/systems")
    assert r.status_code == 200
    odoo = next(s for s in r.json() if s["code"] == "odoo")
    versions = {v["version"]: v for v in odoo["products"][0]["versions"]}
    assert {"17", "19"} <= set(versions)
    snap17 = versions["17"]["interfaces"][0]["snapshots"][0]
    assert snap17["id"] == str(snapshots["17"]) and snap17["snapshot_version"] == "1.0.0"


def test_resolve_and_snapshot_detail(client, snapshots):
    r = client.get("/admin/snapshots/resolve", params={"system": "odoo", "version": "17"})
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == str(snapshots["17"])
    assert (body["system"], body["version"], body["interface"]) == ("odoo", "17", "orm")
    assert body["label"] == "odoo 17 @1.0.0"
    assert body["stats"]["entities"] == 8 and body["source"]["loader"] == "odoo.json"
    assert client.get(f"/admin/schema-snapshots/{snapshots['17']}").json() == body


def test_entities_with_search(client, snapshots):
    r = client.get(f"/admin/schema-snapshots/{snapshots['17']}/entities")
    page = r.json()
    names = [e["technical_name"] for e in page["items"]]
    assert (page["total"], page["limit"], page["offset"]) == (8, 100, 0)
    assert names == sorted(names) and "res.partner" in names
    r = client.get(f"/admin/schema-snapshots/{snapshots['17']}/entities", params={"search": "partner"})
    found = {e["technical_name"]: e for e in r.json()["items"]}
    assert set(found) == {"res.partner", "res.partner.category"} and r.json()["total"] == 2
    assert found["res.partner"]["module"] == "base" and found["res.partner"]["field_count"] == 8


def test_entities_pagination(client, snapshots):
    url = f"/admin/schema-snapshots/{snapshots['17']}/entities"
    all_names = [e["technical_name"] for e in client.get(url).json()["items"]]
    p1 = client.get(url, params={"limit": 3, "offset": 0}).json()
    p3 = client.get(url, params={"limit": 3, "offset": 6}).json()
    assert p1["total"] == p3["total"] == 8
    assert [e["technical_name"] for e in p1["items"]] == all_names[:3]
    assert [e["technical_name"] for e in p3["items"]] == all_names[6:]
    assert client.get(url, params={"limit": 0}).status_code == 422


def test_search_fields(client, snapshots):
    url = f"/admin/schema-snapshots/{snapshots['17']}/fields"
    hits = client.get(url, params={"search": "name"}).json()
    pairs = {(h["entity"], h["technical_name"]) for h in hits}
    assert ("res.partner", "display_name") in pairs and ("product.template", "name") in pairs
    assert all("name" in h["technical_name"] for h in hits)
    hit = next(h for h in hits if (h["entity"], h["technical_name"]) == ("res.partner", "name"))
    entity = client.get(f"/admin/entities/{hit['entity_id']}").json()
    assert entity["technical_name"] == "res.partner"
    assert len(client.get(url, params={"search": "name", "limit": 2}).json()) == 2
    assert client.get(url).status_code == 422  # search requis


def test_entity_detail_fields_and_relations(client, snapshots):
    ents = client.get(
        f"/admin/schema-snapshots/{snapshots['17']}/entities", params={"search": "res.partner"}
    ).json()["items"]
    pid = next(e["id"] for e in ents if e["technical_name"] == "res.partner")
    body = client.get(f"/admin/entities/{pid}").json()
    fields = {f["technical_name"]: f for f in body["fields"]}
    assert fields["commercial_partner_id"]["computed"] and fields["commercial_partner_id"]["stored"]
    assert "is_company" in fields["commercial_partner_id"]["metadata"]["depends"]
    rel = next(r for r in body["relations"] if r["source_field"] == "commercial_partner_id")
    assert (rel["target_entity"], rel["relation_type"]) == ("res.partner", "many_to_one")
    assert rel["target_entity_id"] == pid
    only_fields = client.get(f"/admin/entities/{pid}/fields").json()
    assert [f["technical_name"] for f in only_fields] == [f["technical_name"] for f in body["fields"]]


def test_schema_compare_17_to_19_product_template(client, snapshots):
    r = client.get(
        "/admin/schema-compare",
        params={"from": str(snapshots["17"]), "to": str(snapshots["19"]), "entity": "product.template"},
    )
    assert r.status_code == 200
    body = r.json()
    assert (body["source"], body["target"]) == ("odoo 17 @1.0.0", "odoo 19 @1.0.0")
    assert {c["entity"] for c in body["changes"]} == {"product.template"}
    by_path = {c["path"]: c for c in body["changes"]}
    change = by_path["product.template.property_account_income_id"]
    assert change["change_type"] == "CHANGED"
    assert change["details"]["stored"] == {"from": False, "to": True}
    assert body["counts"]["CHANGED"] == sum(c["change_type"] == "CHANGED" for c in body["changes"])


def test_errors(client, snapshots):
    missing = uuid.uuid4()
    assert client.get(f"/admin/schema-snapshots/{missing}").status_code == 404
    assert client.get(f"/admin/entities/{missing}").status_code == 404
    assert (
        client.get("/admin/snapshots/resolve", params={"system": "odoo", "version": "42"}).status_code == 404
    )
    assert client.get("/admin/schema-snapshots/not-a-uuid").status_code == 422
    assert (
        client.get("/admin/schema-compare", params={"from": str(missing), "to": str(missing)}).status_code
        == 404
    )


def test_cors_for_web_console(client):
    r = client.get("/admin/systems", headers={"Origin": "http://localhost:3000"})
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"
    r = client.get("/admin/systems", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_web_openapi_is_up_to_date():
    # Le client TypeScript de apps/web est généré depuis ce fichier : il doit suivre le backend.
    committed = Path(__file__).parents[2] / "web" / "openapi.json"
    assert committed.read_text(encoding="utf-8") == openapi_json(), (
        "apps/web/openapi.json obsolète : lancer `pnpm gen:api`"
    )
