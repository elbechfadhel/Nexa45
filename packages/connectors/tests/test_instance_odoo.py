"""Instance Management + Discovery + Diff avec l'adapter Odoo, sur odoo17_customer_instance.json.

Le faux serveur XML-RPC (uie_connectors.odoo.testing) sert la fixture : on exerce le vrai code du
provider, et on vérifie qu'aucune méthode d'écriture n'est jamais appelée.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from uie_connectors.odoo import OdooSchemaDiscoveryProvider, OdooSchemaLoader
from uie_connectors.odoo.discovery import READ_ONLY_METHODS, ReadOnlyObjectProxy, ReadOnlyViolation
from uie_connectors.odoo.testing import FakeOdooServer
from uie_core.db.base import Base
from uie_core.identity import GO45_DEV_ORG_ID, SystemContext
from uie_core.instances import FernetSecretStore, InstanceCreate, InstanceService, Origin, ProviderResolver
from uie_core.registry import RegistryService

FIX = Path(__file__).parent / "fixtures"
PASSWORD = "Odoo-Cust0mer-S3cret"
WRITE_METHODS = {"create", "write", "unlink", "copy", "action_install", "button_immediate_install"}


class FakeOdooResolver(ProviderResolver):
    def __init__(self, fake: FakeOdooServer) -> None:
        self.fake = fake

    def resolve(self, system, version, interface):  # noqa: ANN001, ANN201
        return OdooSchemaDiscoveryProvider(proxy_factory=self.fake.proxy)


@pytest.fixture
def fake() -> FakeOdooServer:
    return FakeOdooServer.from_dump(FIX / "odoo17_customer_instance.json", password=PASSWORD)


@pytest.fixture
def base_row(session):
    return RegistryService(session, SystemContext("test")).publish_snapshot(
        OdooSchemaLoader({"json": FIX / "odoo17_ref.json"}).build_snapshot()
    )


@pytest.fixture
def svc(session, fake, base_row) -> InstanceService:
    return InstanceService(
        session,
        SystemContext("test"),
        FernetSecretStore(FernetSecretStore.generate_key()),
        FakeOdooResolver(fake),
    )


@pytest.fixture
def inst(svc):
    return svc.create(
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


def test_create_odoo_instance(inst, base_row):
    assert inst.interface_id == base_row.interface_id
    conn = inst.connections[0]
    assert conn.connector_type == "odoo.xmlrpc" and conn.config == {"db": "customer17", "username": "admin"}
    assert PASSWORD not in conn.secret_ref


def test_test_connection_verifies_read_only_capabilities(svc, inst, fake):
    res = svc.test_connection(inst.id)
    assert res.ok and res.server_info["server_serie"] == "17.0" and res.server_info["uid"] == 2
    status = {c.capability: c.status for c in svc.get(inst.id).capabilities}
    assert {k: status[k] for k in ("READ", "SEARCH", "BULK_READ", "SCHEMA_DISCOVERY")} == dict.fromkeys(
        ("READ", "SEARCH", "BULK_READ", "SCHEMA_DISCOVERY"), "VERIFIED"
    )
    assert {status[k] for k in ("CREATE", "UPDATE", "DELETE", "BULK_WRITE")} == {"DECLARED"}
    assert status["EVENTS"] == "UNAVAILABLE"
    # VERIFIED = réellement exercé ; jamais la moindre écriture
    assert {"search_read", "search", "search_count", "read", "fields_get"} <= fake.methods_called
    assert not fake.methods_called & WRITE_METHODS


def test_wrong_password_nothing_verified(svc, inst, fake):
    fake.password = "other"
    res = svc.test_connection(inst.id)
    assert not res.ok and "refusée" in res.error and PASSWORD not in res.error
    status = {c.capability: c.status for c in svc.get(inst.id).capabilities}
    assert status["READ"] == "UNKNOWN" and status["CREATE"] == "DECLARED"
    assert svc.get(inst.id).connections[0].last_test_status == "FAILED"


def test_read_only_proxy_blocks_writes():
    class Spy:
        called = False

        def execute_kw(self, *a):  # noqa: ANN002, ANN201
            Spy.called = True

    proxy = ReadOnlyObjectProxy(Spy())
    for method in WRITE_METHODS:
        with pytest.raises(ReadOnlyViolation):
            proxy.execute_kw("db", 2, "pwd", "res.partner", method, [{}])
    assert not Spy.called and "create" not in READ_ONLY_METHODS


def test_discovery_snapshots_and_baseline(svc, inst, base_row, fake):
    s1 = svc.discover_schema(inst.id)
    s2 = svc.discover_schema(inst.id)
    assert (s1.sequence, s2.sequence) == (1, 2) and s1.id != s2.id
    assert s1.base_snapshot_id == s2.base_snapshot_id == base_row.id
    assert s1.entity_count == 12 and s1.relation_count > 0 and s1.field_count > 70
    assert s1.provenance["db"] == "customer17" and s1.provenance["provider"] == "OdooSchemaDiscoveryProvider"
    assert not fake.methods_called & WRITE_METHODS


def test_instance_diff_classification(svc, inst):
    svc.discover_schema(inst.id)
    r = svc.get_schema_diff(inst.id)
    s = r.summary
    assert r.base_snapshot.checksum == r.instance_snapshot.base_snapshot.checksum
    # CUSTOM
    assert sorted(i.path for i in s.custom_fields) == [
        "product.product.x_supplier_ref",  # copie related (héritage par délégation)
        "product.template.x_notes_internal",  # Semantic Enrichment : aucune proposition
        "product.template.x_pack_code",  # Semantic Enrichment : proposition packaging_code
        "product.template.x_supplier_ref",  # Product Domain : extension CUSTOM du catalogue
        "res.partner.x_customer_no",
    ]
    assert [i.path for i in s.custom_entities] == ["x_customer_segment"]
    # THIRD_PARTY (hr, project : hors baseline standard)
    assert {i.path for i in s.third_party_entities} == {"hr.employee", "project.project", "rating.rating"}
    assert {i.path for i in s.third_party_fields} == {
        "res.partner.employee_ids",
        "res.partner.rating_ids",  # modules « base, rating » : introduit par rating
        "account.move.rating_ids",  # attribué à account par Odoo, mais pointe vers rating.rating
        "product.template.project_id",  # module sale_project hors baseline
        "product.template.project_template_id",
        "product.template.service_policy",
        "product.template.service_tracking",
        "product.template.rating_ids",
    }
    assert s.third_party_modules == ["hr", "project", "rating", "sale_project"]
    why = next(c for c in r.changes if c.path == "account.move.rating_ids").reason
    assert "rating.rating" in why
    # module absent démontré vs élément manquant non expliqué
    assert [(mm.name, mm.removed_elements) for mm in s.missing_standard_modules] == [("mail", 1)]
    assert [i.path for i in s.missing_standard_elements] == ["res.country.code"]
    assert next(c for c in r.changes if c.path == "res.country.code").origin == Origin.UNKNOWN
    assert next(c for c in r.changes if c.path == "mail.thread").origin == Origin.STANDARD
    # STANDARD modifié → risque de compatibilité
    assert [i.path for i in s.compatibility_risks] == ["res.partner.email"]
    assert s.compatibility_risks[0].details["required"] == {"from": False, "to": True}
    # UNKNOWN : ajouté sans module
    assert next(c for c in r.changes if c.path == "res.partner.legacy_ref").origin == Origin.UNKNOWN
    assert s.by_origin == {Origin.STANDARD: 2, Origin.CUSTOM: 6, Origin.THIRD_PARTY: 17, Origin.UNKNOWN: 2}
    assert (s.entities.added, s.entities.removed) == (4, 1)
    assert (s.fields.added, s.fields.removed, s.fields.changed) == (14, 1, 1)
    assert s.relations.added == 6
    assert svc.recompute_diff(inst.id) == s


def test_standard_instance_has_empty_diff(session, base_row):
    """Une instance identique au standard (même dump) ne produit aucune différence."""
    fake = FakeOdooServer.from_dump(FIX / "odoo17_ref.json", password=PASSWORD)
    svc = InstanceService(
        session,
        SystemContext("test"),
        FernetSecretStore(FernetSecretStore.generate_key()),
        FakeOdooResolver(fake),
    )
    i = svc.create(
        InstanceCreate(
            organization_id=GO45_DEV_ORG_ID,
            name="std17",
            system="odoo",
            version="17",
            endpoint="http://odoo17:8069",
            config={"db": fake.db, "username": "admin"},
            password=SecretStr(PASSWORD),
        )
    )
    svc.discover_schema(i.id)
    assert svc.get_schema_diff(i.id).changes == []


def test_secret_never_exposed(svc, inst, session, caplog):
    caplog.set_level(logging.DEBUG)
    svc.test_connection(inst.id)
    snap = svc.discover_schema(inst.id)
    session.flush()
    assert PASSWORD not in json.dumps(snap.schema_model) + json.dumps(snap.provenance)
    for table in Base.metadata.sorted_tables:
        dump = session.execute(text(f"SELECT coalesce(json_agg(t)::text, '') FROM {table.name} t")).scalar()
        assert PASSWORD not in dump, table.name
    assert PASSWORD not in caplog.text
