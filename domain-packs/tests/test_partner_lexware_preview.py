"""Phase 1a Lexware Office — aperçu, classement D11 et frontières d'architecture.

La SOURCE de ces tests est un système IMAGINAIRE (« acme-erp ») qui fournit un Partner CDM construit à la
main : aucun connecteur de source réel n'est importé. La cible est une instance Lexware Office connectée par
un faux serveur. L'aperçu ne doit ni appeler la cible, ni lire son secret, ni rien enregistrer."""

from __future__ import annotations

import ast
import uuid
from pathlib import Path
from typing import Any

import pytest
import uie_domain_packs  # noqa: F401  (enregistre les bindings cibles)
from pydantic import SecretStr
from sqlalchemy import func, select
from test_partner_lexware_target import BINDING_FILE, LEXWARE_SRC, ROOT, company, person
from uie_cdm import Address, PersonName
from uie_connectors.lexware import LexwareClient, LexwareDiscoveryProvider, LexwareSchemaLoader
from uie_connectors.lexware.ratelimit import TokenBucket
from uie_connectors.lexware.testing import FAKE_KEY, FakeLexwareServer
from uie_core.db.base import Base
from uie_core.exports import DocumentExportState, ExportDocumentRecord, ExportRunRecord
from uie_core.exports.documents import DocumentExportLedger, DocumentScope, RecordOrigin
from uie_core.exports.records import ExportProfileRecord
from uie_core.identity import MembershipRole, Organization, SystemContext, TenantContext
from uie_core.instances import (
    CapabilityCheck,
    ConnectionContext,
    ConnectionTestResult,
    DiffAnalysis,
    FernetSecretStore,
    InstanceCreate,
    InstanceService,
    ProviderResolver,
    SchemaDiscoveryProvider,
)
from uie_core.mapping import RecordStatus
from uie_core.partners import PartnerIssue, PartnerReadResult, PartnerTargetError, PartnerTargetService
from uie_core.registry import (
    Capability,
    DataType,
    EntityDef,
    FieldDef,
    NotFound,
    RegistryService,
    SchemaSnapshotModel,
)


# --------------------------------------------------------------------------- #
# Un système source IMAGINAIRE, et une source de Partner CDM qui n'est aucun connecteur réel
# --------------------------------------------------------------------------- #
class ImaginaryErp(SchemaDiscoveryProvider):
    system_code = "acme-erp"
    connector_type = "acme-erp.fake"

    def declared_capabilities(self) -> dict[Capability, bool]:
        return {c: c == Capability.READ for c in Capability}

    def test_connection(self, ctx: ConnectionContext) -> ConnectionTestResult:
        return ConnectionTestResult(ok=True, server_info={})

    def detect_capabilities(self, ctx: ConnectionContext) -> list[CapabilityCheck]:
        return []

    def discover_schema(self, ctx: ConnectionContext) -> SchemaSnapshotModel:
        raise NotImplementedError

    def classify(self, base, instance, changes) -> DiffAnalysis:  # noqa: ANN001
        raise NotImplementedError


class CountingStore(FernetSecretStore):
    """Compte les lectures de secret : l'aperçu ne doit en faire aucune."""

    def __init__(self) -> None:
        super().__init__(FernetSecretStore.generate_key())
        self.reads = 0

    def get(self, ref: str) -> SecretStr:
        self.reads += 1
        return super().get(ref)


class Resolver(ProviderResolver):
    def __init__(self, server: FakeLexwareServer) -> None:
        self.server = server

    def resolve(self, system: str, version: str, interface: str):  # noqa: ANN201
        if system == "acme-erp":
            return ImaginaryErp()

        def client(ctx: ConnectionContext) -> LexwareClient:
            noop = TokenBucket(clock=lambda: 0.0, sleep=lambda _s: None)
            assert ctx.secret is not None
            return LexwareClient(
                ctx.secret, transport=self.server.transport, bucket=noop, sleep=lambda _s: None
            )

        return LexwareDiscoveryProvider(client_factory=client)


