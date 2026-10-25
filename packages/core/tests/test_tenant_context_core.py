"""V1d commit 2 : contextes obligatoires et isolation multi-tenant dans les services du Core.

Deux organisations A et B, une instance chacune (système imaginaire « demo »). Un contexte tenant ne voit
que son organisation ; une instance de l'autre tenant est introuvable EXACTEMENT comme un id inexistant,
avant tout accès au SecretStore ou au provider (réseau). Connaissance plateforme : lecture seule en tenant.
"""

from __future__ import annotations

import ast
import uuid
from pathlib import Path

import pytest
from pydantic import SecretStr
from test_instances_core import PASSWORD, FakeProvider, FakeResolver, _snap
from uie_core.domains import ProductDomainService, SemanticEnrichmentService
from uie_core.identity import (
    AdminContext,
    MembershipRole,
    Organization,
    PermissionDenied,
    SystemContext,
    TenantContext,
)
from uie_core.instances import FernetSecretStore, InstanceCreate, InstanceService
from uie_core.registry import NotFound, RegistryService

CORE_SRC = Path(__file__).resolve().parents[1] / "src" / "uie_core"
SYSTEM = SystemContext("test")


class CountingSecretStore(FernetSecretStore):
    """Compte chaque déchiffrement : un accès refusé ne doit jamais y arriver."""

    def __init__(self) -> None:
        super().__init__(FernetSecretStore.generate_key())
        self.reads = 0

    def get(self, secret_ref: str) -> SecretStr:
        self.reads += 1
        return super().get(secret_ref)


@pytest.fixture
def two_tenants(session):  # noqa: ANN201
    RegistryService(session, SYSTEM).publish_snapshot(_snap())
    orgs = {}
    for slug in ("supplier-a", "supplier-b"):
        org = Organization(name=slug.upper(), slug=slug)
        session.add(org)
        orgs[slug] = org
    session.flush()
    provider, store = FakeProvider(), CountingSecretStore()
    system = InstanceService(session, SYSTEM, store, FakeResolver(provider))
    inst = {
        slug: system.create(
            InstanceCreate(
                organization_id=org.id,
                name=f"erp-{slug}",
                system="demo",
                version="1",
                endpoint="https://demo.invalid",
                password=SecretStr(PASSWORD),
            )
        )
        for slug, org in orgs.items()
    }
    system.discover_schema(inst["supplier-b"].id)
    provider.discoveries = 0
    store.reads = 0

    def tenant(slug: str) -> InstanceService:
        ctx = TenantContext(uuid.uuid4(), orgs[slug].id, MembershipRole.OWNER)
        return InstanceService(session, ctx, store, FakeResolver(provider))

    return {"orgs": orgs, "inst": inst, "tenant": tenant, "provider": provider, "store": store}


def _not_found(call) -> str:  # noqa: ANN001
    with pytest.raises(NotFound) as exc:
        call()
    return str(exc.value).replace(str(exc.value).split()[1], "<id>")  # message sans l'id


# --------------------------------------------------------------------------- #
# Contextes obligatoires
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "build",
    [
        lambda s: RegistryService(s, None),
        lambda s: InstanceService(s, None, CountingSecretStore()),
        lambda s: ProductDomainService(s, None),
        lambda s: SemanticEnrichmentService(s, None),
        lambda s: RegistryService(s),  # paramètre omis
        lambda s: ProductDomainService(s),
    ],
)
def test_services_refuse_to_work_without_a_context(session, build):
    with pytest.raises(TypeError):
        build(session)


def test_system_context_needs_an_explicit_reason():
    with pytest.raises(ValueError, match="raison"):
        SystemContext("  ")


def test_enrichment_and_domain_services_must_share_the_context(session):
    domains = ProductDomainService(session, SYSTEM)
    with pytest.raises(ValueError, match="même contexte"):
        SemanticEnrichmentService(session, AdminContext(uuid.uuid4()), domains=domains)


