"""Règles R11-R18 (Product Domain V1b.1) : les VRAIES règles du pack Odoo appliquées à des entités et des
champs INVENTÉS (système « shop », jamais tirés de customer17). Au moins 2 positifs et 2 négatifs par règle.

Une règle est formulée sur une propriété du champ (type, calculé / stocké, relation et rôle de la cible,
module, motif de nom) : elle doit donc reconnaître des champs qu'elle n'a jamais vus.
"""

from __future__ import annotations

import pytest
import uie_domain_packs  # noqa: F401 — enregistre les packs découverts
from uie_core.domains import (
    DomainEntityDef,
    DomainFieldDef,
    DomainKey,
    DomainPackRegistry,
    EntityRole,
    FieldGroup,
    FieldOrigin,
    MeaningStatus,
    ProtectionSpec,
    Relevance,
    classify,
)
from uie_core.registry import DataType

ITEM, VARIANT, CATEG, OPTION, VALUE, LINE, OFFER = (
    "shop.item",
    "shop.item.variant",
    "shop.category",
    "shop.option",
    "shop.option.value",
    "shop.item.option.line",
    "shop.supplier.offer",
)
ENTITIES = [
    DomainEntityDef(technical_name=name, role=role, present=True)
    for name, role in [
        (ITEM, EntityRole.PRIMARY),
        (VARIANT, EntityRole.VARIANT),
        (CATEG, EntityRole.CATEGORY),
        (OPTION, EntityRole.ATTRIBUTE),
        (VALUE, EntityRole.ATTRIBUTE_VALUE),
        (LINE, EntityRole.ATTRIBUTE_LINE),
        (OFFER, EntityRole.SUPPLIER_INFO),
    ]
]


def f(entity: str, name: str, data_type: DataType = DataType.STRING, **kw: object) -> DomainFieldDef:
    return DomainFieldDef(
        entity_technical_name=entity,
        field_technical_name=name,
        group=kw.pop("group", FieldGroup.OTHER),
        relevance=Relevance.POSSIBLE,
        label=kw.pop("label", name.replace("_", " ").title()),
        data_type=data_type.value,
        native_type=kw.pop("native_type", None),
        **kw,
    )


def run(fields: list[DomainFieldDef], protected: list[str] = ()) -> dict:  # noqa: ANN001
    spec = DomainPackRegistry.resolve(DomainKey.PRODUCT, "odoo", "17").semantics()
    protection = ProtectionSpec(fields=list(protected), origins=[FieldOrigin.CUSTOM], media=True)
    spec = spec.model_copy(update={"protection": protection})
    return {d.path: d for d in classify(fields, ENTITIES, spec)}


def rule(d: dict, path: str) -> str:
    return d[path].rule_id


def excluded(d: dict, path: str, rule_id: str) -> bool:
    return d[path].rule_id == rule_id and d[path].relevance == Relevance.NOT_RELEVANT


# --------------------------------------------------------------------------- #
def test_r11_back_reference():
    fields = [
        f(
            OPTION,
            "line_ids",
            DataType.COLLECTION,
            native_type="one2many",
            relation_target=LINE,
            inverse_field="option_id",
        ),
        f(LINE, "option_id", DataType.REFERENCE, native_type="many2one", relation_target=OPTION),
        f(
            VALUE,
            "used_in_line_ids",
            DataType.COLLECTION,
            native_type="many2many",
            relation_target=LINE,
            relation_table="shop_line_value_rel",
        ),
        f(
            LINE,
            "value_ids",
            DataType.COLLECTION,
            native_type="many2many",
            relation_target=VALUE,
            relation_table="shop_line_value_rel",
        ),
        # négatifs : protégé (porte la définition des variantes), many2many réflexif, cible hors domaine,
        # inverse qui ne pointe pas vers l'entité courante
        f(
            ITEM,
            "option_line_ids",
            DataType.COLLECTION,
            native_type="one2many",
            relation_target=LINE,
            inverse_field="item_id",
        ),
        f(LINE, "item_id", DataType.REFERENCE, native_type="many2one", relation_target=ITEM),
        f(
            ITEM,
            "alternative_ids",
            DataType.COLLECTION,
            native_type="many2many",
            relation_target=ITEM,
            relation_table="shop_item_alternative_rel",
        ),
        f(
            VARIANT,
            "picking_ids",
            DataType.COLLECTION,
            native_type="one2many",
            relation_target="shop.picking",
            inverse_field="variant_id",
        ),
        f(
            CATEG,
            "offer_ids",
            DataType.COLLECTION,
            native_type="one2many",
            relation_target=OFFER,
            inverse_field="category_id",
        ),
        f(OFFER, "category_id", DataType.REFERENCE, native_type="many2one", relation_target=ITEM),
    ]
    d = run(fields, protected=[f"{ITEM}.option_line_ids"])
    assert excluded(d, f"{OPTION}.line_ids", "R11")
    assert d[f"{OPTION}.line_ids"].reason.endswith(f"carried by {LINE}.option_id")
    assert excluded(d, f"{VALUE}.used_in_line_ids", "R11")
    assert d[f"{ITEM}.option_line_ids"].relevance != Relevance.NOT_RELEVANT
    assert rule(d, f"{ITEM}.alternative_ids") != "R11"
    assert rule(d, f"{VARIANT}.picking_ids") == "R04"  # hors domaine : déjà une autre raison
    assert rule(d, f"{CATEG}.offer_ids") != "R11"


