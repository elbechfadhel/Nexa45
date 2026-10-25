"""ProductDomainService générique : domain pack factice d'un système imaginaire (aucune dépendance Odoo)."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, text
from uie_core.domains import (
    DomainEntityDef,
    DomainFieldDef,
    DomainKey,
    DomainPack,
    DomainPackResolver,
    EntityRole,
    FieldGroup,
    FieldOrigin,
    MediaRole,
    ProductDomainDefinition,
    ProductDomainError,
    ProductDomainService,
    ProductDomainSnapshot,
    Relevance,
    ResolvedDomain,
    summarize_domain,
)
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
)
from uie_core.registry import (
    Capability,
    DataType,
    EntityDef,
    FieldDef,
    RegistryService,
    SchemaSnapshotModel,
)
from uie_core.registry import models as m

PASSWORD = "Sh0p-Secret!"
DOMAINS_SRC = Path(__file__).resolve().parents[1] / "src" / "uie_core" / "domains"


# --------------------------------------------------------------------------- #
# Système imaginaire « shop »
# --------------------------------------------------------------------------- #
def _f(name: str, data_type: DataType = DataType.STRING, **meta: object) -> FieldDef:
    return FieldDef(technical_name=name, label=name.title(), data_type=data_type, metadata=dict(meta))


def _standard() -> list[EntityDef]:
    return [
        EntityDef(
            technical_name="article",
            display_name="Article",
            fields=[
                _f("id", DataType.INTEGER, technical=True),
                _f("title"),
                _f("sku"),
                _f("price", DataType.DECIMAL),
                _f("notes", DataType.TEXT),
                _f("pic_2048", DataType.BINARY),
                _f("pic_512", DataType.BINARY),
            ],
        ),
        EntityDef(
            technical_name="article_variant",
            fields=[_f("article_id", DataType.REFERENCE), _f("pic_variant_1024", DataType.BINARY)],
        ),
        EntityDef(technical_name="invoice", fields=[_f("number")]),  # hors scope
    ]


def _snap(entities: list[EntityDef]) -> SchemaSnapshotModel:
    return SchemaSnapshotModel(
        system="shop",
        product="shop",
        version="1",
        interface="api",
        interface_type="rest",
        source={"loader": "shop"},
        entities=entities,
    )


def _instance_schema(round_: int) -> SchemaSnapshotModel:
    """Instance réelle : champ custom et tiers sur article, entité tierce hors scope, price absent ;
    à la 2e découverte, un champ custom de plus."""
    art, variant, invoice = _standard()
    art_fields = [f for f in art.fields if f.technical_name != "price"]
    art_fields += [_f("zz_supplier"), _f("ext_rating", DataType.DECIMAL, modules=["rating"])]
    if round_ > 1:
        art_fields.append(_f("zz_color"))
    return _snap(
        [
            art.model_copy(update={"fields": art_fields}),
            variant,
            invoice,
            EntityDef(technical_name="ext_employee", fields=[_f("name")]),  # entité tierce hors scope
        ]
    )


class ShopProvider(SchemaDiscoveryProvider):
    system_code = "shop"
    connector_type = "shop.rest"

    def __init__(self) -> None:
        self.round = 0

    def declared_capabilities(self) -> dict[Capability, bool]:
        return {c: True for c in Capability}

    def test_connection(self, ctx: ConnectionContext) -> ConnectionTestResult:
        return ConnectionTestResult(ok=True, capabilities=self.detect_capabilities(ctx))

    def detect_capabilities(self, ctx: ConnectionContext) -> list[CapabilityCheck]:
        return [CapabilityCheck(capability=Capability.READ, status=CapabilityStatus.VERIFIED)]

    def discover_schema(self, ctx: ConnectionContext) -> SchemaSnapshotModel:
        self.round += 1
        return _instance_schema(self.round)

    def classify(self, base, instance, changes) -> DiffAnalysis:  # noqa: ANN001
        def origin(path: str) -> Origin:
            return (
                Origin.CUSTOM if "zz_" in path else Origin.THIRD_PARTY if "ext_" in path else Origin.UNKNOWN
            )

        return DiffAnalysis(
            classified={
                c.path: ClassifiedChange(path=c.path, origin=origin(c.path), reason=f"règle shop ({c.path})")
                for c in changes
            }
        )


class ShopResolver(ProviderResolver):
    def __init__(self, provider: SchemaDiscoveryProvider) -> None:
        self.provider = provider

    def resolve(self, system: str, version: str, interface: str) -> SchemaDiscoveryProvider:
        return self.provider


class ShopPack(DomainPack):
    """Domain pack factice : connaissance déclarée en dur (un vrai pack la lit dans des fichiers)."""

    domain_key = DomainKey.PRODUCT
    system_code = "shop"
    version_ = "1.0.0"
    listed = {
        ("article", "title"): FieldGroup.IDENTITY,
        ("article", "sku"): FieldGroup.IDENTITY,
        ("article", "price"): FieldGroup.COMMERCIAL,
        ("article", "legacy_code"): FieldGroup.IDENTITY,  # déclaré, absent de toutes les versions
    }
    roles = {
        "article": EntityRole.PRIMARY,
        "article_variant": EntityRole.VARIANT,
        "shelf": EntityRole.CATEGORY,
    }

    @property
    def name(self) -> str:
        return "shop/product_domain"

    @property
    def definition_version(self) -> str:
        return self.version_

    def scope_entities(self) -> list[str]:
        return list(self.roles)

    def resolve(self, snapshot: SchemaSnapshotModel) -> ResolvedDomain:
        entities, fields = [], []
        for name, role in self.roles.items():
            ent = snapshot.entity(name)
            entities.append(DomainEntityDef(technical_name=name, role=role, present=ent is not None))
            real = {f.technical_name: f for f in ent.fields} if ent else {}
            pics = sorted((n for n in real if n.startswith("pic_")), key=lambda n: -int(n.rsplit("_", 1)[1]))
            for fname, f in real.items():
                media = None
                if fname in pics:
                    media = (
                        MediaRole.VARIANT
                        if role == EntityRole.VARIANT
                        else (MediaRole.MAIN if fname == pics[0] else MediaRole.DERIVATIVE)
                    )
                if media:
                    group, rel = FieldGroup.MEDIA, Relevance.RELEVANT
                elif (name, fname) in self.listed:
                    group, rel = self.listed[(name, fname)], Relevance.RELEVANT
                elif f.metadata.get("technical"):
                    group, rel = FieldGroup.TECHNICAL, Relevance.NOT_RELEVANT
                else:
                    group, rel = FieldGroup.OTHER, Relevance.POSSIBLE
                fields.append(
                    DomainFieldDef(
                        entity_technical_name=name,
                        field_technical_name=fname,
                        group=group,
                        relevance=rel,
                        listed=(name, fname) in self.listed,
                        is_media=media is not None,
                        media_role=media,
                        label=f.label,
                        data_type=f.data_type.value,
                        modules=list(f.metadata.get("modules") or []),
                    )
                )
            for (ename, fname), group in self.listed.items():
                if ename == name and fname not in real:
                    fields.append(
                        DomainFieldDef(
                            entity_technical_name=name,
                            field_technical_name=fname,
                            present=False,
                            group=group,
                            relevance=Relevance.RELEVANT,
                            listed=True,
                        )
                    )
        return ResolvedDomain(entities=entities, fields=fields)


class ShopPackResolver(DomainPackResolver):
    def __init__(self, pack: DomainPack) -> None:
        self.pack = pack

    def resolve(self, domain_key: DomainKey, system: str, version: str) -> DomainPack:
        assert (domain_key, system, version) == (DomainKey.PRODUCT, "shop", "1")
        return self.pack


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def pack() -> ShopPack:
    return ShopPack()


@pytest.fixture
def domains(session, pack) -> ProductDomainService:
    RegistryService(session, SystemContext("test")).publish_snapshot(_snap(_standard()))
    return ProductDomainService(session, SystemContext("test"), ShopPackResolver(pack))


@pytest.fixture
def instances(session, domains) -> InstanceService:
    return InstanceService(
        session,
        SystemContext("test"),
        FernetSecretStore(FernetSecretStore.generate_key()),
        ShopResolver(ShopProvider()),
    )


@pytest.fixture
def inst(instances):
    i = instances.create(
        InstanceCreate(
            organization_id=GO45_DEV_ORG_ID,
            name="shop-customer",
            system="shop",
            version="1",
            endpoint="https://shop.invalid",
            password=SecretStr(PASSWORD),
        )
    )
    instances.discover_schema(i.id)
    return i


def _field(fields: list[DomainFieldDef], path: str) -> DomainFieldDef:
    return next(f for f in fields if f.path == path)


# --------------------------------------------------------------------------- #
# 1-2. Définition standard
# --------------------------------------------------------------------------- #
def test_definition_resolution_absent_elements_are_not_errors(domains):
    d = domains.get_standard_definition("shop", "1").definition
    roles = {e.technical_name: (e.role, e.present) for e in d.entities}
    assert roles == {
        "article": (EntityRole.PRIMARY, True),
        "article_variant": (EntityRole.VARIANT, True),
        "shelf": (EntityRole.CATEGORY, False),  # déclarée, absente du snapshot : pas d'erreur
    }
    assert _field(d.fields, "article.legacy_code").present is False
    assert _field(d.fields, "article.sku").group == FieldGroup.IDENTITY
    assert _field(d.fields, "article.id").group == FieldGroup.TECHNICAL
    assert _field(d.fields, "article.notes").relevance == Relevance.POSSIBLE
    assert (
        _field(d.fields, "article.pic_2048").media_role,
        _field(d.fields, "article.pic_512").media_role,
    ) == (
        MediaRole.MAIN,
        MediaRole.DERIVATIVE,
    )
    assert _field(d.fields, "article_variant.pic_variant_1024").media_role == MediaRole.VARIANT
    assert not any(f.entity_technical_name == "invoice" for f in d.fields)  # hors scope
    present = {e.technical_name: e.entity_id for e in d.entities if e.present}
    assert all(present.values())  # ids des entités du snapshot standard


def test_definition_checksum_stable_persisted_once_and_bound(domains, session, pack):
    a = domains.get_standard_definition("shop", "1")
    b = domains.get_standard_definition("shop", "1")
    assert (a.id, a.definition.checksum) == (b.id, b.definition.checksum)
    dumped = a.definition.model_dump(mode="json")
    assert ProductDomainDefinition.compute_checksum(dumped) == a.definition.checksum
    assert session.scalar(select(func.count()).select_from(m.ProductDomainDefinitionRecord)) == 1
    # le Registry sait quelles entités participent à PRODUCT (domain_bindings), sans le domain pack
    bindings = RegistryService(session, SystemContext("test")).list_domain_bindings(
        a.definition.base_schema_snapshot_id, "PRODUCT"
    )
    assert sorted(b_.role for b_ in bindings) == ["PRIMARY", "VARIANT"]
    # contenu modifié sans nouvelle definition_version : refusé (définition immutable)
    pack.listed = {**pack.listed, ("article", "notes"): FieldGroup.DESCRIPTION}
    with pytest.raises(ProductDomainError, match="definition_version"):
        domains.get_standard_definition("shop", "1")
    # nouvelle version du pack → nouvelle définition, l'ancienne intacte
    pack.version_ = "1.1.0"
    c = domains.get_standard_definition("shop", "1")
    assert c.id != a.id and c.definition.checksum != a.definition.checksum


# --------------------------------------------------------------------------- #
# 3-5. Domaine effectif
# --------------------------------------------------------------------------- #
def test_instance_domain_binds_exact_snapshots(domains, instances, inst):
    isnap = instances.get_instance_snapshot(inst.id)
    stored = domains.build_instance_domain(inst.id)
    s = stored.snapshot
    assert stored.created is True and stored.instance_schema_snapshot_sequence == 1
    assert s.instance_schema_snapshot_id == isnap.id
    assert s.instance_schema_snapshot_checksum == isnap.checksum
    assert s.base_schema_snapshot_id == isnap.base_snapshot_id
    definition = domains.definition_for_snapshot(isnap.base_snapshot_id).definition
    assert s.standard_definition_checksum == definition.checksum
    assert ProductDomainSnapshot.compute_checksum(s.model_dump(mode="json")) == s.checksum


def test_second_analysis_returns_existing_snapshot(domains, inst, session):
    first = domains.build_instance_domain(inst.id)
    again = domains.build_instance_domain(inst.id)
    assert again.id == first.id and again.created is False
    assert again.snapshot == first.snapshot
    assert session.scalar(select(func.count()).select_from(m.ProductDomainSnapshotRecord)) == 1


def test_new_schema_snapshot_gives_new_domain_snapshot(domains, instances, inst, session):
    first = domains.build_instance_domain(inst.id)
    frozen = session.get(m.ProductDomainSnapshotRecord, first.id).snapshot
    instances.discover_schema(inst.id)  # 2e snapshot d'instance (zz_color en plus)
    second = domains.build_instance_domain(inst.id)
    assert second.id != first.id and second.instance_schema_snapshot_sequence == 2
    assert "article.zz_color" in {f.path for f in second.snapshot.extensions}
    assert session.get(m.ProductDomainSnapshotRecord, first.id).snapshot == frozen  # ancien intact
    assert [h.id for h in domains.list_instance_domains(inst.id)] == [second.id, first.id]
    assert domains.get_instance_domain(inst.id).id == second.id
    assert domains.get_instance_domain(inst.id, first.id).snapshot == first.snapshot


# --------------------------------------------------------------------------- #
# 6. Immutabilité (ORM + trigger)
# --------------------------------------------------------------------------- #
def test_domain_tables_are_immutable(domains, inst, session):
    stored = domains.build_instance_domain(inst.id)
    session.flush()
    for model, row_id in (
        (m.ProductDomainSnapshotRecord, stored.id),
        (m.ProductDomainDefinitionRecord, stored.definition_id),
    ):
        nested = session.begin_nested()
        row = session.get(model, row_id)
        row.checksum = "0" * 64
        with pytest.raises(ValueError, match="immutable"):
            session.flush()
        nested.rollback()
    for table, row_id in (
        ("product_domain_snapshots", stored.id),
        ("product_domain_definitions", stored.definition_id),
    ):
        nested = session.begin_nested()
        with pytest.raises(Exception, match="immutable"):
            session.execute(text(f"UPDATE {table} SET checksum = 'x' WHERE id = :i"), {"i": row_id})
        nested.rollback()


def test_instance_deletion_cascades_domain_snapshots_only(domains, instances, inst, session):
    stored = domains.build_instance_domain(inst.id)
    instances.delete(inst.id)
    session.flush()
    assert session.get(m.ProductDomainSnapshotRecord, stored.id) is None
    assert session.get(m.ProductDomainDefinitionRecord, stored.definition_id) is not None  # standard conservé


# --------------------------------------------------------------------------- #
# 7-8. Extensions et éléments standard absents
# --------------------------------------------------------------------------- #
def test_extensions_are_scope_filtered_with_origin(domains, inst):
    s = domains.build_instance_domain(inst.id).snapshot
    ext = {f.path: f for f in s.extensions}
    assert set(ext) == {"article.zz_supplier", "article.ext_rating"}
    assert (ext["article.zz_supplier"].origin, ext["article.zz_supplier"].relevance) == (
        FieldOrigin.CUSTOM,
        Relevance.UNRESOLVED,
    )
    assert ext["article.ext_rating"].origin == FieldOrigin.THIRD_PARTY
    assert ext["article.ext_rating"].modules == ["rating"] and ext["article.ext_rating"].origin_reason
    # entité tierce ajoutée hors scope : nulle part dans le domaine
    everything = [e.technical_name for e in s.entities] + [f.path for f in s.fields]
    assert not any("ext_employee" in x or x.startswith("invoice") for x in everything)
    # champs standard : origine STANDARD, pertinence du pack conservée
    assert _field(s.fields, "article.sku").origin == FieldOrigin.STANDARD
    assert _field(s.fields, "article.sku").relevance == Relevance.RELEVANT
    # filtres du service
    assert [f.path for f in domains.list_domain_fields(inst.id, origin=FieldOrigin.CUSTOM)] == [
        "article.zz_supplier"
    ]
    assert {f.path for f in domains.list_domain_fields(inst.id, group=FieldGroup.MEDIA)} == {
        "article.pic_2048",
        "article.pic_512",
        "article_variant.pic_variant_1024",
    }
    assert [e.technical_name for e in domains.list_domain_entities(inst.id, role=EntityRole.VARIANT)] == [
        "article_variant"
    ]


def test_missing_standard(domains, inst):
    missing = domains.build_instance_domain(inst.id).snapshot.missing_standard
    assert [(x.object_type, x.entity_technical_name, x.field_technical_name) for x in missing] == [
        ("field", "article", "price")
    ]
    assert missing[0].group == FieldGroup.COMMERCIAL and missing[0].origin == FieldOrigin.UNKNOWN
    # élément déclaré mais déjà absent du standard (shelf, legacy_code) : jamais « manquant »
    s = domains.get_instance_domain(inst.id).snapshot
    summary = summarize_domain(s.entities, s.fields, s.extensions, s.missing_standard)
    assert (
        summary.absent_entities == ["shelf"] and summary.missing_count == 1 and summary.extension_count == 2
    )


def test_analysis_never_contacts_the_instance(domains, instances, inst):
    provider = instances.providers.provider
    before = provider.round
    domains.build_instance_domain(inst.id)
    assert provider.round == before  # uniquement des snapshots persistés


# --------------------------------------------------------------------------- #
# 9. Architecture
# --------------------------------------------------------------------------- #
def test_domains_module_has_no_concrete_system_knowledge():
    forbidden_imports = ("uie_connectors", "uie_domain_packs", "yaml")
    for py in DOMAINS_SRC.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        docstrings = {
            id(n.body[0].value)
            for n in ast.walk(tree)
            if isinstance(n, ast.Module | ast.ClassDef | ast.FunctionDef)
            and n.body
            and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                mods = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
                assert not any(x.startswith(forbidden_imports) for x in mods), f"{py}: {mods}"
            if isinstance(node, ast.Name | ast.Attribute):
                ident = node.id if isinstance(node, ast.Name) else node.attr
                assert "odoo" not in ident.lower(), f"{py}: {ident}"
                assert ident not in {"open", "read_text", "safe_load"}, f"{py}: lecture de fichier ({ident})"
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                low = node.value.lower()
                assert "odoo" not in low and "product.template" not in low, f"{py}: {node.value!r}"
    code = (
        "import sys, uie_core.domains; "
        "print(any(m.split('.')[0] in {'uie_connectors', 'uie_domain_packs', 'yaml'} for m in sys.modules))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "False"
