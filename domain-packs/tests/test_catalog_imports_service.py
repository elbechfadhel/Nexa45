"""V2c.1 commit 2 : CatalogImportService sur le domaine RÉEL de customer17 (dump complet), deux fournisseurs.

Upload (forme du fichier, fichier hors PostgreSQL) → analyse contre SON système (catalogue terminé) → Choose
target / Ignore → Ready. Isolation : import et système d'un autre fournisseur introuvables (même NotFound).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, text
from test_catalog_sync import _answer, world  # noqa: F401 (fixture)
from uie_core.catalog.service import CatalogNotReady
from uie_core.identity import PermissionDenied
from uie_core.imports import CatalogImportService, ImportConflict, ImportFileError, LocalFileStore
from uie_core.imports.records import CatalogImportRecord
from uie_core.registry import NotFound

FIXTURES = Path(__file__).parent / "fixtures" / "imports"
CSV = (FIXTURES / "supplier_catalog.csv").read_bytes()


@pytest.fixture
def files(tmp_path):  # noqa: ANN001, ANN201
    return LocalFileStore(tmp_path / "imports")


@pytest.fixture
def svc(world, session, files):  # noqa: ANN001, ANN201, F811
    def make(slug: str) -> CatalogImportService:
        sync, _ = world["tenant"](slug)
        return CatalogImportService(session, sync, files)

    return make


def _cols(view):  # noqa: ANN001, ANN202
    return {c.source_name: c for c in view.columns}


def test_upload_keeps_the_file_out_of_postgres(svc, session, files):
    view = svc("acme").upload("C:\\Users\\me\\supplier_catalog.csv", CSV)
    rec = view.record
    assert (view.status, rec.filename, rec.row_count, rec.column_count) == (
        "UPLOADED",
        "supplier_catalog.csv",
        10,
        8,
    )
    assert rec.organization_id == svc("acme").ctx.organization_id
    assert files.get(rec.storage_ref) == CSV  # l'original, pour l'import réel (V2c.2)
    assert rec.storage_ref.startswith("file:v1:") and not Path(rec.storage_ref[8:]).is_absolute()
    # aucune valeur de cellule dans PostgreSQL
    for table in ("catalog_imports", "catalog_import_columns"):
        dump = session.execute(text(f"SELECT row_to_json(t)::text FROM {table} t")).scalars().all()
        assert not any("SUP-7781" in row or "Oak Desk" in row for row in dump)


def test_upload_refuses_unreadable_files_and_non_tenants(svc, session, world, files):  # noqa: F811
    with pytest.raises(ImportFileError):
        svc("acme").upload("catalog.pdf", b"%PDF")
    sync = world["sync"]  # SystemContext : un import appartient toujours à une organisation fournisseur
    with pytest.raises(PermissionDenied):
        CatalogImportService(session, sync, files).upload("supplier_catalog.csv", CSV)


def test_analyze_needs_a_finished_catalog_analysis(svc, world):  # noqa: F811
    imp = svc("acme").upload("supplier_catalog.csv", CSV).record
    with pytest.raises(CatalogNotReady):
        svc("acme").analyze(imp.id, world["inst"]["acme"].id)  # 3 questions ouvertes


def test_full_flow_matched_review_unmatched_choose_ignore_ready(svc, world):  # noqa: F811
    _answer(world)
    acme = svc("acme")
    imp = acme.upload("supplier_catalog.csv", CSV).record
    view = acme.analyze(imp.id, world["inst"]["acme"].id)
    cols = _cols(view)
    assert (view.status, view.system_name) == ("ANALYZED", "customer17")
    assert {n: c.status for n, c in cols.items()} == {
        "Product Name": "MATCHED",
        "SKU": "MATCHED",
        "Barcode": "MATCHED",
        "Price": "REVIEW",
        "Category": "MATCHED",
        "Weight": "MATCHED",
        "Supplier Ref": "MATCHED",
        "Material": "UNMATCHED",
    }
    assert cols["Barcode"].target_field == "product.template.barcode"
    assert cols["Price"].target_id == "product:list_price"  # suggestion à confirmer

    targets = {t.id for t in acme.targets(imp.id)}
    assert "product:list_price" in targets and "product:packaging_code" in targets
    with pytest.raises(ImportConflict, match="already used by the column “SKU”"):
        acme.choose_target(imp.id, cols["Price"].position, "product:sku")
    with pytest.raises(NotFound):
        acme.choose_target(imp.id, cols["Price"].position, "product:x_notes_internal")

    view = acme.choose_target(imp.id, cols["Price"].position, "product:list_price")
    assert (_cols(view)["Price"].status, _cols(view)["Price"].decided_by) == ("MATCHED", "SUPPLIER")
    assert view.status == "ANALYZED"
    view = acme.ignore_unmatched(imp.id)  # Material, seule colonne UNMATCHED
    assert _cols(view)["Material"].ignored and view.status == "READY"
    assert len(view.columns) == 8  # rien ne disparaît

    # ré-analyse : recalculée ; le choix explicite sur Price (V2c.2) et l'Ignore all de Material (V2c.2b)
    # sont réappliqués par la mémoire de l'organisation
    view = acme.analyze(imp.id, world["inst"]["acme"].id)
    assert (_cols(view)["Price"].status, _cols(view)["Price"].decided_by) == ("MATCHED", "LEARNED")
    material = _cols(view)["Material"]
    assert (material.status, material.ignored, material.decided_by) == ("UNMATCHED", True, "LEARNED")
    assert view.status == "READY"


def test_tenant_isolation(svc, world):  # noqa: F811
    _answer(world)
    acme, globex = svc("acme"), svc("globex")
    imp = acme.upload("supplier_catalog.csv", CSV).record
    assert [v.record.id for v in acme.list_imports()] == [imp.id]
    assert globex.list_imports() == []
    for call in (
        lambda: globex.get(imp.id),
        lambda: globex.analyze(imp.id, world["inst"]["globex"].id),
        lambda: globex.targets(imp.id),
        lambda: globex.ignore_column(imp.id, 0),
        lambda: globex.delete(imp.id),
    ):
        with pytest.raises(NotFound, match="Import introuvable"):
            call()
    # le système d'un autre fournisseur n'est jamais une cible
    with pytest.raises(NotFound):
        acme.analyze(imp.id, world["inst"]["globex"].id)
    with pytest.raises(NotFound):
        acme.get(uuid.uuid4())


def test_admin_never_crosses_organizations(svc, world, session, files):  # noqa: F811
    _answer(world)
    imp = svc("acme").upload("supplier_catalog.csv", CSV).record
    admin = CatalogImportService(session, world["sync"], files)  # SystemContext : voit tout
    assert admin.get(imp.id).record.id == imp.id
    with pytest.raises(NotFound):
        admin.analyze(imp.id, world["inst"]["globex"].id)


def test_deleted_system_brings_the_import_back_to_upload(svc, world, session):  # noqa: F811
    _answer(world)
    acme = svc("acme")
    imp = acme.upload("supplier_catalog.csv", CSV).record
    acme.analyze(imp.id, world["inst"]["acme"].id)
    sync, _ = world["tenant"]("acme")
    sync.instances.delete(world["inst"]["acme"].id)
    session.expire_all()
    view = acme.get(imp.id)
    assert (view.status, view.columns, view.system_name) == ("UPLOADED", [], None)
    with pytest.raises(ImportConflict):
        acme.targets(imp.id)


def test_delete_removes_the_file_after_commit(svc, session, files):
    acme = svc("acme")
    imp = acme.upload("supplier_catalog.csv", CSV).record
    ref = imp.storage_ref
    acme.delete(imp.id)
    assert session.get(CatalogImportRecord, imp.id) is None
    assert files.get(ref) == CSV  # transaction de test jamais validée : le fichier reste
    session.dispatch.after_commit(session)  # simulation du commit de l'entrypoint
    with pytest.raises(Exception, match="introuvable"):
        files.get(ref)


def test_rolled_back_upload_leaves_no_file(svc, session, files):
    imp = svc("acme").upload("supplier_catalog.csv", CSV).record
    ref = imp.storage_ref
    session.dispatch.after_soft_rollback(session, None)  # simulation de l'échec de l'entrypoint
    with pytest.raises(Exception, match="introuvable"):
        files.get(ref)


# --------------------------------------------------------------------------- #
# V2c.2 — mémoire apprise (organisation), sans LLM
# --------------------------------------------------------------------------- #
LEARN_CSV = b"Product Name,Kuerzel,Price,Material\nChair,A1,12.50,Oak\nDesk,A2,99.00,Steel\n"
LEARN_CSV_2 = b"PRODUCT NAME,KUERZEL,price,Material\nLamp,A3,5.50,Glass\n"  # variantes triviales


def _learn_setup(svc, world):  # noqa: ANN001, ANN202, F811
    _answer(world)
    acme = svc("acme")
    imp = acme.upload("first.csv", LEARN_CSV).record
    return acme, acme.analyze(imp.id, world["inst"]["acme"].id)


def test_learned_mapping_reduces_human_decisions(svc, world, session):  # noqa: F811
    from uie_core.imports.records import LearnedColumnMapping

    acme, first = _learn_setup(svc, world)
    cols = _cols(first)
    assert {n: c.status for n, c in cols.items()} == {
        "Product Name": "MATCHED",
        "Kuerzel": "UNMATCHED",
        "Price": "REVIEW",
        "Material": "UNMATCHED",
    }
    rec = first.record
    assert (rec.resolved_auto, rec.resolved_learned, rec.needs_attention) == (1, 0, 3)

    # TEST 1 : décisions normales du fournisseur, rien de plus
    iid = rec.id
    acme.choose_target(iid, cols["Kuerzel"].position, "product:sku")  # Choose target
    acme.choose_target(iid, cols["Price"].position, "product:list_price")  # Confirm de la suggestion
    acme.choose_target(iid, cols["Product Name"].position, "product:name")  # déjà couvert par les règles
    acme.ignore_column(iid, cols["Material"].position)  # Ignore d'une colonne UNMATCHED : appris (V2c.2b)
    memory = {
        r.header_key: (r.decision, r.semantic_key) for r in session.scalars(select(LearnedColumnMapping))
    }
    assert memory == {
        "kuerzel": ("MAPPED", "sku"),
        "price": ("MAPPED", "list_price"),
        "material": ("NOT_RELEVANT", None),
    }

    # TEST 2 : même contenu, NOUVEL import, en-têtes écrits autrement
    second = acme.upload("second.csv", LEARN_CSV_2).record
    view = acme.analyze(second.id, world["inst"]["acme"].id)
    got = {c.source_name: (c.status, c.decided_by, c.target_id) for c in view.columns}
    assert got["KUERZEL"] == ("MATCHED", "LEARNED", "product:sku")
    assert got["price"] == ("MATCHED", "LEARNED", "product:list_price")
    assert got["PRODUCT NAME"] == ("MATCHED", "AUTO", "product:name")
    assert got["Material"] == ("UNMATCHED", "LEARNED", None)  # ignorée d'office (V2c.2b)
    r2 = view.record
    assert (r2.resolved_auto, r2.resolved_learned, r2.resolved_ignored, r2.needs_attention) == (1, 2, 1, 0)
    assert view.status == "READY"  # aucune manipulation
    usage = {r.header_key: r.usage_count for r in session.scalars(select(LearnedColumnMapping))}
    assert usage == {"kuerzel": 1, "price": 1, "material": 1}

    # TEST 3 : une autre organisation ne voit jamais cette mémoire
    globex = svc("globex")
    other = globex.upload("second.csv", LEARN_CSV_2).record
    world["enrichment"].domains.analyze(world["inst"]["globex"].id)
    e = world["enrichment"]
    gid = world["inst"]["globex"].id
    e.confirm(gid, "product.template", "x_supplier_ref")
    e.change(gid, "product.template", "x_pack_code", "packaging_code")
    e.ignore(gid, "product.template", "x_notes_internal")
    view = globex.analyze(other.id, gid)
    got = {c.source_name: (c.status, c.decided_by, c.ignored) for c in view.columns}
    assert got["KUERZEL"] == ("UNMATCHED", "AUTO", False) and got["price"] == ("REVIEW", "AUTO", False)
    assert got["Material"] == ("UNMATCHED", "AUTO", False)


def test_changed_decision_replaces_the_memory(svc, world, session):  # noqa: F811
    from uie_core.imports.records import LearnedColumnMapping

    acme, first = _learn_setup(svc, world)
    col = _cols(first)["Kuerzel"]
    acme.choose_target(first.record.id, col.position, "product:sku")
    second = acme.analyze(first.record.id, world["inst"]["acme"].id)  # ré-analyse : appliquée
    assert _cols(second)["Kuerzel"].decided_by == "LEARNED"
    acme.choose_target(first.record.id, col.position, "product:supplier_reference")  # Change target
    rows = list(session.scalars(select(LearnedColumnMapping)))
    assert [(r.header_key, r.semantic_key) for r in rows] == [("kuerzel", "supplier_reference")]


def test_memory_is_skipped_when_the_key_is_not_a_target_or_the_type_differs(svc, world, session):  # noqa: F811
    from uie_core.imports.records import LearnedColumnMapping

    acme, first = _learn_setup(svc, world)
    org = first.record.organization_id
    session.add_all(
        [
            LearnedColumnMapping(
                organization_id=org, header_key="material", semantic_key="manufacturer_reference"
            ),
            LearnedColumnMapping(organization_id=org, header_key="kuerzel", semantic_key="weight"),
        ]
    )
    session.flush()
    view = acme.analyze(first.record.id, world["inst"]["acme"].id)
    got = {c.source_name: (c.status, c.decided_by) for c in view.columns}
    assert got["Material"] == ("UNMATCHED", "AUTO")  # clé absente de ce système : pipeline normal
    assert got["Kuerzel"] == ("UNMATCHED", "AUTO")  # texte ≠ poids : pipeline normal


# --------------------------------------------------------------------------- #
# V2c.2b — NOT_RELEVANT appris (organisation + système cible)
# --------------------------------------------------------------------------- #
def _memory_rows(session):  # noqa: ANN001, ANN202
    from uie_core.imports.records import LearnedColumnMapping

    return {
        r.header_key: (r.decision, r.semantic_key, r.system_id is not None)
        for r in session.scalars(select(LearnedColumnMapping))
    }


def test_ignore_all_unmatched_learns_only_unmatched_columns(svc, world, session):  # noqa: F811
    acme, first = _learn_setup(svc, world)
    iid = first.record.id
    acme.ignore_unmatched(iid)  # Kuerzel et Material ; Price (REVIEW) et Product Name (MATCHED) intacts
    assert _memory_rows(session) == {
        "kuerzel": ("NOT_RELEVANT", None, True),
        "material": ("NOT_RELEVANT", None, True),
    }
    view = acme.analyze(iid, world["inst"]["acme"].id)
    got = {c.source_name: (c.status, c.ignored, c.decided_by) for c in view.columns}
    assert got == {
        "Product Name": ("MATCHED", False, "AUTO"),
        "Kuerzel": ("UNMATCHED", True, "LEARNED"),
        "Price": ("REVIEW", False, "AUTO"),
        "Material": ("UNMATCHED", True, "LEARNED"),
    }
    rec = view.record
    assert (rec.resolved_auto, rec.resolved_learned, rec.resolved_ignored, rec.needs_attention) == (
        1,
        0,
        2,
        1,
    )
    assert view.status == "ANALYZED"  # Price reste à décider


def test_ignoring_an_automatic_match_is_not_learned(svc, world, session):  # noqa: F811
    acme, first = _learn_setup(svc, world)
    cols = _cols(first)
    acme.ignore_column(first.record.id, cols["Product Name"].position)  # MATCHED par les règles : ponctuel
    acme.ignore_column(first.record.id, cols["Price"].position)  # REVIEW : appris
    assert _memory_rows(session) == {"price": ("NOT_RELEVANT", None, True)}
    view = acme.analyze(first.record.id, world["inst"]["acme"].id)
    assert (_cols(view)["Product Name"].status, _cols(view)["Product Name"].ignored) == ("MATCHED", False)
    assert _cols(view)["Price"].ignored


def test_mapping_and_not_relevant_replace_each_other(svc, world, session):  # noqa: F811
    acme, first = _learn_setup(svc, world)
    iid, inst = first.record.id, world["inst"]["acme"].id
    art = _cols(first)["Kuerzel"].position
    acme.ignore_column(iid, art)
    assert _memory_rows(session) == {"kuerzel": ("NOT_RELEVANT", None, True)}
    # colonne ignorée d'office → le fournisseur choisit une cible : le mapping remplace le NOT_RELEVANT
    view = acme.analyze(iid, inst)
    assert _cols(view)["Kuerzel"].ignored
    view = acme.choose_target(iid, art, "product:sku")
    assert _memory_rows(session) == {"kuerzel": ("MAPPED", "sku", False)}
    view = acme.analyze(iid, inst)
    assert (_cols(view)["Kuerzel"].status, _cols(view)["Kuerzel"].decided_by) == ("MATCHED", "LEARNED")
    # mapping appris → Ignore : NOT_RELEVANT pour ce système (il prime), le mapping reste pour les autres
    acme.ignore_column(iid, art)
    from uie_core.imports.records import LearnedColumnMapping

    rows = sorted((r.decision, r.semantic_key) for r in session.scalars(select(LearnedColumnMapping)))
    assert rows == [("MAPPED", "sku"), ("NOT_RELEVANT", None)]
    view = acme.analyze(iid, inst)
    assert (_cols(view)["Kuerzel"].ignored, _cols(view)["Kuerzel"].decided_by) == (True, "LEARNED")


def test_choosing_what_the_rules_give_forgets_the_not_relevant(svc, world, session):  # noqa: F811
    acme, first = _learn_setup(svc, world)
    iid = first.record.id
    price = _cols(first)["Price"].position
    acme.ignore_column(iid, price)
    acme.analyze(iid, world["inst"]["acme"].id)
    acme.choose_target(iid, price, "product:list_price")  # la suggestion des règles, mais pas ≥ 0.90
    assert _memory_rows(session) == {"price": ("MAPPED", "list_price", False)}
    name = _cols(first)["Product Name"].position
    from uie_core.imports.records import LearnedColumnMapping

    org = first.record.organization_id
    system_id = world["inst"]["acme"].interface.version.product.system.id
    session.add(
        LearnedColumnMapping(
            organization_id=org, header_key="product name", decision="NOT_RELEVANT", system_id=system_id
        )
    )
    session.flush()
    acme.choose_target(iid, name, "product:name")  # déjà donné par les règles : rien n'est gardé
    assert _memory_rows(session) == {"price": ("MAPPED", "list_price", False)}


def test_empty_column_stays_empty_despite_a_learned_ignore(svc, world, session):  # noqa: F811
    acme, first = _learn_setup(svc, world)
    acme.ignore_column(first.record.id, _cols(first)["Material"].position)
    empty = acme.upload("empty.csv", b"Product Name,Material\nChair,\nDesk,\n").record
    view = acme.analyze(empty.id, world["inst"]["acme"].id)
    material = _cols(view)["Material"]
    assert (material.status, material.ignored, material.decided_by) == ("EMPTY", False, "AUTO")
    assert view.record.resolved_ignored == 0
    from uie_core.imports.records import LearnedColumnMapping

    assert [r.usage_count for r in session.scalars(select(LearnedColumnMapping))] == [0]


def test_not_relevant_is_scoped_to_the_target_system(svc, world, session):  # noqa: F811
    """Un NOT_RELEVANT appris pour un autre système (DATEV...) n'a aucun effet sur un import vers Odoo."""
    from uie_core.imports.records import LearnedColumnMapping
    from uie_core.registry import models as m

    acme, first = _learn_setup(svc, world)
    other = m.System(code="othersys", name="Other system")
    session.add(other)
    session.flush()
    session.add(
        LearnedColumnMapping(
            organization_id=first.record.organization_id,
            header_key="material",
            decision="NOT_RELEVANT",
            system_id=other.id,
        )
    )
    session.flush()
    view = acme.analyze(first.record.id, world["inst"]["acme"].id)
    assert (_cols(view)["Material"].ignored, _cols(view)["Material"].decided_by) == (False, "AUTO")
    # le même en-tête peut être NOT_RELEVANT pour chaque système, une fois par système
    acme.ignore_column(first.record.id, _cols(view)["Material"].position)
    assert session.scalar(text("SELECT count(*) FROM learned_column_mappings")) == 2


