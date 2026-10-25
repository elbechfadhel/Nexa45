"""Domain pack catalog : vocabulaire, validation des fichiers, résolution Odoo (sans base de données).

Fixtures réduites (packages/connectors/tests/fixtures) + dumps réels complets (docs/dumps) pour la
règle de version 17 / 19.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import uie_domain_packs  # noqa: F401 — enregistre les packs découverts
from uie_connectors.odoo import OdooSchemaLoader
from uie_core.domains import (
    DomainKey,
    DomainPackError,
    DomainPackRegistry,
    EntityRole,
    FieldGroup,
    MediaRole,
    Relevance,
)
from uie_domain_packs.catalog import discover_catalog_packs, load_pack_spec, load_vocabulary

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "packages" / "connectors" / "tests" / "fixtures"
DUMPS = ROOT / "docs" / "dumps"


def _resolve(dump: Path, version: str):  # noqa: ANN202
    snap = OdooSchemaLoader({"json": dump}).build_snapshot()
    return DomainPackRegistry.resolve(DomainKey.PRODUCT, "odoo", version).resolve(snap)


def _fields(resolved):  # noqa: ANN001, ANN202
    return {f"{f.entity_technical_name}.{f.field_technical_name}": f for f in resolved.fields}


@pytest.fixture(scope="module")
def ref17():  # noqa: ANN201
    return _resolve(FIX / "odoo17_ref.json", "17")


# --------------------------------------------------------------------------- #
# Vocabulaire et validation
# --------------------------------------------------------------------------- #
def test_vocabulary_is_controlled():
    v = load_vocabulary()
    keys = {k.key: k for k in v.keys}
    assert v.domain_key == DomainKey.PRODUCT and len(keys) == len(v.keys)
    assert {k.group for k in v.keys} == {
        FieldGroup.IDENTITY,
        FieldGroup.COMMERCIAL,
        FieldGroup.DESCRIPTION,
        FieldGroup.CLASSIFICATION,
        FieldGroup.LOGISTICS,
        FieldGroup.VARIANTS,
        FieldGroup.SUPPLIER,
        FieldGroup.MEDIA,
    }
    assert {"sku", "ean_gtin", "supplier_reference", "main_image", "additional_images"} <= set(keys)
    assert keys["additional_images"].cardinality == "multi" and keys["sku"].required_for_catalog
    # V1b : alias, motifs, types attendus, rôle de la cible ; clés ajoutées dans les 8 groupes
    assert {"manufacturer_reference", "packaging_code"} <= set(keys)
    assert keys["category"].relation_target_roles == [EntityRole.CATEGORY]
    assert "supplier reference" in keys["supplier_reference"].aliases and keys["packaging_code"].patterns
    assert all(k.label and k.types for k in v.keys)


def test_pack_semantics_are_loaded_and_versioned():
    pack = DomainPackRegistry.resolve(DomainKey.PRODUCT, "odoo", "17")
    s = pack.semantics()
    # ordre d'évaluation (V1b.1) : R18 avant le vocabulaire, exclusions R11-R17 après lui
    assert [r.id for r in s.rules] == [
        *(f"R{i:02d}" for i in range(1, 8)),
        *("R18", "R08", "R11", "R12", "R15", "R16", "R17", "R13", "R14", "R09", "R10"),
    ]
    assert s.version == f"{pack.definition_version}+vocabulary-{s.vocabulary.version}"
    assert s.field_keys["product.template.default_code"] == "sku"
    assert s.field_keys["product.template.image_1920"] == "main_image"
    assert DomainPackRegistry.vocabulary(DomainKey.PRODUCT) == s.vocabulary


def test_pack_key_unknown_to_the_vocabulary_is_rejected(tmp_path):
    base = tmp_path / "catalog"
    (base / "demo").mkdir(parents=True)
    (base / "vocabulary.yaml").write_text(
        (ROOT / "domain-packs" / "catalog" / "vocabulary.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (base / "demo" / "product_domain.yaml").write_text(
        "domain: PRODUCT\nsystem: demo\ndefinition_version: '1'\n"
        "entities:\n  - {technical_name: a, role: PRIMARY}\n"
        "fields:\n  - {entity: a, field: x, group: identity, key: not_a_key}\n"
        "semantics:\n  rules:\n    - {id: R10, relevance: POSSIBLE, reason: default}\n",
        encoding="utf-8",
    )
    with pytest.raises(DomainPackError, match="not_a_key"):
        discover_catalog_packs(tmp_path)


def test_pack_is_registered_from_its_file():
    pack = DomainPackRegistry.resolve(DomainKey.PRODUCT, "odoo", "17")
    assert pack.name == "catalog/odoo/product_domain.yaml" and pack.definition_version
    assert "product.template" in pack.scope_entities()
    with pytest.raises(DomainPackError, match="ne gère pas"):
        DomainPackRegistry.resolve(DomainKey.PRODUCT, "odoo", "12")


def test_invalid_pack_file_is_rejected(tmp_path):
    bad = tmp_path / "catalog" / "demo" / "product_domain.yaml"
    bad.parent.mkdir(parents=True)
    bad.write_text(
        "domain: PRODUCT\nsystem: demo\ndefinition_version: '1'\n"
        "entities:\n  - {technical_name: a, role: NOT_A_ROLE}\n",
        encoding="utf-8",
    )
    with pytest.raises(DomainPackError, match="invalide"):
        load_pack_spec(bad)
    bad.write_text(
        "domain: PRODUCT\nsystem: demo\ndefinition_version: '1'\n"
        "entities:\n  - {technical_name: a, role: PRIMARY}\n"
        "fields:\n  - {entity: b, field: x, group: identity}\n",
        encoding="utf-8",
    )
    with pytest.raises(DomainPackError, match="hors scope"):
        discover_catalog_packs(tmp_path)


# --------------------------------------------------------------------------- #
# 10-11. Rôles et médias (fixture Odoo 17)
# --------------------------------------------------------------------------- #
def test_odoo_roles(ref17):
    roles = {e.technical_name: (e.role, e.present) for e in ref17.entities}
    assert roles["product.template"] == (EntityRole.PRIMARY, True)
    assert roles["product.product"] == (EntityRole.VARIANT, True)
    assert roles["product.category"] == (EntityRole.CATEGORY, True)
    # fixture réduite : entités d'attributs absentes → present=false, jamais une erreur
    assert roles["product.attribute"] == (EntityRole.ATTRIBUTE, False)
    fields = _fields(ref17)
    assert fields["product.template.default_code"].group == FieldGroup.IDENTITY
    assert fields["product.template.list_price"].relevance == Relevance.RELEVANT
    assert fields["product.template.id"].group == FieldGroup.TECHNICAL
    assert fields["product.template.categ_id"].relation_target == "product.category"
    assert not any(f.startswith(("res.partner", "account.move")) for f in fields)


def test_odoo_media(ref17):
    fields = _fields(ref17)
    assert fields["product.template.image_1920"].media_role == MediaRole.MAIN
    for size in (1024, 512, 256, 128):
        f = fields[f"product.template.image_{size}"]
        assert (f.group, f.is_media, f.media_role) == (FieldGroup.MEDIA, True, MediaRole.DERIVATIVE)
    variant = [f for p, f in fields.items() if p.startswith("product.product.image_variant_")]
    assert len(variant) == 5 and {f.media_role for f in variant} == {MediaRole.VARIANT}
    assert fields["product.template.name"].is_media is False


# --------------------------------------------------------------------------- #
# 15. Règle de version sur les schémas réels complets
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(("version", "detailed_type"), [("17", True), ("19", False)])
def test_real_odoo_versions_resolve(version, detailed_type):
    resolved = _resolve(DUMPS / f"odoo{version}_ref.json", version)
    assert all(e.present for e in resolved.entities)  # les 9 entités du catalogue existent en 17 et 19
    fields = _fields(resolved)
    assert fields["product.template.detailed_type"].present is detailed_type
    assert fields["product.template.image_1920"].media_role == MediaRole.MAIN
    absent = {p for p, f in fields.items() if not f.present}
    assert ("product.template.uom_po_id" in absent) is (version == "19")  # supprimé en 19
    assert ("product.template.uom_ids" in absent) is (version == "17")  # introduit en 19
