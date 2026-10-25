"""Lexware Office comme SOURCE PARTNER par la Supplier API (phase source 1a.1) : un système à schéma fixe
annonce les partenaires comme DÉCLARÉS (aucune analyse), puis l'aperçu DATEV Debitoren/Kreditoren EXISTANT se
fait depuis ses contacts. Faux serveur Lexware (contacts INVENTÉS, GET seulement) ; rien n'est envoyé ni
enregistré. Spec DATEV v5 locale (sources non versionnées) : module sauté si elle est absente."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from test_lexware_partner_api import _client, world  # noqa: F401  (fixture réutilisée)
from test_supplier_api import TECHNICAL_KEYS
from uie_api import deps
from uie_connectors.datev import DatevSchemaLoader
from uie_connectors.lexware import LexwareClient, LexwareRecordSource
from uie_connectors.lexware.ratelimit import TokenBucket
from uie_connectors.lexware.testing import FAKE_KEY
from uie_core.exports import ExportDocumentRecord, ExportRunRecord
from uie_core.identity import SystemContext
from uie_core.registry import RegistryService
from uie_core.sources import RecordSourceResolver

ROOT = Path(__file__).resolve().parents[3]
REAL_SPEC = ROOT / "var" / "datev" / "specs" / "extf_700" / "debitoren-kreditoren_v5.yaml"
pytestmark = pytest.mark.skipif(not REAL_SPEC.exists(), reason="spec DATEV locale absente (spec_builder)")
EXPORT = "/api/exports/datev/debitoren-kreditoren"
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
CONTACTS = [
    {
        "id": "k-1",
        "archived": False,
        "roles": {"customer": {"number": 10001}, "vendor": {"number": 70001}},
        "company": {"name": "Werkstatt Beispiel GmbH", "vatRegistrationId": "DE123456789"},
        "addresses": {
            "billing": [{"street": "Hafenweg 4", "zip": "20457", "city": "Hamburg", "countryCode": "DE"}]
        },
    },
    {
        "id": "k-2",
        "archived": False,
        "roles": {"vendor": {"number": 70002}},
        "person": {"firstName": "Anna", "lastName": "Muster"},
        "addresses": {
            "billing": [{"street": "Ringstraße 9", "zip": "1010", "city": "Wien", "countryCode": "AT"}]
        },
    },
    {"id": "k-3", "archived": True, "roles": {"customer": {"number": 10002}}, "company": {"name": "Alt KG"}},
]


class FakeSources(RecordSourceResolver):
    def __init__(self, server) -> None:  # noqa: ANN001
        self.server = server

    def open(self, system, interface, ctx):  # noqa: ANN001, ANN201
        noop = TokenBucket(clock=lambda: 0.0, sleep=lambda _s: None)
        client = LexwareClient(
            ctx.secret, transport=self.server.transport, bucket=noop, sleep=lambda _s: None
        )
        return LexwareRecordSource(client)


def _keys(body: Any) -> set[str]:
    if isinstance(body, dict):
        return set(body) | {k for v in body.values() for k in _keys(v)}
    if isinstance(body, list):
        return {k for v in body for k in _keys(v)}
    return set()


def test_lexware_contacts_feed_the_existing_datev_preview_without_analysis(world):  # noqa: F811
    w = world
    w["lexware"].contacts = [dict(c) for c in CONTACTS]
    w["app"].dependency_overrides[deps.get_record_sources] = lambda: FakeSources(w["lexware"])
    RegistryService(w["session"], SystemContext("test")).publish_snapshot(
        DatevSchemaLoader({"spec": REAL_SPEC}).build_snapshot()
    )
    alice, bob = _client(w["app"], "alice@acme.io"), _client(w["app"], "bob@globex.io")

    saved = alice.post(
        "/api/systems", json={"system": "lexware", "name": "Books", "fields": {"api_key": FAKE_KEY}}
    )
    assert saved.status_code == 201
    books = saved.json()["id"]

    # le système annonce ce qu'il fournit et que son sens est déclaré (aucune analyse) ; l'ERP analysé, non
    s = alice.get(f"/api/systems/{books}").json()
    assert "partner" in s["provides"] and "partner" in s["declared"]
    erp = alice.get(f"/api/systems/{w['sources']['acme']}").json()
    assert erp["declared"] == [] and "partner" in erp["provides"]

    assert alice.put(f"{EXPORT}/profile", json={"values": PROFILE}).status_code == 200
    calls = len(w["lexware"].calls)
    before = [
        w["session"].scalar(select(func.count()).select_from(t))
        for t in (ExportRunRecord, ExportDocumentRecord)
    ]
    r = alice.get(f"{EXPORT}/preview", params={"system_id": books})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["counts"]["partners"] == 2 and p["counts"]["records"] == 3  # archivé exclu ; k-1 client + fourn.
    assert {(x["name"], x["role"], x["status"]) for x in p["records"]} == {
        ("Werkstatt Beispiel GmbH", "customer", "ready"),
        ("Werkstatt Beispiel GmbH", "supplier", "ready"),
        ("Anna Muster", "supplier", "ready"),
    }
    assert not (_keys(p) & TECHNICAL_KEYS)  # aucune donnée technique côté fournisseur

    # lecture seule : GET de la liste des contacts, rien d'enregistré (ni journal, ni D11)
    assert {(c.method, c.path) for c in w["lexware"].calls[calls:]} == {("GET", "/v1/contacts")}
    assert w["lexware"].writes == []
    after = [
        w["session"].scalar(select(func.count()).select_from(t))
        for t in (ExportRunRecord, ExportDocumentRecord)
    ]
    assert after == before

    # une autre organisation ne voit pas ce système
    assert bob.get(f"{EXPORT}/preview", params={"system_id": books}).status_code == 404
