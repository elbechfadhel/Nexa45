"""Valeurs de référence d'un Odoo connecté (Wizard) : identifiants externes LUS dans ir.model.data, jamais
composés ; lecture seule (search_read uniquement)."""

from __future__ import annotations

from typing import Any

from uie_connectors.odoo.reference import OdooReferenceLookup


class FakeSource:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def call(self, model: str, method: str, args: list[Any], kw: dict[str, Any] | None = None) -> Any:
        self.calls.append((model, method))
        if model == "uom.uom":
            return [
                {"id": 1, "display_name": "Units"},
                {"id": 2, "display_name": "kg"},
                {"id": 7, "display_name": "Box"},
            ]
        if model == "ir.model.data":
            return [
                {"module": "uom", "name": "product_uom_unit", "res_id": 1},
                {"module": "uom", "name": "product_uom_kgm", "res_id": 2},
            ]
        raise AssertionError(model)


class FakeProvider:
    def __init__(self, source: FakeSource) -> None:
        self.source = source

    def _source(self, ctx: Any) -> FakeSource:
        return self.source


def test_external_ids_are_read_never_composed_and_only_read_methods_are_used():
    source = FakeSource()
    lookup = OdooReferenceLookup(provider=FakeProvider(source))  # type: ignore[arg-type]
    options = lookup.options(None, "uom.uom", "id")  # type: ignore[arg-type]
    assert [(o.value, o.label) for o in options] == [
        ("uom.product_uom_unit", "Units"),
        ("uom.product_uom_kgm", "kg"),
    ]
    # « Box » n'a pas d'identifiant externe : jamais proposé (aucun identifiant fabriqué)
    assert {m for _, m in source.calls} == {"search_read"}
    names = lookup.options(None, "uom.uom", None)  # type: ignore[arg-type]
    assert [o.value for o in names] == ["Units", "kg", "Box"]
