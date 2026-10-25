"""Contrôles avant l'import du Wizard : codes-barres, prix manquants, références (catégories, unités),
avec les lignes concernées et une solution simple. Fonctions pures + préparation complète du fichier."""

from __future__ import annotations

import io

import polars as pl
from uie_core.wizard import MappingDecision, TargetField, TargetProposal
from uie_core.wizard.mapping import prepare, render_prepared
from uie_core.wizard.quality import (
    check_barcodes,
    check_reference,
    gs1_valid,
    is_barcode,
    missing_prices,
)


def _f(name: str, type_: str, label: str | None = None, **kw: object) -> TargetField:
    return TargetField(name=name, label=label or name.title(), type=type_, **kw)  # type: ignore[arg-type]


BARCODE = _f("barcode", "char", "Barcode")
PRICE = _f("list_price", "float", "Sales Price")
CATEGORY = _f("categ_id", "many2one", "Product Category", reference=True, relation="product.category")


# --------------------------------------------------------------------------- #
# Codes-barres
# --------------------------------------------------------------------------- #
def test_gs1_check_digit_and_length() -> None:
    assert gs1_valid("4006381333931")  # EAN-13
    assert gs1_valid("036000291452")  # UPC-A
    assert gs1_valid("00123457")  # EAN-8, zéros en tête
    assert not gs1_valid("4006381333932")  # chiffre de contrôle faux
    assert not gs1_valid("12345")  # longueur
    assert not gs1_valid("885909000000")  # 8.85909e+11 relu par un tableur : chiffres perdus


def test_a_barcode_field_is_found_by_its_name_or_label_never_by_system() -> None:
    assert is_barcode(BARCODE) and is_barcode(_f("x_code", "char", "EAN")) and is_barcode(_f("gtin", "char"))
    assert not is_barcode(PRICE) and not is_barcode(_f("default_code", "char", "Internal Reference"))


def test_multiple_invalid_and_duplicate_barcodes_are_not_written_and_listed_with_their_rows() -> None:
    values = [
        "4006381333931",  # 1 : en double avec la ligne 4
        "4006381333931, 036000291452",  # 2 : deux codes
        "12345",  # 3 : invalide
        "4006381333931",  # 4 : en double
        "ABC-1",  # 5 : code interne, gardé (pas une forme GS1)
        "",  # 6 : vide
        "036000291452",  # 7 : valide
        "885909000000",  # 8 : invalide, mais ligne exclue (autre devise) : jamais contrôlée
    ]
    out, issues = check_barcodes(BARCODE, "upc", values, skip={7})
    assert out == ["", "", "", "", "ABC-1", "", "036000291452", "885909000000"]
    by = {i.kind: i for i in issues}
    assert by["barcode_multiple"].rows == [2] and by["barcode_invalid"].rows == [3]
    assert by["barcode_duplicate"].rows == [1, 4] and by["barcode_duplicate"].count == 2
    assert all(i.severity == "not_written" and i.fix and i.column == "upc" for i in issues)
    assert "format the column as text" in by["barcode_invalid"].fix  # notation scientifique d'un tableur


def test_a_barcode_damaged_by_excel_is_named_as_such_never_as_several_codes() -> None:
    values = ["4,00638E+12", "8.85909E+11", "4006381333931", "4006381333931, 036000291452"]
    out, issues = check_barcodes(BARCODE, "EAN", values, skip=set())
    assert out == ["", "", "4006381333931", ""]  # jamais reconstruit
    by = {i.kind: i for i in issues}
    assert set(by) == {"barcode_damaged", "barcode_multiple"}
    assert by["barcode_damaged"].rows == [1, 2] and by["barcode_multiple"].rows == [4]
    assert "damaged by Excel" in by["barcode_damaged"].message
    assert "format the barcode column as text" in by["barcode_damaged"].fix


# --------------------------------------------------------------------------- #
# Prix
# --------------------------------------------------------------------------- #
def test_missing_prices_are_listed_never_invented() -> None:
    series = {"list_price": ["10", "", "", "12"]}
    # ligne 3 : le client a écrit une valeur illisible, déjà signalée ailleurs → pas comptée deux fois
    issues = missing_prices([PRICE], series, {"list_price": "price"}, [0, 1, 2, 3], {"list_price": {0, 2, 3}})
    assert [(i.kind, i.rows, i.severity, i.column) for i in issues] == [
        ("missing_price", [2], "to_check", "price")
    ]
    assert "no amount is invented" in issues[0].message and series["list_price"][1] == ""


