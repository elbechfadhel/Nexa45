"""Lexware Office comme SOURCE (phase source 1a) : contacts → Partner CDM par un binding de source à schéma
fixe, puis aperçu DATEV Debitoren/Kreditoren EXISTANT (inchangé). Faux serveur Lexware, contacts INVENTÉS,
GET seulement : aucune écriture, aucune analyse sémantique, aucune migration."""

from __future__ import annotations

import ast
import uuid
from pathlib import Path
from typing import Any

import pytest
import uie_domain_packs  # noqa: F401  (enregistre les bindings de source et cibles)
from pydantic import SecretStr
from sqlalchemy import func, select
from test_partner_lexware_preview import CountingStore, Resolver
from uie_cdm import PartnerKind, PartnerRole
from uie_connectors.datev import DatevSchemaLoader
from uie_connectors.lexware import LexwareClient, LexwareRecordSource, LexwareSchemaLoader
from uie_connectors.lexware import source as lexware_source
from uie_connectors.lexware.ratelimit import TokenBucket
from uie_connectors.lexware.testing import FAKE_KEY, FakeLexwareServer
from uie_core.domains import DomainPackResolver, PartnerDomainService, SemanticEnrichmentService
from uie_core.exports import ExportDocumentRecord, ExportRunRecord
from uie_core.exports.service import ExportProfileService, PartnerExportService
from uie_core.identity import MembershipRole, Organization, SystemContext, TenantContext
from uie_core.instances import InstanceService
from uie_core.mapping import RecordStatus
from uie_core.partners import PartnerReadPlan, PartnerReadService
from uie_core.partners.service import PartnerError
from uie_core.registry import RegistryService
from uie_core.registry import models as m
from uie_core.sources import (
    RecordSourceRegistry,
    RecordSourceResolver,
    SourceBinding,
    SourceBindingError,
    SourceBindingRegistry,
)

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "packages" / "core" / "src" / "uie_core"
SOURCE_PY = ROOT / "packages" / "connectors" / "src" / "uie_connectors" / "lexware" / "source.py"
REAL_SPEC = ROOT / "var" / "datev" / "specs" / "extf_700" / "debitoren-kreditoren_v5.yaml"
PROFILE = {  # valeurs de TEST (aucune valeur par défaut n'est proposée dans le produit)
    "advisor_number": 1001,
    "client_number": 1,
    "fiscal_year_start": "2026-01-01",
    "account_length": 4,
    "debtor_range": {"start": 10000, "end": 69999},
    "creditor_range": {"start": 70000, "end": 99999},
    "chart_of_accounts": "03",
    "origin_code": "RE",
}

# Contacts INVENTÉS, de la forme rendue par l'API (sondage GET du compte de test, 2026-10-14)
CONTACTS: list[dict[str, Any]] = [
    {
        "id": "c-1",
        "version": 2,
        "archived": False,
        "roles": {"customer": {"number": 10001}, "vendor": {"number": 70001}},
        "company": {
            "name": "Werkstatt Beispiel GmbH",
            "vatRegistrationId": "DE 123 456 789",
            "taxNumber": "12/345",
        },
        "addresses": {
            "billing": [
                {
                    "street": "Hafenweg 4",
                    "supplement": "Halle 2",
                    "zip": "20457",
                    "city": "Hamburg",
                    "countryCode": "DE",
                }
            ],
            "shipping": [{"street": "Lagerstraße 1", "zip": "21079", "city": "Hamburg", "countryCode": "DE"}],
        },
        "emailAddresses": {"business": ["buchhaltung@werkstatt.example"]},
        "phoneNumbers": {"business": ["+49 40 000000"], "fax": ["+49 40 000001"]},
    },
    {
        "id": "c-2",
        "version": 1,
        "archived": False,
        "roles": {"vendor": {"number": 70002}},
        "person": {"salutation": "Frau", "firstName": "Anna", "lastName": "Muster"},
        "addresses": {
            "billing": [{"street": "Ringstraße 9", "zip": "1010", "city": "Wien", "countryCode": "AT"}]
        },
        "emailAddresses": {"private": ["anna.privat@example.org"]},  # jamais lue
        "phoneNumbers": {"mobile": ["+43 600 000000"], "private": ["+43 1 000000"]},
    },
    {
        "id": "c-3",
        "version": 1,
        "archived": False,
        "roles": {"customer": {"number": 10002}},
        "company": {"name": "Ohne Adresse AG"},
    },
    {
        "id": "c-4",
        "version": 1,
        "archived": True,  # archivé : exclu
        "roles": {"customer": {"number": 10003}},
        "company": {"name": "Archiviert KG"},
    },
    {
        "id": "c-5",
        "version": 1,
        "archived": False,
        "roles": {"customer": {"number": 10004}},
        "person": {"lastName": "Nachname"},  # prénom absent : jamais inventé
        "addresses": {
            "billing": [{"street": "Weg 1", "zip": "80331", "city": "München", "countryCode": "de"}]
        },
    },
]