class AnyPartnerSource:
    """Tient lieu de lecteur de partenaires : il rend un Partner CDM, d'où qu'il vienne. La préparation n'en
    attend rien d'autre (`ctx`, `instances`, `read`)."""

    def __init__(self, instances: InstanceService, result: PartnerReadResult) -> None:
        self.ctx = instances.ctx
        self.instances = instances
        self.result = result
        self.reads: list[uuid.UUID] = []

    def read(self, instance_id: uuid.UUID) -> PartnerReadResult:
        self.instances.get(instance_id)  # même contrôle d'appartenance qu'un vrai lecteur
        self.reads.append(instance_id)
        return self.result


PARTNERS = [
    company(),  # READY
    company(source_ref="B-1", name="Alpen Handel AG", organization_name="Alpen Handel AG"),  # READY
    person(),  # READY
    person(
        source_ref="customers.csv:row-9", name="Jan Novak", person=PersonName(full_name="Jan Novak")
    ),  # BLOCKED
    company(
        source_ref="V-2",
        name="Holzwerk Süd KG",
        organization_name="Holzwerk Süd KG",
        addresses=[Address(street="Am Wald 3", city="Ulm")],  # pays manquant → BLOCKED
    ),
    company(
        source_ref="V-3",
        name="Druckhaus Nord GmbH",
        organization_name="Druckhaus Nord GmbH",
        contact={"website": "https://druckhaus.example"},  # écarté → WARNING
    ),
]


@pytest.fixture
def world(session) -> dict[str, Any]:
    registry = RegistryService(session, SystemContext("test"))
    registry.publish_snapshot(LexwareSchemaLoader().build_snapshot())
    registry.publish_snapshot(
        SchemaSnapshotModel(
            system="acme-erp",
            product="acme-erp",
            version="9",
            interface="api",
            interface_type="api",
            metadata={"system_name": "Acme ERP"},
            entities=[
                EntityDef(
                    technical_name="business_partner",
                    fields=[FieldDef(technical_name="bp_id", data_type=DataType.STRING)],
                )
            ],
        )
    )
    orgs = {slug: Organization(name=slug.title(), slug=slug) for slug in ("acme", "globex")}
    session.add_all(orgs.values())
    session.flush()
    server = FakeLexwareServer()
    store = CountingStore()

    def instances(slug: str) -> InstanceService:
        ctx = TenantContext(uuid.uuid4(), orgs[slug].id, MembershipRole.OWNER)
        return InstanceService(session, ctx, store, Resolver(server))

    def make(slug: str) -> dict[str, Any]:
        svc = instances(slug)
        source = svc.create(
            InstanceCreate(name="erp", system="acme-erp", version="9", endpoint="https://erp.example.test")
        )
        target, _ = svc.connect_values("books", "lexware", {"api_key": SecretStr(FAKE_KEY)})
        result = PartnerReadResult(
            plan_checksum="0" * 64,
            partners=PARTNERS,
            issues=[
                PartnerIssue(source_ref="V-2", name="Holzwerk Süd KG", message="Country could not be read")
            ],
        )
        src = AnyPartnerSource(svc, result)
        return {
            "svc": svc,
            "source": source,
            "target": target,
            "src": src,
            "prep": PartnerTargetService(session, svc.ctx, src),
        }

    return {
        "session": session,
        "orgs": orgs,
        "server": server,
        "store": store,
        "acme": make("acme"),
        "make": make,
    }


def _counts(session) -> dict[str, int]:  # noqa: ANN001
    return {
        t.name: session.scalar(select(func.count()).select_from(t)) or 0 for t in Base.metadata.sorted_tables
    }


