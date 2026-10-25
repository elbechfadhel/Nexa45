"""ACCOUNTING phase 3 par la Supplier API : analyse → réponses → sociétés → lecture d'un mois
(Accounting CDM), sur le schéma RÉEL d'accounting17 (l10n_de, SKR03) et ses pièces INVENTÉES, servis par
le faux serveur Odoo. Authentification réelle (deux organisations) ; lecture seule ; aucune donnée
technique dans les réponses."""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from test_partner_export_api import Readers, _keys
from test_product_domain_api import PASSWORD, Resolver
from test_supplier_api import FIX, PW, TECHNICAL_KEYS
from uie_ai import NullProposer
from uie_api import deps
from uie_api.app import create_app
from uie_connectors.odoo import OdooSchemaLoader
from uie_connectors.odoo.discovery import READ_ONLY_METHODS
from uie_connectors.odoo.testing import FakeOdooServer
from uie_core.identity import Organization, OrganizationMembership, SystemContext, User, hash_password
from uie_core.instances import FernetSecretStore, InstanceCreate, InstanceService
from uie_core.registry import RegistryService

DUMPS = Path(__file__).resolve().parents[3] / "docs" / "dumps"


@pytest.fixture
def world(session):  # noqa: ANN001, ANN201
    RegistryService(session, SystemContext("test")).publish_snapshot(
        OdooSchemaLoader({"json": DUMPS / "odoo17_ref.json"}).build_snapshot()
    )
    fake = FakeOdooServer.from_dump(DUMPS / "odoo17_accounting17.json", password=PASSWORD).with_records(
        FIX / "odoo17_accounting17_records.json"
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
    inst = InstanceService(session, SystemContext("test"), secrets, Resolver(fake)).create(
        InstanceCreate(
            name="accounting17",
            system="odoo",
            version="17",
            organization_id=orgs["acme"].id,
            endpoint="http://odoo17-accounting:8069",
            config={"db": fake.db, "username": "admin"},
            password=SecretStr(PASSWORD),
        )
    )
    session.flush()
    app = create_app()
    app.dependency_overrides[deps.get_session] = lambda: session
    app.dependency_overrides[deps.get_secret_store] = lambda: secrets
    app.dependency_overrides[deps.get_provider_resolver] = lambda: Resolver(fake)
    app.dependency_overrides[deps.get_semantic_proposer] = NullProposer
    app.dependency_overrides[deps.get_catalog_readers] = lambda: Readers(fake)
    return {"app": app, "sid": str(inst.id), "fake": fake}


def _client(app, email: str) -> TestClient:  # noqa: ANN001
    c = TestClient(app)
    assert c.post("/auth/login", json={"email": email, "password": PW}).status_code == 200
    return c


def test_accounting_read_end_to_end(world):
    alice, bob, sid = (
        _client(world["app"], "alice@acme.io"),
        _client(world["app"], "bob@globex.io"),
        world["sid"],
    )
    seen: list[Any] = []

    def call(c: TestClient, method: str, url: str, **kw: Any):  # noqa: ANN202
        r = c.request(method, url, **kw)
        if r.headers.get("content-type", "").startswith("application/json"):
            seen.append(r.json())
        return r

    base = f"/api/systems/{sid}/accounting"
    # 1. analyse : les 4 extensions de la localisation allemande sont des questions
    r = call(alice, "POST", f"{base}/analyze")
    assert r.status_code == 200 and r.json()["questions"] == 4, r.text
    assert {s["label"] for s in r.json()["structure"]} == {
        "Invoices and credit notes",
        "Document lines",
        "Chart of accounts",
        "Taxes",
    }
    assert (
        call(alice, "GET", f"{base}/documents", params={"company_id": "1", "period": "2026-01"}).status_code
        == 409
    )
    for q in call(alice, "GET", f"{base}/questions").json():
        url = f"{base}/fields/{q['field']['entity']}/{q['field']['field']}/ignore"
        assert call(alice, "POST", url, json={}).status_code == 200

    # 2. société choisie explicitement, période validée
    assert call(alice, "GET", f"{base}/companies").json() == [{"id": "1", "name": "GO45 Demo GmbH"}]
    assert (
        call(alice, "GET", f"{base}/documents", params={"company_id": "1", "period": "2026-13"}).status_code
        == 400
    )

    # 3. lecture d'un mois
    out = call(alice, "GET", f"{base}/documents", params={"company_id": "1", "period": "2026-01"}).json()
    assert out["period"] == "2026-01" and out["company"]["name"] == "GO45 Demo GmbH"
    assert out["counts"] == {
        "documents": 13,
        "exportable": 10,
        "with_issues": 1,
        "by_status": {"Posted": 11, "Draft": 1, "Cancelled": 1},
        "by_type": {
            "Customer invoice": 9,
            "Customer credit note": 1,
            "Vendor bill": 2,
            "Vendor credit note": 1,
        },
    }
    b = next(d for d in out["documents"] if d["reference"] == "UIE-ACC-B")
    assert (b["type"], b["status"], b["partner"], b["currency"], b["total_amount"]) == (
        "Customer invoice",
        "Posted",
        {"name": "Lena Hoffmann", "role": "customer"},
        "EUR",
        "739.89",
    )
    assert (b["document_date"], b["due_date"], b["balance"]) == ("2026-01-15", "2026-01-30", "0.00")
    assert [(ln["kind"], ln["description"]) for ln in b["lines"]] == [
        ("Item", "Beratung (Stunde)"),
        ("Item", "Beratung Planung"),
        ("Item", "Fachbuch Holzbau"),
        ("Tax", "19%"),
        ("Tax", "7%"),
        ("Amount due", "RE/2026/00002"),
    ]
    assert all(Decimal(d["balance"]) == 0 for d in out["documents"])
    assert [(ln["kind"], ln["account_code"], ln["amount"]) for ln in b["lines"]][:3] == [
        ("Item", "8400", "-360.0"),
        ("Item", "8400", "-190.0"),
        ("Item", "8300", "-79.8"),
    ]
    assert b["lines"][0]["taxes"] == [
        {"name": "19%", "rate": "19.0", "computation": "percent", "usage": "sale", "price_included": False}
    ]
    draft = next(d for d in out["documents"] if d["reference"] == "UIE-ACC-J")
    assert (draft["status"], draft["exportable"], draft["number"]) == ("Draft", False, None)
    unsupported = next(d for d in out["documents"] if d["reference"] == "UIE-ACC-L")
    assert (
        unsupported["issues"] == [{"message": "A line has several taxes"}] and not unsupported["exportable"]
    )

    # 4. isolation, lecture seule, aucune donnée technique (ni compte ni code de taxe d'une cible)
    assert call(bob, "GET", f"{base}/companies").status_code == 404
    assert (
        call(bob, "GET", f"{base}/documents", params={"company_id": "1", "period": "2026-01"}).status_code
        == 404
    )
    assert world["fake"].methods_called <= READ_ONLY_METHODS | {"authenticate", "version"}
    keys = _keys(seen)
    assert not keys & (TECHNICAL_KEYS | {"source_ref", "plan_checksum", "datev", "bu_key", "target_account"})


# --------------------------------------------------------------------------- #
# Phase 4 : préparation DATEV Buchungsstapel (aperçu, aucun fichier) — spec v13 locale, sinon sauté
# --------------------------------------------------------------------------- #
ROOT = Path(__file__).resolve().parents[3]
CRLF = "\r\n"
BS_SPEC = ROOT / "var" / "datev" / "specs" / "extf_700" / "buchungsstapel_v13.yaml"


@pytest.mark.skipif(not BS_SPEC.exists(), reason="spec DATEV locale absente (spec_builder)")
def test_accounting_datev_preparation_end_to_end(world, session):  # noqa: ANN001
    from uie_connectors.datev import DatevSchemaLoader
    from uie_core.exports import ExportAccountAssignmentRecord, ExportProfileRecord
    from uie_core.registry import models as rm

    RegistryService(session, SystemContext("test")).publish_snapshot(
        DatevSchemaLoader({"spec": BS_SPEC}).build_snapshot()
    )
    alice, bob, sid = (
        _client(world["app"], "alice@acme.io"),
        _client(world["app"], "bob@globex.io"),
        world["sid"],
    )
    seen: list[Any] = []

    def call(c: TestClient, method: str, url: str, **kw: Any):  # noqa: ANN202
        r = c.request(method, url, **kw)
        if r.headers.get("content-type", "").startswith("application/json"):
            seen.append(r.json())
        return r

    base = f"/api/systems/{sid}/accounting"
    call(alice, "POST", f"{base}/analyze")
    for q in call(alice, "GET", f"{base}/questions").json():
        call(alice, "POST", f"{base}/fields/{q['field']['entity']}/{q['field']['field']}/ignore", json={})
    assert call(alice, "GET", "/api/accounting/targets").json() == [
        {
            "system": "datev",
            "product": "buchungsstapel",
            "name": "DATEV Buchungsstapel (EXTF)",
            "versions": ["13"],
        }
    ]
    tgt = f"{base}/targets/datev/buchungsstapel"
    jan = {"company_id": "1", "period": "2026-01", "version": "13"}
    assert call(alice, "GET", f"{tgt}/preparation", params=jan).status_code == 409  # dossier DATEV absent

    # dossier DATEV (export Debitoren/Kreditoren) et comptes de personne déjà attribués par cet export
    org = session.get(rm.Instance, uuid.UUID(sid)).organization_id
    shared = ExportProfileRecord(
        organization_id=org,
        source_model="partner",
        target_system="datev",
        target_product="debitoren-kreditoren",
        values={
            "advisor_number": 1001,
            "client_number": 1,
            "fiscal_year_start": "2026-01-01",
            "account_length": 4,
            "chart_of_accounts": "03",
            "origin_code": "RE",
        },
    )
    session.add(shared)
    session.flush()
    for n, (ref, role) in enumerate(
        [("7", "CUSTOMER"), ("8", "CUSTOMER"), ("11", "CUSTOMER"), ("12", "CUSTOMER")]
    ):
        session.add(
            ExportAccountAssignmentRecord(
                profile_id=shared.id,
                instance_id=uuid.UUID(sid),
                source_ref=ref,
                role=role,
                account_number=str(10000 + n),
                origin="ASSIGNED",
            )
        )
    session.flush()
    settings = call(alice, "GET", f"{tgt}/settings").json()
    assert settings["missing"] == ["Currency of the DATEV client (WKZ)"] and not settings["ready"]
    assert {v["name"] for v in settings["values"] if v["shared"]} >= {"account_length", "fiscal_year_start"}
    bad = call(alice, "PUT", f"{tgt}/settings", json={"values": {"currency": "euro"}})
    assert bad.status_code == 422
    assert call(alice, "PUT", f"{tgt}/settings", json={"values": {"currency": "EUR"}}).json()["ready"]

    first = call(alice, "GET", f"{tgt}/preparation", params=jan).json()
    assert first["counts"] == {
        "documents": 13,
        "eligible": 10,
        "ready": 0,
        "warnings": 0,
        "blocked": 11,
        "not_prepared": 2,
    }
    accounts = {m["source_code"]: m for m in first["mappings"] if m["kind"] == "account"}
    a8400 = accounts["8400"]
    assert (a8400["status"], a8400["provenance"]) == ("suggested", "Same account number")
    # confirmer : suggestions des comptes de B, « aucune clé » pour ses taxes (valeurs de TEST)
    for code in ("8400", "8300"):
        m = accounts[code]
        r = call(
            alice,
            "PUT",
            f"{tgt}/mappings/account/{m['ref']}",
            json={"value": m["suggestion"], "accept_suggestion": True, "source_code": code},
        )
        assert r.status_code == 200, r.text
    assert (
        call(
            alice, "PUT", f"{tgt}/mappings/account/{accounts['8400']['ref']}", json={"value": "840"}
        ).status_code
        == 422
    )
    for m in first["mappings"]:
        if m["kind"] == "tax" and m["source_code"] in ("19%", "7%"):
            assert (
                call(alice, "PUT", f"{tgt}/mappings/tax/{m['ref']}", json={"no_value": True}).status_code
                == 200
            )

    out = call(alice, "GET", f"{tgt}/preparation", params=jan).json()
    b = next(d for d in out["documents"] if d["number"] == "RE/2026/00002")
    assert (b["target_status"], b["eligible"], b["balance"], b["partner"]) == (
        "ready",
        True,
        "0.00",
        "Lena Hoffmann",
    )
    assert [
        (Decimal(bk["amount"]), bk["partner_side"], bk["counter_account"], bk["tax"], bk["tax_no_key"])
        for bk in b["bookings"]
    ] == [
        (Decimal("654.50"), "debit", "8400", "19%", True),
        (Decimal("85.39"), "debit", "8300", "7%", True),
    ]
    assert [c["value"] for c in b["bookings"][0]["cells"] if c["field"] in ("001", "002", "008", "010")] == [
        "654,50",
        '"S"',
        "8400",
        "1501",
    ]
    assert [sl["description"] for sl in b["bookings"][0]["source_lines"]] == [
        "Beratung (Stunde)",
        "Beratung Planung",
    ]
    draft = next(d for d in out["documents"] if d["reference"] == "UIE-ACC-J")
    assert draft["target_status"] is None and draft["not_prepared_reason"].startswith("Draft")

    # phase 5 : fichier — aperçu exact, téléchargement du contenu aperçu, journal ; rien n'est envoyé
    assert (
        call(alice, "GET", f"{tgt}/file-preview", params=jan).status_code == 409
    )  # Festschreibung à choisir
    assert call(alice, "GET", f"{tgt}/settings").json()["missing_for_file"] == [
        "Lock the bookings in DATEV (Festschreibung)"
    ]
    assert call(alice, "PUT", f"{tgt}/settings", json={"values": {"lock_bookings": "2"}}).status_code == 422
    assert call(alice, "PUT", f"{tgt}/settings", json={"values": {"lock_bookings": "0"}}).json()["ready"]
    fp = call(alice, "GET", f"{tgt}/file-preview", params=jan).json()
    assert fp["version"] == "13" and fp["counts"]["rows"] == len(fp["rows"])
    assert fp["counts"]["columns"] == 125
    assert fp["counts"]["exported_documents"] + fp["counts"]["excluded_documents"] == 13
    golden = [r for r in fp["rows"] if r["document"] == "RE/2026/00002"]
    assert [
        [c["value"] for c in r["cells"] if c["field"] in ("001", "002", "008", "010")] for r in golden
    ] == [
        ["654,50", '"S"', "8400", "1501"],
        ["85,39", '"S"', "8300", "1501"],
    ]
    assert {e["reference"] for e in fp["excluded"]} >= {"UIE-ACC-J", "UIE-ACC-K", "UIE-ACC-L"}
    assert fp["changes_every_time"] == ["Erzeugt am"] and fp["previous_exports"] == 0
    body = {"company_id": "1", "period": "2026-01", "version": "13"}
    stale = alice.post(f"{tgt}/files", json=body | {"content_token": "0" * 64})
    assert stale.status_code == 409 and "preview the file again" in stale.json()["detail"]
    got = alice.post(f"{tgt}/files", json=body | {"content_token": fp["content_token"]})
    assert got.status_code == 200 and got.headers["content-type"].startswith("text/csv")
    assert 'filename="DATEV_buchungsstapel_GO45-Demo-GmbH_2026-01_' in got.headers["content-disposition"]
    lines = got.content.decode("cp1252").split(CRLF)
    assert lines[0].startswith('"EXTF";700;21;"Buchungsstapel";13;')
    assert len(lines) - 3 == fp["counts"]["rows"]  # en-tête, noms de colonnes, fin de fichier
    assert sum('"RE/2026/00002"' in line.split(";")[10:11] for line in lines[2:]) == 2
    files = call(alice, "GET", f"{tgt}/files").json()
    assert [(f["period"], f["rows"], f["company"]) for f in files] == [
        ("2026-01", fp["counts"]["rows"], "GO45 Demo GmbH")
    ]
    assert call(alice, "GET", f"{tgt}/file-preview", params=jan).json()["previous_exports"] == 1
    # D11 : historique d'export par pièce — avant le téléchargement tout était nouveau, après plus rien
    assert fp["history"] == {
        "new": fp["counts"]["exported_documents"],
        "already_exported": 0,
        "changed_since_export": 0,
        "released": 0,
        "duplicates": 0,
        "will_be_exported": fp["counts"]["exported_documents"],
    }
    assert {d["state"] for d in fp["documents"]} == {"new"} and fp["documents"][0][
        "message"
    ] == "Not exported before"
    again = call(alice, "GET", f"{tgt}/file-preview", params=jan).json()
    n = fp["counts"]["exported_documents"]
    assert again["history"] == {
        "new": 0,
        "already_exported": n,
        "changed_since_export": 0,
        "released": 0,
        "duplicates": 0,
        "will_be_exported": 0,
    }
    assert again["rows"] == [] and again["counts"]["rows"] == 0
    b_state = next(d for d in again["documents"] if d["number"] == "RE/2026/00002")
    assert (b_state["state"], b_state["included"], b_state["target_status"]) == (
        "already_exported",
        False,
        "ready",
    )
    assert b_state["message"].startswith("Already exported on ")
    nothing = alice.post(f"{tgt}/files", json=body)
    assert nothing.status_code == 422 and "No new document to export" in nothing.json()["detail"]
    unconfirmed = alice.post(f"{tgt}/files", json=body | {"include_exported": True})
    assert unconfirmed.status_code == 400 and "confirm it explicitly" in unconfirmed.json()["detail"]
    dup = call(alice, "GET", f"{tgt}/file-preview", params=jan | {"include_exported": "true"}).json()
    assert dup["history"]["duplicates"] == n and dup["history"]["will_be_exported"] == n
    release = {"company_id": "1", "period": "2026-01", "version": "13"}
    unchanged = call(alice, "POST", f"{tgt}/documents/{b_state['ref']}/release", json=release)
    assert unchanged.status_code == 400  # inchangée : rien à autoriser
    files = call(alice, "GET", f"{tgt}/files").json()
    assert (files[0]["tracked"], files[0]["new_documents"], files[0]["already_exported_skipped"]) == (
        True,
        n,
        0,
    )
    assert call(bob, "POST", f"{tgt}/documents/{b_state['ref']}/release", json=release).status_code == 404
    assert call(bob, "POST", f"{tgt}/files/{files[0]['id']}/adopt").status_code == 404
    assert call(bob, "GET", f"{tgt}/file-preview", params=jan).status_code == 404
    assert bob.post(f"{tgt}/files", json=body).status_code == 404
    assert call(bob, "GET", f"{tgt}/files").status_code == 404

    # isolation, lecture seule, aucune donnée technique
    assert call(bob, "GET", f"{tgt}/preparation", params=jan).status_code == 404
    assert (
        call(
            bob, "PUT", f"{tgt}/mappings/account/{accounts['8400']['ref']}", json={"value": "8400"}
        ).status_code
        == 404
    )
    assert world["fake"].methods_called <= READ_ONLY_METHODS | {"authenticate", "version"}
    keys = _keys(seen)
    assert not keys & (
        TECHNICAL_KEYS
        | {
            "source_ref",
            "plan_checksum",
            "snapshot_label",
            "binding_version",
            "document_ref",
            "content_digest",
        }
    )
