"""Lexware Office (phase 1a) : contrat du Registry, connexion par clé d'API, test en lecture seule.

Le contrat testé est le contrat MINIMAL livré avec le connecteur (notre description). Les tests de mécanique
du loader utilisent une mini-spec INVENTÉE. La connexion passe par un faux serveur : aucun appel réseau,
aucune clé réelle ; rien ici ne prouve le comportement de l'API distante."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

import pytest
import uie_connectors  # noqa: F401  (enregistre loaders et providers)
import yaml
from pydantic import SecretStr
from sqlalchemy import func, select, text
from uie_connectors.lexware import API_BASE_URL, LexwareClient, LexwareDiscoveryProvider, LexwareSchemaLoader
from uie_connectors.lexware.client import ALLOWED_METHODS, WRITE_METHODS
from uie_connectors.lexware.ratelimit import TokenBucket
from uie_connectors.lexware.spec import DEFAULT_CONTRACT, LexwareApiSpec
from uie_connectors.lexware.testing import FAKE_KEY, PROFILE, FakeLexwareServer
from uie_core.db.base import Base
from uie_core.identity import MembershipRole, Organization, SystemContext, TenantContext
from uie_core.instances import (
    CapabilityStatus,
    ConnectionContext,
    ConnectionErrorKind,
    ConnectionFailed,
    ConnectionFieldRole,
    ConnectionFieldsError,
    DiscoveryError,
    FernetSecretStore,
    InstanceService,
    ProviderResolver,
)
from uie_core.registry import Capability, DataType, NotFound, RegistryService, SchemaLoaderError, SnapshotRef
from uie_core.registry import models as m
from uie_core.registry.service import SnapshotAlreadyExists

REF = SnapshotRef(system="lexware", product="public-api", version="v1", interface="rest")


def _snap(spec: Path | None = None):
    return LexwareSchemaLoader({"spec": spec} if spec else None).build_snapshot()


# --------------------------------------------------------------------------- #
# A. Registry
# --------------------------------------------------------------------------- #
def test_contract_identity_route_version_and_revision_are_distinct():
    snap = _snap()
    assert (snap.system, snap.product, snap.version, snap.interface, snap.interface_type) == (
        "lexware",
        "public-api",
        "v1",  # version de ROUTE de l'API
        "rest",
        "api",
    )
    assert snap.metadata["system_name"] == "Lexware Office" and snap.metadata["product_name"] == "Public API"
    # la révision du contrat n'est PAS la version de route : elle vit dans la provenance
    assert snap.source["contract_revision"] == "2026-08-11" and snap.source["api_version"] == "v1"
    assert all(url.startswith("https://developers.lexware.io/") for url in snap.source["documentation"])
    assert [e.technical_name for e in snap.entities] == [
        "profile",
        "contacts",
        "countries",
        "posting-categories",
        "vouchers",
        "voucherlist",
        "articles",
    ]


def test_capabilities_are_per_resource_and_come_from_the_contract():
    snap = _snap()
    caps = {e.technical_name: {c.value for c, on in e.capabilities.items() if on} for e in snap.entities}
    assert caps == {
        "profile": {"READ"},
        "contacts": {"READ", "SEARCH", "CREATE", "UPDATE", "EVENTS"},
        "countries": {"READ"},
        "posting-categories": {"READ"},
        "vouchers": {"READ", "SEARCH", "CREATE", "UPDATE"},
        "voucherlist": {"READ", "SEARCH"},
        # articles : la documentation décrit une suppression ; UIE ne l'appelle jamais (aperçu en articles 1a)
        "articles": {"READ", "SEARCH", "CREATE", "UPDATE", "DELETE", "EVENTS"},
    }
    contacts = snap.entity("contacts")
    assert contacts.capabilities[Capability.DELETE] is False  # un contact ne se supprime pas par l'API
    assert contacts.metadata["path"] == "/v1/contacts"
    assert contacts.metadata["documentation"].startswith("https://developers.lexware.io/docs/#")
    # capacités de l'interface : vraies dès qu'une ressource les offre ; aucune découverte de schéma
    # DELETE : offerte par la ressource articles (documentation), donc DÉCLARÉE pour l'interface ; jamais
    # exercée : le client n'a aucune méthode de suppression, même en mode écriture
    assert snap.capabilities[Capability.CREATE] is True and snap.capabilities[Capability.DELETE] is True
    assert "DELETE" not in ALLOWED_METHODS | WRITE_METHODS
    assert snap.capabilities[Capability.SCHEMA_DISCOVERY] is False


def test_contact_and_profile_and_country_field_contracts():
    snap = _snap()
    contacts = {f.technical_name: f for f in snap.entity("contacts").fields}
    # chemin JSON : le nom du Registry n'a pas d'indice de liste, la métadonnée le garde
    street = contacts["addresses.billing.street"]
    assert (
        street.metadata["json_path"] == "addresses.billing[0].street" and street.data_type == DataType.STRING
    )
    assert contacts["emailAddresses.business"].metadata["json_path"] == "emailAddresses.business[0]"
    # rôle = objet dont la présence a un sens ; son numéro est attribué par le système (lecture seule)
    assert contacts["roles.customer"].metadata["presence"] is True
    assert contacts["roles.customer.number"].readonly and contacts["roles.vendor.number"].readonly
    assert contacts["id"].readonly and contacts["archived"].readonly
    assert contacts["version"].metadata["protocol"] is True
    # longueurs et obligations conditionnelles documentées
    assert contacts["note"].metadata["length"] == 1000
    assert contacts["person.salutation"].metadata["length"] == 25
    assert contacts["person.lastName"].metadata["required_group"] == "person"
    assert contacts["company.name"].metadata["required_group"] == "company"
    assert contacts["addresses.billing.countryCode"].metadata["required_group"] == "addresses.billing"
    # aucune obligation inconditionnelle inventée, aucun compte bancaire dans le contrat
    assert not any(f.required for f in contacts.values())
    assert not any("bank" in name.lower() or "iban" in name.lower() for name in contacts)
    assert {"business", "office", "private", "other"} == {
        n.split(".")[1] for n in contacts if n.startswith("emailAddresses.")
    }
    assert {"business", "office", "mobile", "private", "fax", "other"} == {
        n.split(".")[1] for n in contacts if n.startswith("phoneNumbers.")
    }

    profile = {f.technical_name: f for f in snap.entity("profile").fields}
    assert all(f.readonly for f in profile.values())
    assert {"organizationId", "companyName", "created.userName", "businessFeatures"} <= set(profile)
    assert profile["taxType"].metadata["enum"] == ["net", "gross", "vatfree"]

    countries = {f.technical_name: f for f in snap.entity("countries").fields}
    assert countries["taxClassification"].metadata["enum"] == ["de", "intraCommunity", "thirdPartyCountry"]


def test_loading_is_deterministic_and_carries_no_secret():
    a, b = _snap(), _snap()
    assert a.checksum() == b.checksum() and a.model_dump() == b.model_dump()
    dumped = str(a.model_dump()).lower()
    for word in ("bearer", "authorization", "api_key", "apikey", FAKE_KEY.lower()):
        assert word not in dumped
    assert len(a.source["contract_sha256"]) == 64


def test_committed_contract_is_minimal_and_has_no_copied_documentation_text():
    """Garde-fou de licence : le contrat versionné ne décrit que des noms, des types et des limites."""
    raw = DEFAULT_CONTRACT.read_text(encoding="utf-8")
    spec = LexwareApiSpec.load()
    assert [r.name for r in spec.resources] == [
        "profile",
        "contacts",
        "countries",
        "posting-categories",
        "vouchers",
        "voucherlist",
        "articles",
    ]  # phases 1a et 2a, articles 1a
    assert DEFAULT_CONTRACT.stat().st_size < 20_000
    allowed = set(LexwareApiSpec.model_fields) | {"resources"}
    assert set(yaml.safe_load(raw)) <= allowed
    for r in spec.resources:
        assert r.doc.startswith("https://developers.lexware.io/docs/#")  # référence, pas une copie
        for f in r.fields:
            assert len(f.label) <= 40  # un libellé (le nôtre), jamais une phrase de documentation
    # aucune clé libre où ranger du texte (description, help, note…) : le modèle les refuse
    with pytest.raises(ValueError):
        LexwareApiSpec.model_validate({**yaml.safe_load(raw), "description": "x"})


def test_invented_mini_contract_loads_and_bad_contract_is_refused(tmp_path):
    mini = {
        "spec": "uie-lexware-api/1",
        "system": "lexware",
        "system_name": "Lexware Office",
        "product": "public-api",
        "product_name": "Public API",
        "api_version": "v1",
        "interface": "rest",
        "contract_revision": "2099-01-01",
        "sources": ["https://example.test/invented"],
        "resources": [
            {
                "name": "widgets",
                "label": "Widgets",
                "path": "/v1/widgets",
                "doc": "https://example.test/invented#widgets",
                "operations": {"read": True, "create": True, "extra": ["download"]},
                "fields": [
                    {"path": "id", "label": "Id", "type": "uuid", "readonly": True},
                    {"path": "tags[0]", "label": "Tag", "length": 10},
                ],
            }
        ],
    }
    path = tmp_path / "mini.yaml"
    path.write_text(yaml.safe_dump(mini), encoding="utf-8")
    snap = _snap(path)
    widgets = snap.entity("widgets")
    assert [f.technical_name for f in widgets.fields] == ["id", "tags"]
    assert widgets.field("tags").metadata == {"json_path": "tags[0]", "position": 2, "length": 10}
    # une opération sans capacité générique reste une métadonnée de l'entité (aucun changement du Core)
    assert widgets.metadata["operations"] == ["download"]
    assert widgets.capabilities[Capability.CREATE] is True

    mini["resources"][0]["fields"].append({"path": "tags", "label": "Dup"})
    path.write_text(yaml.safe_dump(mini), encoding="utf-8")
    with pytest.raises(SchemaLoaderError):
        _snap(path)


def test_registry_hierarchy_immutability_and_new_revision_as_a_new_snapshot(session, tmp_path):
    svc = RegistryService(session, SystemContext("test"))
    first = svc.publish_snapshot(_snap())
    system = next(s for s in svc.list_systems() if s.code == "lexware")
    assert system.name == "Lexware Office"
    (product,) = system.products
    (version,) = product.versions
    (interface,) = version.interfaces
    assert (product.code, product.name, version.version, interface.code, interface.type) == (
        "public-api",
        "Public API",
        "v1",
        "rest",
        "api",
    )
    assert svc.snapshot_label(first) == "lexware public-api v1 @1.0.0"
    assert svc.publish_snapshot(_snap()).id == first.id  # même contenu : même snapshot

    # le contrat évolue À ROUTE CONSTANTE (/v1) : nouveau snapshot, l'ancien intact
    data = yaml.safe_load(DEFAULT_CONTRACT.read_text(encoding="utf-8"))
    data["contract_revision"] = "2099-01-01"
    data["resources"][1]["fields"].append({"path": "futureField", "label": "Future field"})
    changed = tmp_path / "public_api_v1.yaml"
    changed.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(SnapshotAlreadyExists):
        svc.publish_snapshot(_snap(changed), "1.0.0")  # jamais de modification d'un snapshot publié
    second = svc.publish_snapshot(_snap(changed))
    assert second.id != first.id and second.snapshot_version == "1.0.1"
    session.refresh(interface)
    assert sorted(s.snapshot_version for s in interface.snapshots) == ["1.0.0", "1.0.1"]
    assert svc.resolve_snapshot(REF).id == second.id  # sans révision : le dernier
    old = svc.resolve_snapshot(
        SnapshotRef(system="lexware", product="public-api", version="v1", snapshot_version="1.0.0")
    )
    assert old.id == first.id and old.checksum == first.checksum
    assert (
        old.source["contract_revision"] == "2026-08-11" and second.source["contract_revision"] == "2099-01-01"
    )
    diff = svc.compare_by_id(first.id, second.id)
    assert [c.path for c in diff.changes if c.object_type == "field"] == ["contacts.futureField"]


# --------------------------------------------------------------------------- #
# Connexion : champs déclarés par le connecteur, test en lecture seule
# --------------------------------------------------------------------------- #
class FakeResolver(ProviderResolver):
    """Injecte un provider Lexware dont le client parle au faux serveur (jamais à l'API réelle)."""

    def __init__(self, server: FakeLexwareServer) -> None:
        self.server = server

    def resolve(self, system: str, version: str, interface: str):  # noqa: ANN201
        if system != "lexware":
            return super().resolve(system, version, interface)

        def client(ctx: ConnectionContext) -> LexwareClient:
            noop = TokenBucket(clock=lambda: 0.0, sleep=lambda _s: None)
            assert ctx.secret is not None
            return LexwareClient(
                ctx.secret, transport=self.server.transport, bucket=noop, sleep=lambda _s: None
            )

        return LexwareDiscoveryProvider(client_factory=client)


def test_connection_form_is_declared_by_the_connector_and_needs_only_an_api_key():
    provider = LexwareDiscoveryProvider()
    (field,) = provider.connection_fields
    assert (field.name, field.label, field.role) == ("api_key", "API key", ConnectionFieldRole.SECRET)
    assert provider.endpoint() == API_BASE_URL  # l'adresse appartient au connecteur
    data = provider.connection_input({"api_key": SecretStr(FAKE_KEY)})
    assert data.endpoint == API_BASE_URL and data.config == {}
    assert data.secret is not None and data.secret.get_secret_value() == FAKE_KEY
    assert FAKE_KEY not in repr(data)
    with pytest.raises(ConnectionFieldsError) as missing:
        provider.connection_input({})
    assert missing.value.missing == ["api_key"]
    with pytest.raises(ConnectionFieldsError) as unknown:  # l'adresse ne peut pas être imposée par le client
        provider.connection_input({"api_key": SecretStr(FAKE_KEY), "url": SecretStr("https://evil.test")})
    assert unknown.value.unknown == ["url"] and FAKE_KEY not in str(unknown.value)


def test_profile_connection_test_is_read_only_and_keeps_only_safe_facts():
    server = FakeLexwareServer()
    provider = FakeResolver(server).resolve("lexware", "", "")
    result = provider.test_connection(ConnectionContext(endpoint=API_BASE_URL, secret=SecretStr(FAKE_KEY)))
    assert result.ok and provider.detected_version(result) == "v1"
    assert [(c.method, c.path) for c in server.calls] == [("GET", "/v1/profile")] and server.writes == []
    info = result.server_info
    assert info == {
        "api_version": "v1",
        "organization_id": PROFILE["organizationId"],
        "company_name": "Musterbetrieb Test GmbH",
        "user_name": "Erika Beispiel",
        "business_features": ["INVOICING", "BOOKKEEPING"],
    }
    assert PROFILE["created"]["userEmail"] not in str(info) and FAKE_KEY not in str(result.model_dump())
    assert provider.connection_summary(result) == [
        ("Company", "Musterbetrieb Test GmbH"),
        ("Connected by", "Erika Beispiel"),
    ]
    by_cap = {c.capability: c.status for c in result.capabilities}
    assert by_cap[Capability.READ] == CapabilityStatus.VERIFIED  # seule la lecture a été exercée
    assert by_cap[Capability.CREATE] == CapabilityStatus.DECLARED  # annoncée par le contrat, jamais essayée
    assert by_cap[Capability.DELETE] == CapabilityStatus.UNAVAILABLE
    with pytest.raises(DiscoveryError):
        provider.discover_schema(ConnectionContext(endpoint=API_BASE_URL, secret=SecretStr(FAKE_KEY)))


def test_profile_without_company_name_still_connects():
    # Constat réel (phase 1a.1) : un compte sans nom de société renvoie un profil SANS `companyName` ni
    # `distanceSalesPrinciple` ; le contrat ne les déclare pas obligatoires, la connexion doit réussir.
    profile = {k: v for k, v in PROFILE.items() if k not in ("companyName", "distanceSalesPrinciple")}
    server = FakeLexwareServer(queue=[(200, {}, profile)])
    provider = FakeResolver(server).resolve("lexware", "", "")
    result = provider.test_connection(ConnectionContext(endpoint=API_BASE_URL, secret=SecretStr(FAKE_KEY)))
    assert result.ok and result.server_info["company_name"] is None
    assert provider.connection_summary(result) == [("Connected by", "Erika Beispiel")]


def test_a_user_name_that_is_an_e_mail_address_is_never_kept():
    # Constat réel (phase 1a.1) : `created.userName` contient l'adresse e-mail de l'utilisateur.
    profile = {**PROFILE, "created": {**PROFILE["created"], "userName": PROFILE["created"]["userEmail"]}}
    server = FakeLexwareServer(queue=[(200, {}, profile)])
    provider = FakeResolver(server).resolve("lexware", "", "")
    result = provider.test_connection(ConnectionContext(endpoint=API_BASE_URL, secret=SecretStr(FAKE_KEY)))
    assert result.ok and result.server_info["user_name"] is None
    assert "@" not in str(result.server_info) and "@" not in str(provider.connection_summary(result))


@pytest.mark.parametrize(
    ("setup", "kind"),
    [
        ({"key": "wrong-key"}, ConnectionErrorKind.AUTH_FAILED),
        ({"fail": 403}, ConnectionErrorKind.ACCESS_DENIED),
        ({"fail": 402}, ConnectionErrorKind.ACCESS_DENIED),
        ({"fail": 500}, ConnectionErrorKind.UNEXPECTED),
        ({"key": ""}, ConnectionErrorKind.INCOMPLETE),
    ],
)
def test_connection_failures_are_classified_without_leaking_the_key(setup: dict[str, Any], kind):
    server = FakeLexwareServer(fail={"/v1/profile": setup["fail"]} if "fail" in setup else {})
    provider = FakeResolver(server).resolve("lexware", "", "")
    key = setup.get("key", FAKE_KEY)
    result = provider.test_connection(ConnectionContext(endpoint=API_BASE_URL, secret=SecretStr(key)))
    assert not result.ok and result.error_kind == kind
    assert (key or "x") not in (result.error or "") and "Bearer" not in (result.error or "")
    assert server.writes == []


@pytest.fixture
def world(session) -> dict[str, Any]:
    RegistryService(session, SystemContext("test")).publish_snapshot(_snap())
    orgs = {slug: Organization(name=slug.title(), slug=slug) for slug in ("acme", "globex")}
    session.add_all(orgs.values())
    session.flush()
    server = FakeLexwareServer()
    store = FernetSecretStore(FernetSecretStore.generate_key())

    def svc(slug: str) -> InstanceService:
        ctx = TenantContext(uuid.uuid4(), orgs[slug].id, MembershipRole.OWNER)
        return InstanceService(session, ctx, store, FakeResolver(server))

    return {"session": session, "orgs": orgs, "server": server, "svc": svc}


def test_connect_a_lexware_system_with_only_a_name_and_a_key(world, caplog):
    acme = world["svc"]("acme")
    connector = next(c for c in acme.connectors() if c.system == "lexware")
    assert (connector.name, connector.versions) == ("Lexware Office", ["v1"])
    assert [f.name for f in connector.fields] == ["api_key"]

    values = {"api_key": SecretStr(FAKE_KEY)}
    check = acme.check_connection_values("lexware", values)
    assert check.result.ok and check.version == "v1" and check.supported
    assert world["session"].scalar(select(func.count()).select_from(m.Instance)) == 0  # rien n'est enregistré

    with caplog.at_level(logging.DEBUG):
        inst, _ = acme.connect_values("Books", "lexware", values)
    assert inst.organization_id == world["orgs"]["acme"].id and inst.status == "connected"
    conn = inst.connections[0]
    assert (conn.connector_type, conn.endpoint, conn.config) == ("lexware.rest", API_BASE_URL, {})
    assert conn.secret_ref and conn.secret_ref.startswith("fernet:") and FAKE_KEY not in conn.secret_ref
    assert acme.connection_facts(inst.id) == [
        ("Company", "Musterbetrieb Test GmbH"),
        ("Connected by", "Erika Beispiel"),
    ]
    caps = {c.capability: c.status for c in inst.capabilities}
    assert caps["READ"] == "VERIFIED" and caps["CREATE"] == "DECLARED" and caps["DELETE"] == "UNAVAILABLE"

    # la clé n'est nulle part : ni dans les journaux, ni dans une table, ni dans le dernier test
    assert FAKE_KEY not in caplog.text and "Bearer" not in caplog.text
    session = world["session"]
    session.flush()
    for table in Base.metadata.sorted_tables:
        rows = session.execute(text(f'SELECT CAST(t AS text) FROM "{table.name}" t')).scalars().all()
        assert not any(FAKE_KEY in row for row in rows), table.name
    # uniquement des lectures du profil, jamais d'écriture
    assert {(c.method, c.path) for c in world["server"].calls} == {("GET", "/v1/profile")}
    assert world["server"].writes == []

    # l'autre organisation ne voit rien
    with pytest.raises(Exception, match="introuvable"):
        world["svc"]("globex").get(inst.id)


def test_a_refused_key_saves_nothing(world):
    acme = world["svc"]("acme")
    with pytest.raises(ConnectionFailed) as exc:
        acme.connect_values("Books", "lexware", {"api_key": SecretStr("not-the-key")})
    assert exc.value.check.result.error_kind == ConnectionErrorKind.AUTH_FAILED
    assert "not-the-key" not in str(exc.value)
    assert world["session"].scalar(select(func.count()).select_from(m.Instance)) == 0


def test_other_connectors_still_declare_their_own_form():
    """Le formulaire d'un autre système reste le sien : aucun champ n'est partagé ni supposé."""
    from uie_connectors.odoo.discovery import OdooSchemaDiscoveryProvider

    other = OdooSchemaDiscoveryProvider()
    assert [(f.name, f.role.value) for f in other.connection_fields] == [
        ("url", "ENDPOINT"),
        ("database", "CONFIG"),
        ("username", "CONFIG"),
        ("password", "SECRET"),
    ]
    data = other.connection_input(
        {
            "url": SecretStr(" http://erp.example.test:8069 "),
            "database": SecretStr("main"),
            "username": SecretStr("admin"),
            "password": SecretStr(" pass word "),
        }
    )
    assert data.endpoint == "http://erp.example.test:8069"
    assert data.config == {"db": "main", "username": "admin"}  # clés attendues par ce connecteur, inchangées
    assert data.secret is not None and data.secret.get_secret_value() == " pass word "


# --------------------------------------------------------------------------- #
# Remplacement de la clé d'une instance existante (même instance, liens et historique conservés)
# --------------------------------------------------------------------------- #
NEW_KEY = "fake-key-INVENTED-NEW-1111"  # noqa: S105 — valeur de test inventée


def test_replace_key_checks_first_then_activates_on_the_same_instance(world):
    from uie_core.instances import SecretReplacementFailed  # noqa: F401

    acme, server, session = world["svc"]("acme"), world["server"], world["session"]
    inst, _ = acme.connect_values("books", "lexware", {"api_key": SecretStr(FAKE_KEY)})
    old_ref = inst.connections[0].secret_ref
    server.api_key = NEW_KEY  # la cible n'accepte plus que la nouvelle clé

    check = acme.replace_secret(inst.id, {"api_key": SecretStr(NEW_KEY)})  # vérification seulement
    assert check.result.ok and check.account_checked and not check.activated
    assert inst.connections[0].secret_ref == old_ref  # rien d'enregistré
    assert [(c.method, c.path) for c in server.calls][-1] == ("GET", "/v1/profile") and server.writes == []

    done = acme.replace_secret(inst.id, {"api_key": SecretStr(NEW_KEY)}, activate=True)
    session.flush()
    same = acme.get(inst.id)
    assert done.activated and same.id == inst.id and same.connections[0].secret_ref != old_ref
    _, ctx = acme.connection_context(inst.id)
    assert ctx.secret.get_secret_value() == NEW_KEY
    assert NEW_KEY not in same.connections[0].secret_ref and NEW_KEY not in str(
        same.connections[0].last_test_info
    )


@pytest.mark.parametrize("case", ["refused", "other_account", "empty", "extra_field"])
def test_replace_key_failure_keeps_the_old_key_and_writes_nothing(world, case):
    from uie_core.instances import InstanceError, SecretReplacementFailed

    acme, server = world["svc"]("acme"), world["server"]
    inst, _ = acme.connect_values("books", "lexware", {"api_key": SecretStr(FAKE_KEY)})
    old_ref = inst.connections[0].secret_ref
    values = {"api_key": SecretStr(NEW_KEY)}
    if case == "other_account":
        server.api_key = NEW_KEY
        server.queue = [(200, {}, {**PROFILE, "organizationId": "00000000-0000-4000-8000-0000000000ff"})]
    elif case == "empty":
        values = {"api_key": SecretStr("  ")}
    elif case == "extra_field":
        values = {**values, "url": SecretStr("https://evil.test")}
    with pytest.raises(InstanceError) as exc:
        acme.replace_secret(inst.id, values, activate=True)
    if case != "extra_field":
        assert isinstance(exc.value, SecretReplacementFailed)
    assert inst.connections[0].secret_ref == old_ref and NEW_KEY not in str(exc.value)
    _, ctx = acme.connection_context(inst.id)
    assert ctx.secret.get_secret_value() == FAKE_KEY and server.writes == []


def test_replace_key_of_another_organization_is_not_found(world):
    inst, _ = world["svc"]("acme").connect_values("books", "lexware", {"api_key": SecretStr(FAKE_KEY)})
    with pytest.raises(NotFound):
        world["svc"]("globex").replace_secret(inst.id, {"api_key": SecretStr(NEW_KEY)})
