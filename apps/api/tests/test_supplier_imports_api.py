"""V2c.1 : imports de fichiers catalogue par la Supplier API — UPLOAD → ANALYZE → rapport → décisions.

Domaine réel de customer17 (même instance que les autres tests Supplier), fichier de référence INVENTÉ.
Isolation : l'import de alice est introuvable pour bob ; le système de bob n'est jamais une cible pour alice.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from test_supplier_api import Supplier, _field, _reset_limiter, world  # noqa: F401 — fixtures partagées
from uie_api import deps
from uie_core.imports import LocalFileStore

FIXTURES = Path(__file__).resolve().parents[3] / "domain-packs" / "tests" / "fixtures" / "imports"
CSV = (FIXTURES / "supplier_catalog.csv").read_bytes()
XLSX = (FIXTURES / "supplier_catalog.xlsx").read_bytes()
LEAKS = ("product.template", "categ_id", "default_code", "file:v1", "storage", "sha256", "organization")
SEEN: list[Any] = []


@pytest.fixture
def api(world, tmp_path):  # noqa: ANN001, ANN201, F811
    files = LocalFileStore(tmp_path / "imports")
    world["app"].dependency_overrides[deps.get_file_store] = lambda: files
    world["files"] = files
    return world


def _ready_catalog(client: Supplier, sid: str) -> None:
    assert client.post(f"/api/systems/{sid}/catalog/analyze").status_code == 200
    client.post(_field(sid, "x_supplier_ref", "confirm"), json={})
    client.post(_field(sid, "x_pack_code", "change"), json={"meaning": "packaging_code"})
    client.post(_field(sid, "x_notes_internal", "ignore"), json={})


def _upload(client: Supplier, name: str = "supplier_catalog.csv", data: bytes = CSV):  # noqa: ANN202
    r = client.post("/api/imports", files={"file": (name, data, "application/octet-stream")})
    SEEN.append(r.text)
    return r


def _keep(r):  # noqa: ANN001, ANN202
    SEEN.append(r.text)
    return r


def test_upload_analyze_review_ignore_ready(api):
    alice = Supplier(api["app"], "alice@acme.io")
    sid = api["instances"]["acme"]
    r = _upload(alice)
    assert r.status_code == 201, r.text
    imp = r.json()
    assert (imp["filename"], imp["status"], imp["rows"], imp["columns"], imp["system"]) == (
        "supplier_catalog.csv",
        "UPLOADED",
        10,
        8,
        None,
    )
    iid = imp["id"]

    # catalogue du système pas encore prêt → 409, message simple
    r = _keep(alice.post(f"/api/imports/{iid}/analyze", json={"system_id": sid}))
    assert r.status_code == 409 and r.json()["detail"] == "Analyze this system's catalog first."
    _ready_catalog(alice, sid)

    r = _keep(alice.post(f"/api/imports/{iid}/analyze", json={"system_id": sid}))
    assert r.status_code == 200, r.text
    rep = r.json()
    assert (rep["status"], rep["system"]["name"]) == ("ANALYZED", "customer17")
    # domaine réduit des tests API : ni code-barres ni poids sur ce système (le domaine complet de customer17
    # les a : voir domain-packs/tests/test_catalog_imports_service.py)
    assert rep["counts"] == {"matched": 4, "review": 3, "unmatched": 1, "ignored": 0, "empty": 0}
    cols = {c["name"]: c for c in rep["items"]}
    assert cols["Category"]["target"] == {"id": "product:category", "label": "Category", "group": "Product"}
    assert cols["Category"]["content"] == "Text"
    assert (cols["Barcode"]["status"], cols["Barcode"]["target"], cols["Barcode"]["message"]) == (
        "REVIEW",
        None,
        "Looks like “EAN / GTIN barcode”, but your system has no field for it.",
    )
    assert (cols["Price"]["status"], cols["Price"]["target"]["label"], cols["Price"]["message"]) == (
        "REVIEW",
        "Sales price",
        "Please check this suggestion.",
    )
    assert (cols["Material"]["status"], cols["Material"]["target"]) == ("UNMATCHED", None)

    targets = _keep(alice.get(f"/api/imports/{iid}/targets")).json()
    assert {"id": "product:list_price", "label": "Sales price", "group": "Product"} in targets
    assert not any("x_notes" in t["id"] for t in targets)

    price = cols["Price"]["position"]
    r = _keep(alice.post(f"/api/imports/{iid}/columns/{price}/target", json={"target_id": "product:sku"}))
    assert r.status_code == 409 and "already used" in r.json()["detail"]
    r = _keep(
        alice.post(f"/api/imports/{iid}/columns/{price}/target", json={"target_id": "product:list_price"})
    )
    assert r.json()["counts"] == {"matched": 5, "review": 2, "unmatched": 1, "ignored": 0, "empty": 0}
    assert {c["name"]: c for c in r.json()["items"]}["Price"]["decided_by_you"] is True
    for name in ("Material", "Barcode"):
        r = _keep(alice.post(f"/api/imports/{iid}/columns/{cols[name]['position']}/ignore"))
        assert r.json()["status"] == "ANALYZED"
    r = _keep(alice.post(f"/api/imports/{iid}/columns/{cols['Weight']['position']}/ignore"))
    assert (r.json()["status"], r.json()["counts"]["ignored"]) == ("READY", 3)
    assert r.json()["system"]["kind"] == "Odoo"

    listed = _keep(alice.get("/api/imports")).json()
    assert [(i["id"], i["status"], i["system"]["name"]) for i in listed] == [(iid, "READY", "customer17")]
    assert _keep(alice.get(f"/api/imports/{iid}")).json()["status"] == "READY"


def test_empty_columns_and_ignore_all_unmatched(api):
    alice = Supplier(api["app"], "alice@acme.io")
    sid = api["instances"]["acme"]
    _ready_catalog(alice, sid)
    data = (
        b"Name,Regular price,Material,Colour code,Weight (lbs)\nChair,12.50,Oak,#fff,\nDesk,99,Steel,#000,\n"
    )
    iid = _upload(alice, "shop.csv", data).json()["id"]
    rep = _keep(alice.post(f"/api/imports/{iid}/analyze", json={"system_id": sid})).json()
    cols = {c["name"]: c for c in rep["items"]}
    assert rep["counts"]["empty"] == 1
    assert (cols["Weight (lbs)"]["status"], cols["Weight (lbs)"]["message"]) == (
        "EMPTY",
        "No values in this file.",
    )
    r = alice.post(f"/api/imports/{iid}/columns/{cols['Weight (lbs)']['position']}/ignore")
    assert r.status_code == 409  # une colonne vide n'est pas « ignorée par le fournisseur »
    unmatched = [c["name"] for c in rep["items"] if c["status"] == "UNMATCHED"]
    assert unmatched
    r = _keep(alice.post(f"/api/imports/{iid}/ignore-unmatched"))
    after = {c["name"]: c["status"] for c in r.json()["items"]}
    assert all(after[n] == "IGNORED" for n in unmatched)  # conservées, IGNORED
    assert len(after) == 5 and after["Weight (lbs)"] == "EMPTY"
    assert r.json()["counts"]["unmatched"] == 0 and r.json()["counts"]["ignored"] == len(unmatched)


def test_xlsx_and_bad_files(api):
    alice = Supplier(api["app"], "alice@acme.io")
    r = _upload(alice, "supplier_catalog.xlsx", XLSX)
    assert r.status_code == 201 and (r.json()["rows"], r.json()["columns"]) == (10, 8)
    r = _upload(alice, "notes.txt", b"hello")
    assert r.status_code == 400 and r.json()["detail"] == "Please upload an .xlsx, .csv or CAMT .xml file."
    r = _upload(alice, "big.csv", b"a\n" + b"x\n" * (6 * 1024 * 1024))
    assert r.status_code == 400 and "too large" in r.json()["detail"]


def test_imports_are_isolated_between_suppliers(api):
    alice, bob = Supplier(api["app"], "alice@acme.io"), Supplier(api["app"], "bob@globex.io")
    iid = _upload(alice).json()["id"]
    missing = {"detail": f"Import introuvable : {iid}"}
    assert _keep(bob.get("/api/imports")).json() == []
    assert bob.get(f"/api/imports/{iid}").json() == missing
    bob_sys = api["instances"]["globex"]
    for method, url, body in (
        ("POST", f"/api/imports/{iid}/analyze", {"system_id": bob_sys}),
        ("GET", f"/api/imports/{iid}/targets", None),
        ("POST", f"/api/imports/{iid}/columns/0/ignore", None),
        ("DELETE", f"/api/imports/{iid}", None),
    ):
        r = bob.call(method, url, json=body)
        assert (r.status_code, r.json()) == (404, missing), url
    # le système de bob n'est pas une cible pour alice (même 404 qu'un système inexistant)
    r = alice.post(f"/api/imports/{iid}/analyze", json={"system_id": bob_sys})
    assert r.status_code == 404
    # organization_id fourni par le client : refusé (jamais pris en compte)
    r = alice.post(
        f"/api/imports/{iid}/analyze",
        json={"system_id": api["instances"]["acme"], "organization_id": str(api["orgs"]["globex"].id)},
    )
    assert r.status_code == 422


def test_delete_import(api):
    alice = Supplier(api["app"], "alice@acme.io")
    iid = _upload(alice).json()["id"]
    assert alice.call("DELETE", f"/api/imports/{iid}").status_code == 204
    assert alice.get(f"/api/imports/{iid}").status_code == 404


def test_no_technical_data_in_import_responses():
    assert SEEN, "aucune réponse collectée"
    for text in SEEN:
        body = json.loads(text) if text.startswith(("{", "[")) else text
        dumped = json.dumps(body)
        for leak in LEAKS:
            assert leak not in dumped, (leak, dumped[:300])


def test_second_import_reuses_the_suppliers_choices(api):
    """V2c.2 : aucune action supplémentaire ; les choix du 1er import réduisent les décisions du 2e."""
    alice, bob = Supplier(api["app"], "alice@acme.io"), Supplier(api["app"], "bob@globex.io")
    sid = api["instances"]["acme"]
    _ready_catalog(alice, sid)
    first = _upload(alice).json()["id"]
    rep = _keep(alice.post(f"/api/imports/{first}/analyze", json={"system_id": sid})).json()
    assert rep["analysis"] == {
        "total": 8,
        "automatic": 4,
        "learned": 0,
        "learned_ignored": 0,
        "ai_matched": 0,
        "ai_suggested": 0,
        "ai_not_relevant": 0,
        "needs_attention": 4,
        "empty": 0,
    }
    price = {c["name"]: c for c in rep["items"]}["Price"]
    r = _keep(
        alice.post(
            f"/api/imports/{first}/columns/{price['position']}/target",
            json={"target_id": "product:list_price"},
        )
    )
    assert {c["name"]: c for c in r.json()["items"]}["Price"]["source"] == "you"

    second = _upload(alice).json()["id"]
    rep = _keep(alice.post(f"/api/imports/{second}/analyze", json={"system_id": sid})).json()
    assert rep["analysis"] == {
        "total": 8,
        "automatic": 4,
        "learned": 1,
        "learned_ignored": 0,
        "ai_matched": 0,
        "ai_suggested": 0,
        "ai_not_relevant": 0,
        "needs_attention": 3,
        "empty": 0,
    }
    cols = {c["name"]: c for c in rep["items"]}
    assert (cols["Price"]["status"], cols["Price"]["source"]) == ("MATCHED", "learned")
    assert cols["SKU"]["source"] == "auto"

    # V2c.2b : le fournisseur ignore ce qui reste (Ignore all + Ignore) → le 3e import est prêt d'office
    rest = [c for c in cols.values() if c["status"] in ("REVIEW", "UNMATCHED")]
    _keep(alice.post(f"/api/imports/{second}/ignore-unmatched"))
    for c in rest:
        if c["status"] == "REVIEW":
            _keep(alice.post(f"/api/imports/{second}/columns/{c['position']}/ignore"))
    third = _upload(alice).json()["id"]
    rep = _keep(alice.post(f"/api/imports/{third}/analyze", json={"system_id": sid})).json()
    assert rep["status"] == "READY"
    assert len(rest) == 3
    assert rep["analysis"] == {
        "total": 8,
        "automatic": 4,
        "learned": 1,
        "learned_ignored": 3,
        "ai_matched": 0,
        "ai_suggested": 0,
        "ai_not_relevant": 0,
        "needs_attention": 0,
        "empty": 0,
    }
    got = {c["name"]: (c["status"], c["source"]) for c in rep["items"]}
    assert all(got[c["name"]] == ("IGNORED", "learned") for c in rest)

    # une autre organisation n'en profite jamais
    bob_sid = api["instances"]["globex"]
    _ready_catalog(bob, bob_sid)
    other = _upload(bob).json()["id"]
    rep = _keep(bob.post(f"/api/imports/{other}/analyze", json={"system_id": bob_sid})).json()
    assert (rep["analysis"]["learned"], rep["analysis"]["learned_ignored"]) == (0, 0)
    assert {c["name"]: c for c in rep["items"]}["Price"]["status"] == "REVIEW"


def test_ai_fallback_on_the_first_import(api, tmp_path):
    """V2c.3 : le LLM (replay INVENTÉ) règle ce que la mémoire et les règles laissent ; une colonne
    « Probably not needed » n'est jamais ignorée d'office ; « Ignore all AI not relevant » l'apprend."""
    from uie_ai import ReplayProposer

    replay = tmp_path / "replay.json"
    replay.write_text(
        json.dumps(
            {
                "format": "uie-llm-replay/1",
                "provider": "openai",
                "model": "model-x",
                "import_columns": {
                    "price": {
                        "decision": "TARGET",
                        "semantic_key": "list_price",
                        "confidence": 0.96,
                        "reason": "Selling price.",
                    },
                    "material": {
                        "decision": "NOT_RELEVANT",
                        "semantic_key": "",
                        "confidence": 0.93,
                        "reason": "No catalog target for materials.",
                    },
                },
                "import_meta": {"model": "model-x", "input_tokens": 500, "output_tokens": 80},
            }
        ),
        "utf-8",
    )
    proposer = ReplayProposer(replay)
    api["app"].dependency_overrides[deps.get_semantic_proposer] = lambda: proposer
    alice = Supplier(api["app"], "alice@acme.io")
    sid = api["instances"]["acme"]
    _ready_catalog(alice, sid)
    iid = _upload(alice).json()["id"]
    rep = _keep(alice.post(f"/api/imports/{iid}/analyze", json={"system_id": sid})).json()
    assert len(proposer.column_calls) == 1
    assert (rep["analysis"]["ai_matched"], rep["analysis"]["ai_not_relevant"]) == (1, 1)
    cols = {c["name"]: c for c in rep["items"]}
    assert (cols["Price"]["status"], cols["Price"]["source"], cols["Price"]["confidence_pct"]) == (
        "MATCHED",
        "ai",
        96,
    )
    material = cols["Material"]
    assert (material["status"], material["source"], material["confidence_pct"]) == ("REVIEW", "ai", 93)
    assert material["message"] == "Probably not needed." and material["target"] is None
    assert material["probably_not_needed"] and not cols["Price"]["probably_not_needed"]
    assert cols["SKU"]["confidence_pct"] is None  # règles : aucune confiance AI affichée

    rep = _keep(alice.post(f"/api/imports/{iid}/ignore-ai-not-relevant")).json()
    assert {c["name"]: c for c in rep["items"]}["Material"]["status"] == "IGNORED"
    # ré-analyse : Material est réglée par la mémoire, Price reprend sa réponse (cache par colonne)
    rep = _keep(alice.post(f"/api/imports/{iid}/analyze", json={"system_id": sid})).json()
    assert len(proposer.column_calls) == 1
    assert rep["analysis"]["learned_ignored"] == 1
    body = json.dumps(rep)
    for leak in (
        "openai",
        "model-x",
        "Selling price",
        "No catalog target",
    ):  # ni fournisseur, ni raison brute
        assert leak not in body, leak