# --------------------------------------------------------------------------- #
# F. Aperçu
# --------------------------------------------------------------------------- #
def test_preview_from_an_imaginary_source_counts_and_reasons(world):
    a = world["acme"]
    preview = a["prep"].preview(a["source"].id, a["target"].id)
    assert (preview.source_name, preview.source_system) == (
        "erp",
        "Acme ERP",
    )  # la source n'est PAS nommée Odoo
    assert (preview.target_name, preview.target_system, preview.target) == (
        "books",
        "Lexware Office",
        "Lexware Office contacts",
    )
    assert preview.contract == "lexware public-api v1 @1.0.0"
    assert preview.partners_read == 6
    assert (
        preview.count(RecordStatus.READY),
        preview.count(RecordStatus.WARNING),
        preview.count(RecordStatus.BLOCKED),
    ) == (3, 1, 2)
    by_ref = {r.prepared.source_ref: r for r in preview.records}
    assert by_ref["customers.csv:row-9"].prepared.status == RecordStatus.BLOCKED
    assert by_ref["V-2"].source_issues == ["Country could not be read"]
    assert by_ref["V-3"].prepared.status == RecordStatus.WARNING
    assert by_ref["SAP-BP/0000104711"].prepared.request["company"]["name"] == "Nordwind Handel AG"
    assert a["src"].reads == [a["source"].id]  # la source a été lue une fois, par son identifiant générique


def test_preview_is_side_effect_free_never_calls_the_target_and_reads_no_secret(world):
    a, session = world["acme"], world["session"]
    session.flush()
    before, calls, reads = _counts(session), len(world["server"].calls), world["store"].reads
    first = a["prep"].preview(a["source"].id, a["target"].id)
    second = a["prep"].preview(a["source"].id, a["target"].id)
    session.flush()
    assert _counts(session) == before  # aucune ligne écrite, dans aucune table
    assert session.scalar(select(func.count()).select_from(ExportDocumentRecord)) == 0
    assert session.scalar(select(func.count()).select_from(ExportRunRecord)) == 0
    assert not session.new and not session.dirty
    assert len(world["server"].calls) == calls  # la cible n'est jamais appelée par l'aperçu
    assert world["server"].writes == []
    assert world["store"].reads == reads  # la clé d'API n'est même pas déchiffrée
    assert first.model_dump() == second.model_dump()  # même entrée → même aperçu
    dumped = str(first.model_dump())
    assert FAKE_KEY not in dumped and "Bearer" not in dumped and "fernet:" not in dumped


def test_targets_are_the_connected_systems_that_can_receive_partners(world):
    a = world["acme"]
    (option,) = a["prep"].targets(a["source"].id)
    assert (option.instance_id, option.name, option.system, option.target, option.connected) == (
        a["target"].id,
        "books",
        "Lexware Office",
        "Lexware Office contacts",
        True,
    )
    # un système qui ne reçoit pas de partenaires n'est pas une cible ; deux fois le même système non plus
    with pytest.raises(NotFound):
        a["prep"].preview(a["target"].id, a["source"].id)
    with pytest.raises(PartnerTargetError):
        a["prep"].preview(a["target"].id, a["target"].id)


def test_another_organization_sees_neither_the_source_nor_the_target(world):
    a, b = world["acme"], world["make"]("globex")
    assert [t.instance_id for t in b["prep"].targets(b["source"].id)] == [b["target"].id]
    reads = world["store"].reads
    for source, target in ((a["source"].id, b["target"].id), (b["source"].id, a["target"].id)):
        with pytest.raises(NotFound):
            b["prep"].preview(source, target)
    with pytest.raises(NotFound):
        b["prep"].targets(a["source"].id)
    assert world["store"].reads == reads and b["src"].reads == []  # aucun secret, aucune lecture


# --------------------------------------------------------------------------- #
# G. D11 : empreinte déterministe, portée indépendante de la source, aucun historique créé
# --------------------------------------------------------------------------- #
def test_fingerprint_is_deterministic_and_only_depends_on_the_prepared_request(world):
    a = world["acme"]
    one = {
        r.prepared.source_ref: r.prepared.content
        for r in a["prep"].preview(a["source"].id, a["target"].id).records
    }
    two = {
        r.prepared.source_ref: r.prepared.content
        for r in a["prep"].preview(a["source"].id, a["target"].id).records
    }
    assert {k: v.fingerprint for k, v in one.items()} == {k: v.fingerprint for k, v in two.items()}
    unit = one["SAP-BP/0000104711"].units[0]
    assert unit["company.name"] == "Nordwind Handel AG" and unit["roles.customer"] == "true"
    # rien de volatil ni de secret dans le contenu empreinté : seulement des champs du contrat cible
    assert set(unit) <= {f.field for f in a["prep"].resolve(a["target"].id)[1].binding.fields}
    assert FAKE_KEY not in str(one) and not any("date" in k.lower() or "time" in k.lower() for k in unit)
    assert one["SAP-BP/0000104711"].fingerprint != one["B-1"].fingerprint  # contenu différent