class FakeSources(RecordSourceResolver):
    """Ouvre le VRAI lecteur Lexware sur le faux serveur (transport simulé, pas de limite de débit)."""

    def __init__(self, server: FakeLexwareServer) -> None:
        self.server = server
        self.opened: list[tuple[str, str]] = []

    def open(self, system: str, interface: str, ctx):  # noqa: ANN001, ANN201
        self.opened.append((system, interface))
        assert RecordSourceRegistry.supports(system, interface)  # le connecteur est bien enregistré
        noop = TokenBucket(clock=lambda: 0.0, sleep=lambda _s: None)
        assert ctx.secret is not None
        client = LexwareClient(
            ctx.secret, transport=self.server.transport, bucket=noop, sleep=lambda _s: None
        )
        return LexwareRecordSource(client)


@pytest.fixture
def lex(session) -> dict[str, Any]:  # noqa: ANN001
    RegistryService(session, SystemContext("test")).publish_snapshot(LexwareSchemaLoader().build_snapshot())
    org = Organization(name="Acme", slug="acme")
    session.add(org)
    session.flush()
    server = FakeLexwareServer(contacts=[dict(c) for c in CONTACTS])
    ctx = TenantContext(uuid.uuid4(), org.id, MembershipRole.OWNER)
    instances = InstanceService(session, ctx, CountingStore(), Resolver(server))
    inst, _ = instances.connect_values("books", "lexware", {"api_key": SecretStr(FAKE_KEY)})
    packs = DomainPackResolver()
    sources = FakeSources(server)

    def reader(max_partners: int | None = None) -> PartnerReadService:
        enrichment = SemanticEnrichmentService(
            session, ctx, packs, domains=PartnerDomainService(session, ctx, packs)
        )
        return PartnerReadService(
            session, ctx, instances, enrichment, max_partners=max_partners, sources=sources
        )

    server.calls.clear()  # le test de connexion n'est pas une lecture de partenaires
    return {
        "session": session,
        "ctx": ctx,
        "server": server,
        "inst": inst,
        "reader": reader,
        "sources": sources,
    }


def by_ref(result) -> dict[str, Any]:  # noqa: ANN001
    return {p.source_ref: p for p in result.partners}