def test_import_preview_is_read_only_and_speaks_business(api):
    """V2d.1 : READY → preview (données préparées, problèmes, colonnes non importées), sans nom technique."""
    alice, bob = Supplier(api["app"], "alice@acme.io"), Supplier(api["app"], "bob@globex.io")
    sid = api["instances"]["acme"]
    _ready_catalog(alice, sid)
    iid = _upload(alice).json()["id"]
    rep = _keep(alice.post(f"/api/imports/{iid}/analyze", json={"system_id": sid})).json()
    assert alice.get(f"/api/imports/{iid}/preview").status_code == 409  # pas encore prêt
    cols = {c["name"]: c for c in rep["items"]}
    for c in cols.values():
        if c["status"] == "REVIEW" and c["target"]:
            _keep(
                alice.post(
                    f"/api/imports/{iid}/columns/{c['position']}/target",
                    json={"target_id": c["target"]["id"]},
                )
            )
    _keep(alice.post(f"/api/imports/{iid}/ignore-unmatched"))
    for c in alice.get(f"/api/imports/{iid}").json()["items"]:
        if c["status"] == "REVIEW":  # suggestion sans cible : ignorée
            _keep(alice.post(f"/api/imports/{iid}/columns/{c['position']}/ignore"))
    assert alice.get(f"/api/imports/{iid}").json()["status"] == "READY"

    r = alice.get(f"/api/imports/{iid}/preview")
    assert r.status_code == 200, r.text
    SEEN.append(r.text)
    p = r.json()
    assert (p["rows"], p["importable_rows"], p["system"]["kind"]) == (10, 10, "Odoo")
    data = {d["label"]: d for d in p["data"]}
    assert (data["Product name"]["rows_present"], data["Product name"]["rows_valid"]) == (10, 10)
    assert data["Product name"]["section"] == "Product" and p["not_imported"]["unavailable"] == []
    assert data["Category"]["distinct_values"] >= 1 and data["Category"]["processed_during_import"]
    assert not data["Product name"]["processed_during_import"]
    assert "Material" in p["not_imported"]["no_target"]
    for leak in ("semantic_key", "target_id", "checksum", "product:", "list_price", "categ_id", "binding"):
        assert leak not in r.text, leak
    assert alice.get(f"/api/imports/{iid}/preview").json() == p  # reproductible
    assert bob.get(f"/api/imports/{iid}/preview").status_code == 404  # autre organisation