def test_same_data_from_two_different_sources_gives_the_same_fingerprint():
    """L'empreinte ne contient ni la référence source ni le système d'origine : seulement la requête."""
    from test_partner_lexware_target import prepare

    from_erp, from_file = prepare(company(source_ref="ERP/4711"), company(source_ref="partners.xlsx:row-2"))
    assert from_erp.source_ref != from_file.source_ref
    assert from_erp.request == from_file.request
    assert from_erp.content.fingerprint == from_file.content.fingerprint
    assert "ERP/4711" not in str(from_erp.content.units)


def test_d11_classification_is_reused_as_is_and_preview_never_writes_history(world):
    a, session = world["acme"], world["session"]
    preview = a["prep"].preview(a["source"].id, a["target"].id)
    assert {r.export_state for r in preview.records} == {
        DocumentExportState.NEW
    }  # aucun historique : tout est nouveau
    assert session.scalar(select(func.count()).select_from(ExportDocumentRecord)) == 0

    # Un historique de FICHIER (sans instance cible) pour le même système / produit n'est JAMAIS celui d'une
    # cible par API : depuis la phase 1b, l'historique d'un envoi par API est propre à l'instance cible
    # (ALREADY / CHANGED par l'envoi réel : test_partner_lexware_send.py).
    by_ref = {r.prepared.source_ref: r.prepared for r in preview.records}
    scope = DocumentScope(
        organization_id=a["source"].organization_id,
        instance_id=a["source"].id,
        domain="partner",
        target_system="lexware",
        target_product="public-api",
    )
    profile = ExportProfileRecord(
        organization_id=scope.organization_id,
        source_model="partner",
        target_system="lexware",
        target_product="public-api",
        values={},
    )
    session.add(profile)
    session.flush()
    run = ExportRunRecord(
        organization_id=scope.organization_id,
        profile_id=profile.id,
        instance_id=a["source"].id,
        target_label="test",
        binding_version="1.0.0",
        encoding="utf-8",
        file_sha256="0" * 64,
        file_size=0,
    )
    session.add(run)
    session.flush()
    DocumentExportLedger(session).record(
        scope, run, [(by_ref["SAP-BP/0000104711"].content, RecordOrigin.EXPORTED, None)]
    )
    again = {r.prepared.source_ref: r for r in a["prep"].preview(a["source"].id, a["target"].id).records}
    assert again["SAP-BP/0000104711"].export_state == DocumentExportState.NEW
    assert (
        DocumentExportLedger(session)
        .classify(scope, [by_ref["SAP-BP/0000104711"].content])["SAP-BP/0000104711"]
        .state
        == DocumentExportState.ALREADY_EXPORTED
    )  # le registre des fichiers, lui, est inchangé
    assert (
        session.scalar(select(func.count()).select_from(ExportDocumentRecord)) == 1
    )  # l'aperçu n'ajoute rien


# --------------------------------------------------------------------------- #
# E. Frontières d'architecture
# --------------------------------------------------------------------------- #
SOURCE_WORDS = ("odoo", "res.partner", "xmlrpc", "xml-rpc", "customer_rank", "supplier_rank", "ir.model")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            out.add(node.module or "")
    return out


