"""Loader DATEV : spec `uie-datev-format/1` → snapshot → Registry → Compare.

Fixtures INVENTÉES (`fixtures/datev/`) ; les specs réelles, générées localement depuis les sources DATEV (non
versionnées), sont vérifiées par `test_real_specs_*` seulement si elles sont présentes sur le poste.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import uie_connectors  # noqa: F401  (enregistre les loaders)
import yaml
from uie_connectors.datev import DatevFormatSpec, DatevSchemaLoader
from uie_core.registry import (
    Capability,
    ChangeType,
    DataType,
    LoaderRegistry,
    SchemaLoaderError,
    compare_snapshots,
)

FIX = Path(__file__).parent / "fixtures" / "datev"
REAL = Path(__file__).resolve().parents[3] / "var" / "datev" / "specs" / "extf_700"


def _snap(name: str, folder: Path = FIX):
    return DatevSchemaLoader({"spec": folder / name}).build_snapshot()


def _fields(result, entity: str, change: ChangeType) -> dict[str, dict]:
    return {
        c.path.rsplit(".", 1)[-1]: c.details
        for c in result.changes
        if c.object_type == "field" and c.entity == entity and c.change_type == change
    }


def test_loader_is_registered():
    assert LoaderRegistry.get("datev") is DatevSchemaLoader


def test_identity_entities_and_fields():
    snap = _snap("demo-journal_v2.yaml")
    assert (snap.system, snap.product, snap.version, snap.interface, snap.interface_type) == (
        "datev",
        "demo-journal",
        "2",
        "extf",
        "file",
    )
    assert snap.metadata["system_name"] == "DATEV" and snap.metadata["product_name"] == "Demo Journal"
    assert snap.metadata["header_version"] == "900" and snap.metadata["data_category"] == 91
    assert [e.technical_name for e in snap.entities] == ["header", "demo_journal"]
    header, journal = snap.entities
    assert [f.technical_name for f in header.fields] == ["001", "002", "003"]
    assert header.fields[0].metadata["pattern"] == '^["](DEMO)["]$'
    # dialecte de la notation officielle DATEV, déclaré sur chaque champ qui a un motif (et seulement ceux-là)
    assert header.fields[0].metadata["pattern_syntax"] == "ascii"
    assert "pattern_syntax" not in header.fields[2].metadata
    f = {x.technical_name: x for x in journal.fields}
    assert len(f) == 6
    assert (f["001"].label, f["001"].native_type, f["001"].data_type, f["001"].required) == (
        "Betrag",
        "Betrag",
        DataType.DECIMAL,
        True,
    )
    assert f["001"].metadata == {"position": 1, "length": 10, "decimals": 2}
    assert f["002"].data_type == DataType.STRING  # Konto = identifiant
    assert f["003"].data_type == DataType.DATE
    assert f["005"].data_type == DataType.DECIMAL  # Zahl avec décimales
    assert f["006"].data_type == DataType.INTEGER
    assert f["005"].metadata["aliases"] == ["Anzahl"]


def test_capabilities_bulk_write_only():
    caps = _snap("demo-journal_v1.yaml").capabilities
    assert caps[Capability.BULK_WRITE.value] is True
    assert [k for k, v in caps.items() if v] == [Capability.BULK_WRITE.value]


def test_checksum_is_stable():
    assert _snap("demo-journal_v1.yaml").checksum() == _snap("demo-journal_v1.yaml").checksum()
    assert _snap("demo-journal_v1.yaml").checksum() != _snap("demo-journal_v2.yaml").checksum()


def test_invalid_specs_are_refused(tmp_path):
    with pytest.raises(SchemaLoaderError):
        DatevSchemaLoader({})
    with pytest.raises(SchemaLoaderError):
        DatevSchemaLoader({"spec": tmp_path / "absent.yaml"})
    data = yaml.safe_load((FIX / "demo-journal_v1.yaml").read_text(encoding="utf-8"))
    data["fields"][1]["position"] = 7  # trou dans la numérotation
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(SchemaLoaderError, match="non contiguës"):
        DatevSchemaLoader({"spec": bad})
    data["unknown"] = 1
    bad.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(SchemaLoaderError):
        DatevSchemaLoader({"spec": bad})


def test_spec_round_trip(tmp_path):
    spec = DatevFormatSpec.load(FIX / "demo-journal_v2.yaml")
    spec.dump(tmp_path / spec.file_name)
    assert spec.file_name == "demo-journal_v2.yaml"
    assert DatevFormatSpec.load(tmp_path / spec.file_name) == spec


def test_compare_versions_added_and_adjusted_fields():
    res = compare_snapshots(_snap("demo-journal_v1.yaml"), _snap("demo-journal_v2.yaml"))
    assert set(_fields(res, "demo_journal", ChangeType.ADDED)) == {"005", "006"}
    changed = _fields(res, "demo_journal", ChangeType.CHANGED)
    assert changed == {
        "001": {"length": {"from": 8, "to": 10}},
        "004": {"label": {"from": "Text kurz", "to": "Text kurz (alt)"}},
    }
    assert not res.filtered(ChangeType.REMOVED)
    assert not [c for c in res.changes if c.entity == "header"]
    # une ligne de synthèse par entité modifiée, puis le détail des champs
    assert [c.change_type for c in res.changes if c.object_type == "entity"] == [ChangeType.CHANGED]


# --------------------------------------------------------------------------- #
# specs réelles (générées localement, non versionnées) : conformité au changelog DATEV
# --------------------------------------------------------------------------- #
needs_real = pytest.mark.skipif(not REAL.is_dir(), reason="specs DATEV locales absentes (spec_builder)")


@needs_real
def test_real_specs_buchungsstapel_versions():
    snaps = {v: _snap(f"buchungsstapel_v{v}.yaml", REAL) for v in ("10", "11", "12", "13")}
    counts = {v: len(s.entities[1].fields) for v, s in snaps.items()}
    assert counts == {"10": 121, "11": 122, "12": 124, "13": 125}

    r = compare_snapshots(snaps["12"], snaps["13"])
    assert set(_fields(r, "buchungsstapel", ChangeType.ADDED)) == {"125"}
    assert len([c for c in r.changes if c.object_type == "field"]) == 1

    r = compare_snapshots(snaps["11"], snaps["12"])
    assert set(_fields(r, "buchungsstapel", ChangeType.ADDED)) == {"123", "124"}
    assert set(_fields(r, "buchungsstapel", ChangeType.CHANGED)) == {"040", "041"}
    assert len([c for c in r.changes if c.object_type == "field"]) == 4

    for a, b in (("10", "11"), ("11", "12"), ("12", "13")):
        assert not compare_snapshots(snaps[a], snaps[b]).filtered(ChangeType.REMOVED)


@needs_real
def test_real_specs_debitoren_kreditoren():
    snap = _snap("debitoren-kreditoren_v5.yaml", REAL)
    assert (snap.product, snap.version) == ("debitoren-kreditoren", "5")
    assert [len(e.fields) for e in snap.entities] == [31, 254]
    assert snap.entities[1].fields[0].technical_name == "001"