def test_r12_operational_quantity():
    fields = [
        f(ITEM, "reserved_qty", DataType.DECIMAL, native_type="float"),
        f(VARIANT, "qty_forecast_week", DataType.DECIMAL, native_type="float"),
        f(ITEM, "stock_available", DataType.DECIMAL, native_type="float"),
        f(ITEM, "nbr_orders", DataType.INTEGER, native_type="integer"),
        f(LINE, "option_count", DataType.INTEGER, native_type="integer", computed=True),
        f(OPTION, "number_of_items", DataType.INTEGER, native_type="integer", computed=True, stored=False),
        # négatifs : jeton qty sans mouvement, type non numérique, number_ stocké, quantité du vocabulaire
        f(ITEM, "pack_qty", DataType.DECIMAL, native_type="float"),
        f(ITEM, "reserved_qty_note", DataType.STRING, native_type="char"),
        f(OPTION, "number_of_columns", DataType.INTEGER, native_type="integer"),
        f(OFFER, "min_qty", DataType.DECIMAL, native_type="float"),
    ]
    d = run(fields)
    for name in (
        "shop.item.reserved_qty",
        "shop.item.variant.qty_forecast_week",
        "shop.item.stock_available",
        "shop.item.nbr_orders",
        "shop.item.option.line.option_count",
        "shop.option.number_of_items",
    ):
        assert excluded(d, name, "R12"), name
    assert rule(d, f"{ITEM}.pack_qty") == "R10"
    assert rule(d, f"{ITEM}.reserved_qty_note") == "R10"
    assert rule(d, f"{OPTION}.number_of_columns") == "R10"
    assert d[f"{OFFER}.min_qty"].relevance == Relevance.RELEVANT  # R08 : clé supplier_min_qty


def test_r13_replenishment_and_operations():
    fields = [
        f(ITEM, "reordering_trigger", DataType.STRING, native_type="selection"),
        f(ITEM, "produce_delay", DataType.DECIMAL, native_type="float"),
        f(ITEM, "description_picking_note", DataType.TEXT, native_type="text"),
        f(ITEM, "warehouse_note", DataType.STRING, native_type="char", modules=["stock"]),
        f(VARIANT, "lot_rule", DataType.STRING, native_type="selection", modules=["stock", "stock_account"]),
        # négatifs : module stock mais protégé, module mixte, nom proche sans motif
        f(ITEM, "net_weight", DataType.DECIMAL, native_type="float", modules=["stock"]),
        f(ITEM, "shelf_life_days", DataType.INTEGER, native_type="integer", modules=["stock", "product"]),
        f(ITEM, "delivery_delay_note", DataType.STRING, native_type="char"),
    ]
    d = run(fields, protected=[f"{ITEM}.net_weight"])
    for name in ("reordering_trigger", "produce_delay", "description_picking_note", "warehouse_note"):
        assert excluded(d, f"{ITEM}.{name}", "R13"), name
    assert excluded(d, f"{VARIANT}.lot_rule", "R13")
    assert d[f"{ITEM}.net_weight"].relevance != Relevance.NOT_RELEVANT
    assert rule(d, f"{ITEM}.shelf_life_days") == "R10"
    assert rule(d, f"{ITEM}.delivery_delay_note") == "R10"


