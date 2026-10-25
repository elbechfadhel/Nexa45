"""V2b.1 commit 1 : un fournisseur connecte son Odoo — test sans enregistrement, version détectée, erreurs
classées, création dans SON organisation, garde des adresses internes (E3)."""

from __future__ import annotations

import uuid
import xmlrpc.client
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from test_instance_odoo import FakeOdooResolver
from uie_connectors.odoo import OdooSchemaLoader
from uie_connectors.odoo.testing import FakeOdooServer
from uie_core.identity import MembershipRole, Organization, SystemContext, TenantContext
from uie_core.instances import (
    ALLOW_PRIVATE_ENV,
    ConnectionErrorKind,
    ConnectionFailed,
    EndpointNotAllowed,
    FernetSecretStore,
    InstanceService,
)
from uie_core.registry import NotFound, RegistryService
from uie_core.registry import models as m

FIX = Path(__file__).parent / "fixtures"
PASSWORD = "Supplier-Odoo-S3cret"
URL = "http://odoo.example.test:8069"  # jamais contacté : le faux serveur répond
CONFIG = {"db": "customer17", "username": "admin"}


class CountingStore(FernetSecretStore):
    def __init__(self) -> None:
        super().__init__(FernetSecretStore.generate_key())
        self.puts = 0

    def put(self, secret: SecretStr) -> str:
        self.puts += 1
        return super().put(secret)


@pytest.fixture
def world(session, monkeypatch) -> dict[str, Any]:
    monkeypatch.setenv(ALLOW_PRIVATE_ENV, "true")
    RegistryService(session, SystemContext("test")).publish_snapshot(
        OdooSchemaLoader({"json": FIX / "odoo17_ref.json"}).build_snapshot()
    )
    fake = FakeOdooServer.from_dump(FIX / "odoo17_customer_instance.json", password=PASSWORD)
    fake.db = "customer17"
    orgs = {slug: Organization(name=slug.title(), slug=slug) for slug in ("acme", "globex")}
    session.add_all(orgs.values())
    session.flush()
    store = CountingStore()

    def svc(slug: str, resolver: Any = None) -> InstanceService:
        ctx = TenantContext(uuid.uuid4(), orgs[slug].id, MembershipRole.OWNER)
        return InstanceService(session, ctx, store, resolver or FakeOdooResolver(fake))

    return {"fake": fake, "orgs": orgs, "store": store, "svc": svc, "session": session}


def _instances(session) -> int:  # noqa: ANN001
    return session.scalar(select(func.count()).select_from(m.Instance))


def test_check_detects_the_version_and_saves_nothing(world):
    before = _instances(world["session"])
    check = world["svc"]("acme").check_connection("odoo", URL, CONFIG, SecretStr(PASSWORD))
    assert check.result.ok and (check.system_name, check.version, check.supported) == ("Odoo", "17", True)
    assert _instances(world["session"]) == before and world["store"].puts == 0


def test_errors_are_classified_and_never_contain_the_password(world):
    svc = world["svc"]("acme")
    wrong = svc.check_connection("odoo", URL, CONFIG, SecretStr("wrong-Password-9"))
    assert (wrong.result.ok, wrong.result.error_kind) == (False, ConnectionErrorKind.AUTH_FAILED)
    assert "wrong-Password-9" not in (wrong.result.error or "")
    missing = svc.check_connection("odoo", URL, {"db": "customer17"}, SecretStr(PASSWORD))
    assert missing.result.error_kind == ConnectionErrorKind.INCOMPLETE

    def refused(url: str) -> Any:
        raise ConnectionRefusedError("[Errno 111] Connection refused")

    down = world["svc"]("acme", FakeOdooResolver(type("F", (), {"proxy": staticmethod(refused)})()))
    assert down.check_connection("odoo", URL, CONFIG, SecretStr(PASSWORD)).result.error_kind == (
        ConnectionErrorKind.UNREACHABLE
    )

    class NoDatabase:
        def version(self) -> dict[str, Any]:
            return world["fake"].dump["version_info"]

        def authenticate(self, *args: Any) -> Any:
            raise xmlrpc.client.Fault(1, 'FATAL:  database "nope" does not exist')

    nodb = world["svc"](
        "acme", FakeOdooResolver(type("F", (), {"proxy": staticmethod(lambda url: NoDatabase())})())
    )
    assert nodb.check_connection("odoo", URL, CONFIG, SecretStr(PASSWORD)).result.error_kind == (
        ConnectionErrorKind.DATABASE_NOT_FOUND
    )


def test_unsupported_version_cannot_be_saved(world):
    world["fake"].dump["version_info"] = {**world["fake"].dump["version_info"], "server_serie": "16.0"}
    svc = world["svc"]("acme")
    check = svc.check_connection("odoo", URL, CONFIG, SecretStr(PASSWORD))
    assert (check.result.ok, check.version, check.supported) == (True, "16", False)
    with pytest.raises(ConnectionFailed):
        svc.connect("old-odoo", "odoo", URL, CONFIG, SecretStr(PASSWORD))
    assert world["store"].puts == 0


def test_connect_creates_a_connected_system_in_the_supplier_organization(world):
    acme = world["svc"]("acme")
    inst, check = acme.connect(" my-odoo ", "odoo", URL, CONFIG, SecretStr(PASSWORD))
    assert (inst.name, inst.organization_id, inst.environment) == (
        "my-odoo",
        world["orgs"]["acme"].id,
        "production",
    )
    assert inst.interface.version.version == "17" and check.version == "17"
    conn = inst.connections[0]
    assert (
        conn.last_test_status == "OK"
        and conn.secret_ref.startswith("fernet:")
        and PASSWORD not in conn.secret_ref
    )
    assert [i.name for i in acme.list()] == ["my-odoo"]
    with pytest.raises(NotFound):
        world["svc"]("globex").get(inst.id)  # l'autre fournisseur ne la voit pas
    with pytest.raises(ConnectionFailed):
        acme.connect("bad", "odoo", URL, CONFIG, SecretStr("wrong-Password-9"))
    assert [i.name for i in acme.list()] == ["my-odoo"]  # rien créé après un échec


def test_internal_addresses_are_refused_to_suppliers_unless_allowed(world, monkeypatch):
    monkeypatch.delenv(ALLOW_PRIVATE_ENV)
    acme = world["svc"]("acme")
    for url in (
        "http://localhost:8069",
        "http://127.0.0.1:8027",
        "http://10.0.0.5",
        "http://192.168.1.20:8069",
    ):
        with pytest.raises(EndpointNotAllowed):
            acme.check_connection("odoo", url, CONFIG, SecretStr(PASSWORD))
    for url in ("ftp://odoo.example.test", "odoo.example.test"):
        with pytest.raises(EndpointNotAllowed):
            acme.check_connection("odoo", url, CONFIG, SecretStr(PASSWORD))
    assert acme.check_connection("odoo", "http://93.184.216.34:8069", CONFIG, SecretStr(PASSWORD)).result.ok
    # l'Admin et la CLI ne sont pas concernés
    admin = InstanceService(
        world["session"], SystemContext("test"), world["store"], FakeOdooResolver(world["fake"])
    )
    assert admin.check_connection("odoo", "http://localhost:8069", CONFIG, SecretStr(PASSWORD)).result.ok
    monkeypatch.setenv(ALLOW_PRIVATE_ENV, "true")
    assert acme.check_connection("odoo", "http://localhost:8027", CONFIG, SecretStr(PASSWORD)).result.ok