def test_a_file_without_any_price_column_is_said_once() -> None:
    issues = missing_prices([PRICE, _f("name", "char")], {"name": ["A", "B"]}, {}, [0, 1], {})
    assert [(i.kind, i.count, i.rows) for i in issues] == [("no_price_column", 2, [])]
    assert missing_prices([_f("name", "char")], {"name": ["A"]}, {}, [0], {}) == []  # rien à vendre


# --------------------------------------------------------------------------- #
# Références (catégories, unités)
# --------------------------------------------------------------------------- #
def test_references_without_a_connected_system_are_flagged_unverified() -> None:
    issues = check_reference(CATEGORY, "categories", {"Shoes": [0, 2], "Lamps": [1]}, None, "Odoo 18")
    assert [(i.kind, i.rows, i.count, i.severity) for i in issues] == [
        ("reference_unverified", [1, 2, 3], 3, "to_check")
    ]
    assert "Connect your Odoo 18" in issues[0].fix


def test_unknown_references_get_their_rows_and_a_close_existing_value() -> None:
    known = ["All / Saleable", "Shoes"]
    issues = check_reference(
        CATEGORY, "cat", {"Shoes": [0], "all / saleable": [1, 3], "Lamps": [2]}, known, "Odoo 18"
    )
    (issue,) = issues
    assert (issue.kind, issue.rows, issue.count) == ("reference_unknown", [2, 3, 4], 3)
    assert "“all / saleable” → “All / Saleable”" in issue.fix  # proposé, jamais appliqué
    assert check_reference(CATEGORY, "cat", {"Shoes": [0]}, known, "Odoo 18") == []
    # lecture plafonnée : une valeur absente n'est pas prouvée inconnue → non vérifiée
    (capped,) = check_reference(CATEGORY, "cat", {"Lamps": [0]}, known, "Odoo 18", complete=False)
    assert capped.kind == "reference_unverified"


# --------------------------------------------------------------------------- #
# Préparation complète : lignes, solutions, fichier « Problems »
# --------------------------------------------------------------------------- #
SHOP = b"\n".join(
    [
        b"name,price,ean",
        b"Oak tray,39.9,4006381333931",
        b"Birch crate,,4006381333931",
        b"Pine box,12,4006381333932",
        b"Ash cup,null,036000291452;00123457",
        b"",
    ]
)


def test_prepare_reports_barcodes_and_prices_with_rows_and_fixes() -> None:
    proposal = TargetProposal(
        title="Products",
        summary="",
        configuration=[],
        rows=[],
        fields=[_f("name", "char", "Name", required=True), PRICE, BARCODE],
    )
    decisions = [
        MappingDecision(position=0, target="name"),
        MappingDecision(position=1, target="list_price"),
        MappingDecision(position=2, target="barcode"),
    ]
    summary, rows, pairs = prepare(proposal, SHOP, "shop.csv", decisions)
    found = {i.kind: (i.rows, i.severity) for i in summary.value_issues}
    assert found == {
        "barcode_multiple": ([4], "not_written"),
        "barcode_invalid": ([3], "not_written"),
        "barcode_duplicate": ([1, 2], "not_written"),
        "missing_price": ([2, 4], "to_check"),  # « null » = aucun prix
    }
    assert [r[2] for r in rows] == ["", "", "", ""] and [r[1] for r in rows] == ["39.9", "", "12", ""]
    content = render_prepared(proposal, summary, rows, pairs)
    problems = pl.read_excel(io.BytesIO(content), sheet_name="Problems", infer_schema_length=0)
    assert problems.columns == [
        "Type",
        "Target field",
        "Your column",
        "Problem",
        "Rows in your file",
        "How to fix",
    ]
    duplicate = problems.filter(pl.col("Problem").str.contains("share the same"))
    assert duplicate["Rows in your file"].to_list() == ["1, 2"] and duplicate["Type"].to_list() == [
        "Not written"
    ]
    assert problems["How to fix"].null_count() == 0


