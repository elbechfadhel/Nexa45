"""V2d.1 : plan d'import et lignes préparées (module PUR : aucune base, aucun réseau, aucun LLM).

Cibles INVENTÉES (entité « shop.item ») sur le vocabulaire réel du domaine PRODUCT.
"""

from __future__ import annotations

import uuid

import pytest
import uie_domain_packs  # noqa: F401 — enregistre le pack et le vocabulaire
from uie_core.domains import DomainKey, DomainPackRegistry
from uie_core.imports import ColumnStatus, ColumnType, ImportTarget, ReviewReason
from uie_core.imports.plan import (
    ColumnOutcome,
    IssueCode,
    Labels,
    MediaUrl,
    Ref,
    ValidatedColumn,
    build_import_plan,
    normalize_rows,
    summarize,
)

SPEC = DomainPackRegistry.resolve(DomainKey.PRODUCT, "odoo", "17").semantics()
IMPORT_ID, INSTANCE_ID = uuid.UUID(int=1), uuid.UUID(int=2)


def T(key: str, data_type: str, kind: str = "value", *, required=False, selection=None, group="product"):  # noqa: ANN001, ANN201, N802
    return ImportTarget(
        id=f"{group}:{key}",
        group=group,
        semantic_key=key,
        label=key,
        entity="shop.item",
        field=f"f_{key}",
        data_type=data_type,
        kind=kind,
        required=required,
        selection=selection,
    )


TARGETS = [
    T("name", "string", required=True),
    T("list_price", "decimal"),
    T("weight", "decimal"),
    T("active", "boolean"),
    T("product_type", "enum", selection=[["consu", "Consumable"], ["service", "Service"]], required=True),
    T("category", "reference", "link", required=True),
    T("tags", "collection", "labels"),
    T("main_image", "binary", "media"),
    T("volume", "integer"),
]


def V(
    pos: int,
    name: str,
    typ: ColumnType,
    target: str | None,
    *,
    status=ColumnStatus.MATCHED,
    ignored=False,
    reason=None,
):  # noqa: ANN001, ANN201, N802
    return ValidatedColumn(
        position=pos,
        source_name=name,
        detected_type=typ,
        status=status,
        reason=reason,
        ignored=ignored,
        target_id=target,
    )


TEXT, DEC, INT, BOOL = ColumnType.TEXT, ColumnType.DECIMAL, ColumnType.INTEGER, ColumnType.BOOLEAN
DECISIONS = [
    V(0, "Name", TEXT, "product:name"),
    V(1, "Price", DEC, "product:list_price"),
    V(2, "Active", BOOL, "product:active"),
    V(3, "Kind", TEXT, "product:product_type"),
    V(4, "Category", TEXT, "product:category"),
    V(5, "Tags", TEXT, "product:tags"),
    V(6, "Image", TEXT, "product:main_image"),
    V(7, "Pieces", INT, "product:volume"),
    V(
        8, "Weight", TEXT, "product:weight"
    ),  # détectée texte, cible nombre : importée, valeurs vérifiées par ligne
    V(9, "Gone", TEXT, "product:x_removed"),  # cible disparue
    V(10, "SEO", TEXT, None, status=ColumnStatus.UNMATCHED, ignored=True),
    V(11, "MPN", TEXT, None, status=ColumnStatus.REVIEW, reason=ReviewReason.NOT_IN_SYSTEM, ignored=True),
    V(12, "Barcode", TEXT, "product:name", ignored=True),  # ignorée par le fournisseur
    V(13, "Blank", ColumnType.EMPTY, None, status=ColumnStatus.EMPTY),
]


def plan(decisions=DECISIONS):  # noqa: ANN001, ANN201
    return build_import_plan(
        import_id=IMPORT_ID,
        instance_id=INSTANCE_ID,
        file_sha256="f" * 64,
        decisions=decisions,
        targets=TARGETS,
        spec=SPEC,
    )


def row(**cells):  # noqa: ANN003, ANN201
    names = {d.source_name: d.position for d in DECISIONS}
    out: list = [None] * len(DECISIONS)
    for k, v in cells.items():
        out[names[k]] = v
    return out