# --------------------------------------------------------------------------- #
# Lecture des contacts → Partner CDM
# --------------------------------------------------------------------------- #
def test_contacts_become_partner_cdm_without_any_analysis(lex):
    result = lex["reader"]().read(lex["inst"].id)
    parts = by_ref(result)
    assert sorted(parts) == ["c-1", "c-2", "c-3", "c-5"]  # archivé exclu
    assert result.issues == [] and not result.truncated and result.read == 4

    c1 = parts["c-1"]
    assert (c1.kind, c1.name, c1.organization_name) == (
        PartnerKind.ORGANIZATION,
        "Werkstatt Beispiel GmbH",
        "Werkstatt Beispiel GmbH",
    )
    assert set(c1.roles) == {PartnerRole.CUSTOMER, PartnerRole.SUPPLIER}
    assert {(i.scheme.value, i.value) for i in c1.identifiers} == {
        ("customer_number", "10001"),
        ("supplier_number", "70001"),
    }
    addr = c1.addresses[0]
    assert (addr.street, addr.street2, addr.postal_code, addr.city, addr.country_code) == (
        "Hafenweg 4",
        "Halle 2",
        "20457",
        "Hamburg",
        "DE",
    )
    assert (c1.tax.vat_id, c1.tax.tax_number) == ("DE123456789", "12/345")
    assert (c1.contact.email, c1.contact.phone, c1.contact.fax) == (
        "buchhaltung@werkstatt.example",
        "+49 40 000000",
        "+49 40 000001",
    )
    assert c1.bank_accounts == []  # l'API n'en donne aucun

    c2 = parts["c-2"]
    assert c2.kind == PartnerKind.PERSON and c2.roles == [PartnerRole.SUPPLIER]
    assert (c2.person.full_name, c2.person.first_name, c2.person.last_name) == (
        "Anna Muster",
        "Anna",
        "Muster",
    )
    assert c2.contact.email is None and c2.contact.phone is None  # coordonnées privées jamais lues
    assert c2.contact.mobile == "+43 600 000000" and c2.addresses[0].country_code == "AT"

    assert parts["c-3"].addresses == []  # aucune adresse : rien d'inventé
    c5 = parts["c-5"]
    assert (c5.person.full_name, c5.person.first_name, c5.person.last_name) == ("Nachname", None, "Nachname")
    assert c5.addresses[0].country_code == "DE"  # code ISO normalisé en majuscules


def test_reading_is_get_only_and_writes_nothing_anywhere(lex):
    session = lex["session"]
    before = {
        t: session.scalar(select(func.count()).select_from(t))
        for t in (m.FieldSemanticBinding, m.InstanceSchemaSnapshot, ExportRunRecord, ExportDocumentRecord)
    }
    lex["reader"]().read(lex["inst"].id)
    assert lex["server"].writes == [] and {c.method for c in lex["server"].calls} == {"GET"}
    assert {c.path for c in lex["server"].calls} == {"/v1/contacts"}
    assert lex["sources"].opened == [("lexware", "rest")]
    after = {t: session.scalar(select(func.count()).select_from(t)) for t in before}
    assert after == before  # ni découverte, ni analyse, ni export, ni D11


def test_all_pages_are_read_and_the_cap_is_reported(lex, monkeypatch):
    monkeypatch.setattr(lexware_source, "PAGE_SIZE", 2)
    result = lex["reader"]().read(lex["inst"].id)
    pages = [c for c in lex["server"].calls if c.path == "/v1/contacts"]
    assert len(pages) == 3 and len(result.partners) == 4  # 5 contacts, 2 par page
    lex["server"].calls.clear()
    capped = lex["reader"](max_partners=2).read(lex["inst"].id)
    assert capped.truncated and len(capped.partners) == 2


def test_refused_key_is_a_clear_error_without_secret(lex):
    lex["server"].fail[("GET", "/v1/contacts")] = 401
    with pytest.raises(PartnerError) as exc:
        lex["reader"]().read(lex["inst"].id)
    assert "access refused" in str(exc.value) and FAKE_KEY not in str(exc.value)


def test_plan_comes_from_the_binding_and_is_checked_against_the_registry_contract(lex):
    plan = lex["reader"]().read_plan(lex["inst"].id)
    assert plan.primary.entity == "contacts" and plan.source_checksum
    assert {"name", "first_name", "last_name", "country_code", "customer_rank"} <= set(plan.primary.fields)

    good = SourceBindingRegistry.find("PARTNER", "lexware", "v1", "rest")
    assert good is not None
    bad = SourceBinding.model_validate(
        {
            **good.model_dump(mode="json"),
            "entities": [
                {
                    "role": "PRIMARY",
                    "resource": "contacts",
                    "fields": [{"key": "name", "path": "company.nickname"}],
                }
            ],
        }
    )
    with pytest.raises(SourceBindingError, match="company.nickname"):
        bad.check_contract({"contacts": {"id", "company.name"}})


