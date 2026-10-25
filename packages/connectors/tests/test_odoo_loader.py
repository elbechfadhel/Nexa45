"""Tests du Schema Loader Odoo sur des dumps JSON (sans instance Odoo)."""

from __future__ import annotations

from pathlib import Path

import pytest
import uie_connectors  # noqa: F401  (enregistre le loader)
from uie_connectors.odoo import OdooSchemaLoader
from uie_core.registry import (
    Capability,
    ChangeType,
    DataType,
    LoaderRegistry,
    RelationType,
    compare_snapshots,
)

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def snap17():
    return OdooSchemaLoader({"json": FIX / "odoo17_ref.json"}).build_snapshot()


@pytest.fixture(scope="module")
def snap18():
    return OdooSchemaLoader({"json": FIX / "odoo18_ref.json"}).build_snapshot()


def test_loader_is_registered():
    assert LoaderRegistry.get("odoo") is OdooSchemaLoader
    assert "odoo" in LoaderRegistry.available()


def test_identity_and_source(snap17):
    assert (snap17.system, snap17.product, snap17.version) == ("odoo", "odoo", "17")
    assert (snap17.interface, snap17.interface_type, snap17.edition) == ("orm", "orm", "community")
    assert snap17.source["loader"] == "odoo.json"
    assert "account" in snap17.source["installed_modules"]
    assert {m.technical_name for m in snap17.modules} >= {"base", "product", "account"}


def test_entities_kinds_and_physical(snap17):
    names = {e.technical_name for e in snap17.entities}
    assert "res.config.settings" not in names  # transient exclu par défaut
    partner = snap17.entity("res.partner")
    assert partner.kind == "entity" and partner.physical_name == "res_partner"
    assert partner.module == "base"
    assert snap17.entity("mail.thread").kind == "abstract"
    assert snap17.entity("mail.thread").physical_name is None


def test_field_conversion(snap17):
    p = snap17.entity("res.partner")
    assert p.field("email").data_type == DataType.STRING
    assert p.field("email").physical_column == "email"
    assert p.field("country_id").data_type == DataType.REFERENCE
    assert p.field("country_id").native_type == "many2one"
    dn = p.field("display_name")
    assert dn.computed and not dn.stored and dn.physical_column is None
    assert p.field("child_ids").data_type == DataType.COLLECTION
    assert p.field("child_ids").physical_column is None
    assert p.field("category_id").metadata["relation_table"] == "res_partner_res_partner_category_rel"
    assert p.field("type").data_type == DataType.ENUM
    assert [s["value"] for s in p.field("type").metadata["selection"]] == ["contact", "invoice", "delivery"]
    assert p.field("id").metadata.get("technical") is True
    assert p.field("customer_rank").metadata["modules"] == ["account"]


def test_relations(snap17):
    p = snap17.entity("res.partner")
    rel = {(r.source_field, r.relation_type, r.target_entity, r.inverse_field) for r in p.relations}
    assert ("country_id", RelationType.MANY_TO_ONE, "res.country", None) in rel
    assert ("child_ids", RelationType.ONE_TO_MANY, "res.partner", "parent_id") in rel
    assert ("category_id", RelationType.MANY_TO_MANY, "res.partner.category", None) in rel
    # relation vers un model hors périmètre → tracée, pas bloquante
    assert "account.account" in p.metadata["dangling_relations"]


def test_capabilities(snap17):
    assert snap17.capabilities[Capability.SCHEMA_DISCOVERY] is True
    assert snap17.entity("res.partner").capabilities[Capability.CREATE] is True
    assert snap17.entity("mail.thread").capabilities[Capability.CREATE] is False


def test_include_transient_option():
    snap = OdooSchemaLoader({"json": FIX / "odoo17_ref.json", "include_transient": True}).build_snapshot()
    assert snap.entity("res.config.settings").kind == "transient"


def test_version_compare_17_to_18(snap17, snap18):
    res = compare_snapshots(snap17, snap18)
    paths = {(c.path, c.change_type) for c in res.changes}
    assert ("product.combo", ChangeType.ADDED) in paths
    assert ("product.template.detailed_type", ChangeType.REMOVED) in paths
    assert ("product.template.type", ChangeType.ADDED) in paths
    assert ("product.template.is_storable", ChangeType.ADDED) in paths
    assert ("res.partner", ChangeType.UNCHANGED) not in paths and (
        "res.partner",
        ChangeType.CHANGED,
    ) not in paths