# --------------------------------------------------------------------------- #
# Isolation
# --------------------------------------------------------------------------- #
def test_tenant_sees_only_its_organization(two_tenants):
    t = two_tenants
    a = t["tenant"]("supplier-a")
    assert [i.name for i in a.list()] == ["erp-supplier-a"]
    assert a.get(t["inst"]["supplier-a"].id).name == "erp-supplier-a"
    admin = InstanceService(a.s, AdminContext(uuid.uuid4()), t["store"])
    assert {i.name for i in admin.list()} == {"erp-supplier-a", "erp-supplier-b"}


def test_other_tenant_instance_is_exactly_like_a_missing_one(two_tenants):
    t = two_tenants
    a, b_id = t["tenant"]("supplier-a"), t["inst"]["supplier-b"].id
    other = _not_found(lambda: a.get(b_id))
    missing = _not_found(lambda: a.get(uuid.uuid4()))
    assert other == missing == "Instance <id> introuvable"
    assert _not_found(lambda: a.resolve("erp-supplier-b")) == "Instance <id> introuvable"


@pytest.mark.parametrize(
    "operation",
    ["test_connection", "discover_schema", "get_schema_diff", "list_instance_snapshots", "delete"],
)
def test_cross_tenant_operations_touch_neither_secrets_nor_network(two_tenants, operation):
    t = two_tenants
    a, b = t["tenant"]("supplier-a"), t["inst"]["supplier-b"]
    with pytest.raises(NotFound):
        getattr(a, operation)(b.id)
    assert (t["store"].reads, t["provider"].discoveries, t["provider"].exercised) == (0, 0, [])
    assert a.s.get(type(b), b.id) is not None  # l'instance B est intacte


def test_tenant_creates_only_in_its_own_organization(two_tenants):
    t = two_tenants
    a = t["tenant"]("supplier-a")
    data = dict(name="new-erp", system="demo", version="1", endpoint="https://demo.invalid")
    inst = a.create(InstanceCreate(**data))
    assert inst.organization_id == t["orgs"]["supplier-a"].id
    with pytest.raises(NotFound):  # l'organisation envoyée par le client n'est jamais crue
        a.create(InstanceCreate(**{**data, "name": "x"}, organization_id=t["orgs"]["supplier-b"].id))


def test_domain_and_semantic_services_are_scoped(two_tenants):
    t = two_tenants
    ctx = TenantContext(uuid.uuid4(), t["orgs"]["supplier-a"].id, MembershipRole.MEMBER)
    session = t["tenant"]("supplier-a").s
    domains = ProductDomainService(session, ctx)
    semantics = SemanticEnrichmentService(session, ctx, domains=domains)
    b_id = t["inst"]["supplier-b"].id
    for call in (
        lambda: domains.instance(b_id),
        lambda: domains.analyze(b_id),
        lambda: domains.get_instance_domain(b_id),
        lambda: semantics.summary(b_id),
        lambda: semantics.get_pending_questions(b_id),
        lambda: semantics.confirm(b_id, "article", "sku", "sku"),
        lambda: semantics.ignore(b_id, "article", "sku"),
        lambda: semantics.list_decisions(b_id),
        lambda: semantics.ai_status(b_id),
    ):
        assert _not_found(call) == "Instance <id> introuvable"


# --------------------------------------------------------------------------- #
# Connaissance plateforme : globale, en lecture seule pour un tenant
# --------------------------------------------------------------------------- #
def test_tenant_reads_but_never_writes_the_standard_registry(two_tenants):
    t = two_tenants
    ctx = TenantContext(uuid.uuid4(), t["orgs"]["supplier-a"].id, MembershipRole.OWNER)
    reg = RegistryService(t["tenant"]("supplier-a").s, ctx)
    assert [s.code for s in reg.list_systems()] == ["demo"]
    with pytest.raises(PermissionDenied):
        reg.publish_snapshot(_snap(extra_field=True))
    with pytest.raises(PermissionDenied):
        reg.ensure_system("other")
    RegistryService(reg.s, AdminContext(uuid.uuid4())).ensure_system("other")  # admin : autorisé


def test_contexts_do_not_depend_on_a_web_framework():
    offenders = []
    for path in (CORE_SRC / "identity").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            offenders += [n for n in names if n.split(".")[0] in {"fastapi", "starlette", "typer", "uie_api"}]
    assert offenders == []
