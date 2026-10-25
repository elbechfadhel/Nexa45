"""Tests du System Knowledge Registry : publication immutable, relecture, comparaison."""

from __future__ import annotations

import pytest
from uie_core.identity import SystemContext
from uie_core.registry import (
    Capability,
    ChangeType,
    DataType,
    EntityDef,
    FieldDef,
    ModuleDef,
    RelationDef,
    RelationType,
    SchemaSnapshotModel,
    SnapshotRef,
    compare_snapshots,
)
from uie_core.registry.service import RegistryService, SnapshotAlreadyExists


def _snapshot(version: str, *, extra_field: bool = False, email_required: bool = False):
    partner_fields = [
        FieldDef(technical_name="name", label="Name", data_type=DataType.STRING, required=True),
        FieldDef(technical_name="email", label="Email", data_type=DataType.STRING, required=email_required),
        FieldDef(
            technical_name="country_id", label="Country", data_type=DataType.REFERENCE, native_type="many2one"
        ),
        FieldDef(technical_name="display_name", data_type=DataType.STRING, stored=False, computed=True),
    ]
    if extra_field:
        partner_fields.append(FieldDef(technical_name="x_vat_check", data_type=DataType.BOOLEAN))
    return SchemaSnapshotModel(
        system="demo",
        product="demo",
        version=version,
        interface="orm",
        interface_type="orm",
        source={"loader": "test"},
        modules=[ModuleDef(technical_name="base"), ModuleDef(technical_name="product")],
        entities=[
            EntityDef(
                technical_name="res.partner",
                display_name="Contact",
                module="base",
                physical_name="res_partner",
                fields=partner_fields,
                relations=[
                    RelationDef(
                        source_field="country_id",
                        target_entity="res.country",
                        relation_type=RelationType.MANY_TO_ONE,
                    )
                ],
                capabilities={Capability.READ: True, Capability.CREATE: True},
            ),
            EntityDef(
                technical_name="product.template",
                module="product",
                fields=[
                    FieldDef(technical_name="name", data_type=DataType.STRING, required=True),
                    FieldDef(technical_name="list_price", data_type=DataType.DECIMAL),
                ],
            ),
        ],
        capabilities={Capability.SCHEMA_DISCOVERY: True},
    )


def test_checksum_is_stable_and_content_based():
    a, b = _snapshot("1"), _snapshot("1")
    assert a.checksum() == b.checksum()
    assert a.checksum() != _snapshot("1", extra_field=True).checksum()


def test_publish_then_reload_roundtrip(session):
    svc = RegistryService(session, SystemContext("test"))
    row = svc.publish_snapshot(_snapshot("17"))
    assert row.snapshot_version == "1.0.0"
    assert row.entity_count == 2 and row.field_count == 6

    reloaded = svc.load_snapshot_model(row)
    assert reloaded.entity("res.partner").field("display_name").computed is True
    assert reloaded.entity("res.partner").relations[0].target_entity == "res.country"
    assert reloaded.entity("res.partner").capabilities[Capability.CREATE] is True
    assert reloaded.capabilities[Capability.SCHEMA_DISCOVERY] is True
    assert reloaded.checksum() == _snapshot("17").checksum()


def test_snapshots_are_immutable(session):
    svc = RegistryService(session, SystemContext("test"))
    first = svc.publish_snapshot(_snapshot("17"))
    # même contenu → pas de nouveau snapshot
    same = svc.publish_snapshot(_snapshot("17"))
    assert same.id == first.id
    # contenu différent → nouvelle version auto-incrémentée, l'ancienne reste intacte
    second = svc.publish_snapshot(_snapshot("17", extra_field=True))
    assert second.id != first.id and second.snapshot_version == "1.0.1"
    assert svc.stats(first.id)["fields"] == 6 and svc.stats(second.id)["fields"] == 7
    # version explicite déjà prise → refus
    with pytest.raises(SnapshotAlreadyExists):
        svc.publish_snapshot(_snapshot("17", email_required=True), snapshot_version="1.0.0")