def test_plans_of_analyzed_sources_keep_their_checksum():
    """Le champ ajouté au plan (provenance du binding de source) n'entre pas dans l'empreinte quand il est
    absent : les plans des sources analysées (Odoo…) et les journaux d'export restent comparables."""
    from uie_core.catalog.plan import EntityPlan, PlannedField
    from uie_core.domains.model import EntityRole

    primary = EntityPlan(
        entity="partner",
        role=EntityRole.PRIMARY,
        fields={"name": PlannedField(key="name", field="n", data_type="string")},
    )
    plan = PartnerReadPlan(primary=primary)
    import hashlib
    import json

    legacy = {"primary": primary.model_dump(mode="json"), "bank_account": None, "bank": None, "country": None}
    assert plan.checksum == hashlib.sha256(json.dumps(legacy, sort_keys=True).encode()).hexdigest()
    assert PartnerReadPlan(primary=primary, source_checksum="x").checksum != plan.checksum


# --------------------------------------------------------------------------- #
# Aperçu DATEV Debitoren/Kreditoren EXISTANT, sans aucun changement de la cible
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not REAL_SPEC.exists(), reason="spec DATEV locale absente (spec_builder)")
def test_existing_datev_preview_works_from_a_lexware_source(lex):
    session, ctx = lex["session"], lex["ctx"]
    RegistryService(session, SystemContext("test")).publish_snapshot(
        DatevSchemaLoader({"spec": REAL_SPEC}).build_snapshot()
    )
    ExportProfileService(session, ctx).save("datev", "debitoren-kreditoren", PROFILE)
    before = (
        session.scalar(select(func.count()).select_from(ExportRunRecord)),
        session.scalar(select(func.count()).select_from(ExportDocumentRecord)),
    )
    preview = PartnerExportService(session, ctx, lex["reader"]()).preview(
        "datev", "debitoren-kreditoren", lex["inst"].id
    )
    rows = {(r.source_ref, r.role): r for r in preview.records}
    assert set(rows) == {
        ("c-1", "CUSTOMER"),
        ("c-1", "SUPPLIER"),
        ("c-2", "SUPPLIER"),
        ("c-3", "CUSTOMER"),
        ("c-5", "CUSTOMER"),
    }
    assert preview.partners_read == 4
    assert all(r.account_number for r in rows.values())  # numéros proposés dans les plages du profil
    assert rows[("c-1", "CUSTOMER")].status != RecordStatus.BLOCKED
    assert rows[("c-2", "SUPPLIER")].status != RecordStatus.BLOCKED
    # aperçu : rien d'enregistré (ni journal d'export, ni D11), aucune écriture vers la source
    after = (
        session.scalar(select(func.count()).select_from(ExportRunRecord)),
        session.scalar(select(func.count()).select_from(ExportDocumentRecord)),
    )
    assert after == before and lex["server"].writes == []


# --------------------------------------------------------------------------- #
# Architecture universelle
# --------------------------------------------------------------------------- #
def _imports(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            out.add(node.module or "")
    return out


def test_fixed_schema_mechanism_names_no_system_and_the_connector_knows_no_meaning():
    for py in sorted((CORE / "sources").glob("*.py")):
        low = py.read_text(encoding="utf-8").lower()
        assert not any(w in low for w in ("lexware", "odoo", "datev", "contacts")), py.name
        assert not any(i.startswith("uie_connectors") for i in _imports(py)), py.name
    for py in (CORE / "partners" / "service.py", CORE / "partners" / "plan.py"):
        assert "lexware" not in py.read_text(encoding="utf-8").lower()
    imported = _imports(SOURCE_PY)
    assert "uie_core.sources" in imported
    assert not any(
        i.startswith(
            ("uie_cdm", "uie_core.catalog", "uie_core.partners", "uie_core.domains", "uie_core.exports")
        )
        for i in imported
    )
    # la cible DATEV et D11 ne sont pas touchés par la source
    for rel in ("exports/service.py", "exports/documents.py", "mapping/binding.py"):
        low = (CORE / rel).read_text(encoding="utf-8").lower()
        assert "uie_core.sources" not in low, rel
