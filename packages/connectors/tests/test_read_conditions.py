"""Conditions de lecture génériques (contrat du Core) et leur traduction par le connecteur Odoo : in, eq,
between (bornes incluses), combinaisons, bornes invalides refusées. Aucune notion de domaine ni de système
dans le contrat."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from uie_connectors.odoo.catalog import _domain
from uie_connectors.odoo.testing import FakeOdooServer
from uie_core.catalog import Condition


def test_in_keeps_its_previous_translation():
    assert _domain([Condition(field="product_tmpl_id", values=["7", "8"])]) == [
        ["product_tmpl_id", "in", [7, 8]]
    ]


def test_eq_compares_references_by_identifier_and_texts_as_given():
    assert _domain([Condition(field="company_id", op="eq", values=["1"], data_type="reference")]) == [
        ["company_id", "=", 1]
    ]
    assert _domain([Condition(field="code", op="eq", values=["8400"], data_type="string")]) == [
        ["code", "=", "8400"]
    ]
    assert _domain([Condition(field="active", op="eq", values=["true"], data_type="boolean")]) == [
        ["active", "=", True]
    ]


def test_between_is_inclusive_and_combines_with_other_conditions():
    where = [
        Condition(field="company_id", op="eq", values=["1"], data_type="reference"),
        Condition(field="date", op="between", values=["2026-01-01", "2026-01-31"], data_type="date"),
        Condition(field="move_type", op="in", values=["out_invoice", "in_invoice"]),
    ]
    assert _domain(where) == [
        ["company_id", "=", 1],
        ["date", ">=", "2026-01-01"],
        ["date", "<=", "2026-01-31"],
        ["move_type", "in", ["out_invoice", "in_invoice"]],
    ]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"op": "eq", "values": ["1", "2"]},
        {"op": "between", "values": ["2026-01-01"]},
        {"op": "between", "values": ["2026-02-01", "2026-01-31"], "data_type": "date"},  # début après fin
        {"op": "between", "values": ["2026-13-01", "2026-12-31"], "data_type": "date"},  # date invalide
        {"op": "between", "values": ["10", "9"], "data_type": "integer"},
        {"op": "between", "values": ["a", "9"], "data_type": "decimal"},
        {"op": "like", "values": ["x"]},
    ],
)
def test_invalid_conditions_are_refused(kwargs):  # noqa: ANN001
    with pytest.raises(ValidationError):
        Condition(field="f", **kwargs)


def test_fake_server_applies_the_translated_domain():
    """Le faux serveur Odoo des tests applique =, >= et <= comme Odoo (bornes incluses)."""
    fake = FakeOdooServer(dump={"fields": []}, db="test", password="x")
    fake.records = {
        "account.move": [
            {"id": 1, "date": "2025-12-31", "company_id": [1, "A"]},
            {"id": 2, "date": "2026-01-01", "company_id": [1, "A"]},
            {"id": 3, "date": "2026-01-31", "company_id": [2, "B"]},
            {"id": 4, "date": "2026-02-01", "company_id": [1, "A"]},
        ]
    }
    domain = _domain(
        [
            Condition(field="date", op="between", values=["2026-01-01", "2026-01-31"], data_type="date"),
            Condition(field="company_id", op="eq", values=["1"], data_type="reference"),
        ]
    )
    rows = fake.execute_kw("account.move", "search_read", [domain], {"fields": ["date"]})
    assert [r["id"] for r in rows] == [2]