def test_duplicate_rows_on_the_imported_columns_are_imported_once_and_listed() -> None:
    proposal = TargetProposal(
        title="Products",
        summary="",
        configuration=[],
        rows=[],
        fields=[_f("name", "char", "Name", required=True), _f("default_code", "char", "Reference")],
    )
    csv = b"name,ref,remark\nLamp,L-1,\nCup,C-1,\nLamp,L-1,created twice\nCup,C-1,\n"
    decisions = [
        MappingDecision(position=0, target="name"),
        MappingDecision(position=1, target="default_code"),
    ]
    summary, rows, _ = prepare(proposal, csv, "shop.csv", decisions)
    # la remarque n'est pas importée : la ligne 3 est un doublon de la ligne 1 ; la 4 un doublon exact de la 2
    assert [(e.row, e.reason.split(":")[0]) for e in summary.excluded_rows] == [
        (3, "Same data as row 1 of your file"),
        (4, "Same data as row 2 of your file"),
    ]
    assert summary.rows == 2 and summary.source_rows == 4
    assert {e.kind for e in summary.excluded_rows} == {"duplicate"}  # regroupés par motif dans le portail


def test_missing_categories_are_created_first_with_their_hierarchy_or_left_to_the_target() -> None:
    from uie_core.wizard.mapping import RecordSheet

    category = _f("categ_id", "reference", "Product Category", required=True, reference=True)
    proposal = TargetProposal(
        title="Products",
        summary="",
        configuration=[],
        rows=[],
        fields=[_f("name", "char", "Name", required=True), category],
    )
    csv = (
        b'name,group\nDrill,Werkzeug\nHose,Garten / Schlauch\nSeeds,"[""Garten"",""Saatgut""]"\n'
        b"Pot,Garten / Schlauch\n"
    )
    decisions = [MappingDecision(position=0, target="name"), MappingDecision(position=1, target="categ_id")]
    sheet = {
        "categ_id": RecordSheet(
            field="categ_id",
            label="Product Category",
            entity="product.category",
            name_field="name",
            parent_field="parent_id",
        )
    }
    created, rows, pairs = prepare(
        proposal, csv, "shop.csv", decisions, choices={"records:categ_id": "create"}, records=sheet
    )
    (records,) = created.created_records
    assert records.headers == ["id", "name", "parent_id/id"]
    xmlid = {r[1]: r[0] for r in records.rows}
    assert [(r[1], r[2]) for r in records.rows] == [
        ("Werkzeug", ""),
        ("Garten", ""),
        ("Schlauch", xmlid["Garten"]),
        ("Saatgut", xmlid["Garten"]),
    ]  # chaque niveau une fois, parents d'abord ; la liste JSON est lue comme un chemin
    assert [r[1] for r in rows] == ["Werkzeug", "Garten / Schlauch", "Garten / Saatgut", "Garten / Schlauch"]
    content = render_prepared(proposal, created, rows, pairs)
    first = pl.read_excel(io.BytesIO(content), sheet_name="Product Category (import first)")
    assert first.height == 4
    # autre choix : la catégorie par défaut de la cible pour toutes les lignes
    left, rows, _ = prepare(
        proposal, csv, "shop.csv", decisions, choices={"records:categ_id": "default"}, records=sheet
    )
    assert left.left_to_target == ["Product Category"] and "categ_id" not in left.missing_required
    assert all(r[1] == "" for r in rows) and left.created_records == []


def test_unit_values_are_written_with_the_names_of_the_target() -> None:
    from uie_core.wizard.mapping import UnitSettings, translate_units

    uom = _f("uom_id", "reference", "Unit of Measure", reference=True)
    units = UnitSettings(unit_names={"unit": "Units", "kg": "kg", "m": "m", "l": "L"}, unit_fields=["uom_id"])
    values = ["Stück", "St.", "Stk", "Meter", "kg", "Packung", "Liter", "Bund", ""]
    out, transform, issue = translate_units(uom, "Einheit", values, units, set(), None)
    assert out == ["Units", "Units", "Units", "m", "kg", "Packung", "L", "Bund", ""]
    assert transform and transform.count == 5  # kg déjà au bon nom : rien à transformer
    assert issue and issue.kind == "unit_unknown" and issue.rows == [6, 8] and issue.severity == "to_check"
    # « compter comme une pièce » choisi : l'emballage devient une pièce ; l'inconnu reste signalé
    out, _, issue = translate_units(uom, "Einheit", values, units, set(), "unit")
    assert out[5] == "Units" and issue and issue.rows == [8]


