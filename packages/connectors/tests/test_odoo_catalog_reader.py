"""V2a commit 2 : OdooCatalogReader (lecture seule) sur FakeOdooServer avec un mini catalogue inventé."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from pydantic import SecretStr
from uie_connectors.odoo import OdooCatalogReader
from uie_connectors.odoo.discovery import READ_ONLY_METHODS
from uie_connectors.odoo.testing import FakeOdooServer
from uie_core.catalog import CatalogReaderRegistry, CatalogReadError, Condition, FieldRequest, Ref
from uie_core.instances import ConnectionContext

FIX = Path(__file__).parent / "fixtures"
PASSWORD = "Catalog-Reader-S3cret"
TEMPLATE = [
    FieldRequest(name="name", data_type="string"),
    FieldRequest(name="default_code", data_type="string"),
    FieldRequest(name="list_price", data_type="decimal"),
    FieldRequest(name="categ_id", data_type="reference"),
    FieldRequest(name="active", data_type="boolean"),
    FieldRequest(name="image_128", data_type="binary"),
]


@pytest.fixture
def fake() -> FakeOdooServer:
    server = FakeOdooServer.from_dump(FIX / "odoo17_customer_instance.json", password=PASSWORD)
    return server.with_records(FIX / "odoo17_catalog_records.json")


def _ctx(fake: FakeOdooServer, password: str = PASSWORD) -> ConnectionContext:
    return ConnectionContext(
        endpoint="http://odoo:8069", config={"db": fake.db, "username": "admin"}, secret=SecretStr(password)
    )


def test_reads_pages_in_a_stable_order_with_normalized_values(fake):
    src = OdooCatalogReader(proxy_factory=fake.proxy).open(_ctx(fake))
    assert src.count("product.template") == 3  # archivés compris
    assert src.count("product.template", include_archived=False) == 2
    first = src.read_page("product.template", TEMPLATE, limit=2)
    rest = src.read_page("product.template", TEMPLATE, offset=2, limit=2)
    assert [r.id for r in first + rest] == ["1", "2", "3"]
    chair = first[0]
    assert chair.label == "Office Chair"
    assert chair.values == {
        "name": "Office Chair",
        "default_code": None,  # False Odoo → None
        "list_price": 120.0,
        "categ_id": Ref(id="3", label="All / Furniture / Chairs"),
        "active": True,
        "image_128": True,  # présence seulement (bin_size), jamais le contenu dans une page
    }
    assert first[1].values["image_128"] is False
    assert rest[0].values["active"] is False


def test_filters_by_link_and_reads_by_ids(fake):
    src = OdooCatalogReader(proxy_factory=fake.proxy).open(_ctx(fake))
    variants = src.read_page(
        "product.product",
        [
            FieldRequest(name="product_tmpl_id", data_type="reference"),
            FieldRequest(name="product_template_attribute_value_ids", data_type="collection"),
        ],
        where=[Condition(field="product_tmpl_id", values=["1"])],
    )
    assert [v.id for v in variants] == ["11", "12"]
    assert variants[0].values["product_template_attribute_value_ids"] == ["101", "103"]
    values = src.read(
        "product.template.attribute.value",
        ["103", "101"],
        [
            FieldRequest(name="attribute_id", data_type="reference"),
            FieldRequest(name="name", data_type="string"),
        ],
    )
    assert {v.id: (v.values["attribute_id"].label, v.values["name"]) for v in values} == {
        "101": ("Color", "Black"),
        "103": ("Size", "Standard"),
    }
    assert src.read("product.template", [], TEMPLATE) == []


def test_binary_content_is_read_one_record_at_a_time(fake):
    src = OdooCatalogReader(proxy_factory=fake.proxy).open(_ctx(fake))
    data = src.read_binary("product.template", "1", "image_128")
    assert data is not None and data.startswith(b"\x89PNG")
    assert src.read_binary("product.template", "2", "image_128") is None
    assert base64.b64encode(data).decode().startswith("iVBOR")


def test_only_read_methods_are_ever_called(fake):
    src = OdooCatalogReader(proxy_factory=fake.proxy).open(_ctx(fake))
    src.count("product.template")
    src.read_page("product.template", TEMPLATE)
    src.read("product.category", ["1"], [FieldRequest(name="complete_name", data_type="string")])
    src.read_binary("product.template", "1", "image_1920")
    assert {m for model, m in fake.calls if model != "common"} <= READ_ONLY_METHODS


def test_errors_are_explicit_and_never_leak_the_secret(fake):
    with pytest.raises(CatalogReadError) as exc:
        OdooCatalogReader(proxy_factory=fake.proxy).open(_ctx(fake, password="wrong-Secret-42"))
    assert "wrong-Secret-42" not in str(exc.value)
    src = OdooCatalogReader(proxy_factory=fake.proxy).open(_ctx(fake))
    with pytest.raises(CatalogReadError):
        src.read_page("res.unknown", TEMPLATE)


def test_reader_is_registered_for_odoo():
    assert isinstance(CatalogReaderRegistry.get("odoo", "17", "orm"), OdooCatalogReader)
    with pytest.raises(CatalogReadError):
        CatalogReaderRegistry.get("sap", "1", "odata")