def test_memory_shape_is_enforced_by_postgres(svc, world, session):  # noqa: F811
    from sqlalchemy.exc import IntegrityError
    from uie_core.imports.records import LearnedColumnMapping

    acme, first = _learn_setup(svc, world)
    org = first.record.organization_id
    system_id = world["inst"]["acme"].interface.version.product.system.id
    bad = [
        LearnedColumnMapping(organization_id=org, header_key="a", decision="MAPPED"),  # sans clé
        LearnedColumnMapping(organization_id=org, header_key="b", decision="NOT_RELEVANT"),  # sans système
        LearnedColumnMapping(
            organization_id=org,
            header_key="c",
            decision="NOT_RELEVANT",
            semantic_key="sku",
            system_id=system_id,
        ),
    ]
    for row in bad:
        with session.begin_nested():
            session.add(row)
            with pytest.raises(IntegrityError, match="decision_shape"):
                session.flush()


# --------------------------------------------------------------------------- #
# V2d.1 — preview d'import (lecture seule)
# --------------------------------------------------------------------------- #
class _CountingStore(LocalFileStore):
    def __init__(self, root) -> None:  # noqa: ANN001
        super().__init__(root)
        self.reads = 0

    def get(self, ref: str) -> bytes:
        self.reads += 1
        return super().get(ref)


