"""docker/customer-customize.py est idempotent : un 2e passage ne recrée pas le champ."""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "docker" / "customer-customize.py"


def _load():
    spec = importlib.util.spec_from_file_location("customer_customize", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeModels:
    """Simule ir.model / ir.model.fields : garde les champs créés en mémoire."""

    def __init__(self) -> None:
        self.fields: list[dict] = []
        self.creates = 0

    def execute_kw(self, db, uid, pwd, model, method, args, kw=None):  # noqa: ANN001, ANN201
        if (model, method) == ("ir.model.fields", "search_count"):
            wanted = dict((c[0], c[2]) for c in args[0])
            return sum(1 for f in self.fields if f["name"] == wanted["name"])
        if (model, method) == ("ir.model", "search"):
            return [42]
        if (model, method) == ("ir.model.fields", "create"):
            self.creates += 1
            self.fields.append(args[0])
            return 100 + self.creates
        raise AssertionError(f"appel inattendu {model}.{method}")


def test_customize_is_idempotent():
    mod = _load()
    models = FakeModels()
    first = [
        mod.ensure_custom_field(models, "customer17", 2, "admin", mdl, f) for mdl, f in mod.CUSTOM_FIELDS
    ]
    again = [
        mod.ensure_custom_field(models, "customer17", 2, "admin", mdl, f) for mdl, f in mod.CUSTOM_FIELDS
    ]
    assert first == [True] * 4 and again == [False] * 4
    assert models.creates == 4
    assert models.fields == [
        {
            "name": "x_customer_no",
            "field_description": "Customer No",
            "ttype": "char",
            "state": "manual",
            "model_id": 42,
        },
        {
            "name": "x_supplier_ref",
            "field_description": "Supplier Reference",
            "ttype": "char",
            "state": "manual",
            "model_id": 42,
        },
        {
            "name": "x_pack_code",
            "field_description": "Packaging Code",
            "ttype": "char",
            "state": "manual",
            "model_id": 42,
        },
        {
            "name": "x_notes_internal",
            "field_description": "Internal notes",
            "ttype": "text",
            "state": "manual",
            "model_id": 42,
        },
    ]
    assert [mdl for mdl, _ in mod.CUSTOM_FIELDS] == ["res.partner"] + ["product.template"] * 3