def test_r14_commercial_policy_and_warnings():
    fields = [
        f(ITEM, "return_policy", DataType.ENUM, native_type="selection"),
        f(ITEM, "billing_method", DataType.ENUM, native_type="selection"),
        f(ITEM, "order_warn", DataType.ENUM, native_type="selection"),
        f(ITEM, "order_warn_msg", DataType.TEXT, native_type="text"),
        f(ITEM, "ledger_hint", DataType.STRING, native_type="char", modules=["account"]),
        # négatifs : méthode de coût (valorisation, R16), booléen du catalogue protégé, lien *_method_id
        f(ITEM, "cost_method", DataType.ENUM, native_type="selection"),
        f(ITEM, "is_sellable", DataType.BOOLEAN, native_type="boolean", modules=["account"]),
        f(ITEM, "shipping_method_label", DataType.STRING, native_type="char"),
    ]
    d = run(fields, protected=[f"{ITEM}.is_sellable"])
    for name in ("return_policy", "billing_method", "order_warn", "order_warn_msg", "ledger_hint"):
        assert excluded(d, f"{ITEM}.{name}", "R14"), name
    assert rule(d, f"{ITEM}.cost_method") == "R16"
    assert d[f"{ITEM}.is_sellable"].relevance != Relevance.NOT_RELEVANT
    assert rule(d, f"{ITEM}.shipping_method_label") == "R10"


def test_r15_orm_and_ui_mechanics():
    fields = [
        f(CATEG, "parent_path", DataType.STRING, native_type="char"),
        f(ITEM, "sequence", DataType.INTEGER, native_type="integer"),
        f(ITEM, "priority", DataType.ENUM, native_type="selection"),
        f(ITEM, "valid_combination_ids", DataType.COLLECTION, native_type="many2many", relation_target=LINE),
        f(ITEM, "price_tooltip", DataType.STRING, native_type="char", computed=True, stored=False),
        f(CATEG, "spec_layout", DataType.JSON, native_type="properties_definition"),
        # négatifs : sequence protégée (ordre d'affichage des options), nom proche, display_type protégé
        f(OPTION, "sequence", DataType.INTEGER, native_type="integer"),
        f(ITEM, "sequence_number", DataType.STRING, native_type="char"),
        f(OPTION, "display_type", DataType.ENUM, native_type="selection"),
    ]
    d = run(fields, protected=[f"{OPTION}.sequence", f"{OPTION}.display_type"])
    for path in (
        f"{CATEG}.parent_path",
        f"{ITEM}.sequence",
        f"{ITEM}.priority",
        f"{ITEM}.valid_combination_ids",
        f"{ITEM}.price_tooltip",
        f"{CATEG}.spec_layout",
    ):
        assert excluded(d, path, "R15"), path
    assert d[f"{OPTION}.sequence"].relevance != Relevance.NOT_RELEVANT
    assert d[f"{OPTION}.display_type"].relevance != Relevance.NOT_RELEVANT
    assert rule(d, f"{ITEM}.sequence_number") == "R10"


def test_r16_accounting_valuation():
    fields = [
        f(ITEM, "valuation", DataType.ENUM, native_type="selection"),
        f(CATEG, "property_cost_method", DataType.ENUM, native_type="selection"),
        f(VARIANT, "value_svl", DataType.DECIMAL, native_type="float"),
        f(VARIANT, "avg_cost", DataType.DECIMAL, native_type="monetary"),
        f(ITEM, "property_account_expense_code", DataType.STRING, native_type="char"),
        # négatifs : ce sont des prix, pas des méthodes de valorisation
        f(ITEM, "standard_price", DataType.DECIMAL, native_type="float"),
        f(ITEM, "list_price", DataType.DECIMAL, native_type="float"),
        f(ITEM, "valuation_note", DataType.TEXT, native_type="text"),
    ]
    d = run(fields)
    for path in (
        f"{ITEM}.valuation",
        f"{CATEG}.property_cost_method",
        f"{VARIANT}.value_svl",
        f"{VARIANT}.avg_cost",
        f"{ITEM}.property_account_expense_code",
    ):
        assert excluded(d, path, "R16"), path
    assert d[f"{ITEM}.standard_price"].relevance == Relevance.RELEVANT
    assert d[f"{ITEM}.list_price"].relevance == Relevance.RELEVANT
    assert rule(d, f"{ITEM}.valuation_note") == "R10"


