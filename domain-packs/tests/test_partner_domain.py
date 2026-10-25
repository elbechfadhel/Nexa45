"""Domaine PARTNER (Odoo) : même moteur que PRODUCT, packs `partner/`, sur les schémas RÉELS complets.

Chaîne réelle : baseline standard publiée → instance → découverte (faux serveur XML-RPC) → analyse PARTNER
(domaine effectif + Semantic Enrichment), sans LLM. L'extension custom `x_customer_no` de customer17 doit
devenir UNE question (proposition « customer_number » par le vocabulaire), jamais une correspondance codée
en dur."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
import uie_domain_packs  # noqa: F401 — enregistre les packs découverts
from pydantic import SecretStr
from test_catalog_odoo_instance import FakeOdooResolver
from uie_connectors.odoo import OdooSchemaLoader
from uie_connectors.odoo.testing import FakeOdooServer
from uie_core.domains import (
    DomainKey,
    DomainPackRegistry,
    EntityRole,
    MeaningStatus,
    PartnerDomainService,
    ProductDomainService,
    SemanticEnrichmentService,
)
from uie_core.identity import GO45_DEV_ORG_ID, SystemContext
from uie_core.instances import FernetSecretStore, InstanceCreate, InstanceService
from uie_core.registry import RegistryService

ROOT = Path(__file__).resolve().parents[2]
DUMPS = ROOT / "docs" / "dumps"
PASSWORD = "Odoo-Cust0mer-S3cret"
CTX = SystemContext("test")


def _instance(session, version: str, instance_dump: str):  # noqa: ANN001, ANN202
    RegistryService(session, CTX).publish_snapshot(
        OdooSchemaLoader({"json": DUMPS / f"odoo{version}_ref.json"}).build_snapshot()
    )
    fake = FakeOdooServer.from_dump(DUMPS / instance_dump, password=PASSWORD)
    instances = InstanceService(
        session, CTX, FernetSecretStore(FernetSecretStore.generate_key()), FakeOdooResolver(fake)
    )
    inst = instances.create(
        InstanceCreate(
            organization_id=GO45_DEV_ORG_ID,
            name=f"partner-{instance_dump.split('.')[0]}",
            system="odoo",
            version=version,
            endpoint="http://odoo:8069",
            config={"db": fake.db, "username": "admin"},
            password=SecretStr(PASSWORD),
        )
    )
    instances.discover_schema(inst.id)
    return inst


def _partner(session, inst):  # noqa: ANN001, ANN202
    domains = PartnerDomainService(session, CTX)
    outcome = domains.analyze(inst.id)
    return outcome, SemanticEnrichmentService(session, CTX, domains=domains)


@pytest.fixture
def customer17(session):  # noqa: ANN001, ANN201
    inst = _instance(session, "17", "odoo17_customer.json")
    return inst, *_partner(session, inst)


def test_partner_pack_registered_for_odoo():
    assert "PARTNER/odoo" in DomainPackRegistry.available()


def test_customer17_partner_domain(customer17):
    inst, outcome, svc = customer17
    snap = outcome.stored.snapshot
    assert snap.domain_key == DomainKey.PARTNER
    roles = {e.role: e.technical_name for e in snap.entities if e.present}
    assert roles == {
        EntityRole.PRIMARY: "res.partner",
        EntityRole.BANK_ACCOUNT: "res.partner.bank",
        EntityRole.BANK: "res.bank",
        EntityRole.COUNTRY: "res.country",
    }
    # extensions de customer17 : x_customer_no (CUSTOM) + champs ajoutés par hr / project / rating
    # (THIRD_PARTY) ; seule l'extension custom reste une question, les autres sont exclues par règle
    assert "res.partner.x_customer_no" in [f.path for f in snap.extensions]
    s = svc.summary(inst.id)
    assert s.pending == 1 and not s.complete


def test_customer17_x_customer_no_is_the_only_question(customer17):
    inst, _, svc = customer17
    [q] = svc.get_pending_questions(inst.id)
    assert q.path == "res.partner.x_customer_no"
    assert q.semantic_key == "customer_number"  # vocabulaire (libellé « Customer No »), pas une règle codée


def test_listed_fields_are_known(customer17):
    inst, _, svc = customer17
    rows = {r.path: r for r in svc.list_bindings(inst.id)}
    for path, key in {
        "res.partner.name": "name",
        "res.partner.vat": "vat_id",
        "res.partner.customer_rank": "customer_rank",
        "res.partner.supplier_rank": "supplier_rank",
        "res.partner.country_id": "country",
        "res.country.code": "country_code",
        "res.partner.bank.acc_number": "bank_account_number",
        "res.bank.bic": "bic",
    }.items():
        assert (rows[path].semantic_key, rows[path].meaning_status) == (key, MeaningStatus.KNOWN), path


def test_answer_completes_partner_analysis(customer17):
    inst, _, svc = customer17
    svc.confirm(inst.id, "res.partner", "x_customer_no", "customer_number")
    s = svc.summary(inst.id)
    assert s.pending == 0 and s.complete


def test_partner_and_product_coexist(session, customer17):
    """Même instance, deux domaines : définitions, domain snapshots et bindings séparés ; PRODUCT intact."""
    inst, _, partner = customer17
    product_domains = ProductDomainService(session, CTX)
    product_domains.analyze(inst.id)
    product = SemanticEnrichmentService(session, CTX, domains=product_domains)
    assert product_domains.get_instance_domain(inst.id).snapshot.domain_key == DomainKey.PRODUCT
    assert partner.domains.get_instance_domain(inst.id).snapshot.domain_key == DomainKey.PARTNER
    assert sorted(q.path for q in product.get_pending_questions(inst.id)) == [
        "product.template.x_notes_internal",
        "product.template.x_pack_code",
        "product.template.x_supplier_ref",
    ]
    assert [q.path for q in partner.get_pending_questions(inst.id)] == ["res.partner.x_customer_no"]


@pytest.mark.parametrize("version", ["17", "18", "19"])
def test_standard_odoo_has_no_partner_question(session, version):  # noqa: ANN001
    """Odoo standard : aucune question (une question = une vraie ambiguïté), champs déclarés présents."""
    inst = _instance(session, version, f"odoo{version}_ref.json")
    outcome, svc = _partner(session, inst)
    assert svc.get_pending_questions(inst.id) == []
    # champ déclaré absent de la version (mobile n'existe plus en 19) : hors du domaine effectif, sans erreur
    paths = {f.path for f in outcome.stored.snapshot.fields}
    assert ("res.partner.mobile" in paths) == (version != "19")
    statuses = Counter(r.meaning_status for r in svc.list_bindings(inst.id))
    assert statuses[MeaningStatus.KNOWN] >= 30
