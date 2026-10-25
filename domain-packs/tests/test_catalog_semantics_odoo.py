"""Semantic Enrichment (V1b) de l'instance client Odoo 17 (fixture odoo17_customer_instance.json).

Vrai domain pack YAML (règles R01-R10 + vocabulaire), sans LLM : le moteur classe seul et ne pose au
supplier que les extensions custom ambiguës (x_supplier_ref, x_pack_code, x_notes_internal).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
import uie_domain_packs  # noqa: F401 — enregistre les packs découverts
from pydantic import SecretStr
from test_catalog_odoo_instance import FIX, PASSWORD, FakeOdooResolver
from uie_connectors.odoo import OdooSchemaLoader
from uie_connectors.odoo.testing import FakeOdooServer
from uie_core.domains import (
    DecisionSource,
    MeaningStatus,
    ProductDomainService,
    Relevance,
    SemanticEnrichmentService,
)
from uie_core.identity import GO45_DEV_ORG_ID, SystemContext
from uie_core.instances import FernetSecretStore, InstanceCreate, InstanceService
from uie_core.registry import RegistryService

THIRD_PARTY = [
    "product.template.project_id",
    "product.template.project_template_id",
    "product.template.rating_ids",
    "product.template.service_policy",
    "product.template.service_tracking",
]


@dataclass
class World:
    fake: FakeOdooServer
    instances: InstanceService
    domains: ProductDomainService
    svc: SemanticEnrichmentService
    instance_id: object


@pytest.fixture
def world(session) -> World:
    RegistryService(session, SystemContext("test")).publish_snapshot(
        OdooSchemaLoader({"json": FIX / "odoo17_ref.json"}).build_snapshot()
    )
    fake = FakeOdooServer.from_dump(FIX / "odoo17_customer_instance.json", password=PASSWORD)
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
    domains = ProductDomainService(session, SystemContext("test"))
    return World(
        fake,
        instances,
        domains,
        SemanticEnrichmentService(session, SystemContext("test"), domains=domains),
        inst.id,
    )


@pytest.fixture
def analyzed(world: World):
    calls = len(world.fake.calls)
    outcome = world.domains.analyze(world.instance_id)
    assert len(world.fake.calls) == calls  # jamais de lecture de l'instance pendant l'analyse
    return outcome, {b.path: b for b in world.svc.list_bindings(world.instance_id)}


def test_third_party_extensions_are_excluded_by_rule_never_asked(world, analyzed):
    _, b = analyzed
    questions = {q.path for q in world.svc.get_pending_questions(world.instance_id)}
    for path in THIRD_PARTY:  # 15.
        assert (b[path].relevance, b[path].meaning_status) == (Relevance.NOT_RELEVANT, MeaningStatus.EXCLUDED)
        assert b[path].rule_id in {"R04", "R05"} and b[path].reason and path not in questions
    assert b["product.template.project_id"].reason == (
        "Link to project.project, which is not part of the product catalog"
    )
    assert b["product.template.service_policy"].rule_id == "R05"
    assert "sale_project" in b["product.template.service_policy"].reason
    excluded = {x.path for x in world.svc.list_exclusions(world.instance_id, extensions_only=True)}
    assert excluded == set(THIRD_PARTY)


def test_custom_extensions_are_the_only_questions(world, analyzed):
    outcome, b = analyzed
    sup = b["product.template.x_supplier_ref"]  # 16.
    assert (sup.meaning_status, sup.semantic_key, sup.source) == (
        MeaningStatus.PROPOSED,
        "supplier_reference",
        DecisionSource.RULE,
    )
    assert sup.confidence >= 0.90 and sup.pending
    pack = b["product.template.x_pack_code"]  # 17.
    assert (pack.meaning_status, pack.semantic_key, pack.pending) == (
        MeaningStatus.PROPOSED,
        "packaging_code",
        True,
    )
    notes = b["product.template.x_notes_internal"]  # 18.
    assert (notes.meaning_status, notes.semantic_key, notes.pending) == (MeaningStatus.UNRESOLVED, None, True)
    # copie related sur la variante : suit x_supplier_ref, pas de question en double
    copy = b["product.product.x_supplier_ref"]
    assert (copy.mirror_of, copy.pending, copy.semantic_key) == (
        "product.template.x_supplier_ref",
        False,
        "supplier_reference",
    )
    # CUSTOM, confiance décroissante (égalité à 0.95 : ordre du chemin), sans proposition en dernier
    assert [q.path for q in world.svc.get_pending_questions(world.instance_id)] == [
        "product.template.x_pack_code",
        "product.template.x_supplier_ref",
        "product.template.x_notes_internal",
    ]
    assert outcome.enrichment.pending == 3


def test_standard_catalog_fields_are_known(analyzed):
    _, b = analyzed
    for path, key in {  # 19.
        "product.template.default_code": "sku",
        "product.template.list_price": "list_price",
        "product.template.categ_id": "category",
        "product.template.image_1920": "main_image",
    }.items():
        assert (b[path].meaning_status, b[path].semantic_key) == (MeaningStatus.KNOWN, key), path
        assert b[path].relevance == Relevance.RELEVANT and not b[path].pending
    assert b["product.template.id"].rule_id == "R01"


def test_summary_is_consistent(world, analyzed):
    outcome, b = analyzed
    s = world.svc.summary(world.instance_id)  # 20.
    assert s.identified + s.excluded_auto + s.excluded_by_supplier + s.possible + s.pending == s.total
    assert s.total == len(outcome.stored.snapshot.fields) == len(b)
    assert (s.pending, s.extensions, s.extensions_pending, s.extensions_excluded_auto) == (3, 9, 3, 5)
    assert all(c.present for c in s.structure if c.role.value in {"PRIMARY", "VARIANT", "CATEGORY", "MEDIA"})
    assert s.rules_version and s.rules_version == outcome.enrichment.rules_version


def test_answers_complete_the_analysis_and_survive_a_new_discovery(world, analyzed):
    svc, iid = world.svc, world.instance_id
    svc.confirm(iid, "product.template", "x_supplier_ref")
    svc.change(iid, "product.template", "x_pack_code", "packaging_code")
    svc.ignore(iid, "product.template", "x_notes_internal")
    s = svc.summary(iid)
    assert s.complete and s.pending == 0 and s.excluded_by_supplier == 1
    mirrored = next(b for b in svc.list_bindings(iid) if b.path == "product.product.x_supplier_ref")
    assert (mirrored.meaning_status, mirrored.semantic_key) == (MeaningStatus.CONFIRMED, "supplier_reference")
    # nouvelle découverte (rien n'a changé) → nouvelle analyse : décisions réappliquées, aucune question
    world.instances.discover_schema(iid)
    e = world.domains.analyze(iid).enrichment
    assert e.instance_schema_snapshot_sequence == 2 and (e.reapplied, e.stale, e.pending, e.created) == (
        3,
        0,
        0,
        0,
    )
    assert svc.summary(iid).complete and svc.get_pending_questions(iid) == []