class _NoAI:
    provider, model = "spy", "spy-1"

    def propose_columns(self, request):  # noqa: ANN001, ANN201
        raise AssertionError("le preview ne doit jamais appeler le LLM")


def _ready(svc, world):  # noqa: ANN001, ANN202, F811
    _answer(world)
    acme = svc("acme")
    imp = acme.upload("supplier_catalog.csv", CSV).record
    view = acme.analyze(imp.id, world["inst"]["acme"].id)
    acme.choose_target(imp.id, _cols(view)["Price"].position, "product:list_price")
    return acme, acme.ignore_unmatched(imp.id)


def test_preview_needs_a_ready_import_and_stays_in_the_organization(svc, world):  # noqa: F811
    _answer(world)
    acme = svc("acme")
    imp = acme.upload("supplier_catalog.csv", CSV).record
    acme.analyze(imp.id, world["inst"]["acme"].id)
    with pytest.raises(ImportConflict, match="not ready"):
        acme.preview(imp.id)
    acme, view = _ready(svc, world)
    assert view.status == "READY"
    with pytest.raises(NotFound):
        svc("globex").preview(view.record.id)


def test_preview_is_read_only_and_reads_the_file_once(world, session, tmp_path, monkeypatch):  # noqa: F811
    from uie_core.instances import InstanceService

    for name in ("test_connection", "check_connection", "discover_schema"):  # aucune connexion au système
        monkeypatch.setattr(InstanceService, name, lambda *a, **k: pytest.fail("connexion au système"))
    _answer(world)
    store = _CountingStore(tmp_path / "imports")
    sync, _ = world["tenant"]("acme")
    acme = CatalogImportService(session, sync, store)
    imp = acme.upload("supplier_catalog.csv", CSV).record
    view = acme.analyze(imp.id, world["inst"]["acme"].id)
    acme.choose_target(imp.id, _cols(view)["Price"].position, "product:list_price")
    acme.ignore_unmatched(imp.id)
    session.flush()
    before = (store.reads, imp.status, imp.analyzed_at)
    previewer = CatalogImportService(session, sync, store, _NoAI())
    p = previewer.preview(imp.id)
    assert store.reads == before[0] + 1  # le fichier, une seule fois
    assert not session.new and not session.dirty and (imp.status, imp.analyzed_at) == before[1:]
    assert previewer.preview(imp.id) == p  # reproductible

    assert (p.rows, p.importable_rows) == (10, 10)
    data = {d.label: (d.rows_valid, d.distinct_values) for d in p.data}
    assert data["Product name"] == (10, None) and data["Sales price"][0] == 10
    assert data["Category"][1] == len({line.split(",")[4] for line in CSV.decode().splitlines()[1:]})
    fates = {f.source_name: f.outcome.value for f in p.fates}
    assert fates["Material"] == "NO_TARGET" and fates["Product Name"] == "IMPORTED"