def test_r17_category_operational_defaults():
    fields = [
        f(CATEG, "property_removal_default", DataType.STRING, native_type="char"),
        f(CATEG, "default_putaway_zone", DataType.STRING, native_type="char"),
        f(CATEG, "pack_reserve_mode", DataType.ENUM, native_type="selection"),
        # négatifs : même motif hors catégorie, champs de la catégorie sans motif
        f(ITEM, "property_brand", DataType.STRING, native_type="char"),
        f(CATEG, "reserve", DataType.BOOLEAN, native_type="boolean"),
        f(CATEG, "display_label", DataType.STRING, native_type="char"),
    ]
    d = run(fields)
    for name in ("property_removal_default", "default_putaway_zone", "pack_reserve_mode"):
        assert excluded(d, f"{CATEG}.{name}", "R17"), name
    assert rule(d, f"{ITEM}.property_brand") == "R10"
    assert rule(d, f"{CATEG}.reserve") == "R10"
    assert rule(d, f"{CATEG}.display_label") == "R10"


def test_r18_standard_related_copy_follows_its_source():
    link = f(VARIANT, "item_id", DataType.REFERENCE, native_type="many2one", relation_target=ITEM)
    fields = [
        link,
        f(ITEM, "catalog_title", group=FieldGroup.IDENTITY, listed=True),
        f(VARIANT, "catalog_title", related="item_id.catalog_title", computed=True, stored=False),
        f(ITEM, "return_policy", DataType.ENUM, native_type="selection"),
        f(VARIANT, "return_policy", DataType.ENUM, native_type="selection", related="item_id.return_policy"),
        # négatifs : copie protégée, copie custom (MIRROR), related via un lien hors domaine, chemin long
        f(ITEM, "gross_weight", DataType.DECIMAL, native_type="float"),
        f(VARIANT, "gross_weight", DataType.DECIMAL, native_type="float", related="item_id.gross_weight"),
        f(ITEM, "x_colour_code", origin=FieldOrigin.CUSTOM),
        f(VARIANT, "x_colour_code", origin=FieldOrigin.CUSTOM, related="item_id.x_colour_code"),
        f(
            VARIANT,
            "warehouse_id",
            DataType.REFERENCE,
            native_type="many2one",
            relation_target="shop.warehouse",
        ),
        f(VARIANT, "warehouse_code", related="warehouse_id.code"),
        f(VARIANT, "category_label", related="item_id.categ_id.label"),
    ]
    d = run(fields, protected=[f"{VARIANT}.gross_weight"])
    title = d[f"{VARIANT}.catalog_title"]
    assert (title.rule_id, title.relevance, title.status) == ("R18", Relevance.RELEVANT, MeaningStatus.KNOWN)
    assert title.reason == f"Reflects {ITEM}.catalog_title" and title.mirror_of == f"{ITEM}.catalog_title"
    policy = d[f"{VARIANT}.return_policy"]
    assert (policy.rule_id, policy.relevance) == ("R18", Relevance.NOT_RELEVANT)  # suit l'exclusion R14
    assert rule(d, f"{VARIANT}.gross_weight") != "R18" and d[f"{VARIANT}.gross_weight"].mirror_of is None
    assert rule(d, f"{VARIANT}.x_colour_code") == "MIRROR"
    assert rule(d, f"{VARIANT}.warehouse_code") == "R10"
    assert rule(d, f"{VARIANT}.category_label") == "R10"


@pytest.mark.parametrize("name", ["x_reserved_qty", "x_return_policy", "x_sequence", "x_valuation"])
def test_new_rules_never_exclude_a_custom_field(name):
    d = run(
        [f(ITEM, name, DataType.DECIMAL, native_type="float", origin=FieldOrigin.CUSTOM, modules=["stock"])]
    )
    assert d[f"{ITEM}.{name}"].relevance != Relevance.NOT_RELEVANT and d[f"{ITEM}.{name}"].pending
