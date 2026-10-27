"""V2c.1 commit 1 : compatibilité d'un fichier catalogue (INVENTÉ) avec le domaine réel de customer17.

Même domaine et mêmes 3 réponses du fournisseur que le plan de lecture V2a ; aucun LLM.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from test_catalog_read_plan import _plan, customer17  # noqa: F401 (fixture)
from uie_core.imports import (
    ColumnProfile,
    ColumnStatus,
    ColumnType,
    ImportFileError,
    ReviewReason,
    import_targets,
    match_columns,
    profile_file,
)
from uie_core.imports.matching import IMPORT_KEYS, header_key, header_variants

FIXTURES = Path(__file__).parent / "fixtures" / "imports"
IMPORTS_SRC = Path(__file__).resolve().parents[2] / "packages" / "core" / "src" / "uie_core" / "imports"


@pytest.fixture
def targets_and_spec(customer17):  # noqa: ANN001, ANN201, F811
    _, _, pack, _ = customer17
    spec = pack.semantics()
    return import_targets(_plan(customer17, IMPORT_KEYS), spec), spec


@pytest.mark.parametrize("name", ["supplier_catalog.csv", "supplier_catalog.xlsx"])
def test_profile_reads_shape_only(name):
    p = profile_file((FIXTURES / name).read_bytes(), name)
    assert p.row_count == 10
    assert [(c.name, c.detected_type) for c in p.columns] == [
        ("Product Name", ColumnType.TEXT),
        ("SKU", ColumnType.TEXT),
        ("Barcode", ColumnType.INTEGER),
        ("Price", ColumnType.DECIMAL),
        ("Category", ColumnType.TEXT),
        ("Weight", ColumnType.DECIMAL),
        ("Supplier Ref", ColumnType.TEXT),
        ("Material", ColumnType.TEXT),
    ]
    # la FORME ne contient aucune valeur ; seuls les exemples (en mémoire, pour le LLM, V2c.3) en ont
    assert "Oak" not in p.model_dump_json(exclude={"columns": {"__all__": {"examples"}}})
    assert p.columns[7].examples == ["Oak", "Steel", "Mesh"]


def test_targets_come_from_the_read_plan(targets_and_spec):
    targets, _ = targets_and_spec
    ids = [t.id for t in targets]
    for expected in (
        "product:name",
        "product:sku",
        "product:ean_gtin",
        "product:list_price",
        "product:category",
        "product:weight",
        "product:supplier_reference",  # extension confirmée
        "product:packaging_code",  # extension renommée par le fournisseur
        "supplier:supplier_price",
    ):
        assert expected in ids
    assert not any(t.field == "x_notes_internal" for t in targets)  # ignorée par le fournisseur
    assert not any(t.semantic_key == "product_template" for t in targets)
    by_id = {t.id: t for t in targets}
    assert by_id["product:list_price"].label == "Sales price"
    assert (by_id["product:category"].field, by_id["product:category"].kind) == ("categ_id", "link")


def test_reference_file_against_customer17(targets_and_spec):
    targets, spec = targets_and_spec
    profile = profile_file((FIXTURES / "supplier_catalog.csv").read_bytes(), "supplier_catalog.csv")
    result = {profile.columns[m.position].name: m for m in match_columns(profile.columns, targets, spec)}
    matched = {n: m.target_id for n, m in result.items() if m.status == ColumnStatus.MATCHED}
    assert matched == {
        "Product Name": "product:name",
        "SKU": "product:sku",
        "Barcode": "product:ean_gtin",
        "Category": "product:category",
        "Weight": "product:weight",
        "Supplier Ref": "product:supplier_reference",
    }
    price = result["Price"]
    assert (price.status, price.reason, price.target_id) == (
        ColumnStatus.REVIEW,
        ReviewReason.LOW_CONFIDENCE,
        "product:list_price",
    )
    assert result["Material"].status == ColumnStatus.UNMATCHED
    assert result["Material"].target_id is None


def test_type_mismatch_and_duplicate_target_go_to_review(targets_and_spec):
    targets, spec = targets_and_spec
    cols = [
        ColumnProfile(position=0, name="Weight", detected_type=ColumnType.TEXT, filled=3),
        ColumnProfile(position=1, name="Internal reference", detected_type=ColumnType.TEXT, filled=3),
        ColumnProfile(position=2, name="SKU", detected_type=ColumnType.TEXT, filled=3),
        ColumnProfile(position=3, name="Manufacturer reference", detected_type=ColumnType.TEXT, filled=3),
    ]
    m = match_columns(cols, targets, spec)
    assert (m[0].status, m[0].reason) == (ColumnStatus.REVIEW, ReviewReason.TYPE_MISMATCH)
    # « Internal reference » (alias de sku, 0.95) et « SKU » (0.95) : la première garde la cible
    assert (m[1].status, m[1].target_id) == (ColumnStatus.MATCHED, "product:sku")
    assert (m[2].status, m[2].reason, m[2].target_id) == (
        ColumnStatus.REVIEW,
        ReviewReason.TARGET_TAKEN,
        None,
    )
    # sens reconnu (vocabulaire) mais aucun champ pour lui sur ce système
    assert (m[3].status, m[3].reason, m[3].semantic_key) == (
        ColumnStatus.REVIEW,
        ReviewReason.NOT_IN_SYSTEM,
        "manufacturer_reference",
    )


@pytest.mark.parametrize(
    ("data", "name", "message"),
    [
        (b"a,b\n1,2\n", "file.pdf", "Please upload an .xlsx, .csv or CAMT .xml file."),
        (b"", "empty.csv", "The file is empty."),
        (b"a;b\n", "header_only.csv", "The file has no data rows."),
        (b"PK\x03\x04garbage", "broken.xlsx", "We couldn't read this file. Please check its format."),
    ],
)
def test_unreadable_files_give_a_simple_message(data, name, message):
    with pytest.raises(ImportFileError, match=message.replace(".", r"\.")):
        profile_file(data, name)


def test_type_is_the_95_percent_majority_and_minority_values_are_counted():
    rows = "\n".join(["Price,Code,Flag,Mixed"] + [f"{i}.50,{i},yes,{i}" for i in range(40)])
    data = (rows + "\n22.0016.50,A12,maybe,abc\n,,,\n").encode()
    p = profile_file(data, "shop.csv")
    got = {c.name: (c.detected_type, c.filled, c.invalid) for c in p.columns}
    assert got["Price"] == (ColumnType.DECIMAL, 41, 1)  # 40 / 41 = 97.6 % ≥ 95 % : une valeur invalide
    assert got["Code"] == (ColumnType.INTEGER, 41, 1)
    assert got["Flag"] == (ColumnType.BOOLEAN, 41, 1)
    assert got["Mixed"] == (ColumnType.INTEGER, 41, 1)
    few = profile_file(b"Price\n1.5\n2.5\nabc\n", "few.csv").columns[0]
    assert (few.detected_type, few.invalid) == (ColumnType.TEXT, 0)  # 2 / 3 < 95 % : texte


@pytest.mark.parametrize(
    ("header", "variants"),
    [
        ("Categories", ["Categories", "Category"]),
        ("Weight (lbs)", ["Weight"]),
        ("Length [cm]", ["Length"]),
        ("In stock?", ["In stock"]),
        ("Barcode:", ["Barcode"]),
        ("Product Name", ["Product Name"]),
        ("Class", ["Class"]),
        ("Status", ["Status"]),
        # V2c.4 : « Groupe / Champ » → aussi le dernier segment (compréhension de l'en-tête seulement)
        ("Google Shopping / MPN", ["Google Shopping / MPN", "MPN"]),
        ("Pricing/Sale Prices", ["Pricing/Sale Prices", "Pricing/Sale Price", "Sale Prices", "Sale Price"]),
    ],
)
def test_header_normalization_is_generic(header, variants):
    assert header_variants(header) == variants


def test_normalized_headers_and_empty_columns(targets_and_spec):
    targets, spec = targets_and_spec
    cols = [
        ColumnProfile(position=0, name="Categories", detected_type=ColumnType.TEXT, filled=3),
        ColumnProfile(position=1, name="Weight (kg)", detected_type=ColumnType.DECIMAL, filled=3),
        ColumnProfile(position=2, name="Barcode?", detected_type=ColumnType.TEXT, filled=3),
        ColumnProfile(position=3, name="Name", detected_type=ColumnType.EMPTY, filled=0),
    ]
    m = match_columns(cols, targets, spec)
    assert [(x.status, x.target_id) for x in m] == [
        (ColumnStatus.MATCHED, "product:category"),
        (ColumnStatus.MATCHED, "product:weight"),
        (ColumnStatus.MATCHED, "product:ean_gtin"),
        (ColumnStatus.EMPTY, None),  # vide : jamais de cible, même si l'en-tête correspond
    ]


def test_semicolon_csv_and_latin1_are_read():
    data = "Nom du produit;Prix\nChaise;12,50\nTable;99,00\n".encode("cp1252")
    p = profile_file(data, "fr.csv")
    assert [(c.name, c.detected_type) for c in p.columns] == [
        ("Nom du produit", ColumnType.TEXT),
        ("Prix", ColumnType.DECIMAL),
    ]


def test_imports_core_has_no_concrete_system_knowledge():
    for py in IMPORTS_SRC.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert "odoo" not in node.value.lower() and "product.template" not in node.value, py
            if isinstance(node, ast.Import | ast.ImportFrom):
                mod = getattr(node, "module", None) or ""
                assert not mod.startswith("uie_connectors"), py


# --------------------------------------------------------------------------- #
# V2c.4 — couverture du catalogue cible
# --------------------------------------------------------------------------- #
def test_import_targets_are_the_flat_product_domain_keys_not_the_display_plan(targets_and_spec, customer17):  # noqa: F811
    targets, _ = targets_and_spec
    by_key = {t.semantic_key: t for t in targets if t.group == "product"}
    expected = {
        "tags": ("product_tag_ids", "labels"),
        "cost_price": ("standard_price", "value"),
        "can_be_sold": ("sale_ok", "value"),
        "can_be_purchased": ("purchase_ok", "value"),
        "description_purchase": ("description_purchase", "value"),
        "description_internal": ("description", "value"),
        "main_image": ("image_1920", "media"),
    }
    for key, (field, kind) in expected.items():
        assert (by_key[key].field, by_key[key].kind) == (field, kind), key
    # une clé sans vrai champ sur le système n'est jamais une cible
    assert "manufacturer_reference" not in by_key and "internal_reference" not in by_key
    # jamais de variantes ni de structure (hors périmètre V2c.4)
    for key in (
        "variant_extra_price",
        "attribute_name",
        "attribute_value_name",
        "variants",
        "attribute_lines",
    ):
        assert key not in by_key, key
    # l'affichage V2a ne change pas
    display = _plan(customer17)
    assert "tags" not in display.primary.fields and "cost_price" not in display.primary.fields


def test_tags_and_image_take_text_only_and_mpn_is_understood_without_a_target(targets_and_spec):
    targets, spec = targets_and_spec
    cols = [
        ColumnProfile(position=0, name="Tags", detected_type=ColumnType.TEXT, filled=3),
        ColumnProfile(position=1, name="Image Src", detected_type=ColumnType.TEXT, filled=3),
        ColumnProfile(position=2, name="Google Shopping / MPN", detected_type=ColumnType.TEXT, filled=3),
        ColumnProfile(position=3, name="Cost per item", detected_type=ColumnType.DECIMAL, filled=3),
        ColumnProfile(position=4, name="Main image", detected_type=ColumnType.INTEGER, filled=3),
    ]
    got = {cols[m.position].name: m for m in match_columns(cols, targets, spec)}
    assert (got["Tags"].status, got["Tags"].target_id) == (ColumnStatus.MATCHED, "product:tags")
    mpn = got["Google Shopping / MPN"]
    assert (mpn.status, mpn.reason, mpn.semantic_key, mpn.target_id) == (
        ColumnStatus.REVIEW,
        ReviewReason.NOT_IN_SYSTEM,
        "manufacturer_reference",
        None,
    )
    # « Cost per item » : aucune règle (vocabulaire inchangé) ; cost_price est désormais OFFERTE (au LLM)
    assert got["Cost per item"].status == ColumnStatus.UNMATCHED
    assert any(t.id == "product:cost_price" for t in targets)
    image = got["Main image"]  # une image ne se donne que par une URL (texte)
    assert (image.status, image.reason) == (ColumnStatus.REVIEW, ReviewReason.TYPE_MISMATCH)
    assert header_key("Google Shopping / MPN") == "google shopping mpn"  # clé de mémoire inchangée


def test_last_segment_never_invents_a_target(targets_and_spec):
    """Le dernier segment aide à comprendre l'en-tête ; une cible n'existe que si le système la porte."""
    targets, spec = targets_and_spec
    cols = [
        ColumnProfile(
            position=0, name="Feed / Manufacturer Part Number", detected_type=ColumnType.TEXT, filled=2
        )
    ]
    m = match_columns(cols, targets, spec)[0]
    assert (m.status, m.semantic_key, m.target_id) == (ColumnStatus.REVIEW, "manufacturer_reference", None)
