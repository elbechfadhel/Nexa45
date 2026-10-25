"""V2a commit 1 : plan de lecture du catalogue déduit du Product Domain réel de customer17 (dump complet)
et de ses semantic bindings, après les 3 réponses de référence du fournisseur. Aucun nom de système
dans le Core.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sqlalchemy import select
from test_catalog_customer17_v1b1 import _analyze
from uie_core.catalog import BoundField, CatalogPlanError, build_read_plan
from uie_core.domains import DecisionSource, EntityRole, MeaningStatus
from uie_core.registry import models as m

CATALOG_SRC = Path(__file__).resolve().parents[2] / "packages" / "core" / "src" / "uie_core" / "catalog"
USABLE = {MeaningStatus.KNOWN, MeaningStatus.CONFIRMED}


@pytest.fixture
def customer17(session):  # noqa: ANN001, ANN201
    inst, _, svc = _analyze(session, "17", "odoo17_customer.json")
    svc.confirm(inst.id, "product.template", "x_supplier_ref")
    svc.change(inst.id, "product.template", "x_pack_code", "packaging_code")
    svc.ignore(inst.id, "product.template", "x_notes_internal")
    stored = svc.domains.get_instance_domain(inst.id)
    rows = list(
        session.scalars(select(m.FieldSemanticBinding).where(m.FieldSemanticBinding.instance_id == inst.id))
    )
    pack = svc.domains.pack_for_instance(inst)
    return stored, rows, pack, svc.vocabulary()


def _bound(rows) -> list[BoundField]:  # noqa: ANN001
    return [
        BoundField(
            entity=r.entity_technical_name,
            field=r.field_technical_name,
            key=r.semantic_key,
            human=r.source == DecisionSource.HUMAN,
            pack=r.source == DecisionSource.STANDARD,
        )
        for r in rows
        if r.semantic_key and r.meaning_status in USABLE and not r.pending
    ]


def _plan(customer17, keys=None):  # noqa: ANN001, ANN202
    stored, rows, pack, vocab = customer17
    snap = stored.snapshot
    return build_read_plan(
        snap.entities,
        [*snap.fields, *snap.extensions],
        _bound(rows),
        media_size=pack.media_size,
        labels={k.key: k.label for k in vocab.keys},
        domain_snapshot_id=stored.id,
        keys=keys,
    )


def test_plan_follows_roles_keys_and_links(customer17):
    plan = _plan(customer17)
    p = plan.primary
    assert (p.entity, p.role) == ("product.template", EntityRole.PRIMARY)
    assert {k: f.field for k, f in p.fields.items()} == {
        "name": "name",
        "sku": "default_code",
        "ean_gtin": "barcode",
        "list_price": "list_price",
        "currency": "currency_id",
        "description_sale": "description_sale",
        "weight": "weight",
        "volume": "volume",
        "uom": "uom_id",
        "product_type": "detailed_type",
        "active": "active",
        "category": "categ_id",
    }
    assert (p.media.main, p.media.thumb, p.media.large) == ("image_1920", "image_128", "image_1024")
    # extensions confirmées par le fournisseur ; l'extension ignorée n'est jamais lue
    assert [(e.key, e.field, e.label) for e in p.extras] == [
        ("packaging_code", "x_pack_code", "Packaging code"),
        ("supplier_reference", "x_supplier_ref", "Supplier product code"),
    ]

    v = plan.variant
    assert v.entity == "product.product"
    assert v.fields["product_template"].field == "product_tmpl_id"
    assert v.fields["list_price"].field == "lst_price"  # champ propre, pas la copie related du produit
    assert "name" not in v.fields  # copie related du produit : on garde le nom du produit
    assert v.fields["variant_identity"].field == "product_template_attribute_value_ids"
    assert (v.media.thumb, v.media.large) == ("image_variant_128", "image_variant_1024")

    assert plan.category.fields["category_path"].field == "complete_name"
    assert {k: f.field for k, f in plan.variant_value.fields.items()} == {
        "attribute": "attribute_id",
        "attribute_value_name": "name",
    }
    s = plan.supplier_info
    assert s.fields["product_template"].field == "product_tmpl_id"
    assert (s.fields["supplier_name"].field, s.fields["supplier_price"].field) == ("partner_id", "price")


def test_only_known_or_confirmed_fields_are_read(customer17):
    stored, rows, _, _ = customer17
    plan = _plan(customer17)
    status = {(r.entity_technical_name, r.field_technical_name): r for r in rows}
    for ep in (plan.primary, plan.variant, plan.category, plan.variant_value, plan.supplier_info):
        for f in ep.requested():
            if f.key.startswith("_"):
                continue  # vignette : taille dérivée du champ image identifié
            r = status[(ep.entity, f.field)]
            assert r.meaning_status in USABLE and not r.pending, (ep.entity, f.field, r.meaning_status)
    requested = {f.field for f in plan.primary.requested()}
    assert "x_notes_internal" not in requested  # IGNORED par le fournisseur
    assert plan.checksum == _plan(customer17).checksum  # déterministe


def test_human_decision_changes_the_plan(customer17, session):
    stored, rows, pack, vocab = customer17
    # le fournisseur ignore le code d'emballage : il disparaît des attributs supplémentaires
    for r in rows:
        if r.field_technical_name == "x_pack_code" and r.entity_technical_name == "product.template":
            r.meaning_status = MeaningStatus.IGNORED
    plan = _plan(customer17)
    assert [e.key for e in plan.primary.extras] == ["supplier_reference"]


def test_no_product_entity_is_an_explicit_error(customer17):
    stored, _, _, _ = customer17
    with pytest.raises(CatalogPlanError):
        build_read_plan(stored.snapshot.entities, stored.snapshot.fields, [])


def test_catalog_core_has_no_concrete_system_knowledge():
    for py in CATALOG_SRC.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        docstrings = {
            id(n.body[0].value)
            for n in ast.walk(tree)
            if isinstance(n, ast.Module | ast.ClassDef | ast.FunctionDef)
            and n.body
            and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                mods = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
                assert not any(
                    x.startswith(("uie_connectors", "uie_domain_packs", "fastapi")) for x in mods
                ), py
            if isinstance(node, ast.Name | ast.Attribute):
                ident = node.id if isinstance(node, ast.Name) else node.attr
                assert "odoo" not in ident.lower(), f"{py}: {ident}"
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                low = node.value.lower()
                assert "odoo" not in low and "product." not in low and "image_" not in low, (
                    f"{py}: {node.value!r}"
                )
