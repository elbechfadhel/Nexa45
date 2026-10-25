"""InstanceService générique : provider factice d'un système imaginaire (aucune dépendance Odoo)."""

from __future__ import annotations

import ast
import logging
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from uie_core.db.base import Base
from uie_core.identity import GO45_DEV_ORG_ID, SystemContext
from uie_core.instances import (
    CapabilityCheck,
    CapabilityStatus,
    ClassifiedChange,
    ConnectionContext,
    ConnectionTestResult,
    DiffAnalysis,
    FernetSecretStore,
    InstanceCreate,
    InstanceService,
    Origin,
    ProviderResolver,
    SchemaDiscoveryProvider,
    SecretError,
)
from uie_core.registry import (
    Capability,
    DataType,
    EntityDef,
    FieldDef,
    NotFound,
    RegistryService,
    SchemaSnapshotModel,
)

PASSWORD = "Sup3r-Secret-Pa55!"
CORE_SRC = Path(__file__).resolve().parents[1] / "src" / "uie_core"


def _snap(extra_field: bool = False) -> SchemaSnapshotModel:
    fields = [FieldDef(technical_name="name", data_type=DataType.STRING)]
    if extra_field:
        fields.append(FieldDef(technical_name="zz_custom", data_type=DataType.STRING))
    return SchemaSnapshotModel(
        system="demo",
        product="demo",
        version="1",
        interface="api",
        interface_type="rest",
        source={"loader": "demo", "endpoint": "https://demo.invalid"},
        entities=[EntityDef(technical_name="customer", fields=fields)],
    )


class FakeProvider(SchemaDiscoveryProvider):
    """Système imaginaire : lecture vérifiable, écriture déclarée, pas d'événements."""

    system_code = "demo"
    connector_type = "demo.rest"

    def __init__(self) -> None:
        self.discoveries = 0
        self.exercised: list[Capability] = []
        self.fail_with: str | None = None

    def declared_capabilities(self) -> dict[Capability, bool]:
        return {c: c != Capability.EVENTS for c in Capability}

    def test_connection(self, ctx: ConnectionContext) -> ConnectionTestResult:
        assert ctx.secret is not None and ctx.secret.get_secret_value() == PASSWORD
        if self.fail_with:
            raise RuntimeError(self.fail_with)
        return ConnectionTestResult(
            ok=True, server_info={"api": "v1"}, capabilities=self.detect_capabilities(ctx)
        )

    def detect_capabilities(self, ctx: ConnectionContext) -> list[CapabilityCheck]:
        out = []
        for cap in Capability:
            if cap in (Capability.READ, Capability.SEARCH):
                self.exercised.append(cap)  # « appel réel » simulé
                out.append(
                    CapabilityCheck(capability=cap, status=CapabilityStatus.VERIFIED, detail="GET /customers")
                )
            elif cap == Capability.EVENTS:
                out.append(CapabilityCheck(capability=cap, status=CapabilityStatus.UNAVAILABLE))
            else:
                out.append(CapabilityCheck(capability=cap, status=CapabilityStatus.DECLARED))
        return out

    def discover_schema(self, ctx: ConnectionContext) -> SchemaSnapshotModel:
        self.discoveries += 1
        return _snap(extra_field=self.discoveries > 1)

    def classify(self, base, instance, changes) -> DiffAnalysis:  # noqa: ANN001
        return DiffAnalysis(
            classified={
                c.path: ClassifiedChange(
                    path=c.path, origin=Origin.CUSTOM if "zz_" in c.path else Origin.UNKNOWN
                )
                for c in changes
            }
        )


class FakeResolver(ProviderResolver):
    def __init__(self, provider: SchemaDiscoveryProvider) -> None:
        self.provider = provider

    def resolve(self, system: str, version: str, interface: str) -> SchemaDiscoveryProvider:
        assert (system, version, interface) == ("demo", "1", "api")
        return self.provider


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def svc(session, provider) -> InstanceService:
    RegistryService(session, SystemContext("test")).publish_snapshot(_snap())
    return InstanceService(
        session,
        SystemContext("test"),
        FernetSecretStore(FernetSecretStore.generate_key()),
        FakeResolver(provider),
    )