def test_lexware_target_does_not_depend_on_any_source_connector():
    """Critère d'acceptation : remplacer la source ne demande AUCUN changement dans la cible Lexware."""
    files = sorted(LEXWARE_SRC.rglob("*.py"))
    assert len(files) >= 7
    for py in files:
        imported = _imports(py)
        # aucun autre connecteur (donc aucune source), aucun modèle canonique, aucun domaine métier
        assert not any(
            i.startswith("uie_connectors.")
            and not i.startswith(("uie_connectors.lexware", "uie_connectors.errors"))
            for i in imported
        ), py.name
        assert not any(
            i.startswith(
                (
                    "uie_cdm",
                    "uie_core.partners",
                    "uie_core.accounting",
                    "uie_core.catalog",
                    "uie_core.domains",
                )
            )
            for i in imported
        ), py.name
        low = py.read_text(encoding="utf-8").lower()
        assert not any(word in low for word in SOURCE_WORDS), py.name
    for data in (BINDING_FILE, LEXWARE_SRC / "contract" / "public_api_v1.yaml"):
        low = data.read_text(encoding="utf-8").lower()
        assert not any(word in low for word in SOURCE_WORDS), data.name


def test_binding_reads_only_partner_cdm_paths():
    import yaml

    binding = yaml.safe_load(BINDING_FILE.read_text(encoding="utf-8"))
    view = set(company(bank_accounts=[], roles=["CUSTOMER", "SUPPLIER"]).mapping_view()) | {
        "person.first_name",
        "person.last_name",
        "address.DELIVERY.street",
        "address.DELIVERY.street2",
        "address.DELIVERY.postal_code",
        "address.DELIVERY.city",
        "address.DELIVERY.country_code",
        "contact.website",
        "bank_accounts",
        "identifiers.customer_number",
        "identifiers.supplier_number",
        "name",
    }
    used = {f[k] for f in binding["fields"] for k in ("source", "fallback") if f.get(k)}
    used |= {o["source"] for o in binding["omitted"]} | {
        r["source"] for r in binding["rules"] if r.get("source")
    }
    assert used <= view, sorted(used - view)


def test_partner_cdm_and_core_contain_nothing_specific_to_the_target():
    cdm = (ROOT / "packages" / "cdm" / "src" / "uie_cdm" / "partner.py").read_text(encoding="utf-8").lower()
    schema = (
        (ROOT / "packages" / "cdm" / "schemas" / "partner-1.0.0.json").read_text(encoding="utf-8").lower()
    )
    for word in ("lexware", "lexoffice", "vendor", "contactpersons", "vatregistrationid", "lastname"):
        assert word not in cdm and word not in schema, word
    core = ROOT / "packages" / "core" / "src" / "uie_core"
    for py in sorted(core.rglob("*.py")):
        low = py.read_text(encoding="utf-8").lower()
        assert "lexware" not in low and "lexoffice" not in low, py.relative_to(core)


def test_generic_partner_preparation_names_no_system_at_all():
    """Le service de préparation est générique des DEUX côtés : ni source, ni cible nommée dans son code."""
    target_py = ROOT / "packages" / "core" / "src" / "uie_core" / "partners" / "target.py"
    tree = ast.parse(target_py.read_text(encoding="utf-8"))
    docstrings = {
        id(n.body[0].value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Module | ast.ClassDef | ast.FunctionDef)
        and n.body
        and isinstance(n.body[0], ast.Expr)
        and isinstance(n.body[0].value, ast.Constant)
    }
    texts = [n.id for n in ast.walk(tree) if isinstance(n, ast.Name)]
    texts += [n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)]
    texts += [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
    ]
    for t in texts:
        assert not any(w in t.lower() for w in ("odoo", "lexware", "datev", "sap", "contact")), t
    assert not any(i.startswith("uie_connectors") for i in _imports(target_py))


def test_phase_1b_adds_exactly_one_additive_migration():
    """1a : aucune table. 1b : UNE migration additive (liens, envois, éléments ; D11 : instance cible et
    élément d'envoi facultatifs), rien de supprimé des tables existantes."""
    versions = ROOT / "packages" / "core" / "src" / "uie_core" / "db" / "migrations" / "versions"
    head = "139e224e09c5"  # registre des pièces exportées (D11)
    children = [
        p
        for p in versions.glob("*.py")
        if f'down_revision = "{head}"' in p.read_text("utf-8")
        or f"down_revision = '{head}'" in p.read_text("utf-8")
    ]
    assert [p.name[:12] for p in children] == ["c74bac7b2242"]
    upgrade = children[0].read_text("utf-8").split("def downgrade")[0]
    assert "drop_" not in upgrade