def test_instance_diff_vs_standard(snap17):
    inst = OdooSchemaLoader({"json": FIX / "odoo17_customer_instance.json"}).build_snapshot()
    res = compare_snapshots(snap17, inst, source_label="Odoo 17 standard", target_label="Customer A")
    paths = {(c.path, c.change_type) for c in res.changes}
    assert ("res.partner.x_customer_no", ChangeType.ADDED) in paths
    assert ("res.partner.email", ChangeType.CHANGED) in paths
    added = next(c for c in res.changes if c.path == "res.partner.x_customer_no")
    assert added.details["data_type"] == DataType.STRING
    changed = next(c for c in res.changes if c.path == "res.partner.email")
    assert changed.details["required"] == {"from": False, "to": True}


# --------------------------------------------------------------------------- #
# Extraits de vrais dumps (extract_real.py) : particularités de l'introspection réelle
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def real17():
    return OdooSchemaLoader({"json": FIX / "odoo17_real_extract.json"}).build_snapshot()


@pytest.fixture(scope="module")
def real19():
    return OdooSchemaLoader({"json": FIX / "odoo19_real_extract.json"}).build_snapshot()


def test_real_origin_module_from_load_order(real17):
    # ir.model.modules est alphabétique (« account, account_add_gln, …, base, … »)
    partner = real17.entity("res.partner")
    assert partner.module == "base"
    assert partner.metadata["modules"][0] == "base"
    assert "account" in partner.metadata["modules"]
    assert real17.entity("product.template").module == "product"


def test_real_abstract_inferred_without_ir_model_abstract(real17):
    # Odoo 17 n'expose pas ir.model.abstract : inféré (pas de write_date ni d'ACL)
    for name in ("mail.thread", "format.address.mixin"):
        e = real17.entity(name)
        assert e.kind == "abstract" and e.physical_name is None
        assert e.metadata["kind_inferred"] is True
    # tables / vues SQL sans write_date mais avec ACL : restent des entités
    for name in ("mail.followers", "account.invoice.report"):
        assert real17.entity(name).kind == "entity"
    assert real17.entity("res.partner").kind == "entity"


def test_real_abstract_native_on_odoo19(real19):
    assert real19.entity("mail.thread").kind == "abstract"
    assert "kind_inferred" not in real19.entity("mail.thread").metadata
    assert real19.entity("mail.followers").kind == "entity"


def test_real_computed_fields(real17):
    p = real17.entity("res.partner")
    # ir.model.fields.compute est vide pour les champs non manuels
    dn = p.field("display_name")
    assert dn.computed and not dn.stored
    cp = p.field("commercial_partner_id")
    assert cp.computed and cp.stored  # calculé stocké : visible uniquement via fields_get.depends
    assert "is_company" in cp.metadata["depends"]
    assert not p.field("email").computed and "depends" not in p.field("email").metadata
    # one2many avec domain : fields_get renvoie depends=["child_ids.active"], pas un calcul
    ch = p.field("child_ids")
    assert ch.stored and not ch.computed and "depends" not in ch.metadata


def test_real_translate_normalized(real17, real19):
    n17 = real17.entity("product.template").field("name").metadata
    n19 = real19.entity("product.template").field("name").metadata
    assert n17["translate"] is True and "translate_mode" not in n17
    assert n19["translate"] is True and n19["translate_mode"] == "standard"
    d19 = real19.entity("product.template").field("description").metadata
    assert d19["translate_mode"] == "html_translate"


def test_real_company_dependent(real17, real19):
    # Odoo 17 : non stocké (ir.property), connu seulement via fields_get → pas « calculé »
    f17 = real17.entity("product.template").field("property_account_income_id")
    assert f17.metadata["company_dependent"] is True
    assert not f17.stored and not f17.computed
    # Odoo 19 : stocké (JSONB), exposé par ir.model.fields
    f19 = real19.entity("product.template").field("property_account_income_id")
    assert f19.metadata["company_dependent"] is True and f19.stored and not f19.computed
