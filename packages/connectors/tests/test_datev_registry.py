"""Specs DATEV publiées dans le Registry : arbre système → format → version → extf, résolution par produit,
libellé, Compare et immutabilité (fixtures INVENTÉES)."""

from __future__ import annotations

from pathlib import Path

import pytest
import uie_connectors  # noqa: F401  (enregistre les loaders)
import yaml
from uie_connectors.datev import DatevSchemaLoader
from uie_core.identity import SystemContext
from uie_core.registry import ChangeType, RegistryService, SnapshotRef
from uie_core.registry.service import SnapshotAlreadyExists

FIX = Path(__file__).parent / "fixtures" / "datev"


def _snap(name: str, folder: Path = FIX):
    return DatevSchemaLoader({"spec": folder / name}).build_snapshot()


def _fields(result, entity: str, change: ChangeType) -> set[str]:
    return {
        c.path.rsplit(".", 1)[-1]
        for c in result.changes
        if c.object_type == "field" and c.entity == entity and c.change_type == change
    }


def test_registry_tree_resolve_and_immutability(session, tmp_path):
    svc = RegistryService(session, SystemContext("test"))
    for name in ("demo-journal_v1.yaml", "demo-journal_v2.yaml", "demo-stamm_v1.yaml"):
        svc.publish_snapshot(_snap(name), "1.0.0")
    datev = next(s for s in svc.list_systems() if s.code == "datev")
    assert datev.name == "DATEV"
    tree = {p.code: (p.name, sorted(v.version for v in p.versions)) for p in datev.products}
    assert tree == {"demo-journal": ("Demo Journal", ["1", "2"]), "demo-stamm": ("Demo/Stamm", ["1"])}
    for p in datev.products:
        for v in p.versions:
            assert [(i.code, i.type, [s.snapshot_version for s in i.snapshots]) for i in v.interfaces] == [
                ("extf", "file", ["1.0.0"])
            ]

    # même numéro de version dans deux produits : le produit lève l'ambiguïté
    j1 = svc.resolve_snapshot(SnapshotRef(system="datev", product="demo-journal", version="1"))
    s1 = svc.resolve_snapshot(SnapshotRef(system="datev", product="demo-stamm", version="1"))
    assert j1.id != s1.id
    assert svc.snapshot_label(j1) == "datev demo-journal 1 @1.0.0"

    res = svc.compare(
        SnapshotRef(system="datev", product="demo-journal", version="1"),
        SnapshotRef(system="datev", product="demo-journal", version="2"),
    )
    assert _fields(res, "demo_journal", ChangeType.ADDED) == {"005", "006"}
    assert res.source == "datev demo-journal 1 @1.0.0"

    # snapshot immutable : même version, autre contenu → refus
    data = yaml.safe_load((FIX / "demo-journal_v1.yaml").read_text(encoding="utf-8"))
    data["fields"][0]["label"] = "Betrag (neu)"
    changed = tmp_path / "demo-journal_v1.yaml"
    changed.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(SnapshotAlreadyExists):
        svc.publish_snapshot(_snap(changed.name, tmp_path), "1.0.0")
    # même contenu → le snapshot existant est rendu
    assert svc.publish_snapshot(_snap("demo-journal_v1.yaml")).id == j1.id