def _create(svc: InstanceService):
    return svc.create(
        InstanceCreate(
            organization_id=GO45_DEV_ORG_ID,
            name="demo-client",
            system="demo",
            version="1",
            endpoint="https://demo.invalid",
            config={"account": "acme"},
            password=SecretStr(PASSWORD),
        )
    )


def test_create_instance_declares_but_does_not_verify(svc):
    inst = _create(svc)
    assert inst.interface.version.product.system.code == "demo"
    assert inst.connections[0].connector_type == "demo.rest"
    assert inst.connections[0].secret_ref.startswith("fernet:v1:")
    status = {c.capability: c.status for c in inst.capabilities}
    assert status["READ"] == "DECLARED" and status["EVENTS"] == "UNAVAILABLE"
    assert "VERIFIED" not in status.values()  # jamais VERIFIED sans exercice réel


def test_capability_verified_only_after_real_exercise(svc, provider):
    inst = _create(svc)
    assert provider.exercised == []
    res = svc.test_connection(inst.id)
    assert res.ok and provider.exercised == [Capability.READ, Capability.SEARCH]
    status = {c.capability: c.status for c in svc.get(inst.id).capabilities}
    assert status["READ"] == status["SEARCH"] == "VERIFIED"
    assert status["CREATE"] == status["UPDATE"] == status["DELETE"] == "DECLARED"
    conn = svc.get(inst.id).connections[0]
    assert conn.last_test_status == "OK" and conn.last_test_at is not None and conn.last_test_error is None


def test_failed_connection_is_persisted_and_redacted(svc, provider):
    inst = _create(svc)
    provider.fail_with = f"401 invalid credentials for acme:{PASSWORD}"
    res = svc.test_connection(inst.id)
    assert not res.ok and PASSWORD not in res.error and "***" in res.error
    conn = svc.get(inst.id).connections[0]
    assert conn.last_test_status == "FAILED" and PASSWORD not in conn.last_test_error


def test_each_discovery_creates_a_new_immutable_snapshot(svc, session):
    inst = _create(svc)
    first = svc.discover_schema(inst.id)
    second = svc.discover_schema(inst.id)
    assert (first.sequence, second.sequence) == (1, 2) and first.id != second.id
    assert first.checksum != second.checksum
    assert [s.sequence for s in svc.list_instance_snapshots(inst.id)] == [1, 2]
    # base_snapshot_id = baseline EXACTE du moment, même si un standard plus récent est publié ensuite
    base_id = first.base_snapshot_id
    newer = RegistryService(session, SystemContext("test")).publish_snapshot(_snap(extra_field=True))
    third = svc.discover_schema(inst.id)
    assert svc.get_instance_snapshot(inst.id, 1).base_snapshot_id == base_id
    assert third.base_snapshot_id == newer.id != base_id
    # immutabilité : ORM et trigger PostgreSQL
    with pytest.raises(ValueError, match="immutable"):
        first.checksum = "0" * 64
        session.flush()
    session.rollback()


def test_immutability_enforced_by_database(svc, session):
    inst = _create(svc)
    snap = svc.discover_schema(inst.id)
    session.commit()  # savepoint : reste dans la transaction de test
    with pytest.raises(Exception, match="immutable"):
        session.execute(text("UPDATE instance_schema_snapshots SET checksum='x' WHERE id=:i"), {"i": snap.id})
    session.rollback()


