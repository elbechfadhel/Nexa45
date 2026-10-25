"""V2d.2 : exécution d'un import (domaine RÉEL de customer17, cible d'écriture EN MÉMOIRE, aucun réseau).

prepare (V2d.1) → run (request_key) → relations → identités → un item par ligne (PENDING avant l'appel).
Politique : valeur vide / invalide jamais envoyée, ligne non importable SKIPPED, identité unique → UPDATE,
absente → CREATE, multiple → FAILED, relation manquante créée, ambiguë signalée ; images et offre fournisseur
« not imported yet ». Idempotence : même clé = même run ; réponse perdue → reprise sans doublon.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from test_catalog_imports_service import files, svc  # noqa: F401 (fixtures)
from test_catalog_sync import _answer, world  # noqa: F401 (fixture)
from uie_core.imports import (
    CatalogTarget,
    CatalogWriteError,
    CatalogWriter,
    ImportConflict,
    LabelResolution,
    LabelStatus,
    RecordId,
    RecordIds,
    WriteErrorKind,
)
from uie_core.imports.execution import CatalogImportExecutionService
from uie_core.registry import NotFound

CSV = (
    b"Product Name,SKU,Sales Price,Category,Tags,Image URL\n"
    b'Lamp,L-1,10.50,Office / Desk,"office, light",https://cdn.example/lamp.png\n'  # 1 nouveau
    b"Desk,,99.00,Office / Furniture,office,https://cdn.example/desk.png\n"  # 2 sans SKU → CREATE
    b'Chair,EXIST-1,,Office / Furniture,"office, seat",https://cdn.example/chair.png\n'  # 3 existant
    b"Timer,T-1,89.90 EUR,Ambiguous Cat,timer,not-a-valid-url\n"  # 4 prix et URL invalides, catégorie ambiguë
    b"Shelf,DUP-1,20.00,Office / Desk,office,https://cdn.example/shelf.png\n"  # 5 SKU en double
    b",NONAME-1,5.00,Office / Desk,office,https://cdn.example/x.png\n"  # 6 sans nom → SKIPPED
    b"Lamp bis,L-1,11.00,Office / Desk,light,https://cdn.example/lamp2.png\n"  # 7 même SKU que la ligne 1
)


class MemoryTarget(CatalogTarget):
    """Système cible en mémoire ; `lose_response_on` : cette création réussit, puis la réponse se perd."""

    def __init__(self) -> None:
        self.records: dict[str, dict[str, dict[str, Any]]] = {}
        self.keys: dict[str, str] = {}
        self.labels: dict[str, dict[str, list[str]]] = {
            "product.category": {"office / furniture": ["c1"], "ambiguous cat": ["c2", "c3"]},
            "product.tag": {"office": ["t1"]},
        }
        self.calls: list[tuple[str, Any]] = []
        self.lose_response_on: str | None = None
        self.next_id = 100
        self.seed(
            "product.template", "p-exist", {"default_code": "EXIST-1", "list_price": 42.0, "name": "Old"}
        )
        self.seed("product.template", "p-dup1", {"default_code": "DUP-1"})
        self.seed("product.template", "p-dup2", {"default_code": "DUP-1"})

    def seed(self, entity: str, rid: str, values: dict[str, Any]) -> None:
        self.records.setdefault(entity, {})[rid] = dict(values)

    def _id(self) -> str:
        self.next_id += 1
        return str(self.next_id)

    def find_by(self, entity, field, values, *, include_archived=True):  # noqa: ANN001, ANN201
        self.calls.append(("find_by", (entity, field, tuple(values))))
        out: dict[str, list[str]] = {}
        for rid, rec in self.records.get(entity, {}).items():
            if str(rec.get(field)) in values:
                out.setdefault(str(rec.get(field)), []).append(rid)
        return out

    def resolve_labels(self, entity, labels, *, create_missing):  # noqa: ANN001, ANN201
        self.calls.append(("resolve_labels", (entity, tuple(labels))))
        known = self.labels.setdefault(entity, {})
        out = []
        for label in labels:
            ids = known.get(label.casefold(), [])
            if len(ids) > 1:
                out.append(LabelResolution(label=label, status=LabelStatus.AMBIGUOUS))
            elif ids:
                out.append(LabelResolution(label=label, status=LabelStatus.FOUND, id=ids[0]))
            else:
                known[label.casefold()] = [self._id()]
                out.append(
                    LabelResolution(label=label, status=LabelStatus.CREATED, id=known[label.casefold()][0])
                )
        return out

    def create_with_key(self, entity, key, values):  # noqa: ANN001, ANN201
        self.calls.append(("create_with_key", (entity, key)))
        rid = self.keys.get(key) or self._id()
        self.keys[key] = rid
        self.records.setdefault(entity, {})[rid] = {
            **self.records.get(entity, {}).get(rid, {}),
            **_plain(values),
        }
        if self.lose_response_on is not None and self.lose_response_on == values.get("default_code"):
            self.lose_response_on = None
            raise CatalogWriteError(WriteErrorKind.CONNECTION, "réponse perdue")
        return rid

    def update(self, entity, record_id, values):  # noqa: ANN001, ANN201
        self.calls.append(("update", (entity, record_id)))
        self.records[entity][record_id].update(_plain(values))


def _plain(values: dict[str, Any]) -> dict[str, Any]:
    return {
        k: v.id if isinstance(v, RecordId) else list(v.ids) if isinstance(v, RecordIds) else v
        for k, v in values.items()
    }


class _Writer(CatalogWriter):
    system_code = "odoo"

    def __init__(self, target: MemoryTarget) -> None:
        self.target = target

    def open(self, ctx):  # noqa: ANN001, ANN201
        return self.target


class _Resolver:
    def __init__(self, target: MemoryTarget) -> None:
        self.target = target

    def resolve(self, system, version, interface):  # noqa: ANN001, ANN201
        return _Writer(self.target)


@pytest.fixture
def run_env(svc, world):  # noqa: ANN001, ANN201, F811
    _answer(world)
    acme = svc("acme")
    imp = acme.upload("catalog.csv", CSV).record
    view = acme.analyze(imp.id, world["inst"]["acme"].id)
    wanted = {  # décisions du fournisseur (« Sales Price » détectée texte, confirmée comme dans le cas réel)
        "Sales Price": "product:list_price",
        "Image URL": "product:main_image",
        "Tags": "product:tags",
        "Category": "product:category",
    }
    for c in view.columns:
        if c.source_name in wanted and (c.status != "MATCHED" or c.target_id != wanted[c.source_name]):
            acme.choose_target(imp.id, c.position, wanted[c.source_name])
    for c in acme.get(imp.id).columns:
        if c.status == "REVIEW" and not c.ignored:
            acme.ignore_column(imp.id, c.position)
    view = acme.ignore_unmatched(imp.id)
    assert view.status == "READY"
    target = MemoryTarget()
    return {
        "imports": acme,
        "id": imp.id,
        "target": target,
        "exec": CatalogImportExecutionService(acme, _Resolver(target)),
    }


def _items(run):  # noqa: ANN001, ANN202
    return {i.source_row: i for i in run.items}


def test_reference_execution(run_env):
    env, target = run_env, run_env["target"]
    run = env["exec"].execute(env["id"], "key-1")
    items = _items(run)
    assert {r: (i.action, i.status) for r, i in items.items()} == {
        1: ("CREATE", "DONE"),
        2: ("CREATE", "DONE"),  # aucune identité fiable
        3: ("UPDATE", "DONE"),  # SKU trouvé une fois
        4: ("CREATE", "DONE"),
        5: ("CREATE", "FAILED"),  # SKU porté par deux produits : jamais deviner
        6: ("SKIP", "SKIPPED"),  # obligatoire manquant
        7: ("UPDATE", "DONE"),  # même SKU que la ligne 1 : met à jour l'enregistrement créé
    }
    assert items[5].error_code == "AMBIGUOUS_IDENTITY" and items[6].error_code == "MISSING_REQUIRED"
    assert items[7].target_record_id == items[1].target_record_id
    assert {w["code"] for w in items[7].warnings} == {"DUPLICATE_IDENTITY"}
    assert (run.status, run.created, run.updated, run.skipped, run.failed, run.pending) == (
        "PARTIALLY_COMPLETED",
        3,
        2,
        1,
        1,
        0,
    )
    products = target.records["product.template"]
    timer = products[items[4].target_record_id]
    assert "list_price" not in timer  # « 89.90 EUR » : jamais envoyé
    assert "categ_id" not in timer  # catégorie ambiguë : jamais devinée
    assert {w["code"] for w in items[4].warnings} >= {"INVALID_NUMBER", "AMBIGUOUS_RELATION"}
    assert all("image_1920" not in p for p in products.values())  # images : V2d.2.1
    assert run.not_imported_yet == ["Main image"]
    chair = products["p-exist"]
    assert chair["list_price"] == 42.0 and chair["name"] == "Chair"  # prix vide : valeur existante gardée
    assert chair["categ_id"] == "c1" and chair["product_tag_ids"] == [
        "t1",
        target.labels["product.tag"]["seat"][0],
    ]
    # relations : résolues une fois, les manquantes créées une fois
    assert [c for c in target.calls if c[0] == "resolve_labels"][0][1][0] == "product.category"
    assert len([c for c in target.calls if c[0] == "resolve_labels"]) == 2
    assert run.relations["product:category"]["ambiguous cat"]["status"] == "AMBIGUOUS"


def test_same_request_key_is_the_same_execution(run_env):
    env, target = run_env, run_env["target"]
    first = env["exec"].execute(env["id"], "key-1")
    calls = len(target.calls)
    again = env["exec"].execute(env["id"], "key-1")  # double clic, retry HTTP
    assert again.id == first.id and len(target.calls) == calls  # rien n'est rejoué


def test_lost_response_is_resumed_without_duplicate(run_env):
    env, target = run_env, run_env["target"]
    target.lose_response_on = "T-1"  # ligne 4 : créée dans le système, réponse perdue
    run = env["exec"].execute(env["id"], "key-1")
    assert run.error_kind == "CONNECTION" and run.pending > 0 and run.status == "PARTIALLY_COMPLETED"
    pending = _items(run)[4]
    assert pending.status == "PENDING"
    before = len(target.records["product.template"])
    run = env["exec"].execute(env["id"], "key-1")  # reprise : même clé
    items = _items(run)
    assert items[4].status == "DONE" and run.pending == 0 and run.error is None
    keys = [c[1][1] for c in target.calls if c[0] == "create_with_key"]
    assert keys.count(pending.external_key) == 2  # même clé externe rejouée
    # à la reprise, rien de déjà traité n'est rejoué : aucun nouvel enregistrement
    assert len(target.records["product.template"]) - before == 0
    timers = [p for p in target.records["product.template"].values() if p.get("default_code") == "T-1"]
    assert len(timers) == 1  # aucun doublon


def test_a_second_import_on_purpose_updates_by_sku(run_env):
    env, target = run_env, run_env["target"]
    env["exec"].execute(env["id"], "key-1")
    count = len(target.records["product.template"])
    run = env["exec"].execute(env["id"], "key-2")  # nouvelle confirmation : nouvelle exécution
    items = _items(run)
    assert items[1].action == "UPDATE" and items[4].action == "UPDATE"
    assert items[2].action == "CREATE"  # ligne sans SKU : recréée (aucune identité fiable)
    assert len(target.records["product.template"]) == count + 1
    assert len(env["exec"].list_runs(env["id"])) == 2


def test_guards(run_env, svc, world, session):  # noqa: F811
    env = run_env
    with pytest.raises(ImportConflict):
        env["exec"].execute(env["id"], "")
    with pytest.raises(NotFound):  # autre organisation
        CatalogImportExecutionService(svc("globex"), _Resolver(env["target"])).execute(env["id"], "k")
    inst = world["inst"]["acme"]
    for cap in inst.capabilities:
        if cap.capability == "CREATE":
            cap.status = "UNAVAILABLE"
    session.flush()
    with pytest.raises(ImportConflict, match="does not accept imports"):
        env["exec"].execute(env["id"], "k")
    with pytest.raises(NotFound):
        env["exec"].get_run(env["id"], uuid.uuid4())