def test_preview_flags_a_target_that_disappeared(svc, world):  # noqa: F811
    acme, view = _ready(svc, world)
    # le fournisseur ignore ensuite le champ qui portait « Supplier reference » : la cible n'existe plus
    world["enrichment"].ignore(world["inst"]["acme"].id, "product.template", "x_supplier_ref")
    p = acme.preview(view.record.id)
    fates = {f.source_name: (f.outcome.value, f.label) for f in p.fates}
    assert fates["Supplier Ref"] == ("TARGET_GONE", None)
    assert all(d.label != "Supplier reference" for d in p.data)


def test_prepare_returns_the_rows_v2d2_will_write(svc, world):  # noqa: F811
    from uie_core.imports.plan import Ref

    acme, view = _ready(svc, world)
    plan, rows = acme.prepare(view.record.id)
    assert {c.entity for c in plan.columns} == {"product.template"}  # ce fichier : produit seulement
    first = rows[0].records["product"]
    assert first["name"] == "Oak Desk Organizer" and first["default_code"] == "ACM-1001"
    assert first["list_price"] == 24.9 and first["categ_id"] == Ref(label="Office / Desk Accessories")


PRICE_CSV = (
    b"Product Name,SKU,Sales Price,Image URL\n"
    b"Lamp,L-1,89.90,https://cdn.example/lamp.png\n"
    b"Desk,D-1,,https://cdn.example/desk.png\n"
    b"Chair,C-1,89.90 EUR,https://cdn.example/chair.png\n"
    b"Timer,T-1,39.90,not-a-valid-url\n"
    b"Shelf,S-1,119.00,https://cdn.example/shelf.png\n"
    b"Crate,,12.50,https://cdn.example/crate.png\n"
)