def test_diff_summary_is_generic_and_reproducible(svc):
    inst = _create(svc)
    svc.discover_schema(inst.id)
    svc.discover_schema(inst.id)  # le 2e ajoute zz_custom
    r = svc.get_schema_diff(inst.id)
    assert r.instance_snapshot.sequence == 2 and r.base_snapshot.id == r.instance_snapshot.base_snapshot_id
    assert [c.path for c in r.summary.custom_fields] == ["customer.zz_custom"]
    assert r.summary.fields.added == 1 and r.summary.by_origin[Origin.CUSTOM] == 1
    assert svc.recompute_diff(inst.id) == r.summary  # même résultat depuis le snapshot stocké
    assert svc.get_schema_diff(inst.id, 1).summary.fields.added == 0


def test_secret_never_stored_logged_or_snapshotted(svc, session, caplog):
    caplog.set_level(logging.DEBUG)
    inst = _create(svc)
    svc.test_connection(inst.id)
    svc.discover_schema(inst.id)
    session.flush()
    for table in Base.metadata.sorted_tables:
        dump = session.execute(text(f"SELECT coalesce(json_agg(t)::text, '') FROM {table.name} t")).scalar()
        assert PASSWORD not in dump, table.name
    assert PASSWORD not in caplog.text
    assert PASSWORD not in repr(
        InstanceCreate(
            organization_id=GO45_DEV_ORG_ID,
            name="x",
            system="demo",
            version="1",
            endpoint="e",
            password=PASSWORD,
        )
    )


def test_secret_store_rejects_wrong_key():
    ref = FernetSecretStore(FernetSecretStore.generate_key()).put(SecretStr(PASSWORD))
    with pytest.raises(SecretError) as exc:
        FernetSecretStore(FernetSecretStore.generate_key()).get(ref)
    assert PASSWORD not in str(exc.value)


def test_core_has_no_dependency_on_concrete_systems():
    # 1) aucun import de connecteur ni identifiant Odoo dans le code du Core (docstrings exclues)
    for py in CORE_SRC.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                mods = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
                assert not any(m.startswith("uie_connectors") for m in mods), py
            if isinstance(node, ast.Name | ast.Attribute):
                ident = node.id if isinstance(node, ast.Name) else node.attr
                assert "odoo" not in ident.lower(), f"{py}: {ident}"
    # 2) importer le service n'importe aucun connecteur
    code = "import sys, uie_core.instances; print(any(m.startswith('uie_connectors') for m in sys.modules))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "False"


# --------------------------------------------------------------------------- #
# Suppression
# --------------------------------------------------------------------------- #
class SpySecretStore(FernetSecretStore):
    def __init__(self) -> None:
        super().__init__(FernetSecretStore.generate_key())
        self.deleted: list[str] = []

    def delete(self, secret_ref: str) -> None:
        super().delete(secret_ref)
        self.deleted.append(secret_ref)


INSTANCE_TABLES = {
    "instances": "id = :i",
    "instance_connections": "instance_id = :i",
    "instance_capabilities": "instance_id = :i",
    "instance_schema_snapshots": "instance_id = :i",
}


def _rows(session, inst_id) -> dict[str, int]:
    out = {
        t: session.execute(text(f"SELECT count(*) FROM {t} WHERE {w}"), {"i": inst_id}).scalar()
        for t, w in INSTANCE_TABLES.items()
    }
    out["instance_schema_diffs"] = session.execute(
        text(
            "SELECT count(*) FROM instance_schema_diffs d JOIN instance_schema_snapshots s "
            "ON s.id = d.instance_snapshot_id WHERE s.instance_id = :i"
        ),
        {"i": inst_id},
    ).scalar()
    return out


def _standard_state(session, snapshot_id) -> tuple:
    return session.execute(
        text(
            "SELECT s.checksum, s.entity_count, s.field_count, s.status, "
            "(SELECT count(*) FROM schema_entities e WHERE e.snapshot_id = s.id), "
            "(SELECT count(*) FROM schema_fields f JOIN schema_entities e ON e.id = f.entity_id "
            " WHERE e.snapshot_id = s.id) FROM schema_snapshots s WHERE s.id = :i"
        ),
        {"i": snapshot_id},
    ).one()


