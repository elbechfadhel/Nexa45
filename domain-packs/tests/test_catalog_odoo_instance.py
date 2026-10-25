"""Domaine PRODUCT effectif de l'instance client Odoo 17 (fixture odoo17_customer_instance.json).

Chaîne réelle : fixture standard publiée → instance → découverte via le faux serveur XML-RPC →
ProductDomainService avec le vrai domain pack YAML. L'analyse ne rappelle jamais le serveur.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import uie_domain_packs  # noqa: F401 — enregistre les packs découverts
from pydantic import SecretStr
from uie_connectors.odoo import OdooSchemaDiscoveryProvider, OdooSchemaLoader
from uie_connectors.odoo.testing import FakeOdooServer
from uie_core.domains import EntityRole, FieldGroup, FieldOrigin, ProductDomainService, Relevance
from uie_core.identity import GO45_DEV_ORG_ID, SystemContext
from uie_core.instances import FernetSecretStore, InstanceCreate, InstanceService, ProviderResolver
from uie_core.registry import RegistryService

FIX = Path(__file__).resolve().parents[2] / "packages" / "connectors" / "tests" / "fixtures"
PASSWORD = "Odoo-Cust0mer-S3cret"


class FakeOdooResolver(ProviderResolver):
    def __init__(self, fake: FakeOdooServer) -> None:
        self.fake = fake

    def resolve(self, system, version, interface):  # noqa: ANN001, ANN201
        return OdooSchemaDiscoveryProvider(proxy_factory=self.fake.proxy)


@pytest.fixture
def fake() -> FakeOdooServer:
    return FakeOdooServer.from_dump(FIX / "odoo17_customer_instance.json", password=PASSWORD)


@pytest.fixture
def setup(session, fake):  # noqa: ANN201
    RegistryService(session, SystemContext("test")).publish_snapshot(
        OdooSchemaLoader({"json": FIX / "odoo17_ref.json"}).build_snapshot()
    )
    instances = InstanceService(
        session,
        SystemContext("test"),
        FernetSecretStore(FernetSecretStore.generate_key()),
        FakeOdooResolver(fake),
    )
    inst = instances.create(
        InstanceCreate(
            organization_id=GO45_DEV_ORG_ID,
            name="customer17",
            system="odoo",
            version="17",
            endpoint="http://odoo17-customer:8069",
            config={"db": "customer17", "username": "admin"},
            password=SecretStr(PASSWORD),
        )
    )
    instances.discover_schema(inst.id)
    return ProductDomainService(session, SystemContext("test")), inst


def test_standard_definition_odoo17(session):
    RegistryService(session, SystemContext("test")).publish_snapshot(
        OdooSchemaLoader({"json": FIX / "odoo17_ref.json"}).build_snapshot()
    )
    d = ProductDomainService(session, SystemContext("test")).get_standard_definition("odoo", "17").definition
    roles = {e.technical_name: e.role for e in d.entities if e.present}
    assert roles["product.template"] == EntityRole.PRIMARY and roles["product.product"] == EntityRole.VARIANT
    assert d.pack == "catalog/odoo/product_domain.yaml"


def test_customer_product_domain(setup, fake):
    svc, inst = setup
    calls = len(fake.calls)
    stored = svc.build_instance_domain(inst.id)
    assert len(fake.calls) == calls  # aucune lecture de l'instance pendant l'analyse
    s = stored.snapshot
    ext = {f.path: f for f in s.extensions}

    # 12. x_supplier_ref → extension CUSTOM, UNRESOLVED (métadonnées techniques reprises du schéma)
    sup = ext["product.template.x_supplier_ref"]
    assert (sup.origin, sup.relevance, sup.label, sup.native_type) == (
        FieldOrigin.CUSTOM,
        Relevance.UNRESOLVED,
        "Supplier Reference",
        "char",
    )
    # 13. project_id (sale_project) → extension THIRD_PARTY, UNRESOLVED, provenance conservée
    prj = ext["product.template.project_id"]
    assert (prj.origin, prj.relevance) == (FieldOrigin.THIRD_PARTY, Relevance.UNRESOLVED)
    assert prj.modules == ["sale_project"] and prj.relation_target == "project.project"
    assert set(ext) == {
        "product.template.x_supplier_ref",
        "product.template.x_pack_code",
        "product.template.x_notes_internal",
        "product.product.x_supplier_ref",
        "product.template.project_id",
        "product.template.project_template_id",
        "product.template.rating_ids",
        "product.template.service_policy",
        "product.template.service_tracking",
    }

    # 14. hr / project n'apparaissent nulle part dans le domaine
    names = (
        [e.technical_name for e in s.entities]
        + [f.path for f in s.fields]
        + [f"{x.entity_technical_name}.{x.field_technical_name}" for x in s.missing_standard]
    )
    assert not any(n.startswith(("hr.", "project.", "res.partner", "rating.")) for n in names)

    # standard conservé, médias présents dans l'instance
    assert {f.path for f in s.fields if f.group == FieldGroup.MEDIA} >= {
        "product.template.image_1920",
        "product.product.image_variant_1920",
    }
    assert s.missing_standard == []  # aucun élément produit standard retiré dans cette instance


def test_customer_filters_through_service(setup):
    svc, inst = setup
    svc.build_instance_domain(inst.id)
    custom = svc.list_domain_fields(inst.id, origin=FieldOrigin.CUSTOM)
    assert [f.path for f in custom] == [
        "product.template.x_supplier_ref",
        "product.template.x_pack_code",
        "product.template.x_notes_internal",
        "product.product.x_supplier_ref",
    ]
    unresolved = svc.list_domain_fields(inst.id, relevance=Relevance.UNRESOLVED)
    assert {f.path for f in unresolved} == {
        "product.template.x_supplier_ref",
        "product.template.x_pack_code",
        "product.template.x_notes_internal",
        "product.product.x_supplier_ref",
        "product.template.project_id",
        "product.template.project_template_id",
        "product.template.rating_ids",
        "product.template.service_policy",
        "product.template.service_tracking",
    }
