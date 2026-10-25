"""Champs protégés (Product Domain V1b.1) : liste de sûreté du pack, jamais NOT_RELEVANT par une règle.

Le moteur réel (règles du pack Odoo + vocabulaire) est appliqué aux dumps standard complets 17 / 18 / 19
(docs/dumps) et à l'instance client (fixture odoo17_customer_instance.json, domaine effectif persisté) :
aucun champ protégé présent, aucune extension custom, aucun média n'est exclu par une règle.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import uie_domain_packs  # noqa: F401 — enregistre les packs découverts
from test_catalog_semantics_odoo import analyzed, world  # noqa: F401 — fixtures partagées
from uie_connectors.odoo import OdooSchemaLoader
from uie_core.domains import (
    DecisionSource,
    DomainEntityDef,
    DomainFieldDef,
    DomainKey,
    DomainPackRegistry,
    EntityRole,
    FieldGroup,
    FieldOrigin,
    Relevance,
    classify,
)
from uie_domain_packs.catalog.spec import load_protected_fields_spec

ROOT = Path(__file__).resolve().parents[2]
DUMPS = ROOT / "docs" / "dumps"
PROTECTED = load_protected_fields_spec(ROOT / "domain-packs" / "catalog" / "odoo" / "protected_fields.yaml")


def _pack(version: str = "17"):  # noqa: ANN202
    return DomainPackRegistry.resolve(DomainKey.PRODUCT, "odoo", version)


def _classify_dump(version: str):  # noqa: ANN202
    pack = _pack(version)
    resolved = pack.resolve(OdooSchemaLoader({"json": DUMPS / f"odoo{version}_ref.json"}).build_snapshot())
    fields = {f.path: f for f in resolved.fields if f.present}
    return fields, {d.path: d for d in classify(resolved.fields, resolved.entities, pack.semantics())}


def _excluded_by_rule(decision) -> bool:  # noqa: ANN001
    return decision.relevance == Relevance.NOT_RELEVANT and decision.source == DecisionSource.RULE


def test_protected_file_is_the_pack_protection():
    protection = _pack().semantics().protection
    assert protection.fields == PROTECTED.paths()
    assert protection.origins == [FieldOrigin.CUSTOM] and protection.media
    assert "product.template.company_id" in protection.fields  # relation hors scope, mais du catalogue


@pytest.mark.parametrize("version", ["17", "18", "19"])
def test_protected_fields_never_excluded_by_rule(version):
    fields, decisions = _classify_dump(version)
    present = [p for p in PROTECTED.paths() if p in fields]
    assert len(present) >= 75  # la liste couvre bien le schéma réel (quelques champs n'existent pas en 19)
    excluded = sorted(p for p in present if _excluded_by_rule(decisions[p]))
    assert excluded == []
    media = sorted(p for p, f in fields.items() if f.is_media and _excluded_by_rule(decisions[p]))
    assert media == []


def test_protected_fields_never_excluded_on_customer_instance(world, analyzed):  # noqa: F811
    _, bindings = analyzed
    present = [p for p in PROTECTED.paths() if p in bindings]
    assert present, "le domaine effectif de l'instance contient des champs protégés"
    excluded = sorted(
        p
        for p in [
            *present,
            *(p for p, b in bindings.items() if b.origin == FieldOrigin.CUSTOM or b.field.is_media),
        ]
        if bindings[p].relevance == Relevance.NOT_RELEVANT and bindings[p].source == DecisionSource.RULE
    )
    assert excluded == []


# --------------------------------------------------------------------------- #
# Champs inventés : une règle d'exclusion qui correspond est sautée, jamais appliquée
# --------------------------------------------------------------------------- #
ENTITIES = [
    DomainEntityDef(technical_name="product.template", role=EntityRole.PRIMARY, present=True),
    DomainEntityDef(technical_name="product.product", role=EntityRole.VARIANT, present=True),
]


def _field(name: str, **kw) -> DomainFieldDef:  # noqa: ANN003
    base = {
        "entity_technical_name": "product.template",
        "group": FieldGroup.OTHER,
        "relevance": Relevance.POSSIBLE,
    }
    return DomainFieldDef(field_technical_name=name, **{**base, **kw})


def test_custom_fields_never_excluded_by_rule():
    custom = FieldOrigin.CUSTOM
    fields = [
        # R04 : lien vers une entité hors catalogue
        _field(
            "x_warehouse_id",
            origin=custom,
            data_type="reference",
            native_type="many2one",
            relation_target="stock.warehouse",
        ),
        # R06 : helper calculé non stocké
        _field(
            "x_line_count",
            origin=custom,
            data_type="integer",
            native_type="integer",
            computed=True,
            stored=False,
        ),
        # R07 : drapeau d'interface
        _field("x_price_visible", origin=custom, data_type="boolean", native_type="boolean"),
        # R01 : champ marqué technique
        _field("x_sync_token", origin=custom, data_type="string", native_type="char", technical=True),
    ]
    # témoins : les mêmes champs, en standard, sont bien exclus (la protection est la seule différence)
    standard = [
        f.model_copy(
            update={"field_technical_name": f.field_technical_name[2:], "origin": FieldOrigin.STANDARD}
        )
        for f in fields
    ]
    decisions = {d.path: d for d in classify(fields + standard, ENTITIES, _pack().semantics())}
    for f in fields:
        d = decisions[f.path]
        assert d.relevance != Relevance.NOT_RELEVANT, f.path
        assert d.pending, f"{f.path} : une extension custom est posée au supplier"
    assert all(_excluded_by_rule(decisions[f.path]) for f in standard)


def test_media_never_excluded_by_rule():
    fields = [
        _field("image_512", data_type="binary", native_type="binary", is_media=True, technical=True),
        _field(
            "image_1024",
            data_type="binary",
            native_type="binary",
            is_media=True,
            computed=True,
            stored=False,
            related="product_variant_id.image_1024",
        ),
    ]
    witness = [
        f.model_copy(update={"field_technical_name": "blob_" + f.field_technical_name, "is_media": False})
        for f in fields
    ]
    decisions = {d.path: d for d in classify(fields + witness, ENTITIES, _pack().semantics())}
    for f in fields:
        assert decisions[f.path].relevance == Relevance.RELEVANT, f.path
    assert _excluded_by_rule(decisions["product.template.blob_image_512"])  # témoin technique exclu


@pytest.mark.parametrize("version", ["17", "18", "19"])
def test_protected_fields_declared_by_the_pack_carry_a_semantic_key(version):
    """§4 : un champ protégé déclaré par le pack est KNOWN avec une clé du vocabulaire (pas seulement
    « reconnu ») ; exception : type en 17, où detailed_type porte product_type (clé single)."""
    fields, decisions = _classify_dump(version)
    exceptions = {"product.template.type"} if version == "17" else set()
    without_key = sorted(
        p for p in PROTECTED.paths() if p in fields and fields[p].listed and not decisions[p].semantic_key
    )
    assert without_key == sorted(exceptions)