@pytest.fixture
def spy_svc(session, provider):
    RegistryService(session, SystemContext("test")).publish_snapshot(_snap())
    store = SpySecretStore()
    return InstanceService(session, SystemContext("test"), store, FakeResolver(provider)), store


def test_delete_removes_instance_data_and_keeps_standard(spy_svc, session):
    svc, store = spy_svc
    inst = _create(svc)
    svc.test_connection(inst.id)
    s1 = svc.discover_schema(inst.id)
    svc.discover_schema(inst.id)  # le 2e snapshot a un diff non vide
    base_id = s1.base_snapshot_id
    before_std = _standard_state(session, base_id)
    before = _rows(session, inst.id)
    assert before["instance_schema_snapshots"] == 2 and before["instance_schema_diffs"] >= 1

    report = svc.delete(inst.id)
    session.commit()  # entrypoint : commit de la transaction

    assert (report.connections, report.capabilities, report.snapshots, report.secrets) == (1, 9, 2, 1)
    assert report.diffs == before["instance_schema_diffs"] and report.base_snapshot_ids == (base_id,)
    assert _rows(session, inst.id) == dict.fromkeys(before, 0)
    assert _standard_state(session, base_id) == before_std  # standard ni supprimé ni modifié
    with pytest.raises(NotFound):
        svc.get(inst.id)
    assert len(store.deleted) == 1 and store.deleted[0].startswith("fernet:v1:")


def test_secret_deleted_only_after_commit(spy_svc, session):
    svc, store = spy_svc
    inst = _create(svc)
    session.commit()
    svc.delete(inst.id)
    assert store.deleted == []  # pas avant le commit
    session.rollback()  # suppression annulée : l'instance et son secret restent
    assert svc.get(inst.id).connections[0].secret_ref
    session.commit()
    assert store.deleted == []  # le hook a été oublié au rollback
    svc.delete(inst.id)
    session.commit()
    assert len(store.deleted) == 1


def test_standard_snapshot_cannot_be_deleted_while_referenced(svc, session):
    inst = _create(svc)
    base_id = svc.discover_schema(inst.id).base_snapshot_id
    nested = session.begin_nested()
    with pytest.raises(IntegrityError) as refused:
        session.execute(text("DELETE FROM schema_snapshots WHERE id = :i"), {"i": base_id})
    # code SQLSTATE plutôt que le message, traduit selon la langue du serveur PostgreSQL :
    # 23001 = restrict_violation (FK RESTRICT), 23503 = foreign_key_violation (NO ACTION)
    assert refused.value.orig.sqlstate in {"23001", "23503"}
    nested.rollback()
    svc.delete(inst.id)  # une fois l'instance supprimée, plus rien ne référence le standard via elle
    session.flush()
    assert _standard_state(session, base_id) is not None


def test_foreign_keys_cascade_to_instance_data_only(session):
    rules = dict(
        session.execute(
            text(
                "SELECT tc.table_name || '.' || kcu.column_name || '->' || ccu.table_name, rc.delete_rule "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu USING (constraint_name) "
                "JOIN information_schema.constraint_column_usage ccu USING (constraint_name) "
                "JOIN information_schema.referential_constraints rc USING (constraint_name) "
                "WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_name LIKE 'instance%'"
            )
        ).all()
    )
    assert rules == {
        "instance_connections.instance_id->instances": "CASCADE",
        "instance_capabilities.instance_id->instances": "CASCADE",
        "instance_schema_snapshots.instance_id->instances": "CASCADE",
        "instance_schema_diffs.instance_snapshot_id->instance_schema_snapshots": "CASCADE",
        "instance_schema_snapshots.base_snapshot_id->schema_snapshots": "RESTRICT",
        "instances.interface_id->system_interfaces": "RESTRICT",
        "instances.organization_id->organizations": "RESTRICT",  # V1d : jamais supprimée en silence
    }


def test_delete_unknown_instance(svc):
    import uuid

    with pytest.raises(NotFound):
        svc.delete(uuid.uuid4())