def test_resolve_and_browse(session):
    svc = RegistryService(session, SystemContext("test"))
    svc.publish_snapshot(_snapshot("17"))
    snap = svc.resolve_snapshot(SnapshotRef(system="demo", version="17"))
    names = [e.technical_name for e in svc.list_entities(snap.id)]
    assert names == ["product.template", "res.partner"]
    ent = svc.get_entity(snap.id, "res.partner")
    assert {f.technical_name for f in ent.fields} >= {"name", "email", "country_id"}
    assert [n for n, _ in svc.search_fields(snap.id, "price")] == ["product.template"]
    systems = svc.list_systems()
    assert systems[0].code == "demo" and systems[0].products[0].versions[0].version == "17"


def test_version_compare(session):
    svc = RegistryService(session, SystemContext("test"))
    svc.publish_snapshot(_snapshot("17"))
    svc.publish_snapshot(_snapshot("18", extra_field=True, email_required=True))
    res = svc.compare(SnapshotRef("demo", "17"), SnapshotRef("demo", "18"))
    by_path = {(c.path, c.change_type) for c in res.changes}
    assert ("res.partner.x_vat_check", ChangeType.ADDED) in by_path
    assert ("res.partner.email", ChangeType.CHANGED) in by_path
    assert ("res.partner", ChangeType.CHANGED) in by_path
    assert res.counts()["ADDED"] == 1 and res.counts()["CHANGED"] == 2


def test_compare_pure_function_removed_entity():
    a = _snapshot("17")
    b = a.model_copy(update={"entities": a.entities[:1]})
    res = compare_snapshots(a, b)
    assert [c.path for c in res.filtered(ChangeType.REMOVED)] == ["product.template"]


def test_compare_for_entity_with_dotted_names():
    # « product.template.attribute.value » commence par « product.template. » : le filtre
    # par entité ne doit pas se fier au préfixe du path
    def snap(label: str) -> SchemaSnapshotModel:
        return SchemaSnapshotModel(
            system="s",
            product="p",
            version="1",
            interface="i",
            interface_type="orm",
            entities=[
                EntityDef(
                    technical_name="product.template", fields=[FieldDef(technical_name="name", label=label)]
                ),
                EntityDef(
                    technical_name="product.template.attribute.value",
                    fields=[FieldDef(technical_name="name", label=label)],
                ),
            ],
        )

    res = compare_snapshots(snap("A"), snap("B"))
    assert res.counts()["CHANGED"] == 4  # 2 entités + 2 champs
    only = res.for_entity("product.template")
    assert {c.path for c in only.changes} == {"product.template", "product.template.name"}
    assert only.counts()["CHANGED"] == 2


def test_compare_field_length_and_decimals_metadata():
    # formats de fichier : longueur / décimales d'une colonne comparées quand le loader les fournit
    def snap(meta: dict) -> SchemaSnapshotModel:
        return SchemaSnapshotModel(
            system="s",
            product="p",
            version="1",
            interface="i",
            interface_type="file",
            entities=[EntityDef(technical_name="e", fields=[FieldDef(technical_name="001", metadata=meta)])],
        )

    res = compare_snapshots(snap({"length": 8, "decimals": 2}), snap({"length": 10, "decimals": 2}))
    [field] = [c for c in res.changes if c.object_type == "field"]
    assert field.details == {"length": {"from": 8, "to": 10}}
    # absentes des deux côtés (Odoo) ou métadonnée non comparée : aucune différence
    assert not compare_snapshots(snap({}), snap({})).changes
    assert not compare_snapshots(snap({"position": 1}), snap({"position": 2})).changes


def test_publish_uses_display_names_and_product_label(session):
    svc = RegistryService(session, SystemContext("test"))
    snap = _snapshot("1").model_copy(
        update={"product": "fmt", "metadata": {"system_name": "Demo System", "product_name": "Demo Format"}}
    )
    row = svc.publish_snapshot(snap)
    product = row.interface.version.product
    assert (product.system.name, product.name) == ("Demo System", "Demo Format")
    assert svc.snapshot_label(row) == "demo fmt 1 @1.0.0"
    assert svc.snapshot_label(svc.publish_snapshot(_snapshot("1"))) == "demo 1 @1.0.0"  # produit = système