def test_every_column_has_one_fate():
    p = plan()
    fates = {f.source_name: f.outcome for f in p.fates}
    assert fates == {
        "Name": ColumnOutcome.IMPORTED,
        "Price": ColumnOutcome.IMPORTED,
        "Active": ColumnOutcome.IMPORTED,
        "Kind": ColumnOutcome.IMPORTED,
        "Category": ColumnOutcome.IMPORTED,
        "Tags": ColumnOutcome.IMPORTED,
        "Image": ColumnOutcome.IMPORTED,
        "Pieces": ColumnOutcome.IMPORTED,
        "Weight": ColumnOutcome.IMPORTED,
        "Gone": ColumnOutcome.TARGET_GONE,
        "SEO": ColumnOutcome.NO_TARGET,
        "MPN": ColumnOutcome.NO_TARGET,
        "Barcode": ColumnOutcome.IGNORED,
        "Blank": ColumnOutcome.EMPTY,
    }
    by = {c.source_name: c for c in p.columns}
    # obligatoire sans valeur implicite = obligatoire ET texte : le nom, pas la catégorie ni le type
    assert by["Name"].required and not by["Category"].required and not by["Kind"].required
    assert by["Price"].recommended and by["Category"].recommended  # required_for_catalog du vocabulaire
    assert p.issues == []


def test_values_are_prepared_never_resolved():
    rows = normalize_rows(
        [
            row(
                Name=" Oak tray ",
                Price="19,90",
                Active="yes",
                Kind="Service",
                Category="Office / Desk",
                Tags="wood, gift, wood",
                Image="https://cdn.example/tray.png",
                Pieces="3",
            ),
            row(Name="Crate", Price="1.299,50", Active="0", Kind="consu", Category="Storage", Pieces=4.0),
            row(Name="Box", Price=12, Active=True),
        ],
        plan(),
    )
    first = rows[0].records["product"]
    assert first == {
        "f_name": "Oak tray",
        "f_list_price": 19.9,
        "f_active": True,
        "f_product_type": "service",  # libellé de la sélection → clé
        "f_category": Ref(label="Office / Desk"),
        "f_tags": Labels(labels=["wood", "gift"]),
        "f_main_image": MediaUrl(url="https://cdn.example/tray.png"),
        "f_volume": 3,
    }
    second = rows[1].records["product"]
    assert (second["f_list_price"], second["f_active"], second["f_product_type"], second["f_volume"]) == (
        1299.5,
        False,
        "consu",
        4,
    )
    assert rows[2].records["product"] == {
        "f_name": "Box",
        "f_list_price": 12.0,
        "f_active": True,
    }  # vide : absent
    assert rows[0].source_row == 1 and all(r.importable for r in rows)


@pytest.mark.parametrize(
    ("cells", "code"),
    [
        ({"Price": "abc"}, IssueCode.INVALID_NUMBER),
        ({"Pieces": "2.5"}, IssueCode.INVALID_NUMBER),
        ({"Active": "maybe"}, IssueCode.INVALID_BOOLEAN),
        ({"Kind": "simple"}, IssueCode.INVALID_CHOICE),
        ({"Image": "tray.png"}, IssueCode.INVALID_URL),
    ],
)
def test_invalid_values_are_reported_not_written_and_do_not_block(cells, code):
    r = normalize_rows([row(Name="Tray", **cells)], plan())[0]
    assert [i.code for i in r.issues if i.code != IssueCode.MISSING_RECOMMENDED] == [code]
    assert r.importable and r.records["product"]["f_name"] == "Tray"
    assert len(r.records["product"]) == 1  # la valeur invalide n'est pas écrite


def test_missing_required_blocks_the_row_missing_recommended_only_warns():
    rows = normalize_rows([row(Price="5"), row(Name="Tray")], plan())
    codes = [(i.code, i.target_id) for i in rows[0].issues]
    assert codes == [
        (IssueCode.MISSING_REQUIRED, "product:name"),
        (IssueCode.MISSING_RECOMMENDED, "product:product_type"),
        (IssueCode.MISSING_RECOMMENDED, "product:category"),
    ]
    assert not rows[0].importable
    assert {i.code for i in rows[1].issues} == {IssueCode.MISSING_RECOMMENDED}  # prix, catégorie
    assert rows[1].importable


def test_no_column_for_a_required_field_blocks_every_row():
    p = plan([d for d in DECISIONS if d.source_name != "Name"])
    assert [(i.code, i.target_id) for i in p.issues] == [(IssueCode.NO_COLUMN_FOR_REQUIRED, "product:name")]
    rows = normalize_rows([row(Price="5")], p)
    assert not rows[0].importable
    s = summarize(p, rows)
    assert (s.importable_rows, s.issues[0].code, s.issues[0].blocking) == (
        0,
        IssueCode.NO_COLUMN_FOR_REQUIRED,
        True,
    )