def test_a_confirmed_mapping_survives_until_the_import_plan(svc, world, session):  # noqa: F811
    """Régression V2d.1 (« Sales Price » classée « no longer available ») : cycle complet analyse →
    confirmation du fournisseur → décision persistée → plan d'import → preview, sur la même instance."""
    _answer(world)
    acme = svc("acme")
    imp = acme.upload("price_test.csv", PRICE_CSV).record
    view = acme.analyze(imp.id, world["inst"]["acme"].id)
    price = _cols(view)["Sales Price"]
    assert price.detected_type == "text"  # 4 valeurs numériques sur 5 : sous le seuil de détection (95 %)
    # 1-2. le fournisseur confirme la cible
    acme.choose_target(imp.id, price.position, "product:list_price")
    image = _cols(acme.get(imp.id))["Image URL"]
    if image.target_id != "product:main_image":
        acme.choose_target(imp.id, image.position, "product:main_image")
    for c in acme.get(imp.id).columns:
        if c.status == "REVIEW" and not c.ignored:
            acme.ignore_column(imp.id, c.position)
    view = acme.ignore_unmatched(imp.id)
    assert view.status == "READY"
    # 3. décision persistée
    session.expire_all()
    stored = _cols(acme.get(imp.id))["Sales Price"]
    assert (stored.status, stored.target_id, stored.decided_by) == (
        "MATCHED",
        "product:list_price",
        "SUPPLIER",
    )
    # 4-5. plan d'import sur la même instance : ni obsolète ni indisponible
    plan, rows = acme.prepare(imp.id)
    fates = {f.source_name: f.outcome.value for f in plan.fates}
    assert fates["Sales Price"] == "IMPORTED"
    # 6. dans « Data to import », présentes ≠ valides
    p = acme.preview(imp.id)
    data = {d.label: (d.rows_present, d.rows_valid) for d in p.data}
    assert data["Sales price"] == (5, 4)
    assert data["Main image"] == (6, 5)
    assert not any(f.outcome.value == "TARGET_GONE" for f in p.fates)
    # 7. manquante (ligne 2), présente mais invalide (ligne 3), URL invalide (ligne 4), SKU manquant (ligne 6)
    issues = {(i.code.value, i.label): i.rows for i in p.issues}
    assert issues[("MISSING_RECOMMENDED", "Sales price")] == [2]
    assert issues[("INVALID_NUMBER", "Sales price")] == [3]
    assert issues[("INVALID_URL", "Main image")] == [4]
    assert issues[("MISSING_RECOMMENDED", "SKU / internal reference")] == [6]
    assert p.importable_rows == 6  # aucun de ces problèmes ne bloque le produit
    assert rows[0].records["product"]["list_price"] == 89.9
