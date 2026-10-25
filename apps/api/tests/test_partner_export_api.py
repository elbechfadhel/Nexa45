"""PARTNER de bout en bout par la Supplier API : Odoo (schéma RÉEL de customer17 + partenaires INVENTÉS, faux
serveur) → analyse PARTNER → réponse x_customer_no → profil d'export → aperçu → fichier EXTF → journal.

Authentification réelle (deux organisations). Cible : spec DATEV Debitoren/Kreditoren v5 générée localement
(sources non versionnées) : le module est sauté si elle est absente. Aucune écriture dans Odoo, rien envoyé.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from test_product_domain_api import PASSWORD, Resolver
from test_supplier_api import FIX, PW, TECHNICAL_KEYS
from uie_ai import NullProposer
from uie_api import deps
from uie_api.app import create_app
from uie_connectors.datev import DatevSchemaLoader
from uie_connectors.odoo import OdooCatalogReader, OdooSchemaLoader
from uie_connectors.odoo.discovery import READ_ONLY_METHODS
from uie_connectors.odoo.testing import FakeOdooServer
from uie_core.catalog import CatalogReaderResolver
from uie_core.identity import Organization, OrganizationMembership, SystemContext, User, hash_password
from uie_core.instances import FernetSecretStore, InstanceCreate, InstanceService
from uie_core.registry import RegistryService

ROOT = Path(__file__).resolve().parents[3]
DUMPS = ROOT / "docs" / "dumps"
REAL_SPEC = ROOT / "var" / "datev" / "specs" / "extf_700" / "debitoren-kreditoren_v5.yaml"
pytestmark = pytest.mark.skipif(not REAL_SPEC.exists(), reason="spec DATEV locale absente (spec_builder)")
TARGET = "/api/exports/datev/debitoren-kreditoren"
PROFILE = {  # valeurs de TEST (aucune valeur par défaut n'est proposée dans le produit)
    "advisor_number": 1001,
    "client_number": 1,
    "fiscal_year_start": "2026-01-01",
    "account_length": 4,
    "debtor_range": {"start": 10000, "end": 69999},
    "creditor_range": {"start": 70000, "end": 99999},
    "chart_of_accounts": "03",
    "origin_code": "RE",
}


class Readers(CatalogReaderResolver):
    def __init__(self, fake: FakeOdooServer) -> None:
        self.fake = fake

    def resolve(self, system, version, interface):  # noqa: ANN001, ANN201
        return OdooCatalogReader(proxy_factory=self.fake.proxy)


@pytest.fixture
def partner_world(session):  # noqa: ANN001, ANN201
    """Deux organisations (acme, globex), chacune une instance « customer17 » : schéma RÉEL complet
    (docs/dumps), partenaires INVENTÉS servis par le faux serveur Odoo."""
    registry = RegistryService(session, SystemContext("test"))
    registry.publish_snapshot(OdooSchemaLoader({"json": DUMPS / "odoo17_ref.json"}).build_snapshot())
    registry.publish_snapshot(DatevSchemaLoader({"spec": REAL_SPEC}).build_snapshot())
    fake = FakeOdooServer.from_dump(DUMPS / "odoo17_customer.json", password=PASSWORD).with_records(
        FIX / "odoo17_partner_records.json"
    )
    secrets = FernetSecretStore(FernetSecretStore.generate_key())
    orgs = {slug: Organization(name=slug.title(), slug=slug) for slug in ("acme", "globex")}
    session.add_all(orgs.values())
    session.flush()
    for email, slug in (("alice@acme.io", "acme"), ("bob@globex.io", "globex")):
        u = User(email=email, display_name=email, password_hash=hash_password(SecretStr(PW)))
        session.add(u)
        session.flush()
        session.add(OrganizationMembership(organization_id=orgs[slug].id, user_id=u.id, role="OWNER"))
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
                config={"db": fake.db, "username": "admin"},
                password=SecretStr(PASSWORD),
            )
        )
        instances[slug] = str(inst.id)
    session.flush()
    app = create_app()
    app.dependency_overrides[deps.get_session] = lambda: session
    app.dependency_overrides[deps.get_secret_store] = lambda: secrets
    app.dependency_overrides[deps.get_provider_resolver] = lambda: Resolver(fake)
    app.dependency_overrides[deps.get_semantic_proposer] = NullProposer
    app.dependency_overrides[deps.get_catalog_readers] = lambda: Readers(fake)
    return {"app": app, "instances": instances, "fake": fake}


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


def test_partner_export_end_to_end(partner_world):
    w = partner_world
    alice, bob = _client(w["app"], "alice@acme.io"), _client(w["app"], "bob@globex.io")
    sid = w["instances"]["acme"]
    seen: list[Any] = []

    def call(c: TestClient, method: str, url: str, **kw: Any):  # noqa: ANN202
        r = c.request(method, url, **kw)
        if r.headers.get("content-type", "").startswith("application/json"):
            seen.append(r.json())
        return r

    # 1. analyse PARTNER : une seule question, proposée par le vocabulaire
    r = call(alice, "POST", f"/api/systems/{sid}/partners/analyze")
    assert r.status_code == 200 and r.json()["questions"] == 1, r.text
    assert {s["label"] for s in r.json()["structure"]} == {"Partners", "Bank accounts", "Countries"}
    [q] = call(alice, "GET", f"/api/systems/{sid}/partners/questions").json()
    assert (q["field"]["field"], q["suggestion"]["meaning"]["key"]) == ("x_customer_no", "customer_number")

    # 2. pas d'aperçu tant que l'analyse n'est pas complète ni le profil rempli
    assert call(alice, "GET", f"{TARGET}/preview", params={"system_id": sid}).status_code == 409
    url = f"/api/systems/{sid}/partners/fields/res.partner/x_customer_no/confirm"
    assert call(alice, "POST", url, json={}).status_code == 200
    assert call(alice, "GET", f"{TARGET}/preview", params={"system_id": sid}).status_code == 409  # profil

    # 3. profil : validé par le binding et les motifs du Registry
    bad = call(alice, "PUT", f"{TARGET}/profile", json={"values": {**PROFILE, "account_length": 9}})
    assert bad.status_code == 422 and "account_length" in bad.json()["errors"]
    profile = call(alice, "GET", f"{TARGET}/profile").json()
    assert profile["complete"] is False and profile["values"] == {}
    ranges = [p for p in profile["params"] if p["type"] == "range"]
    assert ranges and all(p["default"] is None for p in ranges)  # aucune plage proposée par défaut
    ok = call(alice, "PUT", f"{TARGET}/profile", json={"values": PROFILE})
    assert ok.status_code == 200 and ok.json()["complete"] is True, ok.text

    # 4. aperçu : lecture seule, numéros proposés, statuts
    p = call(alice, "GET", f"{TARGET}/preview", params={"system_id": sid}).json()
    assert p["counts"] == {
        "partners": 7,
        "records": 7,
        "ready": 4,
        "warnings": 2,
        "blocked": 1,
        "not_exported": 1,
        "new_accounts": 6,
    }
    rows = {(r["name"], r["role"]): r for r in p["records"]}
    assert {k: (v["account_number"], v["status"]) for k, v in rows.items()} == {
        ("Holzbau Beispiel GmbH", "customer"): ("10000", "ready"),
        ("Anna Beispiel", "customer"): ("10001", "ready"),
        ("Alpen Handel AG", "customer"): ("10002", "ready"),
        ("Alpen Handel AG", "supplier"): ("70000", "ready"),
        ("Van Dijk Hout BV", "supplier"): ("70001", "warning"),
        ("Nordwald Holz KG", "supplier"): ("70002", "warning"),
        ("Sehr Lange Firmenbezeichnung Für Holzverarbeitung Und Möbelbau Süd GmbH", "customer"): (
            "10003",
            "blocked",
        ),
    }
    hb = {c["label"]: c["value"] for c in rows[("Holzbau Beispiel GmbH", "customer")]["columns"]}
    assert (hb["Kunden-/Lief.-Nr."], hb["EU-Land"], hb["EU-UStID"], hb["Sprache"]) == (
        "10042",
        "DE",
        "123456789",
        "1",
    )
    assert [n["name"] for n in p["not_exported"]] == ["Interessent Ohne Rolle GmbH"]

    # 5. fichier : les lignes bloquées ne sont pas écrites, numéros réservés, rien envoyé
    f = call(alice, "POST", f"{TARGET}/files", json={"system_id": sid})
    assert f.status_code == 200 and f.headers["content-type"].startswith("text/csv")
    assert 'filename="DATEV_debitoren-kreditoren_' in f.headers["content-disposition"]
    lines = f.content.decode("cp1252").split("\r\n")
    assert lines[0].startswith('"EXTF";700;16;"Debitoren/Kreditoren";5;')
    assert lines[0].split(";")[10:14] == ["1001", "1", "20260101", "4"]
    assert lines[1].startswith("Kontonummer;Name (Adressattyp Unternehmen);")
    assert len(lines) == 2 + 6 + 1 and lines[-1] == ""  # CRLF final
    assert lines[2].startswith('10000;"Holzbau Beispiel GmbH";')
    assert "Sehr Lange" not in f.content.decode("cp1252")

    # 6. relance : numéros conservés ; le bloqué n'a rien réservé
    again = {
        (r["name"], r["role"]): r
        for r in call(alice, "GET", f"{TARGET}/preview", params={"system_id": sid}).json()["records"]
    }
    assert again[("Holzbau Beispiel GmbH", "customer")]["account_new"] is False
    assert (
        again[("Sehr Lange Firmenbezeichnung Für Holzverarbeitung Und Möbelbau Süd GmbH", "customer")][
            "account_new"
        ]
        is True
    )
    [run] = call(alice, "GET", "/api/exports/runs").json()
    assert (run["records"], run["blocked"], run["new_accounts"], run["system"]) == (6, 1, 6, "customer17")

    # 7. isolation : l'autre organisation ne voit ni le système, ni le profil, ni le journal
    assert call(bob, "GET", f"{TARGET}/preview", params={"system_id": sid}).status_code == 404
    assert call(bob, "POST", f"{TARGET}/files", json={"system_id": sid}).status_code == 404
    assert call(bob, "GET", f"{TARGET}/profile").json()["values"] == {}
    assert call(bob, "GET", "/api/exports/runs").json() == []

    # 8. lecture seule vers Odoo, aucune clé technique dans les réponses
    assert w["fake"].methods_called <= READ_ONLY_METHODS | {"authenticate", "version"}
    assert not (_keys(seen) & (TECHNICAL_KEYS | {"source_ref", "binding_version", "target_snapshot_id"}))
