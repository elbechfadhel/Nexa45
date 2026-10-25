"""Phase 1a Lexware Office par la Supplier API : connecter Lexware Office avec une clé d'API, puis voir ce que
les clients et fournisseurs d'un système source y deviendraient.

La source de ce parcours est un faux serveur Odoo (schéma réel de customer17, partenaires INVENTÉS) : UN
fournisseur de Partner CDM parmi d'autres, pas une intégration particulière (la cible est testée sans aucune
source dans `domain-packs/tests/test_partner_lexware_*.py`). La cible est un faux serveur Lexware : aucun
appel réseau, aucune clé réelle. Rien n'est envoyé ; l'aperçu n'écrit rien."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select
from test_product_domain_api import PASSWORD
from test_supplier_api import FIX, PW, TECHNICAL_KEYS
from uie_ai import NullProposer
from uie_api import deps
from uie_api.app import create_app
from uie_connectors.lexware import LexwareClient, LexwareDiscoveryProvider, LexwareSchemaLoader
from uie_connectors.lexware.ratelimit import TokenBucket
from uie_connectors.lexware.testing import FAKE_KEY, FakeLexwareServer
from uie_connectors.odoo import OdooCatalogReader, OdooSchemaLoader
from uie_connectors.odoo.discovery import READ_ONLY_METHODS, OdooSchemaDiscoveryProvider
from uie_connectors.odoo.testing import FakeOdooServer
from uie_core.catalog import CatalogReaderResolver
from uie_core.exports import ExportDocumentRecord, ExportRunRecord
from uie_core.identity import Organization, OrganizationMembership, SystemContext, User, hash_password
from uie_core.instances import (
    ALLOW_PRIVATE_ENV,
    ConnectionContext,
    FernetSecretStore,
    InstanceCreate,
    InstanceService,
    ProviderResolver,
)
from uie_core.registry import RegistryService
from uie_core.registry import models as m

DUMPS = Path(__file__).resolve().parents[3] / "docs" / "dumps"
NOTICE = "Nothing is sent to Lexware Office until you select records and confirm."


class Providers(ProviderResolver):
    """Chaque système a SON provider : le faux Odoo pour la source, le faux Lexware pour la cible."""

    def __init__(self, odoo: FakeOdooServer, lexware: FakeLexwareServer) -> None:
        self.odoo, self.lexware = odoo, lexware

    def resolve(self, system, version, interface):  # noqa: ANN001, ANN201
        if system != "lexware":
            return OdooSchemaDiscoveryProvider(proxy_factory=self.odoo.proxy)

        def client(ctx: ConnectionContext) -> LexwareClient:
            noop = TokenBucket(clock=lambda: 0.0, sleep=lambda _s: None)
            assert ctx.secret is not None
            return LexwareClient(
                ctx.secret, transport=self.lexware.transport, bucket=noop, sleep=lambda _s: None
            )

        return LexwareDiscoveryProvider(client_factory=client)


class Readers(CatalogReaderResolver):
    def __init__(self, fake: FakeOdooServer) -> None:
        self.fake = fake

    def resolve(self, system, version, interface):  # noqa: ANN001, ANN201
        return OdooCatalogReader(proxy_factory=self.fake.proxy)


@pytest.fixture
def world(session, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setenv(ALLOW_PRIVATE_ENV, "true")
    registry = RegistryService(session, SystemContext("test"))
    registry.publish_snapshot(OdooSchemaLoader({"json": DUMPS / "odoo17_ref.json"}).build_snapshot())
    registry.publish_snapshot(LexwareSchemaLoader().build_snapshot())
    odoo = FakeOdooServer.from_dump(DUMPS / "odoo17_customer.json", password=PASSWORD).with_records(
        FIX / "odoo17_partner_records.json"
    )
    lexware = FakeLexwareServer()
    secrets = FernetSecretStore(FernetSecretStore.generate_key())
    orgs = {slug: Organization(name=slug.title(), slug=slug) for slug in ("acme", "globex")}
    session.add_all(orgs.values())
    session.flush()
    for email, slug in (("alice@acme.io", "acme"), ("bob@globex.io", "globex")):
        u = User(email=email, display_name=email, password_hash=hash_password(SecretStr(PW)))
        session.add(u)
        session.flush()
        session.add(OrganizationMembership(organization_id=orgs[slug].id, user_id=u.id, role="OWNER"))
    providers = Providers(odoo, lexware)
    svc = InstanceService(session, SystemContext("test"), secrets, providers)
    sources = {}
    for slug, org in orgs.items():
        inst = svc.create(
            InstanceCreate(
                name="erp",
                system="odoo",
                version="17",
                organization_id=org.id,
                endpoint="http://odoo17-customer:8069",
                config={"db": odoo.db, "username": "admin"},
                password=SecretStr(PASSWORD),
            )
        )
        sources[slug] = str(inst.id)
    session.flush()
    app = create_app()
    app.dependency_overrides[deps.get_session] = lambda: session
    app.dependency_overrides[deps.get_secret_store] = lambda: secrets
    app.dependency_overrides[deps.get_provider_resolver] = lambda: providers
    app.dependency_overrides[deps.get_semantic_proposer] = NullProposer
    app.dependency_overrides[deps.get_catalog_readers] = lambda: Readers(odoo)
    return {"app": app, "sources": sources, "odoo": odoo, "lexware": lexware, "session": session}


def _client(app, email: str) -> TestClient:  # noqa: ANN001
    c = TestClient(app)
    assert c.post("/auth/login", json={"email": email, "password": PW}).status_code == 200
    return c


def _keys(body: Any) -> set[str]:
    if isinstance(body, dict):
        return set(body) | {k for v in body.values() for k in _keys(v)}
    if isinstance(body, list):
        return {k for v in body for k in _keys(v)}
    return set()


def test_connect_lexware_with_a_key_then_preview_partners(world):
    w = world
    alice, bob = _client(w["app"], "alice@acme.io"), _client(w["app"], "bob@globex.io")
    source = w["sources"]["acme"]
    seen: list[Any] = []

    def call(c: TestClient, method: str, url: str, **kw: Any):  # noqa: ANN202
        r = c.request(method, url, **kw)
        if r.headers.get("content-type", "").startswith("application/json"):
            seen.append(r.json())
        return r

    # 1. le formulaire de chaque système vient de SON connecteur
    forms = {c["system"]: c for c in call(alice, "GET", "/api/connectors").json()}
    assert set(forms) == {"odoo", "lexware"}
    assert (forms["lexware"]["name"], forms["lexware"]["versions"]) == ("Lexware Office", ["v1"])
    assert [(f["name"], f["label"], f["kind"], f["required"]) for f in forms["lexware"]["fields"]] == [
        ("api_key", "API key", "secret", True)
    ]
    assert [f["name"] for f in forms["odoo"]["fields"]] == ["url", "database", "username", "password"]

    # 2. test sans enregistrement : clé refusée, clé manquante, champ non déclaré, système inconnu
    check = "/api/systems/check-connection"
    wrong = call(alice, "POST", check, json={"system": "lexware", "fields": {"api_key": "wrong-key-123"}})
    assert wrong.status_code == 200 and wrong.json() == {
        "ok": False,
        "detected": None,
        "supported": False,
        "message": "The credentials were not accepted. Check them and try again.",
        "details": [],
    }
    assert "wrong-key-123" not in wrong.text
    assert call(alice, "POST", check, json={"system": "lexware", "fields": {}}).status_code == 422
    sneaky = {"system": "lexware", "fields": {"api_key": FAKE_KEY, "url": "https://evil.example"}}
    refused = call(alice, "POST", check, json=sneaky)
    assert refused.status_code == 422 and FAKE_KEY not in refused.text  # l'adresse n'est jamais au choix
    assert call(alice, "POST", check, json={"system": "nope", "fields": {}}).status_code == 404

    # 3. test réussi : société du compte, version de l'API ; rien n'est enregistré
    ok = call(alice, "POST", check, json={"system": "lexware", "fields": {"api_key": FAKE_KEY}})
    assert ok.status_code == 200 and ok.json() == {
        "ok": True,
        "detected": "Lexware Office v1",
        "supported": True,
        "message": None,
        "details": [
            {"label": "Company", "value": "Musterbetrieb Test GmbH"},
            {"label": "Connected by", "value": "Erika Beispiel"},
        ],
    }
    assert [s["name"] for s in call(alice, "GET", "/api/systems").json()] == ["erp"]

    # 4. enregistrement : nom + clé, rien d'autre
    saved = call(
        alice,
        "POST",
        "/api/systems",
        json={"system": "lexware", "name": "Books", "fields": {"api_key": FAKE_KEY}},
    )
    assert saved.status_code == 201, saved.text
    target = saved.json()
    assert (target["name"], target["system"], target["system_code"]) == (
        "Books",
        "Lexware Office v1",
        "lexware",
    )
    assert target["connection"]["status"] == "connected" and target["database"] is None
    assert target["server_url"] is None  # l'adresse de l'API appartient au connecteur : jamais affichée
    # ce que chaque système sait faire ici vient des connecteurs et des packs, pas de son nom
    assert (target["provides"], target["receives"]) == (
        ["accounting", "partner"],
        ["accounting", "partner", "product"],
    )
    # sources 1a.1 / 1b.1 : contacts et pièces lisibles sans analyse (schéma fixe)
    assert target["declared"] == ["accounting", "partner"]
    assert target["details"][0] == {"label": "Company", "value": "Musterbetrieb Test GmbH"}
    erp = call(alice, "GET", f"/api/systems/{source}").json()
    assert (erp["provides"], erp["receives"]) == (["accounting", "partner", "product"], [])
    assert erp["declared"] == []  # source analysée : questions avant l'export
    assert FAKE_KEY not in saved.text and FAKE_KEY not in call(alice, "GET", "/api/systems").text

    # 5. la source propose la cible connectée ; l'aperçu exige une analyse PARTNER complète
    targets = call(alice, "GET", f"/api/systems/{source}/partners/targets").json()
    assert targets == [
        {
            "system_id": target["id"],
            "name": "Books",
            "system": "Lexware Office",
            "target": "Lexware Office contacts",
            "connected": True,
        }
    ]
    preview_url = f"/api/systems/{source}/partners/targets/{target['id']}/preview"
    assert call(alice, "GET", preview_url).status_code == 409  # pas encore analysé
    assert call(alice, "POST", f"/api/systems/{source}/partners/analyze").status_code == 200
    confirm = f"/api/systems/{source}/partners/fields/res.partner/x_customer_no/confirm"
    assert call(alice, "POST", confirm, json={}).status_code == 200

    # 6. aperçu
    session = w["session"]
    lexware_calls = len(w["lexware"].calls)
    r = call(alice, "GET", preview_url)
    assert r.status_code == 200, r.text
    p = r.json()
    assert (p["source_name"], p["source_system"]) == ("erp", "Odoo")
    assert (p["target_name"], p["target_system"], p["target"]) == (
        "Books",
        "Lexware Office",
        "Lexware Office contacts",
    )
    assert p["notice"] == NOTICE and p["sending_available"] is True
    assert p["counts"]["partners"] == 7 == len(p["partners"])
    assert p["counts"]["ready"] + p["counts"]["warning"] + p["counts"]["blocked"] == 7
    assert p["history"] == {"new": 7, "already_sent": 0, "changed_since_sent": 0}
    rows = {x["name"]: x for x in p["partners"]}

    holzbau = rows["Holzbau Beispiel GmbH"]
    assert (holzbau["kind"], holzbau["roles"], holzbau["state"]) == ("company", ["customer"], "new")
    assert holzbau["status"] in ("ready", "warning") and holzbau["request"] is not None
    values = {v["label"]: v["value"] for v in holzbau["values"]}
    assert values["Company name"] == "Holzbau Beispiel GmbH" and values["Customer role"] == "Yes"
    assert '"company"' in holzbau["request"] and '"customer": {}' in holzbau["request"]
    # le numéro client de la source n'est pas imposé à la cible, et c'est dit
    assert "10042" not in holzbau["request"]
    assert "Customer number is not sent: Lexware Office assigns its own number" in [
        i["message"] for i in holzbau["issues"]
    ]

    both = rows["Alpen Handel AG"]  # client ET fournisseur : UN contact, deux rôles
    assert both["roles"] == ["customer", "supplier"] and '"vendor": {}' in both["request"]

    anna = rows["Anna Beispiel"]  # la source ne sépare pas le nom de famille : bloqué, jamais deviné
    assert (anna["kind"], anna["status"], anna["request"]) == ("person", "blocked", None)
    assert [i["level"] for i in anna["issues"] if "last name" in i["message"]] == ["blocking"]

    no_role = rows["Interessent Ohne Rolle GmbH"]
    assert no_role["status"] == "blocked" and no_role["roles"] == []

    # 7. aperçu sans effet : la cible n'est pas appelée, rien n'est enregistré, deux aperçus identiques
    assert len(w["lexware"].calls) == lexware_calls and w["lexware"].writes == []
    assert {(c.method, c.path) for c in w["lexware"].calls} == {("GET", "/v1/profile")}
    assert session.scalar(select(func.count()).select_from(ExportDocumentRecord)) == 0
    assert session.scalar(select(func.count()).select_from(ExportRunRecord)) == 0
    assert call(alice, "GET", preview_url).json() == p

    # 8. isolation : l'autre organisation ne voit ni la cible ni l'aperçu ; pas de cible pour sa source
    assert call(bob, "GET", preview_url).status_code == 404
    other = f"/api/systems/{w['sources']['globex']}/partners/targets"
    assert call(bob, "GET", other).json() == []
    assert call(bob, "GET", f"{other}/{target['id']}/preview").status_code == 404
    assert "Books" not in [s["name"] for s in call(bob, "GET", "/api/systems").json()]

    # 9. lecture seule vers la source, aucun secret ni donnée technique dans les réponses
    assert w["odoo"].methods_called <= READ_ONLY_METHODS | {"authenticate", "version"}
    text = str(seen)
    assert FAKE_KEY not in text and "Bearer" not in text and "fernet:" not in text
    assert not (_keys(seen) & (TECHNICAL_KEYS | {"source_ref", "binding_version", "contract", "fingerprint"}))
    conn = session.get(m.Instance, target["id"]).connections[0]
    assert conn.secret_ref.startswith("fernet:") and FAKE_KEY not in str(conn.config)


def test_removing_the_target_system_removes_its_secret_and_leaves_the_source(world):
    alice = _client(world["app"], "alice@acme.io")
    body = {"system": "lexware", "name": "Books", "fields": {"api_key": FAKE_KEY}}
    target = alice.post("/api/systems", json=body).json()
    assert alice.delete(f"/api/systems/{target['id']}").status_code == 200
    assert [s["name"] for s in alice.get("/api/systems").json()] == ["erp"]
    assert alice.get(f"/api/systems/{world['sources']['acme']}/partners/targets").json() == []
    assert world["lexware"].writes == []  # supprimer le système chez nous n'écrit rien chez lui