def test_the_unit_of_a_column_comes_from_its_header_and_converts_to_the_target_unit() -> None:
    from uie_core.wizard.values import check_column

    weight = _f("weight", "float", "Weight")
    r = check_column(weight, "Gewicht (kg)", 7, ["1,45", "0,62", "120 g", ""], {})
    assert r.values == ["1.45", "0.62", "0.12", ""] and r.issues == []  # 120 g → 0,12 kg (facteur exact)
    r = check_column(weight, "item_weight", 3, ["2 Pounds", "399 g", "16 ounces"], {}, target_unit="kg")
    # facteurs exacts, précision de la valeur source (règle de `convert`) : 2 lb → 0,91 kg ; 16 oz → 0,454 kg
    assert r.values == ["0.91", "0.399", "0.454"] and [q.answer for q in r.questions] == ["kg"]


# --------------------------------------------------------------------------- #
# Identifiants externes des lignes (colonne « id »)
# --------------------------------------------------------------------------- #
def test_each_row_gets_a_stable_external_id_from_its_internal_reference() -> None:
    from uie_core.wizard.mapping import RecordIdentity, record_ids

    proposal = TargetProposal(
        title="Products",
        summary="",
        configuration=[],
        rows=[],
        entity="product.template",
        fields=[_f("name", "char", "Name", required=True), _f("default_code", "char", "Internal Reference")],
    )
    csv = b"name,ref\nLamp,L-1\nCup,\nVase,A 1\nBowl,A-1\nCup,\nPlate,L-1\n"
    decisions = [
        MappingDecision(position=0, target="name"),
        MappingDecision(position=1, target="default_code"),
    ]
    identity = RecordIdentity(entity="product.template", key_field="default_code", name_field="name")
    summary, rows, pairs = prepare(proposal, csv, "shop.csv", decisions)
    ids = record_ids(identity, summary.headers, rows)
    prefix = "__import__.nexa45_product_template_"
    assert ids[0] == prefix + "l_1"  # référence interne
    assert ids[1] == prefix + "name_cup"  # sans référence : le nom
    # « A 1 » et « A-1 » donneraient le même texte : départagés par une empreinte de la valeur
    assert ids[2] != ids[3] and ids[2].startswith(prefix + "a_1_") and ids[3].startswith(prefix + "a_1_")
    assert ids[5] == prefix + "l_1_2"  # même référence sur une autre ligne : jamais fusionnées
    # même fichier → mêmes identifiants (un réimport met à jour au lieu de créer en double)
    assert record_ids(identity, summary.headers, rows) == ids

    content = render_prepared(proposal, summary, rows, pairs, identity=identity)
    data = pl.read_excel(io.BytesIO(content), sheet_name="Data", infer_schema_length=0)
    kept = [i for i in range(len(rows)) if i + 1 not in {e.row for e in summary.excluded_rows}]
    assert data.columns[0] == "id"
    assert data.get_column("id").to_list() == [ids[i] for i in kept]
    assert data.get_column("id").n_unique() == data.height
    readme = pl.read_excel(io.BytesIO(content), sheet_name="Read me", has_header=False)
    line = next(r for r in readme.iter_rows() if r[0] == "Record identifiers")
    assert "Internal Reference (otherwise Name)" in line[1] and "updates these records" in line[1]


def test_without_identity_the_data_sheet_has_no_id_column() -> None:
    proposal = TargetProposal(
        title="Products", summary="", configuration=[], rows=[], fields=[_f("name", "char", "Name")]
    )
    summary, rows, pairs = prepare(
        proposal, b"name\nLamp\n", "shop.csv", [MappingDecision(position=0, target="name")]
    )
    data = pl.read_excel(io.BytesIO(render_prepared(proposal, summary, rows, pairs)), sheet_name="Data")
    assert data.columns == ["name"]
