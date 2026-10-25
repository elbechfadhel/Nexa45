"""V2d.2 : écrivain de catalogue Odoo contre un serveur XML-RPC SIMULÉ (aucun réseau, aucune base réelle).

Traduction des opérations génériques du Core (find_by, resolve_labels, create_with_key, update) en
appels Odoo ; liste blanche des méthodes (jamais unlink) ; création idempotente par identifiant
externe (`load`) ; erreurs classées et sans secret.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr
from uie_connectors.odoo.discovery import ReadOnlyViolation
from uie_connectors.odoo.testing import FakeOdooServer
from uie_connectors.odoo.writer import WRITE_METHODS, OdooCatalogWriter, WriteObjectProxy
from uie_core.imports import CatalogWriteError, LabelStatus, RecordId, RecordIds, WriteErrorKind
from uie_core.instances import ConnectionContext

ROOT = Path(__file__).resolve().parents[3]
DUMP = ROOT / "docs" / "dumps" / "odoo17_customer.json"
PASSWORD = "s3cret-not-real"


@pytest.fixture
def server() -> FakeOdooServer:
    s = FakeOdooServer.from_dump(DUMP, password=PASSWORD)
    s.records = {
        "product.template": [
            {"id": 1, "name": "Chair", "default_code": "EXIST-1", "list_price": 42.0, "active": True},
            {"id": 2, "name": "Old", "default_code": "ARCH-1", "active": False},
        ],
        "product.category": [
            {"id": 1, "name": "All", "complete_name": "All", "parent_id": False},
            {"id": 2, "name": "Office", "complete_name": "All / Office", "parent_id": [1, "All"]},
        ],
        "product.tag": [
            {"id": 1, "name": "Office"},
            {"id": 2, "name": "dup"},
            {"id": 3, "name": "Dup"},
        ],
    }
    return s


def _target(server: FakeOdooServer, password: str = PASSWORD):  # noqa: ANN202
    ctx = ConnectionContext(
        endpoint="http://odoo:8069",
        config={"db": server.db, "username": "admin"},
        secret=SecretStr(password),
    )
    return OdooCatalogWriter(proxy_factory=server.proxy).open(ctx)


def test_find_by_includes_archived_records(server):
    t = _target(server)
    assert t.find_by("product.template", "default_code", ["EXIST-1", "ARCH-1", "NEW"]) == {
        "EXIST-1": ["1"],
        "ARCH-1": ["2"],
    }


def test_labels_are_found_without_case_created_once_and_never_guessed(server):
    t = _target(server)
    tags = {
        r.label: r for r in t.resolve_labels("product.tag", ["office", "new tag", "DUP"], create_missing=True)
    }
    assert (tags["office"].status, tags["office"].id) == (LabelStatus.FOUND, "1")
    assert tags["new tag"].status == LabelStatus.CREATED
    assert tags["DUP"].status == LabelStatus.AMBIGUOUS and tags["DUP"].id is None
    again = t.resolve_labels("product.tag", ["New Tag"], create_missing=True)[0]
    assert (again.status, again.id) == (LabelStatus.FOUND, tags["new tag"].id)  # idempotent par libellé
    cats = {
        r.label: r
        for r in t.resolve_labels(
            "product.category",
            ["All / Office", "Office", "All / Office / Desk", "Garden"],
            create_missing=True,
        )
    }
    assert (cats["All / Office"].status, cats["All / Office"].id) == (LabelStatus.FOUND, "2")
    assert (cats["Office"].status, cats["Office"].id) == (LabelStatus.FOUND, "2")  # nom unique
    desk = next(r for r in server.records["product.category"] if r["name"] == "Desk")
    assert desk["parent_id"] == 2 and desk["complete_name"] == "All / Office / Desk"  # chaîne de parents
    assert cats["Garden"].status == LabelStatus.CREATED
    missing = t.resolve_labels("product.tag", ["nothing"], create_missing=False)[0]
    assert missing.status == LabelStatus.NOT_FOUND


def test_create_with_key_is_idempotent_through_an_external_identifier(server):
    t = _target(server)
    values = {
        "name": "Lamp",
        "default_code": "L-1",
        "list_price": 10.5,
        "sale_ok": True,
        "categ_id": RecordId(id="2"),
        "product_tag_ids": RecordIds(ids=["1", "2"]),
    }
    rid = t.create_with_key("product.template", "abc123", values)
    lamp = next(r for r in server.records["product.template"] if r["id"] == int(rid))
    assert (lamp["categ_id"], lamp["product_tag_ids"], lamp["list_price"]) == (2, [1, 2], "10.5")
    assert server.xmlids["__uie_import__.item_abc123"] == ("product.template", int(rid))
    count = len(server.records["product.template"])
    assert t.create_with_key("product.template", "abc123", {**values, "list_price": 11.0}) == rid
    assert len(server.records["product.template"]) == count  # rejouée : aucun doublon
    with pytest.raises(CatalogWriteError) as err:
        t.create_with_key("product.template", "other", {"default_code": "X"})
    assert err.value.kind == WriteErrorKind.VALIDATION and "name" in err.value.message


def test_lost_response_then_retry_creates_nothing_twice(server):
    t = _target(server)
    server.lose_response_on = "__uie_import__.item_k1"
    with pytest.raises(CatalogWriteError) as err:
        t.create_with_key("product.template", "k1", {"name": "Timer", "default_code": "T-1"})
    assert err.value.kind == WriteErrorKind.CONNECTION
    count = len(server.records["product.template"])  # créé côté Odoo, réponse perdue
    rid = t.create_with_key("product.template", "k1", {"name": "Timer", "default_code": "T-1"})
    assert len(server.records["product.template"]) == count
    assert [r["id"] for r in server.records["product.template"] if r.get("default_code") == "T-1"] == [
        int(rid)
    ]


def test_update_writes_ids_and_commands(server):
    t = _target(server)
    t.update("product.template", "1", {"categ_id": RecordId(id="2"), "product_tag_ids": RecordIds(ids=["1"])})
    chair = server.records["product.template"][0]
    assert (chair["categ_id"], chair["product_tag_ids"], chair["list_price"]) == (2, [1], 42.0)


def test_only_whitelisted_methods_and_no_secret_in_errors(server):
    t = _target(server)
    t.find_by("product.template", "default_code", ["EXIST-1"])
    t.resolve_labels("product.tag", ["office"], create_missing=True)
    t.create_with_key("product.template", "z", {"name": "Z"})
    t.update("product.template", "1", {"name": "Chair 2"})
    assert {m for model, m in server.calls if model != "common"} <= WRITE_METHODS
    assert "unlink" not in WRITE_METHODS
    with pytest.raises(ReadOnlyViolation):
        WriteObjectProxy(server.proxy("http://odoo/xmlrpc/2/object")).execute_kw(
            server.db, 2, PASSWORD, "product.template", "unlink", [[1]]
        )
    with pytest.raises(CatalogWriteError) as err:
        _target(server, password="wrong-password-xyz")
    assert err.value.kind == WriteErrorKind.RIGHTS and "wrong-password-xyz" not in err.value.message