class _MemoryTarget:
    """Système cible en mémoire (V2d.2) : aucun réseau ; enregistre les écritures."""

    def __init__(self) -> None:
        self.records: dict[str, dict] = {}
        self.keys: dict[str, str] = {}
        self.writes = 0

    def find_by(self, entity, field, values, *, include_archived=True):  # noqa: ANN001, ANN201
        return {}

    def resolve_labels(self, entity, labels, *, create_missing):  # noqa: ANN001, ANN201
        from uie_core.imports import LabelResolution, LabelStatus

        return [
            LabelResolution(label=lab, status=LabelStatus.CREATED, id=f"r{i}") for i, lab in enumerate(labels)
        ]

    def create_with_key(self, entity, key, values):  # noqa: ANN001, ANN201
        self.writes += 1
        rid = self.keys.setdefault(key, f"p{len(self.keys) + 1}")
        self.records[rid] = values
        return rid

    def update(self, entity, record_id, values):  # noqa: ANN001, ANN201
        self.writes += 1


def test_import_into_the_system_is_idempotent_and_speaks_business(api):
    """V2d.2 : READY → import réel (écrivain en mémoire) ; même clé = même exécution ; rien de technique."""
    from uie_core.imports import CatalogWriter

    target = _MemoryTarget()

    class _Writer(CatalogWriter):
        system_code = "odoo"

        def open(self, ctx):  # noqa: ANN001, ANN201
            return target

    class _Writers:
        def resolve(self, *a):  # noqa: ANN002, ANN201
            return _Writer()

    api["app"].dependency_overrides[deps.get_catalog_writers] = _Writers
    alice, bob = Supplier(api["app"], "alice@acme.io"), Supplier(api["app"], "bob@globex.io")
    sid = api["instances"]["acme"]
    _ready_catalog(alice, sid)
    iid = _upload(alice).json()["id"]
    rep = _keep(alice.post(f"/api/imports/{iid}/analyze", json={"system_id": sid})).json()
    key = {"request_key": "confirm-0001"}
    assert alice.post(f"/api/imports/{iid}/runs", json=key).status_code == 409  # pas encore prêt
    for c in rep["items"]:
        if c["status"] == "REVIEW" and c["target"]:
            _keep(
                alice.post(
                    f"/api/imports/{iid}/columns/{c['position']}/target",
                    json={"target_id": c["target"]["id"]},
                )
            )
    _keep(alice.post(f"/api/imports/{iid}/ignore-unmatched"))
    for c in alice.get(f"/api/imports/{iid}").json()["items"]:
        if c["status"] == "REVIEW":
            _keep(alice.post(f"/api/imports/{iid}/columns/{c['position']}/ignore"))

    assert alice.post(f"/api/imports/{iid}/runs", json={"request_key": "bad key!"}).status_code == 422
    r = alice.post(f"/api/imports/{iid}/runs", json=key)
    assert r.status_code == 200, r.text
    SEEN.append(r.text)
    run = r.json()
    assert (run["status"], run["rows"], run["created"], run["failed"]) == ("COMPLETED", 10, 10, 0)
    assert any(rel["label"] == "Category" and rel["created"] >= 1 for rel in run["relations"])
    writes = target.writes
    again = alice.post(f"/api/imports/{iid}/runs", json=key).json()  # double clic / nouvel essai
    assert again["id"] == run["id"] and target.writes == writes
    assert alice.get(f"/api/imports/{iid}/runs/{run['id']}").json()["id"] == run["id"]
    assert [x["id"] for x in alice.get(f"/api/imports/{iid}/runs").json()] == [run["id"]]
    listed = {i["id"]: i for i in alice.get("/api/imports").json()}
    assert listed[iid]["last_run"]["status"] == "COMPLETED"
    for leak in ("product.template", "default_code", "__uie_import__", "external_key", "target_record", "p1"):
        assert leak not in r.text, leak
    assert bob.post(f"/api/imports/{iid}/runs", json=key).status_code == 404  # autre organisation
    assert bob.get(f"/api/imports/{iid}/runs/{run['id']}").status_code == 404