def test_summary_counts_values_distinct_labels_and_samples_and_is_deterministic():
    data = [
        row(Name=f"P{i}", Price="x" if i % 4 == 0 else "2", Category=["A", "a", "B"][i % 3], Tags="t1, t2")
        for i in range(1, 13)
    ]
    p1, p2 = plan(), plan()
    s1 = summarize(p1, normalize_rows(data, p1))
    s2 = summarize(p2, normalize_rows(data, p2))
    assert s1 == s2 and p1.checksum == p2.checksum
    by = {d.target_id: d for d in s1.data}
    assert (by["product:name"].rows_present, by["product:name"].rows_valid) == (12, 12)
    assert (by["product:list_price"].rows_present, by["product:list_price"].rows_valid) == (12, 9)
    assert by["product:category"].distinct_values == 2  # « A » et « a » : une seule valeur
    assert by["product:tags"].distinct_values == 2
    invalid = next(i for i in s1.issues if i.code == IssueCode.INVALID_NUMBER)
    assert (invalid.count, invalid.rows, invalid.blocking) == (3, [4, 8, 12], False)
    assert s1.rows == 12 and s1.importable_rows == 12
    # autre fichier (empreinte) → autre plan
    other = build_import_plan(
        import_id=IMPORT_ID,
        instance_id=INSTANCE_ID,
        file_sha256="e" * 64,
        decisions=DECISIONS,
        targets=TARGETS,
        spec=SPEC,
    )
    assert other.checksum != p1.checksum


def test_a_confirmed_mapping_survives_the_detected_column_type():
    """Régression (Sales Price) : une colonne détectée TEXTE (une valeur « 89.90 EUR », sous le seuil de
    95 %), confirmée par le fournisseur vers une cible NUMÉRIQUE, reste importée ; ses valeurs sont vérifiées
    ligne par ligne : manquante / présente valide / présente invalide. Seule une cible disparue la rend
    indisponible."""
    decisions = [
        V(0, "Name", TEXT, "product:name"),
        V(1, "Sales Price", TEXT, "product:list_price"),  # décision du fournisseur, type détecté texte
        V(2, "Image URL", TEXT, "product:main_image"),
    ]
    p = plan(decisions)
    assert [f.outcome for f in p.fates] == [ColumnOutcome.IMPORTED] * 3
    data = [
        ["Lamp", "89.90", "https://cdn.example/a.png"],
        ["Desk", "", "https://cdn.example/b.png"],
        ["Chair", "89.90 EUR", "https://cdn.example/c.png"],
        ["Timer", "39.90", "not-a-valid-url"],
    ]
    rows = normalize_rows([r + [None] * 11 for r in data], p)
    assert rows[0].records["product"]["f_list_price"] == 89.9  # présente et valide
    by_row = {r.source_row: {(i.target_id, i.code) for i in r.issues} for r in rows}
    assert ("product:list_price", IssueCode.MISSING_RECOMMENDED) in by_row[2]  # manquante
    assert ("product:list_price", IssueCode.INVALID_NUMBER) in by_row[3]  # présente mais invalide
    assert ("product:main_image", IssueCode.INVALID_URL) in by_row[4]
    assert all(r.importable for r in rows)  # rien de tout cela ne bloque le produit
    s = summarize(p, rows)
    by = {d.target_id: d for d in s.data}
    assert (by["product:list_price"].rows_present, by["product:list_price"].rows_valid) == (3, 2)
    assert (by["product:main_image"].rows_present, by["product:main_image"].rows_valid) == (4, 3)

    # une cible réellement retirée de l'instance reste « no longer available »
    gone = build_import_plan(
        import_id=IMPORT_ID,
        instance_id=INSTANCE_ID,
        file_sha256="f" * 64,
        decisions=decisions,
        targets=[t for t in TARGETS if t.id != "product:list_price"],
        spec=SPEC,
    )
    assert [f.outcome for f in gone.fates] == [
        ColumnOutcome.IMPORTED,
        ColumnOutcome.TARGET_GONE,
        ColumnOutcome.IMPORTED,
    ]
