"""Articles Lexware Office par la Supplier API : phase 1a (choisir la cible, décider la base des prix et le
taux des taxes, voir ce que chaque produit deviendrait ; l'aperçu n'écrit rien) et phase 1b (envoi des
produits choisis après confirmation explicite, faux serveur Lexware).

La source est un faux serveur Odoo (schéma réel de customer17, mini catalogue INVENTÉ) : UN fournisseur de
Product CDM parmi d'autres (la préparation est testée sans aucune source dans
`domain-packs/tests/test_product_lexware_preview.py`). La cible est un faux serveur Lexware : aucun appel
réseau."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from test_lexware_partner_api import Providers, Readers, _client, _keys
from test_product_domain_api import PASSWORD
from test_supplier_api import PW, TECHNICAL_KEYS
from uie_ai import NullProposer
from uie_api import deps
from uie_api.app import create_app
from uie_connectors.lexware import LexwareClient, LexwareRecordSender, LexwareSchemaLoader
from uie_connectors.lexware.ratelimit import TokenBucket
from uie_connectors.lexware.testing import FAKE_KEY, FakeLexwareServer
from uie_connectors.odoo import OdooSchemaLoader
from uie_connectors.odoo.testing import FakeOdooServer
from uie_core.delivery import RecordSenderResolver
from uie_core.domains import ProductDomainService, SemanticEnrichmentService
from uie_core.exports.records import ExportValueMappingRecord
from uie_core.identity import Organization, OrganizationMembership, SystemContext, User, hash_password
from uie_core.instances import (
    ALLOW_PRIVATE_ENV,
    ConnectionContext,
    FernetSecretStore,
    InstanceCreate,
    InstanceService,
)
from uie_core.registry import RegistryService

ROOT = Path(__file__).resolve().parents[3]
DUMPS = ROOT / "docs" / "dumps"
RECORDS = ROOT / "packages" / "connectors" / "tests" / "fixtures" / "odoo17_catalog_records.json"


class FakeSenders(RecordSenderResolver):
    """Le vrai expéditeur Lexware, branché sur le faux serveur (aucun réseau)."""

    def __init__(self, server: FakeLexwareServer) -> None:
        self.server = server

    def supports(self, system: str, interface: str) -> bool:
        return system == "lexware"

    def create(self, system: str, interface: str, ctx: ConnectionContext) -> LexwareRecordSender:
        assert ctx.secret is not None
        noop = TokenBucket(clock=lambda: 0.0, sleep=lambda _s: None)
        client = LexwareClient(
            ctx.secret, transport=self.server.transport, bucket=noop, sleep=lambda _s: None, writable=True
        )
        return LexwareRecordSender(ctx, client=client)


@pytest.fixture
def world(session, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setenv(ALLOW_PRIVATE_ENV, "true")
    system = SystemContext("test")
    registry = RegistryService(session, system)
    registry.publish_snapshot(OdooSchemaLoader({"json": DUMPS / "odoo17_ref.json"}).build_snapshot())
    registry.publish_snapshot(LexwareSchemaLoader().build_snapshot())
    odoo = FakeOdooServer.from_dump(DUMPS / "odoo17_customer.json", password=PASSWORD).with_records(RECORDS)
    odoo.records = copy.deepcopy(odoo.records)
    for row in odoo.records["product.template"]:
        row["taxes_id"] = [1]
    odoo.records["account.tax"] = [{"id": 1, "display_name": "19% Umsatzsteuer"}]
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
    svc = InstanceService(session, system, secrets, providers)
    source = svc.create(
        InstanceCreate(
            name="erp",
            system="odoo",
            version="17",
            organization_id=orgs["acme"].id,
            endpoint="http://odoo17-customer:8069",
            config={"db": odoo.db, "username": "admin"},
            password=SecretStr(PASSWORD),
        )
    )
    svc.discover_schema(source.id)
    domains = ProductDomainService(session, system)
    domains.analyze(source.id)
    answers = SemanticEnrichmentService(session, system, domains=domains)
    answers.confirm(source.id, "product.template", "x_supplier_ref")
    answers.change(source.id, "product.template", "x_pack_code", "packaging_code")
    answers.ignore(source.id, "product.template", "x_notes_internal")
    session.flush()
    app = create_app()
    app.dependency_overrides[deps.get_session] = lambda: session
    app.dependency_overrides[deps.get_secret_store] = lambda: secrets
    app.dependency_overrides[deps.get_provider_resolver] = lambda: providers
    app.dependency_overrides[deps.get_semantic_proposer] = NullProposer
    app.dependency_overrides[deps.get_catalog_readers] = lambda: Readers(odoo)
    app.dependency_overrides[deps.get_record_senders] = lambda: FakeSenders(lexware)
    return {"app": app, "source": str(source.id), "lexware": lexware, "session": session}


def test_prepare_products_for_lexware_office_through_the_supplier_api(world):
    w = world
    alice, bob = _client(w["app"], "alice@acme.io"), _client(w["app"], "bob@globex.io")
    seen = []
    saved = alice.post(
        "/api/systems", json={"system": "lexware", "name": "Books", "fields": {"api_key": FAKE_KEY}}
    )
    assert saved.status_code == 201, saved.text
    target = saved.json()["id"]
    base = f"/api/systems/{w['source']}/products/targets"

    # 1. cibles possibles des produits : le système Lexware connecté
    targets = alice.get(base).json()
    seen.append(targets)
    assert [(t["system_id"], t["system"], t["target"], t["connected"]) for t in targets] == [
        (target, "Lexware Office", "Lexware Office articles", True)
    ]

    # 2. aperçu sans décision : tout est bloqué, avec la raison ; rien n'est envoyé
    calls = len(w["lexware"].calls)
    first = alice.get(f"{base}/{target}/preview")
    assert first.status_code == 200, first.text
    body = first.json()
    seen.append(body)
    assert (body["notice"], body["sending_available"], body["price_basis"]) == (
        "Nothing is sent to Lexware Office until you select products and confirm.",
        True,
        None,
    )
    assert {(p["action"], p["sendable"]) for p in body["products"]} == {("none", False)}  # tout bloqué
    assert (body["price_bases"], body["tax_rates"]) == (["net", "gross"], ["0", "7", "19"])
    assert body["counts"] == {"products": 3, "ready": 0, "warning": 0, "blocked": 3, "archived": 1}
    assert body["taxes"] == [{"tax_reference": "1", "label": "19% Umsatzsteuer", "products": 3, "rate": None}]
    assert "Choose whether the prices of the source are net or gross (Settings)" in [
        i["message"] for i in body["products"][0]["issues"]
    ]

    # 3. décisions explicites, contrôlées contre le contrat de la cible
    assert alice.put(f"{base}/{target}/price-basis", json={"price_basis": "net"}).status_code == 204
    bad = alice.put(f"{base}/{target}/tax-rates/1", json={"rate": "16"})
    assert bad.status_code == 400 and "0, 7, 19" in bad.json()["detail"]
    ok = alice.put(f"{base}/{target}/tax-rates/1", json={"rate": "19", "label": "19% Umsatzsteuer"})
    assert ok.status_code == 204
    assert w["session"].scalar(select(func.count()).select_from(ExportValueMappingRecord)) == 2

    # 4. aperçu prêt : un article par variante, numéro = SKU, prix net, taux décidé
    ready = alice.get(f"{base}/{target}/preview").json()
    seen.append(ready)
    assert ready["counts"]["ready"] == 3 and ready["price_basis"] == "net"
    red = next(p for p in ready["products"] if p["sku"] == "CHAIR-01-RED")
    assert (red["title"], red["kind"], red["status"], red["state"]) == (
        "Office Chair (Color: Red, Size: Standard)",
        "goods",
        "ready",
        "new",
    )
    assert {v["label"]: v["value"] for v in red["values"]}["Net price"] == "130.0"
    assert '"taxRate": 19.0' in red["request"]
    assert len(w["lexware"].calls) == calls and w["lexware"].writes == []  # la cible n'est jamais appelée

    # 5. aucune donnée technique, aucun secret ; une autre organisation ne voit rien
    assert not (_keys(seen) & TECHNICAL_KEYS)
    assert FAKE_KEY not in str(seen) and "snapshot" not in str(seen)
    assert bob.get(base).status_code == 404
    assert bob.get(f"{base}/{target}/preview").status_code == 404
    assert bob.put(f"{base}/{target}/price-basis", json={"price_basis": "gross"}).status_code == 404
    assert alice.delete(f"{base}/{target}/tax-rates/1").status_code == 204
    assert alice.delete(f"{base}/{target}/price-basis").status_code == 204
    assert w["session"].scalar(select(func.count()).select_from(ExportValueMappingRecord)) == 0


def test_send_selected_products_to_lexware_office_through_the_supplier_api(world):
    w = world
    w["lexware"].writable = True
    alice, bob = _client(w["app"], "alice@acme.io"), _client(w["app"], "bob@globex.io")
    saved = alice.post(
        "/api/systems", json={"system": "lexware", "name": "Books", "fields": {"api_key": FAKE_KEY}}
    )
    target = saved.json()["id"]
    base = f"/api/systems/{w['source']}/products/targets/{target}"
    alice.put(f"{base}/price-basis", json={"price_basis": "net"})
    alice.put(f"{base}/tax-rates/1", json={"rate": "19", "label": "19% Umsatzsteuer"})
    preview = alice.get(f"{base}/preview").json()
    red = next(p for p in preview["products"] if p["sku"] == "CHAIR-01-RED")
    assert (red["action"], red["sendable"], red["linked"]) == ("create", True, False)
    ref, key = red["source_reference"], "key-0001-product-send"

    # sans confirmation explicite : refusé, rien n'est écrit
    no = alice.post(f"{base}/sends", json={"request_key": key, "references": [ref], "confirm": False})
    assert no.status_code == 400 and w["lexware"].writes == []

    sent = alice.post(f"{base}/sends", json={"request_key": key, "references": [ref], "confirm": True})
    assert sent.status_code == 200, sent.text
    run = sent.json()
    assert (run["status"], run["counts"]["sent"]) == ("completed", 1)
    assert run["items"][0]["name"] == "Office Chair (Color: Red, Size: Standard)"
    assert [c.method for c in w["lexware"].writes] == ["POST"]
    again = alice.post(f"{base}/sends", json={"request_key": key, "references": [ref], "confirm": True})
    assert again.json()["id"] == run["id"] and len(w["lexware"].writes) == 1  # même clé : même envoi
    assert [r["id"] for r in alice.get(f"{base}/sends").json()] == [run["id"]]
    assert alice.get(f"{base}/sends/{run['id']}").json()["counts"]["sent"] == 1
    after = next(p for p in alice.get(f"{base}/preview").json()["products"] if p["source_reference"] == ref)
    assert (after["state"], after["linked"], after["sendable"]) == ("already_sent", True, False)

    # aucune donnée technique, aucun secret ; une autre organisation ne peut ni envoyer ni lire
    assert not (_keys([run]) & TECHNICAL_KEYS) and FAKE_KEY not in str(run)
    other = bob.post(
        f"{base}/sends", json={"request_key": "key-0002-x", "references": [ref], "confirm": True}
    )
    assert other.status_code == 404 and bob.get(f"{base}/sends").status_code == 404
    assert len(w["lexware"].writes) == 1
