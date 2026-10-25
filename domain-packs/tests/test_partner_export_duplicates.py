"""Protection contre les doublons DATEV entre sources (étape 1, sans migration) : un partenaire déjà exporté
depuis une AUTRE source du même profil (même n° de TVA normalisé, même rôle) est BLOQUÉ dans l'aperçu et
n'est jamais écrit ; jamais par le nom seul ; aucun numéro existant modifié ; D11 non touché.

Deux sources à schéma fixe (faux serveurs Lexware, clés différentes, contacts INVENTÉS) dans la même
organisation : la règle est générique, elle ne dépend ni du système source ni de la cible."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
import uie_domain_packs  # noqa: F401
from pydantic import SecretStr
from sqlalchemy import func, select
from test_partner_lexware_preview import CountingStore
from uie_connectors.datev import DatevSchemaLoader
from uie_connectors.lexware import (
    LexwareClient,
    LexwareDiscoveryProvider,
    LexwareRecordSource,
    LexwareSchemaLoader,
)
from uie_connectors.lexware.ratelimit import TokenBucket
from uie_connectors.lexware.testing import FakeLexwareServer
from uie_core.domains import DomainPackResolver, PartnerDomainService, SemanticEnrichmentService
from uie_core.exports import ExportDocumentRecord
from uie_core.exports.records import ExportAccountAssignmentRecord
from uie_core.exports.service import ExportProfileService, PartnerExportService
from uie_core.identity import MembershipRole, Organization, SystemContext, TenantContext
from uie_core.instances import InstanceService, ProviderResolver
from uie_core.mapping import RecordStatus
from uie_core.partners import PartnerReadService
from uie_core.registry import RegistryService
from uie_core.sources import RecordSourceResolver

ROOT = Path(__file__).resolve().parents[2]
REAL_SPEC = ROOT / "var" / "datev" / "specs" / "extf_700" / "debitoren-kreditoren_v5.yaml"
pytestmark = pytest.mark.skipif(not REAL_SPEC.exists(), reason="spec DATEV locale absente (spec_builder)")
TARGET = ("datev", "debitoren-kreditoren")
PROFILE = {  # valeurs de TEST
    "advisor_number": 1001,
    "client_number": 1,
    "fiscal_year_start": "2026-01-01",
    "account_length": 4,
    "debtor_range": {"start": 10000, "end": 69999},
    "creditor_range": {"start": 70000, "end": 99999},
    "chart_of_accounts": "03",
    "origin_code": "RE",
}
KEYS = {"old": "old-source-key-0001", "new": "new-source-key-0002"}
ADDRESS = {"billing": [{"street": "Weg 1", "zip": "20457", "city": "Hamburg", "countryCode": "DE"}]}


def contact(cid: str, name: str, vat: str | None, roles: dict[str, Any]) -> dict[str, Any]:
    company: dict[str, Any] = {"name": name}
    if vat:
        company["vatRegistrationId"] = vat
    return {"id": cid, "archived": False, "roles": roles, "company": company, "addresses": ADDRESS}


CUSTOMER, VENDOR = {"customer": {}}, {"vendor": {}}
# source déjà exportée : Alpha client (10000), Beta fournisseur (70000), Gamma client sans TVA (10001)
OLD = [
    contact("o-1", "Alpha GmbH", "DE111111111", CUSTOMER),
    contact("o-2", "Beta AG", "DE222222222", VENDOR),
    contact("o-3", "Gamma KG", None, CUSTOMER),
]
NEW = [
    contact("n-1", "Alpha Handels GmbH", "de 111 111 111", CUSTOMER),  # même TVA (autre forme), autre nom
    contact("n-2", "Beta AG", "DE222222222", CUSTOMER),  # même TVA mais AUTRE rôle : pas un doublon
    contact("n-3", "Gamma KG", None, CUSTOMER),  # même NOM, sans TVA : jamais rapproché par le nom
    contact("n-4", "Delta SE", "DE444444444", CUSTOMER),  # nouveau
]


class Servers:
    def __init__(self) -> None:
        self.by_key = {
            KEYS["old"]: FakeLexwareServer(api_key=KEYS["old"], contacts=[dict(c) for c in OLD]),
            KEYS["new"]: FakeLexwareServer(api_key=KEYS["new"], contacts=[dict(c) for c in NEW]),
        }

    def client(self, secret: SecretStr) -> LexwareClient:
        server = self.by_key[secret.get_secret_value()]
        noop = TokenBucket(clock=lambda: 0.0, sleep=lambda _s: None)
        return LexwareClient(secret, transport=server.transport, bucket=noop, sleep=lambda _s: None)


class Providers(ProviderResolver):
    def __init__(self, servers: Servers) -> None:
        self.servers = servers

    def resolve(self, system, version, interface):  # noqa: ANN001, ANN201
        return LexwareDiscoveryProvider(client_factory=lambda ctx: self.servers.client(ctx.secret))


class Sources(RecordSourceResolver):
    def __init__(self, servers: Servers) -> None:
        self.servers = servers

    def open(self, system, interface, ctx):  # noqa: ANN001, ANN201
        return LexwareRecordSource(self.servers.client(ctx.secret))


@pytest.fixture
def dup(session) -> dict[str, Any]:  # noqa: ANN001
    registry = RegistryService(session, SystemContext("test"))
    registry.publish_snapshot(LexwareSchemaLoader().build_snapshot())
    registry.publish_snapshot(DatevSchemaLoader({"spec": REAL_SPEC}).build_snapshot())
    org = Organization(name="Acme", slug="acme")
    session.add(org)
    session.flush()
    ctx = TenantContext(uuid.uuid4(), org.id, MembershipRole.OWNER)
    servers = Servers()
    instances = InstanceService(session, ctx, CountingStore(), Providers(servers))
    old, _ = instances.connect_values("old books", "lexware", {"api_key": SecretStr(KEYS["old"])})
    new, _ = instances.connect_values("new books", "lexware", {"api_key": SecretStr(KEYS["new"])})
    profile = ExportProfileService(session, ctx).save(*TARGET, PROFILE)
    # état d'un export PRÉCÉDENT depuis l'ancienne source (numéros déjà dans DATEV)
    for ref, role, number in (
        ("o-1", "CUSTOMER", "10000"),
        ("o-2", "SUPPLIER", "70000"),
        ("o-3", "CUSTOMER", "10001"),
    ):
        session.add(
            ExportAccountAssignmentRecord(
                profile_id=profile.id,
                instance_id=old.id,
                source_ref=ref,
                role=role,
                account_number=number,
                origin="ASSIGNED",
            )
        )
    session.flush()
    packs = DomainPackResolver()
    partners = PartnerReadService(
        session,
        ctx,
        instances,
        SemanticEnrichmentService(session, ctx, packs, domains=PartnerDomainService(session, ctx, packs)),
        sources=Sources(servers),
    )
    for srv in servers.by_key.values():
        srv.calls.clear()
    return {
        "session": session,
        "old": old,
        "new": new,
        "servers": servers,
        "profile": profile,
        "export": PartnerExportService(session, ctx, partners),
    }


def assignments(session, profile_id) -> set[tuple[str, str, str]]:  # noqa: ANN001
    return {
        (str(r.instance_id), r.source_ref, r.account_number)
        for r in session.scalars(
            select(ExportAccountAssignmentRecord).where(
                ExportAccountAssignmentRecord.profile_id == profile_id
            )
        )
    }


def rows(preview) -> dict[tuple[str, str], Any]:  # noqa: ANN001
    return {(r.source_ref, r.role): r for r in preview.records}


def test_same_vat_and_role_from_another_source_is_blocked_never_by_name(dup):
    p = rows(dup["export"].preview(*TARGET, dup["new"].id))
    blocked = p[("n-1", "CUSTOMER")]
    assert blocked.status == RecordStatus.BLOCKED
    assert (
        blocked.issues[0].message
        == "Already exported from old books as 10000 (same VAT ID): not exported twice"
    )
    assert p[("n-2", "CUSTOMER")].status != RecordStatus.BLOCKED  # Beta n'était exporté que comme fournisseur
    assert p[("n-3", "CUSTOMER")].status != RecordStatus.BLOCKED  # même nom, aucune TVA : jamais rapproché
    assert p[("n-4", "CUSTOMER")].status != RecordStatus.BLOCKED
    # l'ancienne source a été relue en GET seulement
    old = dup["servers"].by_key[KEYS["old"]]
    assert old.writes == [] and {c.path for c in old.calls} == {"/v1/contacts"}


def test_blocked_duplicate_is_never_written_and_existing_numbers_never_change(dup):
    session = dup["session"]
    before = assignments(session, dup["profile"].id)
    d11 = session.scalar(select(func.count()).select_from(ExportDocumentRecord))
    out = dup["export"].generate(*TARGET, dup["new"].id)
    assert out.run.records == 3 and out.run.blocked == 1  # n-1 jamais écrit
    after = assignments(session, dup["profile"].id)
    assert before <= after  # aucun numéro existant modifié ni retiré
    added = after - before
    assert {ref for _, ref, _ in added} == {"n-2", "n-3", "n-4"}  # aucun numéro réservé pour le doublon
    assert all(i == str(dup["new"].id) for i, _, _ in added)
    assert session.scalar(select(func.count()).select_from(ExportDocumentRecord)) == d11  # D11 non touché


def test_unreadable_other_source_blocks_new_partners_with_a_strong_identity_only(dup):
    dup["servers"].by_key[KEYS["old"]].fail[("GET", "/v1/contacts")] = 401
    p = rows(dup["export"].preview(*TARGET, dup["new"].id))
    for key in (("n-1", "CUSTOMER"), ("n-2", "CUSTOMER"), ("n-4", "CUSTOMER")):
        assert p[key].status == RecordStatus.BLOCKED
        assert p[key].issues[0].message == "Duplicate check not possible: old books could not be read"
    assert p[("n-3", "CUSTOMER")].status != RecordStatus.BLOCKED  # sans TVA : rien à comparer


def test_existing_numbers_of_the_same_source_are_kept_and_not_rechecked(dup):
    session = dup["session"]
    session.add(
        ExportAccountAssignmentRecord(
            profile_id=dup["profile"].id,
            instance_id=dup["new"].id,
            source_ref="n-1",
            role="CUSTOMER",
            account_number="10050",
            origin="ASSIGNED",
        )
    )
    session.flush()
    p = rows(dup["export"].preview(*TARGET, dup["new"].id))
    assert (p[("n-1", "CUSTOMER")].account_number, p[("n-1", "CUSTOMER")].account_new) == ("10050", False)
    assert p[("n-1", "CUSTOMER")].status != RecordStatus.BLOCKED  # état existant conservé, jamais modifié


def test_no_other_source_in_the_profile_means_no_extra_read(dup):
    p = rows(dup["export"].preview(*TARGET, dup["old"].id))  # la source déjà exportée : numéros existants
    assert all(r.status != RecordStatus.BLOCKED for r in p.values())
    assert dup["servers"].by_key[KEYS["new"]].calls == []  # aucune autre source attribuée : jamais lue
